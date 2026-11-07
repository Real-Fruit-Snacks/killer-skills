#!/usr/bin/env python3
"""Run a command in a killer-ci sandbox, under the sandbox's conditions.

A copy of this file sits in every killer-ci sandbox, next to run.py and ci.json:

  python cirun.py -- <command> [args...]
  python cirun.py --repeat 5 -- <command> [args...]
  python cirun.py --cwd sub/dir --timeout 120 -- <command> [args...]

It runs the command as run.py does (the sandbox's own home and temp folders, no credentials,
the network only if the sandbox allows it), from the project copy spelled the way ci.json
spells it: a sandbox reached through an 8.3 short name or a symlink stays reached that way,
as a CI runner's temp folder is. Around the runs it applies the conditions that act while
tests run: busy helper processes beside them (load), a read-only checkout (readonly) and the
umask. {junit} and {json} in the command become files in the sandbox's ci/ folder, one per
run, for test runners that write their results to a file.

The command's exit code is the verdict: this exits 0 when every run passed and 1 when any
failed or timed out. Every run's output is saved under logs/. Standard library only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
CONF = "ci.json"
BUSY = "import time\nend = time.monotonic() + {secs}\nwhile time.monotonic() < end:\n    pass\n"
# Windows: deny writing data, adding files or folders, extended attributes and deleting children.
# Reading, listing and (since the file's own DELETE isn't denied) deleting a file stay allowed.
# The project folder gets it for itself only; each thing in it but .git gets it inherited.
DENY_TOP = "(WD,AD,WEA,DC)"
DENY_TREE = "(OI)(CI)(WD,AD,WEA,DC)"
DENY_FILE = "(WD,AD,WEA)"


def load_conf(here: Path = HERE) -> dict:
    return json.loads((Path(here) / CONF).read_text(encoding="utf-8"))


def runner(root: Path):
    spec = importlib.util.spec_from_file_location("killer_run", Path(root) / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- readonly

def _system32() -> Path:
    return Path(os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows") / "System32"


def windows_sid() -> str | None:
    r = subprocess.run([str(_system32() / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
                       capture_output=True, text=True, errors="replace")
    m = re.search(r"(S-1-[\d-]+)", r.stdout or "")
    return m.group(1) if m else None


def _icacls(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(_system32() / "icacls.exe"), *args], capture_output=True, text=True, errors="replace")


def _working_tree(repo: Path):
    """Every folder and file of the checkout, but not .git, links, or files with more than one hard link.

    .git is left alone: a local clone shares its object files with the real project through hard links, so
    changing their permissions would change the real project's too. Yields (path, lstat mode)."""
    for dirpath, dirnames, filenames in os.walk(repo):
        if Path(dirpath) == Path(repo):
            dirnames[:] = [d for d in dirnames if d != ".git"]
        try:
            yield dirpath, os.lstat(dirpath).st_mode
        except OSError:
            pass
        for name in filenames:
            p = os.path.join(dirpath, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not stat.S_ISLNK(st.st_mode) and st.st_nlink <= 1:
                yield p, st.st_mode


def _top_entries(repo: Path) -> list[Path]:
    return [p for p in Path(repo).iterdir() if p.name != ".git"]


def lock(repo: Path) -> dict | None:
    """Make the checkout read-only (not .git); returns what unlock needs, or None if it couldn't."""
    repo = Path(repo)
    if os.name == "nt":
        sid = windows_sid()
        if not sid or _icacls(str(repo), "/deny", f"*{sid}:{DENY_TOP}", "/Q").returncode:
            return None
        for p in _top_entries(repo):
            spec = DENY_TREE if p.is_dir() and not p.is_symlink() else DENY_FILE
            _icacls(str(p), "/deny", f"*{sid}:{spec}", "/Q")
        return {"sid": sid}
    modes = list(_working_tree(repo))
    files = [x for x in modes if not stat.S_ISDIR(x[1])]
    dirs = [x for x in modes if stat.S_ISDIR(x[1])]
    for p, mode in files + dirs:   # folders last, though chmod doesn't need write access to the folder
        try:
            os.chmod(p, stat.S_IMODE(mode) & ~0o222)
        except OSError:
            pass
    return {"modes": [[p, m] for p, m in files + dirs]}


def unlock(repo: Path, info: dict | None = None) -> None:
    """Undo lock. Without info (a crash left it locked), make the checkout writable again (not .git)."""
    repo = Path(repo)
    if not repo.is_dir() or repo.is_symlink():
        return
    if os.name == "nt":
        sid = (info or {}).get("sid") or windows_sid()
        if sid:
            _icacls(str(repo), "/remove:d", f"*{sid}", "/C", "/Q")
            for p in _top_entries(repo):
                _icacls(str(p), "/remove:d", f"*{sid}", "/T", "/C", "/Q")
        return
    if info and info.get("modes"):
        for p, mode in reversed(info["modes"]):
            try:
                os.chmod(p, stat.S_IMODE(mode))
            except OSError:
                pass
        return
    for p, mode in _working_tree(repo):
        try:
            os.chmod(p, stat.S_IMODE(mode) | stat.S_IWUSR)
        except OSError:
            pass


# ---------------------------------------------------------------- load

def start_load(n: int, secs: float) -> list[subprocess.Popen]:
    """n busy processes; each stops by itself after secs, in case nothing is left to stop it."""
    out = []
    for _ in range(max(0, int(n))):
        out.append(subprocess.Popen([sys.executable, "-c", BUSY.format(secs=int(secs) + 1)], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return out


def stop_load(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        try:
            p.kill()
            p.wait(10)
        except (OSError, subprocess.TimeoutExpired):
            pass


# ---------------------------------------------------------------- running

def next_log(logs: Path) -> int:
    nums = [int(m.group(1)) for f in logs.glob("*.log") if (m := re.match(r"(\d+)-", f.name))]
    return 1 + max(nums or [0])


def run_all(cmd: list[str], repeat: int, timeout: float, cwd: str | None, extra_env: dict,
            conf: dict | None = None) -> dict:
    conf = conf or load_conf()
    root = Path(conf["root"])
    run = runner(root)
    env = run.build_env(root)
    for k, v in extra_env.items():
        if os.name == "nt":
            for name in [n for n in env if n.upper() == k.upper()]:
                del env[name]
        env[k] = v
    where = root / "repo" / cwd if cwd else root / "repo"
    if not where.is_dir():
        raise SystemExit(f"error: no folder {where}")
    if os.name != "nt":   # the folder as this sandbox spells it, not the caller's: POSIX getcwd() gives the real path
        env["PWD"] = str(where)
    logs, out_dir = root / "logs", root / "ci"
    logs.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)
    n = next_log(logs)
    if conf.get("umask") and os.name != "nt":
        os.umask(int(str(conf["umask"]), 8))
    helpers = start_load(conf.get("load") or 0, timeout * repeat + 60)
    info, runs = None, []
    try:
        if conf.get("readonly"):
            info = lock(root / "repo")
        for k in range(1, repeat + 1):
            files = {"junit": out_dir / f"junit-{n:03d}-{k}.xml", "json": out_dir / f"results-{n:03d}-{k}.json"}
            for f in files.values():
                f.unlink(missing_ok=True)
            cmd_k = [x.replace("{junit}", str(files["junit"])).replace("{json}", str(files["json"])) for x in cmd]
            r = run.run_once(cmd_k, where, env, timeout)
            path = logs / f"{n:03d}-{k}.log"
            path.write_text(f"$ {' '.join(cmd_k)}\n# cwd {where}\n# exit {r['code']}"
                            f"{' (timed out)' if r['timed_out'] else ''}, {r['secs']} s\n\n{r['out']}", encoding="utf-8")
            r["log"] = str(path)
            r["files"] = {name: str(f) for name, f in files.items() if f.is_file()}
            runs.append(r)
    finally:
        if conf.get("readonly"):
            unlock(root / "repo", info)
        stop_load(helpers)
    return {"cmd": cmd, "cwd": str(where), "runs": runs, "load": len(helpers),
            "readonly": (info is not None) if conf.get("readonly") else None}


def main(argv=None) -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="cirun.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repeat", type=int, default=1, help="run it this many times and count failures")
    p.add_argument("--timeout", type=float, default=900, help="seconds per run before it's killed (default 900)")
    p.add_argument("--cwd", help="folder inside the project copy to run from")
    p.add_argument("--env", action="append", default=[], help="NAME=VALUE for these runs only")
    p.add_argument("--json", help="write every run's result here, as JSON")
    p.add_argument("--quiet", action="store_true", help="don't print the summary")
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="-- then the command")
    a = p.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        p.error("give the command after --")
    if a.repeat < 1:
        p.error("--repeat must be 1 or more")
    extra = {}
    for e in a.env:
        name, eq, value = e.partition("=")
        if not eq or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            p.error(f"--env takes NAME=VALUE, not {e!r}")
        extra[name] = value
    conf = load_conf()
    res = run_all(cmd, a.repeat, a.timeout, a.cwd, extra, conf)
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    run = runner(Path(conf["root"]))
    if not a.quiet:
        print(run.summary(res))
        if res["load"]:
            print(f"(with {res['load']} busy helper processes beside it)")
        if res["readonly"] is False:
            print("note: the checkout couldn't be made read-only")
    sys.exit(1 if any(run.failed(r) for r in res["runs"]) else 0)


if __name__ == "__main__":
    main()
