#!/usr/bin/env python3
"""Shared machinery for the killer skills' bookkeeping scripts: run folders, scope and sandboxes.

This file is copied byte for byte into every skill that uses it, next to run.py, by
tools/sync.py. Edit tools/shared/kit.py, then run `python tools/sync.py`; CI fails when a
copy drifts. A skill's script loads it by path, because `python -I` leaves the script's
own folder off sys.path:

    import importlib.util
    spec = importlib.util.spec_from_file_location("killer_kit", HERE / "kit.py")
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)

What it gives a script:

  run folders   kit.init_run(...) makes ~/.claude/killer-<skill>/<project>/<date>-<slug>/,
                writes config.json and scope.txt, and prints what it found.
                KILLER_<SKILL>_HOME moves the runs.
  scope         the whole project, one folder, uncommitted changes, or a branch's changes.
  sandboxes     kit.make_sandbox(...) copies the project (committed and uncommitted work)
                into the temp folder, with its own home, temp and app-data folders, no
                credentials, and a dead proxy for the network unless the run allows it.
                KILLER_<SKILL>_SANDBOXES moves them. Each sandbox has:
                  repo/      the project copy, a git repository whose HEAD is a commit of
                             the project as copied ("the base")
                  scratch/   where the agent keeps its files and writes reply.json
                  logs/      every run's output
                  run.py     runs a command in repo/ with the sandbox's environment
                With base=False the copy keeps the project's own git state instead: HEAD,
                branch and what's staged as they are in the project, uncommitted work left
                uncommitted, and no base commit, so tools that read git (a version from git
                describe, a hook that reads what's staged) behave as they do in the project.
                Such a sandbox can't be diffed. scratch= names the scratch folder.
  diffs         kit.box_diff(box) is everything an agent changed in repo/ since the base,
                as a patch with binary files; kit.apply_patch(folder, patch) applies one.
  fresh reruns  kit.run_fresh(...) runs a command in a new sandbox, optionally with a
                patch applied and a scratch folder copied in, then deletes it. A claim an
                agent makes about its own sandbox is checked this way, never taken on trust.

The isolation is environment variables, not a container. With the network off, everything
goes through a proxy that doesn't exist: Python, Node.js, git, curl, npm and pip honour it;
Windows PowerShell 5.1 and programs that open sockets themselves don't. Standard library only.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shlex
import shutil
import site
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT_VERSION = 1
REPLY = "reply.json"
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache",
             ".pytest_cache", "dist", "build", "target", ".next", ".cache"}
SECRET_NAMES = r"TOKEN|SECRET|PASSW(OR)?D|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL|SESSION_?KEY"
DROP_NAMES = ["SSH_AUTH_SOCK", "SSH_AGENT_PID", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH_COMMAND",
              # the host's import paths, so code in a sandbox can't import the real project's copy
              "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "NODE_PATH"]
DEAD_PROXY = "http://127.0.0.1:9"
LOOPBACK = "localhost,127.0.0.1,::1"
OFFLINE_NOTE = ("off, through a dead proxy (this machine's own addresses still work): Python, Node.js, "
                "git, curl, npm and pip honour it; Windows PowerShell 5.1 and raw sockets don't")
TEXT_LIMIT = 1_000_000
RAW = ["-c", "core.autocrlf=false", "-c", "core.safecrlf=false"]   # bytes as they are on disk
BASE_IDENTITY = ["-c", "user.name=killer-skills", "-c", "user.email=killer-skills@localhost",
                 "-c", "commit.gpgsign=false", *RAW]
JUNK = ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/"]

_runner = None


def runner():
    """run.py from this folder, loaded by path."""
    global _runner
    if _runner is None:
        spec = importlib.util.spec_from_file_location("killer_run", HERE / "run.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _runner = mod   # published only once it's loaded: threads may call this at once
    return _runner


# ---------------------------------------------------------------- small helpers

def fail(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def utf8_stdout() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def today() -> str:
    return dt.date.today().isoformat()


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def load_json(path: Path, default):
    path, deadline = Path(path), time.monotonic() + 15
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else default
        except PermissionError:   # Windows, while another process replaces the file
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)


def save_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{secrets.token_hex(2)}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    replace(tmp, path)


def replace(src: Path, dst: Path, wait: float = 15) -> None:
    """os.replace, retried: on Windows it fails while another process has dst open."""
    deadline = time.monotonic() + wait
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)


@contextlib.contextmanager
def locked(ws: Path, timeout: float = 300):
    """One script at a time may change a run's files; commands can still run in parallel."""
    ws = Path(ws)
    ws.mkdir(parents=True, exist_ok=True)
    lock, start = ws / ".lock", time.monotonic()
    # On Windows a lock file that's just been deleted stays "delete pending" while another process still
    # has it open (checking its age, say): creating, checking or deleting it then raises PermissionError.
    # That's the lock being busy, not an error, so wait it out like a held lock.
    busy = (FileExistsError, PermissionError) if os.name == "nt" else (FileExistsError,)
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except busy:
            try:
                if time.time() - lock.stat().st_mtime > 600:   # left by a crash
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            except PermissionError:
                if os.name != "nt":
                    raise
            if time.monotonic() - start > timeout:
                fail(f"{lock} is held by another process; delete it if nothing is running")
            time.sleep(0.05)
    try:
        yield
    finally:
        deadline = time.monotonic() + 15
        while True:
            try:
                lock.unlink(missing_ok=True)
                break
            except PermissionError:   # Windows, while another process checks the lock
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.02)


def git(cwd: Path, *args: str, check: bool = True, env: dict | None = None, data: str | None = None) -> str:
    r = subprocess.run(["git", "-C", str(cwd), "-c", "core.longpaths=true", *args], input=data,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env)
    if check and r.returncode:
        fail(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout if r.returncode == 0 else ""


def git_ok(cwd: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True).returncode == 0


def split0(s: str) -> list[str]:
    return [x for x in s.split("\0") if x]


def rmtree(path: Path) -> None:
    def retry(func, p, _exc):   # git's object files are read-only, which stops Windows deleting them
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def read_reply(path: Path) -> dict:
    """An agent's JSON: the whole file, or the last ```json block in a saved reply."""
    path = Path(path)
    if not path.is_file():
        fail(f"no reply at {path}")
    text = path.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
        for block in reversed(re.findall(r"```(?:json)?[ \t]*\n(.*?)```", text, re.S)):
            try:
                data = json.loads(block)
                break
            except json.JSONDecodeError:
                continue
        if data is None:
            fail(f"{path} has no JSON that parses")
    if not isinstance(data, dict):
        fail(f"{path}: expected a JSON object")
    return data


def as_list(v) -> list:
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def as_cmd(v) -> list[str]:
    """A command as a list of arguments; a string is split shell-style (use forward slashes)."""
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.strip():
        return shlex.split(v)
    return []


def strip_dashes(cmd: list[str]) -> list[str]:
    return cmd[1:] if cmd[:1] == ["--"] else cmd


def parse_env(pairs) -> dict:
    out = {}
    for p in pairs or []:
        name, eq, value = p.partition("=")
        if not eq or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            fail(f"--env takes NAME=VALUE, not {p!r}")
        out[name] = value
    return out


def slugify(s: str, default: str = "run") -> str:
    return re.sub(r"[^a-z0-9-]+", "-", (s or "").lower()).strip("-")[:40].strip("-") or default


# ---------------------------------------------------------------- the project and its scope

def find_top(path: Path) -> tuple[Path, bool]:
    out = git(path, "rev-parse", "--show-toplevel", check=False).strip()
    return (Path(out).resolve(), True) if out else (Path(path).resolve(), False)


def has_head(top: Path) -> bool:
    return git_ok(top, "rev-parse", "--verify", "-q", "HEAD")


def project_key(top: Path, is_git: bool) -> str:
    ident = git(top, "config", "--get", "remote.origin.url", check=False).strip() if is_git else ""
    ident = ident or (str(top).lower() if os.name == "nt" else str(top))
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", top.name).strip("-") or "project"
    return f"{slug}-{hashlib.sha1(ident.encode()).hexdigest()[:8]}"


def all_files(top: Path, is_git: bool) -> list[str]:
    if is_git:
        names = split0(git(top, "ls-files", "-z", "-c", "-o", "--exclude-standard"))
        return sorted({n for n in names if (top / n).is_file()})
    out = []
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in filenames:
            out.append((Path(dirpath) / f).relative_to(top).as_posix())
    return sorted(out)


def changed_since(top: Path, base: str) -> list[str]:
    names = split0(git(top, "diff", "--name-only", "--no-renames", "-z", base))
    names += split0(git(top, "ls-files", "-z", "-o", "--exclude-standard"))
    return sorted(set(names))


def default_base(top: Path) -> str:
    for ref in ("origin/main", "origin/master", "main", "master"):
        if git_ok(top, "rev-parse", "--verify", "-q", ref + "^{commit}"):
            return ref
    fail("no main or master branch to compare with; give one, like --scope branch:develop")


def is_text(path: Path) -> bool:
    try:
        if path.stat().st_size > TEXT_LIMIT:
            return False
        with path.open("rb") as f:
            return b"\0" not in f.read(8192)
    except OSError:
        return False


def excluded(f: str, patterns) -> bool:
    for p in patterns or []:
        p = p.replace("\\", "/").rstrip("/")
        if fnmatch.fnmatch(f, p) or f.startswith(p + "/") or fnmatch.fnmatch(f.rsplit("/", 1)[-1], p):
            return True
    return False


def resolve_scope(spec: str, top: Path, is_git: bool) -> tuple[dict, list[str]]:
    """repo | path:<folder or file> | changes | branch[:<base>] -> (scope record, files)."""
    kind, _, arg = (spec or "repo").partition(":")
    if kind == "repo":
        files, scope = all_files(top, is_git), {"kind": "repo"}
    elif kind == "path":
        if not arg:
            fail("give the folder or file, like --scope path:src")
        p = Path(arg)
        rel = (p.resolve() if p.is_absolute() else (top / p).resolve())
        try:
            sub = rel.relative_to(top).as_posix()
        except ValueError:
            fail(f"{arg} isn't inside {top}")
        sub = "" if sub == "." else sub
        files = [f for f in all_files(top, is_git) if not sub or f == sub or f.startswith(sub + "/")]
        scope = {"kind": "path", "path": sub or "."}
    elif kind in ("changes", "branch"):
        if not is_git or not has_head(top):
            fail(f"--scope {kind} needs a git repository with at least one commit")
        if kind == "changes":
            base, ref = "HEAD", "HEAD"
        else:
            ref = arg or default_base(top)
            base = git(top, "merge-base", ref, "HEAD").strip()
        files = [f for f in changed_since(top, base) if (top / f).is_file()]
        scope = {"kind": kind, "base": base, "ref": ref}
    else:
        fail(f"unknown scope {spec!r}: use repo, path:<folder>, changes or branch[:<base>]")
    return scope, files


def describe_scope(scope: dict) -> str:
    return {"repo": "the whole project", "path": scope.get("path"), "changes": "uncommitted changes",
            "branch": f"changes since {scope.get('ref')}"}.get(scope["kind"], scope["kind"])


# ---------------------------------------------------------------- run folders

def env_name(skill: str, what: str) -> str:
    return f"KILLER_{re.sub(r'[^A-Za-z0-9]', '_', skill).upper()}_{what}"


def runs_home(skill: str) -> Path:
    return Path(os.environ.get(env_name(skill, "HOME")) or Path.home() / ".claude" / f"killer-{skill}").expanduser()


def sandboxes_home(skill: str) -> Path:
    return Path(os.environ.get(env_name(skill, "SANDBOXES")) or tempfile.gettempdir()).expanduser() / f"k{skill[:2]}"


def init_run(skill: str, repo: str, *, scope: str = "repo", slug: str | None = None, target: str = "",
             network: bool = False, include=(), exclude=(), env=(), workspace: str | None = None,
             force: bool = False, extra: dict | None = None, quiet: bool = False) -> tuple[Path, dict, list[str]]:
    """Make a run folder and its config.json; returns (run folder, config, text files in scope)."""
    path = Path(repo).expanduser().resolve()
    if not path.is_dir():
        fail(f"no folder {path}")
    top, is_git = find_top(path)
    scope_rec, files = resolve_scope(scope, top, is_git)
    files = [f for f in files if not excluded(f, exclude)]
    text = [f for f in files if is_text(top / f)]
    key = project_key(top, is_git)
    proj = runs_home(skill) / key
    name = slugify(slug or scope_rec["kind"])
    if workspace:
        ws = Path(workspace).expanduser().resolve()
        if (ws / "config.json").exists() and not force:
            fail(f"{ws} already has a run; pass --force to start over")
    else:
        ws, n = proj / f"{today()}-{name}", 2
        while (ws / "config.json").exists():
            ws, n = proj / f"{today()}-{name}-{n}", n + 1
    head = git(top, "rev-parse", "HEAD", check=False).strip() if is_git else ""
    cfg = {
        "kit": KIT_VERSION,
        "skill": skill,
        "run_id": secrets.token_hex(3),
        "repo": str(top),
        "project": key,
        "git": is_git,
        "head": head or None,
        "branch": (git(top, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip() or None) if is_git else None,
        "scope": scope_rec,
        "target": target or "",
        "network": bool(network),
        "created": now(),
        "include": list(include or []),
        "exclude": list(exclude or []),
        "env": parse_env(env),
        "sandboxes_home": str(sandboxes_home(skill)),
        "sandboxes": {},
        **(extra or {}),
    }
    ws.mkdir(parents=True, exist_ok=True)
    save_json(ws / "config.json", cfg)
    (ws / "scope.txt").write_text("".join(f + "\n" for f in text), encoding="utf-8")
    if not quiet:
        print(f"Run folder: {ws}")
        print(f"Project: {top}" + (f" ({cfg['branch'] or 'detached'} at {head[:10]})" if head else ""))
        skipped = len(files) - len(text)
        print(f"Scope: {describe_scope(scope_rec)}, {len(text)} text files"
              + (f" ({skipped} binary or over 1 MB left out)" if skipped else ""))
        if not text:
            print("warning: nothing in scope")
        print(f"Network in sandboxes: {'allowed' if network else 'off'}")
        if cfg["env"]:
            print(f"Set in every sandbox: {', '.join(cfg['env'])}")
    return ws, cfg, text


def load_run(workspace: str | Path) -> tuple[Path, dict]:
    ws = Path(workspace).expanduser().resolve()
    cfg = load_json(ws / "config.json", None)
    if cfg is None:
        fail(f"no config.json in {ws}; run init first")
    return ws, cfg


def update_cfg(ws: Path, fn):
    """Change config.json under the run's lock; returns what fn returns."""
    with locked(ws):
        cfg = load_json(Path(ws) / "config.json", {})
        out = fn(cfg)
        save_json(Path(ws) / "config.json", cfg)
    return out


def scope_files(ws: Path) -> list[str]:
    p = Path(ws) / "scope.txt"
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln] if p.is_file() else []


# ---------------------------------------------------------------- sandboxes

def toolchain_homes() -> dict:
    """Where installed toolchains keep their packages. A new home folder would hide them."""
    home = Path.home()
    if os.name == "nt":
        browsers = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "ms-playwright"
    elif sys.platform == "darwin":
        browsers = home / "Library" / "Caches" / "ms-playwright"
    else:
        browsers = Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache") / "ms-playwright"
    found = {
        "PYTHONUSERBASE": Path(site.getuserbase()),
        "CARGO_HOME": home / ".cargo",
        "RUSTUP_HOME": home / ".rustup",
        "GOPATH": home / "go",
        "NUGET_PACKAGES": home / ".nuget" / "packages",
        "GRADLE_USER_HOME": home / ".gradle",
        "PLAYWRIGHT_BROWSERS_PATH": browsers,
    }
    return {k: str(v) for k, v in found.items() if k not in os.environ and v.exists()}


def sandbox_env(root: Path, network: bool, extra: dict, scratch: str = "scratch", path_first=()) -> dict:
    home, tmp = root / "home", root / "tmp"
    folders = {
        "HOME": home, "USERPROFILE": home,
        "APPDATA": home / "AppData" / "Roaming", "LOCALAPPDATA": home / "AppData" / "Local",
        "XDG_CONFIG_HOME": home / ".config", "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local" / "share", "XDG_STATE_HOME": home / ".local" / "state",
        "TEMP": tmp, "TMP": tmp, "TMPDIR": tmp,
    }
    for p in folders.values():
        p.mkdir(parents=True, exist_ok=True)
    env = {k: str(v) for k, v in folders.items()}
    if os.name == "nt":
        env["HOMEDRIVE"], env["HOMEPATH"] = home.drive, str(home)[len(home.drive):]
    env.update(toolchain_homes())
    # Toolchains still find what's installed, but anything a command installs lands in the sandbox.
    env.update({"CARGO_INSTALL_ROOT": str(home / ".cargo-install"), "GOBIN": str(home / "go" / "bin"),
                "npm_config_prefix": str(home / ".npm-global")})
    env.update({
        "KILLER_SANDBOX": str(root), "KILLER_REPO": str(root / "repo"), "KILLER_SCRATCH": str(root / scratch),
        "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
    })
    if not network:
        names = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]
        for n in names + ([x.lower() for x in names] if os.name != "nt" else []):
            env[n] = DEAD_PROXY
        env["NO_PROXY"] = LOOPBACK   # a project's tests often talk to a server on this machine
        if os.name != "nt":
            env["no_proxy"] = LOOPBACK
        env.update({"NODE_USE_ENV_PROXY": "1", "npm_config_offline": "true", "PIP_NO_INDEX": "1",
                    "UV_OFFLINE": "1", "CARGO_NET_OFFLINE": "true", "GOPROXY": "off"})
    # "{sandbox}" in a setting is this sandbox's folder, so each agent gets its own copy of, say, a
    # trap folder that stands in for the user's real projects folder.
    here = lambda v: str(v).replace("{sandbox}", str(root))
    env.update({k: here(v) for k, v in extra.items()})
    return {"network": network, "set": env, "drop": DROP_NAMES, "drop_pattern": SECRET_NAMES,
            "path_first": [here(d) for d in path_first]}


def stage_exact(repo: Path, files, env: dict | None = None) -> None:
    """Stage these files exactly as they are on disk, deletions included.

    git takes a file as unchanged when its size and times match what its index recorded. A file copied
    with its old modification time (copy_path keeps it) and the same size, a lockfile whose version
    digits changed, can match by chance and be left out of the commit. So drop the files from the index
    first: then git hashes every one of them."""
    files = list(dict.fromkeys(files))
    present = [f for f in files if (Path(repo) / f).exists() or (Path(repo) / f).is_symlink()]
    for i in range(0, len(files), 100):
        git(repo, "--literal-pathspecs", "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", *files[i:i + 100],
            env=env)
    for i in range(0, len(present), 100):
        git(repo, "--literal-pathspecs", "add", "-A", "--", *present[i:i + 100], env=env)


def copy_path(src: Path, dst: Path) -> None:
    if src.is_dir() and not src.is_symlink():
        shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        shutil.copy2(src, dst, follow_symlinks=False)


def box_path(cfg: dict, name: str) -> Path:
    return Path(cfg["sandboxes_home"]) / cfg["run_id"] / name


def _commit_base(dst: Path, excludes: list[str]) -> str:
    """Commit the copy as it stands, so box_diff can show exactly what an agent changed."""
    info = dst / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    with (info / "exclude").open("a", encoding="utf-8", newline="\n") as f:
        f.write("".join(f"{j}\n" for j in JUNK) + "".join(f"/{e.strip('/')}\n" for e in excludes))
    # Compare bytes as they are on disk: no line-ending conversion and no filters (git LFS),
    # whatever the project's .gitattributes says. info/attributes outranks every other one.
    (info / "attributes").write_text("* -text -filter\n", encoding="utf-8", newline="\n")
    (dst / ".git" / "index").unlink(missing_ok=True)   # so every file is hashed again, raw
    git(dst, *RAW, "add", "-A")
    git(dst, *BASE_IDENTITY, "commit", "-q", "--no-verify", "--allow-empty", "-m", "sandbox base")
    return git(dst, "rev-parse", "HEAD").strip()


def make_sandbox(cfg: dict, ws: Path, name: str, include=(), rev: str | None = None,
                 network: bool | None = None, env: dict | None = None, *, base: bool = True,
                 scratch: str = "scratch", path_first=()) -> Path:
    """A fresh copy of the project for one agent or one check; returns the sandbox folder.

    base=False keeps the project's git state and makes no base commit (see the module docstring)."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        fail("a sandbox name is letters, digits, dots, dashes and underscores")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", scratch) or scratch in ("repo", "logs", "home", "tmp"):
        fail(f"{scratch!r} can't be a scratch folder's name")
    root = box_path(cfg, name)
    if root.exists():
        fail(f"sandbox {name} already exists at {root}; pick another name, or run clean")
    src, dst = Path(cfg["repo"]), root / "repo"
    root.mkdir(parents=True)
    update_cfg(ws, lambda c: c.setdefault("sandboxes", {}).__setitem__(name, str(root)))
    notes = []
    try:
        if cfg["git"] and has_head(src):
            sha = git(src, "rev-parse", "--verify", (rev or "HEAD") + "^{commit}").strip()
            r = subprocess.run(["git", "-c", "core.longpaths=true", "clone", "-q", "--no-checkout", str(src), str(dst)],
                               capture_output=True, text=True)
            if r.returncode:
                fail(f"git clone into the sandbox failed: {r.stderr.strip()}")
            git(dst, "remote", "remove", "origin")   # nothing in the sandbox can push to the real repo
            # The project's own line-ending settings, so an older revision checks out the way the
            # project's working tree does (Git for Windows sets core.autocrlf=true system-wide; a
            # project may turn it off for itself). The clone has no checkout yet, so they apply.
            for key in ("user.name", "user.email", "core.autocrlf", "core.eol"):
                v = git(src, "config", key, check=False).strip()
                if v:
                    git(dst, "config", key, v)
            if rev:
                git(dst, "checkout", "-q", "--detach", sha)
            else:
                # The project's files exactly as they are on disk, uncommitted work, line endings
                # and all, rather than as git would check them out.
                branch = cfg.get("branch")
                if branch:
                    git(dst, "update-ref", f"refs/heads/{branch}", sha)
                    git(dst, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
                else:
                    git(dst, "update-ref", "--no-deref", "HEAD", sha)
                git(dst, "read-tree", sha)
                for rel in all_files(src, True):
                    copy_path(src / rel, dst / rel)
                if not base:   # what's staged in the project is staged here too
                    staged = split0(git(src, "diff", "--cached", "--name-only", "--no-renames", "-z"))
                    for i in range(0, len(staged), 100):
                        git(dst, "add", "-A", "--", *staged[i:i + 100])
                    git(dst, "update-index", "-q", "--refresh", check=False)
            if (src / ".gitmodules").exists():
                notes.append("submodules aren't copied")
        else:
            if rev:
                fail("--rev needs a git repository with commits")
            shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns(".git"))
            if base:
                git(dst, "init", "-q")
        included = []
        for inc in dict.fromkeys([*cfg.get("include", []), *include]):
            s = (src / inc).resolve()
            if not s.exists():
                notes.append(f"--include {inc}: not found, skipped")
                continue
            copy_path(s, dst / s.relative_to(src))
            included.append(s.relative_to(src).as_posix())
        if base:
            (root / "base.txt").write_text(_commit_base(dst, included) + "\n", encoding="utf-8")
        for d in (scratch, "logs"):
            (root / d).mkdir(exist_ok=True)
        extra = {**cfg.get("env", {}), **(env or {})}
        first = [*path_first, *cfg.get("path_first", [])]   # this sandbox's own stand-ins win over the run's
        save_json(root / "env.json", sandbox_env(root, cfg["network"] if network is None else network, extra, scratch,
                                                 first))
        shutil.copy2(HERE / "run.py", root / "run.py")
    except BaseException:
        drop_sandbox(ws, cfg, name)
        raise
    for n in notes:
        print(f"note: {n}")
    return root


def find_sandbox(cfg: dict, name: str) -> Path:
    box = Path(cfg.get("sandboxes", {}).get(name) or box_path(cfg, name))
    if not box.is_dir():
        fail(f"no sandbox called {name} in this run")
    return box


def drop_sandbox(ws: Path, cfg: dict, name: str) -> None:
    path = update_cfg(ws, lambda c: c.get("sandboxes", {}).pop(name, None))
    cfg.get("sandboxes", {}).pop(name, None)
    rmtree(Path(path) if path else box_path(cfg, name))


def clean_sandboxes(ws: Path, cfg: dict) -> int:
    names = list(load_json(Path(ws) / "config.json", {}).get("sandboxes", {}))
    for n in names:
        drop_sandbox(ws, cfg, n)
    holder = Path(cfg["sandboxes_home"]) / cfg["run_id"]
    with contextlib.suppress(OSError):
        holder.rmdir()
    return len(names)


def run_command_line(root: Path) -> str:
    return f'"{sys.executable}" "{root / "run.py"}" -- <command>'


def sandbox_text(root: Path, network: bool, rev: str | None = None, scratch: str = "scratch") -> str:
    """What `<script> sandbox` prints, for the lead to paste into a brief."""
    return "\n".join([
        f"  project copy: {root / 'repo'}" + (f" (at {rev})" if rev else ""),
        f"  run things:   {run_command_line(root)}   (--stdin <file> feeds it input)",
        f"  scratch:      {root / scratch}   (the agent writes its reply to {REPLY} here)",
        f"  network:      {'allowed' if network else OFFLINE_NOTE}",
        "  home, temp and app-data folders are the sandbox's own; tokens and keys are left out",
    ])


def keep_scratch(box: Path, dst: Path, scratch: str = "scratch") -> None:
    """Copy an agent's scratch folder into the run, without its reply."""
    rmtree(dst)
    shutil.copytree(Path(box) / scratch, dst, ignore=shutil.ignore_patterns("__pycache__", REPLY, "*.pyc"))


def box_base(box: Path) -> str:
    p = Path(box) / "base.txt"
    if not p.is_file():
        fail(f"{box} was made with base=False, so it has no base commit to diff against")
    return p.read_text(encoding="utf-8").strip()


def box_diff(box: Path, paths=()) -> str:
    """Everything changed in the sandbox's repo/ since its base, as a binary-safe patch.

    The text is decoded with surrogateescape, so a file in any encoding survives; save it
    with write_patch and load it with read_patch."""
    repo, base = Path(box) / "repo", box_base(box)
    index = Path(box) / f"diff-index-{secrets.token_hex(3)}"
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    where = list(paths) or ["."]
    try:
        git(repo, "read-tree", base, env=env)
        git(repo, *RAW, "add", "-A", "--", *where, env=env)
        r = subprocess.run(["git", "-C", str(repo), "-c", "core.longpaths=true", *RAW, "diff", "--cached", "--binary",
                            "--no-renames", "--no-color", "--no-ext-diff", base, "--", *where],
                           capture_output=True, env=env)
        if r.returncode:
            fail(f"git diff in {repo}: {r.stderr.decode('utf-8', 'replace').strip()}")
        return r.stdout.decode("utf-8", "surrogateescape")
    finally:
        index.unlink(missing_ok=True)


def write_patch(path: Path, patch: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(patch.encode("utf-8", "surrogateescape"))


def read_patch(path: Path) -> str:
    return Path(path).read_bytes().decode("utf-8", "surrogateescape")


def changed_files(patch: str) -> list[str]:
    """The files a patch touches, relative to the project."""
    out = []
    for m in re.finditer(r"^diff --git a/(.+?) b/(.+)$", patch, re.M):
        out.append(m.group(2))
    return sorted(dict.fromkeys(out))


def apply_patch(folder: Path, patch: str, check_only: bool = False) -> tuple[bool, str]:
    """Apply a patch from box_diff to a folder (a sandbox's repo/ or the real project)."""
    if not patch.strip():
        return True, "empty patch"
    args = [*RAW, "apply", "--binary", "--whitespace=nowarn"] + (["--check"] if check_only else [])
    r = subprocess.run(["git", "-C", str(folder), *args, "-"], input=patch.encode("utf-8", "surrogateescape"),
                       capture_output=True)
    msg = (r.stderr or r.stdout).decode("utf-8", errors="replace").strip()
    return r.returncode == 0, msg or "applied"


def classify(r: dict) -> str:
    """A reproduction's run: 'shows' (failed and said what it saw), 'behaves', or 'inconclusive'."""
    if not runner().failed(r):
        return "behaves"
    if not r["timed_out"] and re.search(r"^\s*OBSERVED:", r["out"], re.M):
        return "shows"
    return "inconclusive"


def run_in(box: Path, cmd: list[str], repeat: int = 1, timeout: float = 300, cwd: str | None = None,
           log: bool = True, stdin: Path | None = None) -> dict:
    return runner().run_many(Path(box), cmd, repeat, timeout, cwd, log, stdin=stdin)


def run_fresh(cfg: dict, ws: Path, cmd: list[str], *, patch: str | None = None, scratch_from: Path | None = None,
              repeat: int = 1, timeout: float = 300, cwd: str | None = None, rev: str | None = None,
              hint: str = "fresh", keep: bool = False, setup: list[list[str]] | None = None) -> dict:
    """Run cmd in a brand-new sandbox (with a patch applied and a scratch folder copied in), then delete it.

    Returns run.py's result, plus "patch_error" when the patch didn't apply (then nothing ran) and
    "setup" with the results of any setup commands run first (a failed one stops the run)."""
    name = f"{re.sub(r'[^A-Za-z0-9._-]+', '-', hint)[:24]}-{secrets.token_hex(3)}"
    root = make_sandbox(cfg, ws, name, rev=rev)
    try:
        if patch:
            ok, msg = apply_patch(root / "repo", patch)
            if not ok:
                return {"cmd": cmd, "cwd": str(root / "repo"), "runs": [], "patch_error": msg}
        if scratch_from and Path(scratch_from).is_dir():
            shutil.copytree(scratch_from, root / "scratch", dirs_exist_ok=True)
        done = []
        for step in setup or []:
            s = run_in(root, step, 1, timeout, cwd, log=False)
            done.append(s)
            if runner().failed(s["runs"][0]):
                return {"cmd": cmd, "cwd": str(root / "repo"), "runs": [], "setup": done, "setup_failed": True}
        res = run_in(root, cmd, repeat, timeout, cwd, log=False)
        if done:
            res["setup"] = done
        if keep:
            res["sandbox"] = str(root)
        return res
    finally:
        if not keep:
            drop_sandbox(ws, cfg, name)


def failed(r: dict) -> bool:
    return runner().failed(r)


def summary(res: dict) -> str:
    if res.get("patch_error"):
        return f"the patch didn't apply: {res['patch_error']}"
    if res.get("setup_failed"):
        return "a setup step failed:\n" + runner().summary(res["setup"][-1])
    return runner().summary(res)


def git_status(repo: Path) -> str:
    """The real project's `git status --porcelain`, to compare before and after a phase."""
    return git(repo, "status", "--porcelain=v1", "--untracked-files=all", check=False)
