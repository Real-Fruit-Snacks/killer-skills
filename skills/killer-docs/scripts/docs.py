#!/usr/bin/env python3
"""Bookkeeping for a killer-docs run: docs that are true and that a newcomer can follow.

Commands:
  init         create the run folder; list the doc files and the commands the docs describe
  extract      pull the code blocks out of the docs; say which will run here, and why the rest won't
  skip         leave blocks out of every run, with the reason (the side-effect check)
  journey      add or list the journeys a newcomer walks (install and first run, a guide, ...)
  sandbox      make an isolated copy of the project for one agent
  guard        save the real project's git status, or compare it with the saved one
  run-blocks   run each doc's blocks in order, in its own fresh sandbox, and compare shown output
  help-drift   run each command's --help and compare its options and subcommands with the docs
  links        check relative links, anchors and image paths (web links too, with the network on)
  add          add a finding by hand (the user's, your own)
  collect      read a newcomer's or fact checker's reply.json; rerun its reproductions in fresh copies
  walk-result  record or correct how a newcomer's journey went
  update       change a finding's status, kind, severity or note
  list         the findings so far
  show         one finding, or one block's latest result, in full
  repro        rerun a finding's reproduction in a fresh sandbox
  branch       make (or switch to) the local branch the doc fixes go on
  commit       commit doc fixes on that branch, after checking the message and the diff
  recheck      after fixes: blocks, help, links and reproductions again, compared with the first run
  decide       record a call the user should check
  wait         record an outward-facing action that waits for the user's OK
  report       write report.md and summary.json
  clean        delete this run's sandboxes

Runs live in ~/.claude/killer-docs/<project>/<date>-<slug>/ and sandboxes in the temp folder;
KILLER_DOCS_HOME and KILLER_DOCS_SANDBOXES move them. Agents write reply.json in their
sandbox's scratch folder. Exit codes: 0 fine, 1 a check failed, 2 a usage or setup error.
Standard library only. Run `docs.py <command> --help` for options.
"""
from __future__ import annotations

import argparse
import difflib
import functools
import hashlib
import importlib.util
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.parse
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
if not (HERE / "kit.py").is_file():
    print(f"error: {HERE / 'kit.py'} is missing; in the killer-skills repo, run tools/sync.py", file=sys.stderr)
    raise SystemExit(2)
_spec = importlib.util.spec_from_file_location("killer_kit", HERE / "kit.py")
kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit)

SKILL = "docs"
fail = kit.fail
WIN = os.name == "nt"
SEVERITY = ("high", "medium", "low")
KINDS = ("wrong", "missing", "unclear", "stale", "broken-link", "drift", "code")
STATUSES = ("candidate", "proven", "fixed", "dismissed")
OUTCOMES = ("reached", "reached-with-help", "stuck")
SCOPES = "all, start, cli, path:<file or folder>, changes or branch[:<base>]"
DEFAULT_TIMEOUT = 60
DEFAULT_CAP = 300
BAD = ("failed", "differs", "timed-out")
NOT_CHECKED = ("skipped", "no-tool", "network", "blocked")
RUNNABLE = ("shell", "code", "doctest")

MD_EXT = {".md", ".markdown", ".mdx"}
DOC_DIRS = {"docs", "doc", "documentation", "guide", "guides", "tutorial", "tutorials", "man", "manual", "wiki"}
EXAMPLE_DIRS = {"examples", "example", "samples", "sample", "demo", "demos"}
SKIP_PARTS = {"node_modules", "vendor", "third_party", "third-party", "tests", "test", "__tests__", "spec", "specs",
              "fixtures", "testdata", "test-data", ".venv", "venv", "site-packages", "dist", "build", "target"}
EXAMPLE_RUNNERS = {".py": "python", ".js": "node", ".mjs": "node", ".cjs": "node", ".ts": "ts", ".mts": "ts",
                   ".sh": "sh", ".bash": "sh", ".ps1": "ps", ".rb": "ruby", ".pl": "perl", ".php": "php", ".go": "go"}
HISTORY_RE = re.compile(r"^(changelog|changes|history|news|releases?|release[-_]notes)(\.|$)", re.I)
TEMPLATE_RE = re.compile(r"^\.github/(ISSUE_TEMPLATE|DISCUSSION_TEMPLATE|PULL_REQUEST_TEMPLATE)|^\.github/pull_request_template", re.I)
START_RE = re.compile(r"getting[-_ ]?started|quick[-_ ]?start|install|setup|set[-_]up|tutorial|intro|overview|usage|first[-_ ]steps", re.I)
IMPORTANT_RE = re.compile(r"install|quick ?start|getting started|set ?up|first|usage|tutorial|readme", re.I)

# ---------------------------------------------------------------- block languages

SH_LANGS = {"sh", "bash", "zsh", "shell", "ksh", "dash", "shellscript", "shell-script"}
SH_CONSOLE = {"console", "shell-session", "sh-session", "shellsession", "bash-session", "terminal", "term", "cli",
              "command-line", "commandline", "prompt"}
PS_LANGS = {"powershell", "pwsh", "ps1", "ps", "posh", "psm1"}
PS_CONSOLE = {"pwsh-session", "ps1con", "powershell-session", "pscon"}
CMD_LANGS = {"cmd", "bat", "batch", "dosbatch", "winbatch"}
CMD_CONSOLE = {"doscon", "cmd-session"}
PY_LANGS = {"python", "py", "python3", "py3"}
PYCON = {"pycon", "python-repl", "pyrepl", "python-console", "doctest", "python-session"}
JS_LANGS = {"javascript", "js", "node", "nodejs", "mjs", "cjs"}
TS_LANGS = {"typescript", "ts", "mts", "cts"}
OTHER_RUN = {"ruby": "ruby", "rb": "ruby", "perl": "perl", "pl": "perl", "php": "php", "go": "go", "golang": "go"}
OUTPUT_LANGS = {"output", "stdout", "out", "console-output", "result", "results"}
TEXTISH = {"text", "txt", "plaintext", "plain", "none", "nohighlight", "log", "ansi"}
CONFIG_LANGS = {"json", "jsonc", "json5", "yaml", "yml", "toml", "ini", "cfg", "conf", "properties", "env", "dotenv",
                "xml", "hcl", "editorconfig"}

SH_PROMPT = re.compile(r"^\s{0,4}(?:\([^)\s]+\)\s+)?(?:[\w.-]+@[\w.-]+(?::\S*?)?\s*|~\S*?\s*)?\$(?:\s|$)")
PS_PROMPT = re.compile(r"^\s{0,4}PS(?:\s+[^>\n]*)?>\s?")
CMD_PROMPT = re.compile(r"^\s{0,4}[A-Za-z]:\\[^>\n]*>\s?")
NATIVE_PROMPT = re.compile(r"^\s{0,4}>\s(?=\S)")
PYCON_PROMPT = re.compile(r"^\s*>>>(?:\s|$)")
OUTPUT_LABEL = re.compile(r"(?i)\b(output|outputs|prints?|printed|you('ll| will| should)? see|should see|displays?|"
                          r"shows?|returns?|gives|results? in|looks? like|produces?|responds?|response|answers?)\b")
ANNOTATIONS = {"skip", "output-may-differ", "setup", "needs-network", "background", "timeout"}

# ---------------------------------------------------------------- output normalisation

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
NORMS = [
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<id>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"), "<time>"),
    (re.compile(r"\b(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+)?" + MONTHS + r"\s+\d{1,2}(?:st|nd|rd|th)?,?(?:\s+\d{4})?\b"), "<time>"),
    (re.compile(r"\b\d{1,2}\s+" + MONTHS + r",?\s+\d{4}\b"), "<time>"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), "<time>"),
    (re.compile(r"\b\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:\s?[AaPp][Mm])?\b"), "<time>"),
    (re.compile(r"\b1[5-9]\d{8}(?:\d{3})?\b"), "<time>"),
    (re.compile(r"\b\d+m\d+(?:\.\d+)?s\b"), "<duration>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:ns|µs|us|ms|s|sec|secs|seconds?|m|mins?|minutes?|h|hrs?|hours?)\b"), "<duration>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<id>"),
    (re.compile(r"\bv?\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z]+)*\b"), "<version>"),
    (re.compile(r"\bv\d+\.\d+\b"), "<version>"),
    (re.compile(r"(?i)\b(version|ver\.?)\s+\d+(?:\.\d+)+\b"), r"\1 <version>"),
    (re.compile(r"\b(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,64}\b"), "<id>"),
]
ELISION = re.compile(r"^\s*(?:\.\.\.|…|\[\.\.\.\]|\[…\]|<\.\.\.>|\(\.\.\.\)|#\s*\.\.\.|\.\.\.\s*\(?(?:snip|truncated|more)\)?"
                     r"|\[(?:snip|truncated)\]|<snip>|\(output truncated\))\s*$", re.I)
OFFLINE_RE = re.compile(
    r"Could not find a version that satisfies|No matching distribution found|ENOTFOUND|EAI_AGAIN|getaddrinfo|"
    r"ECONNREFUSED|ENOTCACHED|ProxyError|Cannot connect to proxy|Failed to establish a new connection|"
    r"Could not resolve host|Temporary failure in name resolution|Name or service not known|network is unreachable|"
    r"unable to access 'https?://|Failed to connect to|ETIMEDOUT|--offline|offline mode|failed to download|"
    r"no matching package named|GOPROXY=off|module lookup disabled|127\.0\.0\.1:9\b|Network is unreachable", re.I)
NOT_FOUND_RES = [
    re.compile(r"(?:^|\n)[^\n]*?(?:line \d+: |: \d+: )?([^\s:'\"]+): (?:command )?not found[ \t]*(?:\n|$)"),
    re.compile(r"'([^']+)' is not recognized as an internal or external command"),
    re.compile(r"The term '([^']+)' is not recognized as (?:the )?name of a cmdlet"),
    re.compile(r"No module named '?(pip|venv|ensurepip)'?\b"),
    re.compile(r"\b(pip) isn't available in this sandbox"),
]
NAME_ERROR = re.compile(r"NameError: name '(\w+)' is not defined")

# ---------------------------------------------------------------- what a block may not do here

PLACEHOLDERS = [
    re.compile(r"<\s*your[-_ ][^>]*>", re.I),
    re.compile(r"\bYOUR_[A-Z0-9_]+\b"),
    re.compile(r"\byour[-_](?:api[-_]?key|token|user(?:name)?|password|name|email|project|org(?:anization)?|"
               r"repo(?:sitory)?|domain|key|id|secret|account|app|bucket|server|host)\b", re.I),
    re.compile(r"…"),
    re.compile(r"(?<![\w.])x{3,}(?![\w.])", re.I),
    re.compile(r"https?://(?:[\w-]+\.)*example\.(?:com|org|net)\b", re.I),
    re.compile(r"\b(?:CHANGEME|CHANGE_ME|REPLACE_?ME)\b"),
    re.compile(r"<\s*(?:insert|replace|enter)\b[^>]*>", re.I),
]
SHELL_PLACEHOLDERS = [
    re.compile(r"(?<![<\w$-])<[A-Za-z][\w.-]*(?: [\w.-]+)*>(?!>)"),
    re.compile(r"(?:^|\s)\.\.\.(?=\s|$)"),
    re.compile(r"\[(?:options?|OPTIONS?|args?|ARGS?|flags?|FLAGS?|arguments?|ARGUMENTS?)[^\]]*\]"),
    re.compile(r"\{\{\s*[\w.]+\s*\}\}"),
]
PKG_SYSTEM = {"apt", "apt-get", "yum", "dnf", "zypper", "pacman", "apk", "brew", "port", "snap", "choco",
              "winget", "scoop", "nix-env", "emerge", "flatpak"}
ADMIN = {"sudo", "doas", "runas", "pkexec"}
SERVICES = {"systemctl", "launchctl", "crontab", "update-rc.d", "chkconfig", "schtasks", "service"}
CONTAINERS = {"docker", "podman", "kubectl", "helm", "minikube", "vagrant", "nerdctl", "kind", "docker-compose"}
WINDOWS_APPS = {"open", "xdg-open", "start", "explorer", "explorer.exe", "code", "vim", "vi", "nvim", "nano",
                "emacs", "notepad", "subl", "gedit", "invoke-item", "ii"}
MACHINE = {"shutdown", "reboot", "halt", "diskutil", "mount", "umount", "netsh", "iptables", "ufw"}
REPL = {"python", "python3", "py", "node", "irb", "ipython", "psql", "mysql", "sqlite3", "bash", "sh", "zsh",
        "fish", "pwsh", "powershell", "cmd", "r", "ghci", "iex", "lua", "julia", "deno", "bun"}
FETCHERS = {"curl", "wget", "http", "https", "aria2c", "iwr", "irm", "invoke-webrequest", "invoke-restmethod",
            "start-bitstransfer"}
LOCAL_HOSTS = re.compile(r"^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|::1|[\w.-]+\.localhost)$", re.I)
URL_RE = re.compile(r"https?://[^\s'\"<>()`]+", re.I)
WRITE_VERBS = re.compile(r"(?:^|[\s;&|(])(rm|rmdir|del|erase|remove-item|ri|mv|move|move-item|cp|copy|copy-item|"
                         r"ln|mkdir|md|new-item|ni|tee|chmod|chown|touch|set-content|out-file)\b|>>?", re.I)
ABS_OUTSIDE = re.compile(r"(?<![\w.~$/-])/\*?(?=\s|$)|(?<![\w.~$/-])/(?:usr|etc|opt|var|bin|sbin|lib|lib64|Library|Applications|System|tmp|private|"
                         r"srv|root|home|Users|mnt|media|Volumes)\b|\b[A-Za-z]:[\\/]|%(?:ProgramFiles|SystemRoot|windir|"
                         r"ProgramData|APPDATA|LOCALAPPDATA|USERPROFILE)%|\$env:(?:ProgramFiles|SystemRoot|windir|ProgramData)",
                         re.I)
ATTRIBUTION = re.compile(r"co-authored-by\s*:|generated (?:with|by)\b|🤖", re.I)
AI_NAMES = re.compile(r"\b(claude|anthropic|chatgpt|openai|copilot)\b", re.I)


def runner():
    """The sandbox runner (run.py), loaded through the kit."""
    return kit.runner()


# ---------------------------------------------------------------- small helpers

def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()


def split_lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def read_text(path: Path) -> str:
    return Path(path).read_bytes().decode("utf-8", errors="replace").lstrip("﻿")


def fwd(p) -> str:
    return str(p).replace("\\", "/")


def shq(s) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def psq(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def short(s: str, n: int = 70) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def tail(text: str, n: int = 40) -> str:
    lines = (text or "").rstrip("\n").split("\n")
    return "\n".join(lines[-n:])


def this_os() -> str:
    return "windows" if WIN else ("macos" if sys.platform == "darwin" else "linux")


OS_NAMES = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}


def load(a) -> tuple[Path, dict]:
    return kit.load_run(a.workspace)


def load_findings(ws: Path) -> dict:
    return kit.load_json(ws / "findings.json", {"next": 1, "findings": []})


def mutate_findings(ws: Path, fn):
    with kit.locked(ws):
        db = load_findings(ws)
        out = fn(db)
        kit.save_json(ws / "findings.json", db)
    return out


def get_finding(db: dict, fid: str) -> dict:
    for f in db["findings"]:
        if f["id"].lower() == str(fid).lower():
            return f
    fail(f"no finding {fid}")


def append_json(ws: Path, name: str, item) -> None:
    with kit.locked(ws):
        items = kit.load_json(ws / name, [])
        items.append(item)
        kit.save_json(ws / name, items)


def latest_pass(ws: Path, kind: str) -> int:
    nums = [int(m.group(1)) for f in (ws / "passes").glob(f"{kind}-*.json") if (m := re.match(rf"{kind}-(\d+)\.json$", f.name))]
    return max(nums or [0])


def load_pass(ws: Path, kind: str, n: int | None = None):
    n = latest_pass(ws, kind) if n is None else n
    return kit.load_json(ws / "passes" / f"{kind}-{n}.json", None) if n else None


def rel_to(path: str, top: Path, cfg: dict) -> str:
    """A path an agent reported, as a path relative to the project."""
    s = str(path or "").strip().strip("`'\"").replace("\\", "/")
    s = re.sub(r"(:\d+(-\d+)?)+$", "", s)
    m = re.search(rf"/{re.escape(cfg['run_id'])}/[^/]+/repo/(.+)$", s)
    if m:
        s = m.group(1)
    else:
        t = fwd(top).rstrip("/") + "/"
        if s.lower().startswith(t.lower()):
            s = s[len(t):]
    return s[2:] if s.startswith("./") else s


def as_int(v):
    try:
        return int(str(v).strip().split("-")[0])
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- finding the docs and the commands

def doc_type(rel: str, top: Path) -> str | None:
    p = PurePosixPath(rel)
    parts = [x.lower() for x in p.parts]
    name, ext = p.name, p.suffix.lower()
    if TEMPLATE_RE.search(rel):
        return None
    if any(x.startswith(".") and x != ".github" for x in parts[:-1]) or any(x in SKIP_PARTS for x in parts[:-1]):
        return None
    if any(x in EXAMPLE_DIRS for x in parts[:-1]):
        if ext in MD_EXT:
            return "md"
        if ext in EXAMPLE_RUNNERS and not name.startswith(("_", "test_", "conftest")) and name != "setup.py":
            return "example"
        return None
    if ext in MD_EXT:
        return "md"
    if ext == ".rst":
        return "rst"
    if re.search(r"\.[1-9][a-z]*$", name) and ext not in MD_EXT:
        try:
            head = (top / rel).read_bytes()[:400].decode("utf-8", "replace")
        except OSError:
            return None
        return "man" if re.search(r"^(\.TH|\.\\\"|'\\\"|\.Dd)", head, re.M) else None
    if ext == ".txt" and (parts[0] in DOC_DIRS or re.match(r"(?i)(readme|install|usage|contributing)", name)):
        return "txt"
    if not ext and re.match(r"(?i)(readme|install|usage|contributing)$", name):
        return "txt"
    return None


def doc_order(rel: str):
    p = PurePosixPath(rel)
    rank = 0 if (len(p.parts) == 1 and p.name.upper().startswith("README")) else \
        1 if len(p.parts) == 1 else 2 if p.parts[0].lower() in DOC_DIRS else 3
    return (rank, rel.lower())


def find_docs(cfg: dict) -> list[list[str]]:
    """[path, type] for every doc in the run's scope, README first."""
    top = Path(cfg["repo"])
    kind = cfg["doc_scope"]
    if kind in ("all", "start", "cli"):
        files = kit.all_files(top, cfg["git"])
    else:
        _, files = kit.resolve_scope(cfg["scope_spec"], top, cfg["git"])
    files = [f for f in files if not kit.excluded(f, cfg.get("exclude"))]
    out = []
    for f in files:
        t = doc_type(f, top)
        if not t:
            continue
        if kind == "start":
            p = PurePosixPath(f)
            if not ((len(p.parts) == 1 and p.name.upper().startswith("README")) or START_RE.search(p.stem)):
                continue
            if t == "example":
                continue
        out.append([f, t])
    for f in cfg.get("docs_init", []):
        if f[0] not in {x[0] for x in out} and (top / f[0]).is_file():
            out.append(f)
    return sorted(out, key=lambda x: doc_order(x[0]))


def all_doc_paths(cfg: dict) -> list[list[str]]:
    """Every doc in the project, whatever the scope (for what counts as documented)."""
    top = Path(cfg["repo"])
    out = [[f, t] for f in kit.all_files(top, cfg["git"]) if (t := doc_type(f, top)) and t != "example"]
    return sorted(out, key=lambda x: doc_order(x[0]))


def mini_toml(text: str) -> dict:
    """Just enough TOML for the sections docs.py reads, for Python 3.10 (no tomllib)."""
    root: dict = {}
    cur = root
    for raw in split_lines(text):
        line = raw.split("#", 1)[0].strip() if not re.search(r"[\"'].*#.*[\"']", raw) else raw.strip()
        if not line:
            continue
        m = re.match(r"^\[\[([^\]]+)\]\]$", line)
        if m:
            parent = root
            keys = [k.strip().strip('"') for k in m[1].split(".")]
            for k in keys[:-1]:
                parent = parent.setdefault(k, {})
            cur = {}
            parent.setdefault(keys[-1], []).append(cur)
            continue
        m = re.match(r"^\[([^\]]+)\]$", line)
        if m:
            cur = root
            for k in [k.strip().strip('"') for k in m[1].split(".")]:
                cur = cur.setdefault(k, {})
            continue
        m = re.match(r"^([\"']?[\w.-]+[\"']?)\s*=\s*(.+)$", line)
        if m:
            key, val = m[1].strip("\"'"), m[2].strip()
            if val[:1] in "\"'":
                val = val[1:].split(val[0], 1)[0]
            elif val in ("true", "false"):
                val = val == "true"
            elif val.startswith("{"):
                val = dict(re.findall(r"([\w-]+)\s*=\s*[\"']([^\"']*)[\"']", val))
            cur[key] = val
    return root


def load_toml(path: Path) -> dict:
    try:
        text = read_text(path)
    except OSError:
        return {}
    try:
        import tomllib
        return tomllib.loads(text)
    except ImportError:
        return mini_toml(text)
    except Exception:
        return mini_toml(text)


def find_entry_points(top: Path, files: list[str], extra: list[str]) -> list[dict]:
    """Commands the project installs or ships, and how to run each one in a sandbox."""
    out: dict[str, dict] = {}

    def add(name, how, source, **kw):
        if name and name not in out:
            out[name] = {"name": name, "how": how, "source": source, **kw}

    pj = top / "package.json"
    if pj.is_file():
        try:
            data = json.loads(read_text(pj))
        except ValueError:
            data = {}
        b = data.get("bin")
        if isinstance(b, str):
            add(str(data.get("name", top.name)).split("/")[-1], "argv", "package.json bin", argv=["node", b], paths=[b])
        elif isinstance(b, dict):
            for k, v in b.items():
                add(k, "argv", "package.json bin", argv=["node", str(v)], paths=[str(v)])
    pp = top / "pyproject.toml"
    if pp.is_file():
        t = load_toml(pp)
        scripts = dict((t.get("project") or {}).get("scripts") or {})
        scripts.update(((t.get("tool") or {}).get("poetry") or {}).get("scripts") or {})
        for k, v in scripts.items():
            if isinstance(v, str) and ":" in v:
                mod, attr = v.split(":", 1)
                add(k, "python-entry", "pyproject.toml scripts", module=mod.strip(), attr=attr.strip().split()[0],
                    paths=module_paths(mod.strip()))
    sc = top / "setup.cfg"
    if sc.is_file():
        import configparser
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read_string(read_text(sc))
            for line in cp.get("options.entry_points", "console_scripts", fallback="").splitlines():
                m = re.match(r"\s*([\w.-]+)\s*=\s*([\w.]+)\s*:\s*([\w.]+)", line)
                if m:
                    add(m[1], "python-entry", "setup.cfg console_scripts", module=m[2], attr=m[3], paths=module_paths(m[2]))
        except configparser.Error:
            pass
    sp = top / "setup.py"
    if sp.is_file():
        for m in re.finditer(r"['\"]\s*([\w.-]+)\s*=\s*([\w.]+)\s*:\s*([\w.]+)\s*['\"]", read_text(sp)):
            add(m[1], "python-entry", "setup.py console_scripts", module=m[2], attr=m[3], paths=module_paths(m[2]))
    ct = top / "Cargo.toml"
    if ct.is_file():
        t = load_toml(ct)
        for b in t.get("bin") or []:
            if isinstance(b, dict) and b.get("name"):
                add(b["name"], "argv", "Cargo.toml [[bin]]", argv=["cargo", "run", "-q", "--bin", b["name"], "--"], paths=[])
        name = (t.get("package") or {}).get("name")
        if name and (top / "src" / "main.rs").is_file():
            add(name, "argv", "Cargo.toml package", argv=["cargo", "run", "-q", "--bin", name, "--"], paths=[])
    for f in files:
        p = PurePosixPath(f)
        if len(p.parts) == 3 and p.parts[0] == "cmd" and p.name == "main.go":
            add(p.parts[1], "argv", "cmd/<name>/main.go", argv=["go", "run", f"./cmd/{p.parts[1]}"], paths=[])
        if len(p.parts) == 2 and p.parts[0] == "bin":
            how = script_runner(top / f)
            if how:
                add(p.stem if p.suffix in (".py", ".js", ".sh", ".cmd", ".ps1") else p.name, how[0], "bin/",
                    argv=how[1], paths=[f])
    for spec in extra or []:
        name, eq, cmd = spec.partition("=")
        if not eq or not name.strip() or not cmd.strip():
            fail(f"--cli takes NAME=COMMAND, like --cli 'greet=python greet.py'; not {spec!r}")
        argv = shlex.split(cmd)
        paths = [x for x in argv[1:2] if "/" in x or x.endswith((".py", ".js", ".sh"))]
        out[name.strip()] = {"name": name.strip(), "how": "argv", "source": "--cli", "argv": argv, "paths": paths}
    return list(out.values())


def module_paths(mod: str) -> list[str]:
    base = mod.replace(".", "/")
    return [f"{base}.py", f"src/{base}.py", f"{base}/__main__.py", f"src/{base}/__main__.py"]


def script_runner(path: Path):
    try:
        head = path.read_bytes()[:200].decode("utf-8", "replace").split("\n", 1)[0]
    except OSError:
        return None
    rel = f"bin/{path.name}"
    if path.suffix == ".py" or re.match(r"#!.*\bpython", head):
        return "python-script", [rel]
    if path.suffix in (".js", ".mjs", ".cjs") or re.match(r"#!.*\bnode\b", head):
        return "argv", ["node", rel]
    if path.suffix in (".sh", ".bash") or re.match(r"#!.*\b(ba)?sh\b", head):
        return "argv", ["sh", rel]
    if WIN and path.suffix in (".cmd", ".bat"):
        return "argv", ["cmd", "/d", "/c", rel]
    return None


def clean_which(name: str) -> str | None:
    """A tool on the PATH, leaving out Windows' store stubs and WSL's launchers."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        if WIN and re.search(r"[\\/](system32|windowsapps)([\\/]|$)", d, re.I):
            continue
        found = shutil.which(name, path=d)
        if found:
            return found
    return None


def find_tools() -> dict:
    t: dict = {}
    sh = clean_which("sh")
    if sh:
        t["sh"] = sh
        sib = Path(sh).with_name("bash.exe" if WIN else "bash")
        t["bash"] = str(sib) if sib.exists() else clean_which("bash")
    elif not WIN:
        t["bash"] = clean_which("bash")
    if WIN:
        t["powershell"] = clean_which("pwsh") or shutil.which("powershell")
        t["cmd"] = os.environ.get("ComSpec") or shutil.which("cmd")
    else:
        t["powershell"] = clean_which("pwsh")
    node = clean_which("node")
    if node:
        t["node"] = node
        try:
            v = subprocess.run([node, "-p", "process.versions.node"], capture_output=True, text=True, timeout=30).stdout.strip()
            t["node_version"] = v
        except (OSError, subprocess.SubprocessError):
            pass
    for name in ("ruby", "perl", "php", "go", "fish"):
        p = clean_which(name)
        if p:
            t[name] = p
    return {k: v for k, v in t.items() if v}


def probe_python(exe: str | None) -> dict | None:
    if not exe or (WIN and "windowsapps" in str(exe).lower()):
        return None
    try:
        r = subprocess.run([exe, "-I", "-c", "import sys, venv; print(sys.executable); print('%d.%d.%d' % sys.version_info[:3])"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.split("\n")
    if r.returncode or len(out) < 2 or not out[0].strip():
        return None
    path = out[0].strip()
    if list(Path(path).parent.glob("python*._pth")):
        return None
    return {"path": path, "version": out[1].strip(), "kind": "full"}


def find_python(explicit: str | None = None) -> dict:
    """The Python the sandboxes use: a full one that can make virtual environments, if there is one."""
    if explicit:
        info = probe_python(explicit)
        if info:
            return info
        if not Path(explicit).exists() and not shutil.which(explicit):
            fail(f"--python {explicit}: not found")
        return {"path": shutil.which(explicit) or explicit, "version": "?", "kind": "plain"}
    info = probe_python(sys.executable)
    if info:
        return info
    cands = []
    if WIN and shutil.which("py"):
        try:
            r = subprocess.run(["py", "-3", "-c", "import sys; print(sys.executable)"], capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                cands.append(r.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    cands += [clean_which("python3"), clean_which("python")]
    for c in dict.fromkeys(x for x in cands if x):
        info = probe_python(c)
        if info:
            return info
    return {"path": sys.executable, "version": platform.python_version(), "kind": "plain"}


# ---------------------------------------------------------------- parsing the docs

FENCE_RE = re.compile(r"^(?P<ind>[ \t]*)(?P<f>`{3,}|~{3,})(?P<info>.*)$")
CLOSE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*$")
ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
ANNOT_RE = re.compile(r"<!--\s*docs:\s*(.*?)\s*-->", re.I)
RST_DIR = re.compile(r"^(?P<ind>\s*)\.\.\s+(?:code-block|code|sourcecode)::\s*(?P<lang>\S*)\s*$")
RST_ANN = re.compile(r"^\s*\.\.\s+docs:\s*(.*)$", re.I)
RST_UNDER = re.compile(r"^([=\-~^\"'`#*+:.<>_])\1{2,}\s*$")


def parse_annotations(texts) -> dict:
    out = {}
    for t in texts:
        for tok in re.split(r"[\s,;]+", t.strip()):
            if not tok:
                continue
            name, _, val = tok.partition("=")
            out[name.lower()] = val or True
    return out


def strip_indent(line: str, n: int) -> str:
    i = 0
    while i < len(line) and i < n and line[i] == " ":
        i += 1
    return line[i:]


def parse_markdown(text: str):
    lines = split_lines(text)
    n = len(lines)
    blocks, heads, stack = [], [], []
    pending: dict = {}
    prose: list = []
    in_comment = False
    i = 0
    if n and lines[0].strip() == "---":
        for j in range(1, min(n, 300)):
            if lines[j].strip() in ("---", "..."):
                i = j + 1
                break

    def heading(level, title, line_no):
        nonlocal stack, prose, pending
        stack = [s for s in stack if s[0] < level] + [(level, title)]
        heads.append({"line": line_no, "level": level, "title": title})
        prose, pending = [], {}

    while i < n:
        line = lines[i]
        if in_comment:
            if "-->" in line:
                in_comment = False
            i += 1
            continue
        m = FENCE_RE.match(line)
        if m and not (m["f"][0] == "`" and "`" in m["info"]):
            ind = len(m["ind"].expandtabs(4))
            fence = m["f"]
            body, j, closed = [], i + 1, False
            while j < n:
                c = CLOSE_RE.match(lines[j])
                if c and c[1][0] == fence[0] and len(c[1]) >= len(fence):
                    closed = True
                    break
                body.append(strip_indent(lines[j], ind))
                j += 1
            blocks.append({"line": i + 1, "end": min(j + 1, n), "info": m["info"].strip(), "body": body, "ann": pending,
                           "heading": [t for _, t in stack], "prose": prose[-6:], "closed": closed, "section": len(heads)})
            pending, prose = {}, []
            i = j + 1
            continue
        h = ATX_RE.match(line)
        if h:
            heading(len(h[1]), (h[2] or "").strip(), i + 1)
            i += 1
            continue
        if (i + 1 < n and line.strip() and SETEXT_RE.match(lines[i + 1]) and (i == 0 or not lines[i - 1].strip())
                and not re.match(r"^\s*([|>]|[-*+]\s|\d+[.)]\s|<)", line)):
            heading(1 if lines[i + 1].strip()[0] == "=" else 2, line.strip(), i + 1)
            i += 2
            continue
        anns = ANNOT_RE.findall(line)
        if anns:
            pending.update(parse_annotations(anns))
            i += 1
            continue
        if "<!--" in line and "-->" not in line[line.index("<!--"):]:
            in_comment = True
            i += 1
            continue
        if line.strip():
            prose.append((i + 1, line))
            pending = {}
        i += 1
    return blocks, heads


def parse_rst(text: str):
    lines = split_lines(text)
    n = len(lines)
    blocks, heads, stack, levels = [], [], [], []
    pending: dict = {}
    prose: list = []
    i = 0
    while i < n:
        line = lines[i]
        a = RST_ANN.match(line)
        if a:
            pending.update(parse_annotations([a[1]]))
            i += 1
            continue
        d = RST_DIR.match(line)
        literal = not d and line.rstrip().endswith("::") and not line.strip().startswith("..")
        if d or literal:
            ind = len(d["ind"]) if d else len(line) - len(line.lstrip())
            j = i + 1
            if d:
                while j < n and re.match(r"^\s+:[\w-]+:", lines[j]):
                    j += 1
            while j < n and not lines[j].strip():
                j += 1
            body = []
            while j < n and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > ind):
                body.append(lines[j])
                j += 1
            while body and not body[-1].strip():
                body.pop()
            if body:
                k = min(len(x) - len(x.lstrip()) for x in body if x.strip())
                blocks.append({"line": i + 1, "end": j, "info": d["lang"] if d else "", "body": [x[k:] for x in body],
                               "ann": pending, "heading": [t for _, t in stack], "prose": prose[-6:], "closed": True,
                               "section": len(heads)})
                pending, prose = {}, []
                i = j
                continue
        if (i + 1 < n and line.strip() and not line[0].isspace() and RST_UNDER.match(lines[i + 1])
                and len(lines[i + 1].strip()) >= len(line.strip())):
            ch = lines[i + 1].strip()[0]
            if ch not in levels:
                levels.append(ch)
            level = levels.index(ch) + 1
            stack = [s for s in stack if s[0] < level] + [(level, line.strip())]
            heads.append({"line": i + 1, "level": level, "title": line.strip()})
            prose, pending = [], {}
            i += 2
            continue
        if line.strip():
            prose.append((i + 1, line))
            pending = {}
        i += 1
    return blocks, heads


def parse_txt(text: str):
    lines = split_lines(text)
    blocks, i = [], 0
    while i < len(lines):
        if SH_PROMPT.match(lines[i]):
            ind = len(lines[i]) - len(lines[i].lstrip())
            j, body = i, []
            while j < len(lines) and lines[j].strip():
                body.append(lines[j][ind:] if lines[j][:ind].strip() == "" else lines[j])
                j += 1
            blocks.append({"line": i + 1, "end": j, "info": "", "body": body, "ann": {}, "heading": [], "prose": [],
                           "closed": True, "section": 0})
            i = j
        else:
            i += 1
    return blocks, []


def lang_of(info: str) -> str:
    m = re.match(r"^[{.\s]*([A-Za-z0-9_+#-]+)", info or "")
    if not m:
        return ""
    lang = m[1].lower()
    return lang[9:] if lang.startswith("language-") else lang


def needs_more(cmd: str) -> bool:
    s = cmd.rstrip()
    if s.endswith(("\\", "|", "&&", "||", "`")):
        return True
    clean = re.sub(r"\\.", "", s)
    return clean.count("'") % 2 == 1 or clean.count('"') % 2 == 1


def heredoc_delim(cmd: str) -> str | None:
    m = re.search(r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?", cmd)
    return m[1] if m else None


def parse_console(lines: list[str], prompt: re.Pattern, cont: re.Pattern | None = None) -> list[dict]:
    """Split a console session into commands (after a prompt) and the output each one shows."""
    cmds: list[dict] = []
    cur = None
    doc = None
    for idx, line in enumerate(lines):
        if cur is not None and doc:
            text = re.sub(r"^\s*>\s?", "", line)
            cur["cmd"] += "\n" + text
            if text.strip() == doc:
                doc = None
            continue
        m = prompt.match(line)
        if m and not (cur is not None and needs_more(cur["cmd"]) and not cur["out"]):
            cur = {"cmd": line[m.end():].rstrip(), "at": idx, "out": []}
            if cur["cmd"].strip():
                cmds.append(cur)
            else:
                cur = None
                continue
            doc = heredoc_delim(cur["cmd"])
            continue
        if cur is not None and not cur["out"] and cont is not None and cont.match(line):
            cur["cmd"] += "\n" + line[cont.match(line).end():]
            continue
        if cur is not None and not cur["out"] and needs_more(cur["cmd"]):
            cur["cmd"] += "\n" + re.sub(r"^\s*>\s?", "", line)
            continue
        if cur is not None:
            cur["out"].append(line)
    for c in cmds:
        while c["out"] and not c["out"][-1].strip():
            c["out"].pop()
        c["out"] = "\n".join(c["out"]) if any(x.strip() for x in c["out"]) else None
    return cmds


def native_dialect() -> str:
    return "ps" if WIN else "sh"


def classify(raw: dict, doc: str, typ: str) -> dict:
    lines = raw["body"]
    nonblank = [x for x in lines if x.strip()]
    has = lambda rx: any(rx.match(x) for x in nonblank)
    lang = lang_of(raw["info"])
    b = {"doc": doc, "line": raw["line"], "end": raw["end"], "info": raw["info"], "lang": lang, "kind": "other",
         "dialect": None, "runner": None, "heading": raw["heading"], "prose": [p[1] for p in raw["prose"]],
         "section": raw["section"], "ann": raw["ann"], "code": "\n".join(lines), "commands": None, "shown": None}
    if raw.get("example"):
        b.update(kind="code", runner=EXAMPLE_RUNNERS[PurePosixPath(doc).suffix.lower()], example=True)
        if b["runner"] in ("sh", "ps"):
            b.update(kind="shell", dialect=b["runner"], runner=None)
        return b

    def console(dialect, prompt, cont=None):
        b.update(kind="shell", dialect=dialect, commands=parse_console(lines, prompt, cont))
        if not b["commands"]:
            b.update(commands=None)

    if lang in SH_LANGS or lang in SH_CONSOLE or lang == "fish":
        if has(SH_PROMPT):
            console("fish" if lang == "fish" else "sh", SH_PROMPT)
        else:
            b.update(kind="shell", dialect="fish" if lang == "fish" else "sh")
    elif lang in PS_LANGS or lang in PS_CONSOLE:
        if has(PS_PROMPT):
            console("ps", PS_PROMPT, re.compile(r"^\s{0,4}>>\s?"))
        elif lang in PS_CONSOLE and has(NATIVE_PROMPT):
            console("ps", NATIVE_PROMPT)
        else:
            b.update(kind="shell", dialect="ps")
    elif lang in CMD_LANGS or lang in CMD_CONSOLE:
        if has(CMD_PROMPT):
            console("cmd", CMD_PROMPT)
        elif has(NATIVE_PROMPT):
            console("cmd", NATIVE_PROMPT)
        else:
            b.update(kind="shell", dialect="cmd")
    elif lang in PY_LANGS:
        if nonblank and PYCON_PROMPT.match(nonblank[0]):
            b.update(kind="doctest", runner="python")
        else:
            b.update(kind="code", runner="python")
    elif lang in PYCON:
        b.update(kind="doctest", runner="python")
    elif lang in JS_LANGS:
        b.update(kind="code", runner="node")
    elif lang in TS_LANGS:
        b.update(kind="code", runner="ts")
    elif lang in OTHER_RUN:
        b.update(kind="code", runner=OTHER_RUN[lang])
    elif lang in OUTPUT_LANGS:
        b.update(kind="output-tagged")
    elif lang in TEXTISH:
        b.update(kind="text")
    elif lang in CONFIG_LANGS:
        b.update(kind="config")
    elif not lang:
        if has(SH_PROMPT):
            console("sh", SH_PROMPT)
        elif has(PS_PROMPT):
            console("ps", PS_PROMPT, re.compile(r"^\s{0,4}>>\s?"))
        elif has(CMD_PROMPT):
            console("cmd", CMD_PROMPT)
        elif nonblank and PYCON_PROMPT.match(nonblank[0]):
            b.update(kind="doctest", runner="python")
        elif nonblank and NATIVE_PROMPT.match(nonblank[0]):
            console(native_dialect(), NATIVE_PROMPT)
            b["native_prompt"] = True
        else:
            b.update(kind="text")
    return b


def link_outputs(blocks: list[dict]) -> None:
    """A text or output block right after a runnable one is what that block prints."""
    for prev, cur in zip(blocks, blocks[1:]):
        if prev["kind"] not in ("shell", "code") or prev.get("commands") or prev.get("example"):
            continue
        if cur["kind"] not in ("output-tagged", "text") or cur["section"] != prev["section"] or len(cur["prose"]) > 3:
            continue
        label = " ".join(cur["prose"])
        if cur["kind"] == "output-tagged" or not cur["prose"] or OUTPUT_LABEL.search(label):
            prev["shown"], prev["shown_line"] = cur["code"], cur["line"]
            cur["kind"], cur["output_of"] = "output", prev["line"]
            for k, v in cur["ann"].items():
                prev["ann"].setdefault(k, v)
    for b in blocks:
        if b["kind"] in ("output-tagged", "text"):
            b["kind"] = "output" if b["kind"] == "output-tagged" else "other"


def strip_md(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[*_`#>|]", "", s)
    return " ".join(s.split())


def os_mentions(t: str) -> set:
    low = t.lower()
    found = set()
    if re.search(r"\bwin(dows)?\b|\bpowershell\b|\bcmd(\.exe)?\b|command prompt", low):
        found.add("windows")
    if re.search(r"\bmac ?os\b|\bos ?x\b|\bmac\b|\bdarwin\b", low):
        found.add("macos")
    if re.search(r"\blinux\b|\bubuntu\b|\bdebian\b|\bfedora\b|\bcentos\b|\brhel\b|\bwsl\b|\balpine\b", low):
        found.add("linux")
    if re.search(r"\bunix\b|\bposix\b|\bbsd\b", low):
        found |= {"macos", "linux"}
    return found


def platform_of(b: dict) -> set | None:
    """The systems a block is labelled for: a short label right before it, or its heading."""
    cands = []
    for line in reversed(b["prose"][-3:]):
        t = strip_md(line)
        if len(t) <= 80 and (t.endswith(":") or re.match(r"^\s*(\*\*|__)", line)
                             or re.search(r"<(TabItem|Tab|summary|b|strong)\b", line, re.I)):
            cands.append(line if re.search(r"<(TabItem|Tab)\b", line, re.I) else t)
    if b["heading"]:
        cands.append(b["heading"][-1])
    for c in cands:
        found = os_mentions(c)
        if found:
            return found
    return None


def code_lines(b: dict) -> list[str]:
    """The block's commands without comment lines (what the checks look at)."""
    text = "\n".join(c["cmd"] for c in b["commands"]) if b.get("commands") else b["code"]
    if b["kind"] == "doctest":
        text = "\n".join(re.sub(r"^\s*(>>>|\.\.\.)\s?", "", x) for x in split_lines(text) if re.match(r"^\s*(>>>|\.\.\.)", x))
    out = []
    for x in split_lines(text):
        s = x.strip()
        if not s or s.startswith("#") or s.startswith("//") or s.lower().startswith(("rem ", "::")):
            continue
        out.append(x)
    return out


def segments(lines: list[str]) -> list[list[str]]:
    """Each simple command, as a list of words (roughly: split on ; && || | and newlines)."""
    out = []
    for line in lines:
        for seg in re.split(r"&&|\|\||;|\||\$\(|`|\(|\)", line):
            seg = seg.strip()
            if not seg:
                continue
            try:
                words = shlex.split(seg, posix=True)
            except ValueError:
                words = seg.split()
            while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
                words = words[1:]
            if words:
                out.append(words)
    return out


def cmd_word(w: str) -> str:
    w = w.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return re.sub(r"\.(exe|cmd|bat|ps1)$", "", w)


def nonlocal_url(text: str) -> str | None:
    for m in URL_RE.finditer(text):
        try:
            host = urllib.parse.urlsplit(m.group(0)).hostname or ""
        except ValueError:
            continue
        if host and not LOCAL_HOSTS.match(host):
            return m.group(0)
    return None


def placeholder_in(b: dict) -> str | None:
    text = "\n".join(code_lines(b))
    rxs = PLACEHOLDERS + (SHELL_PLACEHOLDERS if b["kind"] == "shell" else [])
    for rx in rxs:
        m = rx.search(text)
        if m:
            return m.group(0).strip()
    return None


def system_reason(b: dict) -> str | None:
    if b["kind"] != "shell":
        return None
    lines = code_lines(b)
    text = "\n".join(lines)
    for words in segments(lines):
        w = cmd_word(words[0])
        rest = [x.lower() for x in words[1:]]
        if w in ADMIN or (w == "su" and "-c" in rest):
            return f"runs as administrator ({w})"
        if w in PKG_SYSTEM or (w in ("conda", "mamba") and rest[:1] and rest[0] in ("install", "create", "env")):
            return f"installs system packages ({w})"
        if w in ("setx",) or (w == "reg" and rest[:1] and rest[0] in ("add", "delete", "import")):
            return f"changes your settings in the registry ({w})"
        if w in SERVICES or (w == "sc" and rest[:1] and rest[0] in ("create", "delete", "config", "start", "stop")):
            return f"changes services or scheduled tasks ({w})"
        if w in CONTAINERS:
            return f"uses services outside the sandbox ({w})"
        if w in MACHINE or w.startswith("mkfs") or w == "dd":
            return f"changes the machine ({w})"
        if w in WINDOWS_APPS:
            return f"opens a window or an editor ({w})"
        if w in ("ssh", "scp", "sftp") or (w == "rsync" and any(":" in x for x in rest)):
            return f"connects to another machine ({w})"
        if (w == "git" and rest[:1] == ["push"]) or (w in ("npm", "pnpm", "yarn") and rest[:1] and rest[0] in ("publish", "login")) \
                or (w == "twine" and rest[:1] == ["upload"]) or (w == "cargo" and rest[:1] == ["publish"]) \
                or (w == "gem" and rest[:1] == ["push"]) or w == "gh" or (w == "poetry" and rest[:1] == ["publish"]):
            return f"publishes or changes something online ({' '.join(words[:2])})"
    if re.search(r"\b(Set|New|Remove)-ItemProperty\b.*\b(HK(CU|LM)|Registry::)", text, re.I):
        return "changes the registry"
    if re.search(r"\[(System\.)?Environment\]::SetEnvironmentVariable\([^)]*,\s*['\"]?(\[[\w.]+\]::)?(User|Machine)", text, re.I):
        return "changes your environment settings for good"
    if re.search(r"\bSet-ExecutionPolicy\b", text, re.I) and not re.search(r"-Scope\s+Process\b", text, re.I):
        return "changes PowerShell's execution policy"
    if re.search(r"\b(Register-ScheduledTask|New-Service)\b", text, re.I):
        return "changes services or scheduled tasks"
    if re.search(r"-Verb\s+RunAs\b", text, re.I):
        return "runs as administrator"
    for line in lines:
        if WRITE_VERBS.search(line) and ABS_OUTSIDE.search(re.sub(r"/dev/(null|stdout|stderr|tty)\b", "", line)):
            return "writes outside the sandbox (an absolute path)"
    return None


def pip_needs_network(words: list[str]) -> bool:
    args = [x for x in words]
    if "--no-index" in args:
        return False
    takes = {"-r", "--requirement", "-c", "--constraint", "-i", "--index-url", "--extra-index-url", "-f",
             "--find-links", "-t", "--target", "--prefix", "--root", "--python-version", "--platform"}
    i, local_only = 0, True
    while i < len(args):
        x = args[i]
        if x in ("-r", "--requirement") or x.startswith("--requirement="):
            return True
        if x in ("-e", "--editable"):
            i += 1
            if i < len(args) and re.match(r"^(git\+|hg\+|svn\+|https?:)", args[i]):
                return True
            i += 1
            continue
        if x in takes:
            i += 2
            continue
        if x.startswith("-"):
            i += 1
            continue
        if not (x.startswith((".", "/", "~", "file:")) or "/" in x or "\\" in x or re.match(r"^[A-Za-z]:", x)
                or x.endswith((".whl", ".tar.gz", ".zip"))):
            local_only = False
        i += 1
    return not local_only


def network_reason(b: dict, top: Path) -> str | None:
    if b["ann"].get("needs-network"):
        return "marked needs-network in the doc"
    lines = code_lines(b)
    text = "\n".join(lines)
    if b["kind"] in ("code", "doctest"):
        url = nonlocal_url(text)
        return f"reaches {short(url, 50)}" if url else None
    deps = node_has_deps(top)
    for words in segments(lines):
        w = cmd_word(words[0])
        rest = words[1:]
        low = [x.lower() for x in rest]
        seg = " ".join(words)
        if w in FETCHERS and nonlocal_url(seg):
            return f"downloads ({w})"
        if w == "git" and low[:1] and low[0] in ("clone", "fetch", "pull", "ls-remote", "submodule"):
            return f"needs a remote (git {low[0]})"
        if w in ("pip", "pip3") and low[:1] and low[0] in ("install", "download") and pip_needs_network(rest[1:]):
            return f"installs from a package index ({w} {short(' '.join(rest), 40)})"
        if w in ("python", "python3", "py") and "-m" in low:
            k = low.index("-m")
            if low[k + 1:k + 2] == ["pip"] and low[k + 2:k + 3] and low[k + 2] in ("install", "download") \
                    and pip_needs_network(rest[k + 3:]):
                return "installs from a package index (python -m pip install)"
        if w == "uv" and low[:2] == ["pip", "install"] and pip_needs_network(rest[2:]):
            return "installs from a package index (uv pip install)"
        if w == "uv" and low[:1] and low[0] in ("sync", "add", "lock", "tool", "python"):
            return f"installs from a package index (uv {low[0]})"
        if w == "pipx" and low[:1] and low[0] in ("install", "run") and pip_needs_network(rest[1:]):
            return f"installs from a package index (pipx {low[0]})"
        if w in ("poetry", "pdm", "pipenv") and low[:1] and low[0] in ("install", "add", "update", "lock", "sync"):
            return f"installs from a package index ({w} {low[0]})"
        if w in ("npm", "pnpm", "bun"):
            sub = low[0] if low else ""
            pos = [x for x in rest[1:] if not x.startswith("-")]
            if sub in ("install", "i", "ci", "add", "isntall"):
                if pos and any(not x.startswith((".", "/", "file:")) for x in pos):
                    return f"installs from a registry ({w} {sub} {short(' '.join(pos), 30)})"
                if deps:
                    return f"installs the project's dependencies from a registry ({w} {sub})"
            if sub in ("create", "exec", "x", "dlx", "update", "up", "audit", "outdated", "view", "info", "search") \
                    or (sub == "init" and pos):
                return f"needs a registry ({w} {sub})"
        if w == "yarn":
            sub = low[0] if low else "install"
            if sub in ("install", "add", "up", "upgrade", "dlx", "create") and (sub != "install" or deps):
                return f"installs from a registry (yarn {sub})"
        if w in ("npx", "pnpx", "bunx"):
            pos = [x for x in rest if not x.startswith("-")]
            if pos and not (top / "node_modules" / ".bin" / pos[0]).exists():
                return f"downloads a package ({w} {pos[0]})"
        if w == "cargo" and low[:1] and low[0] in ("install", "add", "update", "fetch", "search"):
            return f"needs crates.io (cargo {low[0]})"
        if w == "go" and (low[:1] in (["get"],) or low[:2] in (["mod", "download"], ["mod", "tidy"])
                          or (low[:1] == ["install"] and any("@" in x for x in low))):
            return f"needs the Go module proxy (go {' '.join(low[:2])})"
        if (w == "gem" and low[:1] and low[0] in ("install", "update")) or w == "bundle" and (not low or low[0] in ("install", "update")):
            return f"installs gems ({w})"
        if w == "composer" and low[:1] and low[0] in ("install", "update", "require", "create-project"):
            return f"installs from Packagist (composer {low[0]})"
        if w == "dotnet" and low[:1] and low[0] in ("restore", "add", "tool"):
            return f"needs NuGet (dotnet {low[0]})"
        if w in ("mvn", "mvnw", "gradle", "gradlew", "sbt", "lein"):
            return f"downloads build plugins ({w})"
        if w not in ("echo", "printf", "export", "set", "write-host", "write-output", "cat", "#") and nonlocal_url(seg):
            return f"reaches {short(nonlocal_url(seg), 50)}"
    return None


@functools.lru_cache(maxsize=8)
def node_has_deps(top: Path) -> bool:
    pj = top / "package.json"
    if not pj.is_file():
        return False
    try:
        data = json.loads(read_text(pj))
    except ValueError:
        return True
    return bool(data.get("dependencies") or data.get("devDependencies") or data.get("optionalDependencies"))


def interactive_reason(b: dict) -> str | None:
    if b["kind"] != "shell":
        return None
    for words in segments(code_lines(b)):
        if len(words) == 1 and cmd_word(words[0]) in REPL:
            return f"starts an interactive session ({words[0]})"
    return None


def runner_missing(b: dict, cfg: dict) -> str | None:
    tools = cfg["tools"]
    if b["kind"] == "shell":
        d = b["dialect"]
        if d == "sh" and not (tools.get("bash") or tools.get("sh")):
            return "needs sh, which isn't on this machine"
        if d == "ps" and not tools.get("powershell"):
            return "needs PowerShell, which isn't on this machine"
        if d == "cmd" and not (WIN and tools.get("cmd")):
            return "needs Windows' cmd"
        if d == "fish" and not tools.get("fish"):
            return "needs fish, which isn't on this machine"
        return None
    r = b.get("runner")
    if r == "python":
        return None
    if r == "node":
        return None if tools.get("node") else "needs Node.js, which isn't on this machine"
    if r == "ts":
        v = tuple(int(x) for x in re.findall(r"\d+", tools.get("node_version", "0.0"))[:2]) if tools.get("node") else (0,)
        return None if v >= (22, 6) else "needs Node.js 22.6 or later to run TypeScript"
    if r == "go":
        if not tools.get("go"):
            return "needs Go, which isn't on this machine"
        return None if re.search(r"^\s*package\s+main\b", b["code"], re.M) else "a fragment, not a program (no package main)"
    if r in ("ruby", "perl", "php"):
        return None if tools.get(r) else f"needs {r}, which isn't on this machine"
    return "no runner for this language"


def build_blocks(cfg: dict, ws: Path) -> dict:
    """Every code block in the docs in scope, classified, with whether and why it won't run."""
    top = Path(cfg["repo"])
    docs = find_docs(cfg)
    skips = kit.load_json(ws / "skips.json", {})
    out, hashes, problems, notes = [], {}, [], []
    for rel, typ in docs:
        try:
            text = read_text(top / rel)
        except OSError:
            continue
        hashes[rel] = sha(text)
        if typ == "md":
            raw, _ = parse_markdown(text)
        elif typ == "rst":
            raw, _ = parse_rst(text)
        elif typ == "txt":
            raw, _ = parse_txt(text)
        elif typ == "example":
            raw = [{"line": 1, "end": len(split_lines(text)), "info": "", "body": split_lines(text), "ann": {},
                    "heading": [], "prose": [], "closed": True, "section": 0, "example": True}]
        else:
            continue
        this = []
        for r in raw:
            if not r["closed"]:
                problems.append({"doc": rel, "line": r["line"], "problem": "a code block that's never closed: everything after it renders as code"})
            b = classify(r, rel, typ)
            parts = PurePosixPath(rel).parts
            b["group"] = "/".join(parts[:next(i for i, p in enumerate(parts) if p.lower() in EXAMPLE_DIRS) + 1]) + "/" \
                if typ == "example" else rel
            for k in b["ann"]:
                if k not in ANNOTATIONS:
                    notes.append(f"{rel}:{b['line']}: unknown annotation docs: {k}")
            this.append(b)
        link_outputs(this)
        out += this
    seen: dict = {}
    ordinals: dict = {}
    for i, b in enumerate(out, 1):
        b["id"] = f"b{i}"
        base = sha("\0".join([b["group"], b["kind"], b["lang"], b["code"]]))[:12]
        seen[base] = seen.get(base, 0) + 1
        b["key"] = base if seen[base] == 1 else f"{base}-{seen[base]}"
        if b["kind"] in RUNNABLE:
            h = (b["doc"], " > ".join(b["heading"]))
            ordinals[h] = ordinals.get(h, 0) + 1
            b["pos"] = [b["doc"], " > ".join(b["heading"]), ordinals[h]]
            if b["ann"].get("timeout"):
                b["timeout"] = as_int(b["ann"]["timeout"])
            if b["ann"].get("background"):
                b["background"] = True
                b["settle"] = as_int(b["ann"]["background"]) if b["ann"]["background"] is not True else 3
            b["setup"] = bool(b["ann"].get("setup"))
            b["may_differ"] = bool(b["ann"].get("output-may-differ"))
            b["skip"] = decide_skip(b, cfg, skips, top)
    cap, ran = cfg.get("max_blocks", DEFAULT_CAP), 0
    for b in out:
        if b["kind"] in RUNNABLE and not b["skip"]:
            ran += 1
            if ran > cap:
                b["skip"] = {"why": "cap", "reason": f"over the cap of {cap} blocks a run (init --max-blocks)"}
    return {"at": kit.now(), "hashes": hashes, "docs": docs, "blocks": out, "problems": problems, "notes": notes}


def decide_skip(b: dict, cfg: dict, skips: dict, top: Path) -> dict | None:
    if b["key"] in skips:
        return {"why": "lead", "reason": skips[b["key"]].get("reason") or "left out by the lead"}
    if b["ann"].get("skip"):
        return {"why": "marked", "reason": "marked `docs: skip` in the doc"}
    if HISTORY_RE.match(PurePosixPath(b["doc"]).name):
        return {"why": "history", "reason": "in a changelog, which describes past versions"}
    plat = platform_of(b)
    if plat and this_os() not in plat:
        names = " and ".join(OS_NAMES[x] for x in sorted(plat))
        return {"why": "platform", "reason": f"for {names}; this machine runs {OS_NAMES[this_os()]}"}
    if b["kind"] == "shell" and b["dialect"] in ("ps", "cmd") and not WIN and not plat:
        if b["dialect"] == "cmd" or not cfg["tools"].get("powershell"):
            return {"why": "platform", "reason": f"a {'cmd' if b['dialect'] == 'cmd' else 'PowerShell'} block; this machine runs {OS_NAMES[this_os()]}"}
    miss = runner_missing(b, cfg)
    if miss:
        return {"why": "no-runner", "reason": miss}
    r = system_reason(b)
    if r:
        return {"why": "system", "reason": r}
    ph = placeholder_in(b)
    if ph:
        return {"why": "placeholder", "reason": f"has a placeholder to fill in: {short(ph, 40)}"}
    if not cfg["network"]:
        r = network_reason(b, top)
        if r:
            return {"why": "network", "reason": f"needs the network: {r}"}
    r = interactive_reason(b)
    if r:
        return {"why": "interactive", "reason": r}
    return None


def extract(cfg: dict, ws: Path) -> dict:
    data = build_blocks(cfg, ws)
    kit.save_json(ws / "blocks.json", data)
    return data


def where(b: dict) -> str:
    return f"{b['doc']}:{b['line']}"


def block_label(b: dict) -> str:
    if b["kind"] == "shell":
        d = {"sh": "sh", "ps": "PowerShell", "cmd": "cmd", "fish": "fish"}[b["dialect"]]
        return f"{d} session" if b.get("commands") else d
    if b["kind"] == "doctest":
        return "Python session"
    return {"python": "Python", "node": "JavaScript", "ts": "TypeScript"}.get(b.get("runner"), b.get("runner") or b["kind"])


# ---------------------------------------------------------------- sandboxes for the docs

PYSHIM = r'''"""Run Python the way `python ...` would, in a docs sandbox (also for embedded Pythons)."""
import os, runpy, sys
args = sys.argv[1:]
allow_pip = True
if args and args[0].startswith("--kd-pip="):
    allow_pip = args.pop(0) == "--kd-pip=yes"
mod = code = None
while args and args[0].startswith("-") and args[0] != "-":
    a = args.pop(0)
    if a == "-m" and args:
        mod = args.pop(0)
        break
    if a.startswith("-m") and len(a) > 2:
        mod = a[2:]
        break
    if a == "-c" and args:
        code = args.pop(0)
        break
    if a in ("-V", "--version"):
        print("Python " + sys.version.split()[0])
        sys.exit(0)
    if a in ("-W", "-X") and args:
        args.pop(0)
if mod in ("pip", "pip3") and not allow_pip:
    sys.stderr.write("pip isn't available in this sandbox: its Python has no private environment to install into\n")
    sys.exit(127)
if mod:
    sys.argv = [mod] + args
    sys.path.insert(0, os.getcwd())
    runpy.run_module(mod, run_name="__main__", alter_sys=True)
elif code is not None:
    sys.argv = ["-c"] + args
    sys.path.insert(0, "")
    exec(compile(code, "<string>", "exec"), {"__name__": "__main__", "__builtins__": __builtins__})
elif args and args[0] != "-":
    sys.argv = list(args)
    sys.path.insert(0, os.path.dirname(os.path.abspath(args[0])))
    runpy.run_path(args[0], run_name="__main__")
else:
    exec(compile(sys.stdin.read(), "<stdin>", "exec"), {"__name__": "__main__", "__builtins__": __builtins__})
'''
NO_PIP = "pip isn't available in this sandbox: its Python has no private environment to install into"


def python_env(box: Path, cfg: dict) -> tuple[dict, list[Path]]:
    """A Python for the sandbox whose installs land in the sandbox, never in your own Python."""
    info = cfg["python"]
    shim = box / "kd-python" / "pyshim.py"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(PYSHIM, encoding="utf-8")
    note = ""
    if info["kind"] == "full":
        venv = box / "venv"
        try:
            r = subprocess.run([info["path"], "-m", "venv", "--without-pip", "--system-site-packages", str(venv)],
                               capture_output=True, text=True, timeout=300)
            ok = r.returncode == 0
            note = (r.stderr or r.stdout).strip()[-200:]
        except (OSError, subprocess.SubprocessError) as e:
            ok, note = False, str(e)
        bindir = venv / ("Scripts" if WIN else "bin")
        vpy = bindir / ("python.exe" if WIN else "python")
        if ok and vpy.exists():
            if WIN:
                (bindir / "python3.cmd").write_text('@"%~dp0python.exe" %*\r\n', encoding="utf-8")
                for n in ("pip", "pip3"):
                    (bindir / f"{n}.cmd").write_text('@"%~dp0python.exe" -m pip %*\r\n', encoding="utf-8")
                (bindir / "py.cmd").write_text(f'@python "{shim}" --kd-pip=yes %*\r\n', encoding="utf-8")
            else:
                for n in ("pip", "pip3"):
                    p = bindir / n
                    p.write_text('#!/bin/sh\nexec python -m pip "$@"\n', encoding="utf-8", newline="\n")
                    p.chmod(0o755)
            return {"kind": "venv", "argv": [str(vpy)], "venv": str(venv), "shim": str(shim)}, [bindir]
    pybin = box / "kd-python" / "bin"
    pybin.mkdir(parents=True, exist_ok=True)
    interp = info["path"]
    if WIN:
        for n in ("python", "python3", "py"):
            (pybin / f"{n}.cmd").write_text(f'@"{interp}" "{shim}" --kd-pip=no %*\r\n', encoding="utf-8")
        for n in ("pip", "pip3"):
            (pybin / f"{n}.cmd").write_text(f"@echo {NO_PIP} 1>&2\r\n@exit /b 127\r\n", encoding="utf-8")
    else:
        for n in ("python", "python3"):
            p = pybin / n
            p.write_text(f'#!/bin/sh\nexec {shq(interp)} {shq(shim)} --kd-pip=no "$@"\n', encoding="utf-8", newline="\n")
            p.chmod(0o755)
        for n in ("pip", "pip3"):
            p = pybin / n
            p.write_text(f"#!/bin/sh\necho {shq(NO_PIP)} >&2\nexit 127\n", encoding="utf-8", newline="\n")
            p.chmod(0o755)
    return {"kind": "shim", "argv": [interp, str(shim), "--kd-pip=no"], "shim": str(shim),
            "note": note or "no Python that can make a virtual environment"}, [pybin]


def harden(box: Path, cfg: dict, python: bool) -> dict:
    """Point the installs docs ask for (pip --user, npm -g, cargo install ...) into the sandbox."""
    envp = box / "env.json"
    spec = kit.load_json(envp, {})
    s, drop = spec.setdefault("set", {}), spec.setdefault("drop", [])
    home = box / "home"
    ver = re.findall(r"\d+", cfg["python"].get("version", "3.0"))[:2] or ["3", "0"]
    userbase = home / ".python-user"
    npm = home / ".npm-global"
    s.update({
        "PYTHONUSERBASE": str(userbase), "npm_config_prefix": str(npm), "CARGO_INSTALL_ROOT": str(home / ".cargo-install"),
        "GOBIN": str(home / "go-bin"), "GEM_HOME": str(home / ".gem"), "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1", "npm_config_update_notifier": "false",
        "npm_config_fund": "false", "npm_config_audit": "false", "PAGER": "cat", "GIT_PAGER": "cat",
    })
    drop += [n for n in ("FORCE_COLOR", "CLICOLOR_FORCE", "PYTHONSTARTUP", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV",
                         "CONDA_PREFIX", "PIP_USER", "PIP_REQUIRE_VIRTUALENV") if n not in drop]
    bins: list[Path] = []
    pyenv = {"kind": "none", "argv": [cfg["python"]["path"]]}
    if python:
        pyenv, pybins = python_env(box, cfg)
        bins += pybins
        if pyenv["kind"] == "venv":
            s["VIRTUAL_ENV"] = pyenv["venv"]
    bins += [npm] if WIN else [npm / "bin"]
    bins += [userbase / f"Python{ver[0]}{ver[1]}" / "Scripts"] if WIN else [userbase / "bin"]
    bins += [home / ".cargo-install" / "bin", home / "go-bin"]
    base = s.get("PATH") or os.environ.get("PATH", "")
    s["PATH"] = os.pathsep.join([str(b) for b in bins] + [base])
    kit.save_json(envp, spec)
    kit.save_json(box / "pyenv.json", pyenv)
    return pyenv


def make_box(cfg: dict, ws: Path, name: str, python: bool = False, network: bool | None = None) -> Path:
    box = kit.make_sandbox(cfg, ws, name, network=network)
    try:
        harden(box, cfg, python)
    except BaseException:
        kit.drop_sandbox(ws, cfg, name)
        raise
    return box


def project_uses_python(cfg: dict) -> bool:
    top = Path(cfg["repo"])
    return any((top / f).exists() for f in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"))


PY_WORDS = re.compile(r"(?<![\w.-])(python3?|py|pip3?|pipx|poetry|uv|pdm|hatch|tox|pytest)(?![\w-])")


def needs_python(blocks: list[dict]) -> bool:
    for b in blocks:
        if b.get("runner") == "python" or b["kind"] == "doctest":
            return True
        if b["kind"] == "shell" and PY_WORDS.search("\n".join(code_lines(b))):
            return True
    return False


def fresh_name(hint: str) -> str:
    return f"{re.sub(r'[^A-Za-z0-9._-]+', '-', hint)[:28].strip('-') or 'box'}-{secrets.token_hex(3)}"


def fresh_run(cfg: dict, ws: Path, cmd: list[str], scratch: Path | None, hint: str, timeout: float = 300) -> dict:
    """Run a command in a brand-new sandbox (with an agent's scratch files copied in), then delete it."""
    name = fresh_name(hint)
    box = make_box(cfg, ws, name, python=project_uses_python(cfg) or bool(PY_WORDS.search(" ".join(cmd))))
    try:
        if scratch and Path(scratch).is_dir():
            shutil.copytree(scratch, box / "scratch", dirs_exist_ok=True)
        return kit.run_in(box, cmd, 1, timeout, log=False)
    finally:
        kit.drop_sandbox(ws, cfg, name)


# ---------------------------------------------------------------- running a doc's blocks

BASH_SAVE = r'''kd_save() {
  kd_rc=$?
  set +e
  for kd_n in $(compgen -v); do
    case "$kd_n" in
      BASH*|EUID|PPID|UID|SHELLOPTS|GROUPS|FUNCNAME|PIPESTATUS|RANDOM|SECONDS|LINENO|HISTCMD|EPOCHREALTIME|EPOCHSECONDS|SRANDOM|_|kd_*|PWD|OLDPWD|SHLVL|COLUMNS|LINES|DIRSTACK|OPTIND|OPTARG|OPTERR|IFS|PS1|PS2|PS3|PS4|COMP_WORDBREAKS|MACHTYPE|HOSTTYPE|OSTYPE|HOSTNAME|BASHPID|BASHOPTS|HISTFILE|HISTSIZE|HISTFILESIZE|MAILCHECK|PROMPT_COMMAND|TERM) continue ;;
    esac
    declare -p "$kd_n" 2>/dev/null
  done > @NEW@
  mv -f @NEW@ @STATE@
  { pwd -W 2>/dev/null || pwd; } > @CWD@
  exit "$kd_rc"
}
trap kd_save EXIT'''
SH_SAVE = r'''kd_save() {
  kd_rc=$?
  set +e
  export -p > @NEW@
  mv -f @NEW@ @STATE@
  pwd > @CWD@
  exit "$kd_rc"
}
trap kd_save EXIT'''
PS_SAVE = r'''  $kd_lines = foreach ($kd_v in (Get-ChildItem env:)) { "[Environment]::SetEnvironmentVariable('{0}', '{1}')" -f $kd_v.Name.Replace("'", "''"), ([string]$kd_v.Value).Replace("'", "''") }
  [IO.File]::WriteAllLines(@STATE@, [string[]]$kd_lines, (New-Object System.Text.UTF8Encoding $true))
  [IO.File]::WriteAllText(@CWD@, (Get-Location).ProviderPath)'''
WORST = ("failed", "timed-out", "differs", "blocked", "network", "no-tool", "passed")


class Session:
    """One reader at one terminal: a doc's blocks run in order in one sandbox, and the working
    folder and the variables a shell block sets carry on to the next block, as they would."""

    def __init__(self, cfg: dict, ws: Path, box: Path, logdir: Path):
        self.cfg, self.ws, self.box = cfg, ws, box
        self.repo = box / "repo"
        self.dir = box / "kd-blocks"
        self.dir.mkdir(exist_ok=True)
        self.logdir = logdir
        self.cwd = self.repo
        self.tok = secrets.token_hex(4)
        self.tools = cfg["tools"]
        self.pyenv = kit.load_json(box / "pyenv.json", {"kind": "none", "argv": [cfg["python"]["path"]]})
        self.py = self.pyenv["argv"]
        self.network = cfg["network"]
        self.entry_names = {e["name"] for e in cfg.get("entry_points", [])}
        self.env_path = runner().build_env(box).get("PATH", "")
        self.bg: list[dict] = []
        self.n = 0
        self.not_run: list[str] = []
        self.py_done: list[tuple[str, str]] = []
        self.pycon_done: list[tuple[str, str]] = []

    # ---- helpers
    def rel_cwd(self) -> str | None:
        try:
            rel = os.path.relpath(Path(self.cwd).resolve(), self.repo.resolve())
        except (ValueError, OSError):
            return None
        return None if rel == "." else rel

    def read_cwd(self) -> None:
        f = self.dir / "cwd.txt"
        if not f.is_file():
            return
        p = f.read_text(encoding="utf-8-sig", errors="replace").strip()
        f.unlink(missing_ok=True)
        if WIN and re.match(r"^/[a-zA-Z](/|$)", p):
            p = p[1].upper() + ":" + (p[2:] or "/")
        if p and Path(p).is_dir():
            self.cwd = Path(p)

    def sh_shims(self) -> list[str]:
        if not WIN or self.pyenv["kind"] == "none":
            return []
        shim = shq(fwd(self.pyenv["shim"]))
        if self.pyenv["kind"] == "venv":
            return ['python3() { python "$@"; }', 'pip() { python -m pip "$@"; }', 'pip3() { python -m pip "$@"; }',
                    f'py() {{ python {shim} --kd-pip=yes "$@"; }}']
        interp = " ".join(shq(fwd(x)) for x in self.py)
        return [f'python() {{ {interp} "$@"; }}', 'python3() { python "$@"; }', 'py() { python "$@"; }',
                'pip() { python -m pip "$@"; }', 'pip3() { python -m pip "$@"; }']

    def script(self, b: dict, background: bool = False) -> list[str]:
        """Write the block as a script for its shell; return the command that runs it."""
        d = b["dialect"]
        cmds = b.get("commands")
        path = self.dir / f"b{self.n}"
        tok = self.tok
        if d in ("sh", "fish"):
            shell = self.tools.get("fish") if d == "fish" else (self.tools.get("bash") or self.tools.get("sh"))
            q = lambda p: shq(fwd(p))
            L = []
            if d == "sh":
                L += [f"[ -f {q(self.dir / 'state.sh')} ] && . {q(self.dir / 'state.sh')}"]
            L += [f"cd {q(self.cwd)} || exit 97"]
            if d == "sh":
                L += self.sh_shims()
                if not background:
                    save = BASH_SAVE if self.tools.get("bash") else SH_SAVE
                    L += [save.replace("@NEW@", q(self.dir / "state.new")).replace("@STATE@", q(self.dir / "state.sh"))
                          .replace("@CWD@", q(self.dir / "cwd.txt"))]
            if cmds:
                for k, c in enumerate(cmds, 1):
                    L += [f"printf '%s\\n' 'KDMARK_{tok}_{k}_start'", c["cmd"],
                          f"kd_rc=$?; printf '\\n%s\\n' \"KDMARK_{tok}_{k}_end_$kd_rc\""]
                    if c["out"] is None:
                        L.append('[ "$kd_rc" -eq 0 ] || exit "$kd_rc"')
            else:
                L += ["set -e", b["code"]]
            path = path.with_suffix(".sh")
            path.write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")
            return [shell, fwd(path)]
        if d == "ps":
            st, cw = self.dir / "state.ps1", self.dir / "cwd.txt"
            L = ["$ProgressPreference = 'SilentlyContinue'",
                 "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false",
                 "$OutputEncoding = New-Object System.Text.UTF8Encoding $false",
                 f"if (Test-Path -LiteralPath {psq(st)}) {{ . {psq(st)} }}",
                 f"Set-Location -LiteralPath {psq(self.cwd)}",
                 "$global:LASTEXITCODE = 0"]
            if cmds:
                L += ["$ErrorActionPreference = 'Continue'", "try {"]
                for k, c in enumerate(cmds, 1):
                    L += [f"'KDMARK_{tok}_{k}_start'", "$global:LASTEXITCODE = 0", c["cmd"],
                          "$kd_ok = $?; $kd_rc = if ($kd_ok -and -not $LASTEXITCODE) { 0 } elseif ($LASTEXITCODE) { $LASTEXITCODE } else { 1 }",
                          "''", f"\"KDMARK_{tok}_{k}_end_$kd_rc\""]
                    if c["out"] is None:
                        L.append("if ($kd_rc -ne 0) { exit $kd_rc }")
            else:
                L += ["$ErrorActionPreference = 'Stop'", "$PSNativeCommandUseErrorActionPreference = $true", "try {",
                      b["code"], "if ($LASTEXITCODE) { exit $LASTEXITCODE }"]
            if background:
                L += ["} finally { }"]
            else:
                L += ["} finally {", PS_SAVE.replace("@STATE@", psq(st)).replace("@CWD@", psq(cw)), "}"]
            path = path.with_suffix(".ps1")
            path.write_text("\n".join(L) + "\n", encoding="utf-8-sig", newline="\n")
            return [self.tools["powershell"], "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(path)]
        st, envf, cw = self.dir / "state.cmd", self.dir / "env.txt", self.dir / "cwd.txt"
        L = ["@echo off", "chcp 65001 >nul", f'if exist "{st}" call "{st}"', f'cd /d "{self.cwd}"', 'set "kd_rc=0"']
        if cmds:
            for k, c in enumerate(cmds, 1):
                L += [f"echo KDMARK_{tok}_{k}_start", c["cmd"], 'set "kd_rc=%errorlevel%"', "echo.",
                      f"echo KDMARK_{tok}_{k}_end_%kd_rc%"]
                if c["out"] is None:
                    L.append('if not "%kd_rc%"=="0" goto kd_end')
        else:
            depth = 0
            for line in split_lines(b["code"]):
                L.append(line)
                s = line.strip()
                depth += s.count("(") - s.count(")")
                low = s.lower()
                if depth <= 0 and s and not s.endswith("^") and not low.startswith((":", "rem ", "@rem", "::", "@echo",
                                                                                     "echo off", "setlocal", "goto", "exit")):
                    L += ['set "kd_rc=%errorlevel%"', 'if not "%kd_rc%"=="0" goto kd_end']
        L += ['set "kd_rc=0"', ":kd_end", f'set > "{envf}"', f'cd > "{cw}"', "exit /b %kd_rc%"]
        path = path.with_suffix(".cmd")
        path.write_text("\r\n".join(L) + "\r\n", encoding="utf-8")
        return [self.tools["cmd"], "/d", "/c", str(path)]

    def after_cmd(self) -> None:
        envf = self.dir / "env.txt"
        if not envf.is_file():
            return
        lines = ["@echo off"]
        for raw in envf.read_bytes().decode("utf-8", "replace").splitlines():
            name, eq, val = raw.partition("=")
            if eq and name and not name.startswith("=") and not name.lower().startswith("kd_"):
                lines.append(f'set "{name}={val.replace("%", "%%")}"')
        (self.dir / "state.cmd").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        envf.unlink(missing_ok=True)

    def log(self, b: dict, r: dict, extra: str = "") -> str:
        self.logdir.mkdir(parents=True, exist_ok=True)
        p = self.logdir / f"{b['id']}-{self.n}.txt"
        p.write_text(f"# {where(b)} ({block_label(b)})\n# exit {r.get('code')}{' (timed out)' if r.get('timed_out') else ''}, "
                     f"{r.get('secs')} s\n{extra}\n{r.get('out', '')}", encoding="utf-8")
        return str(p)

    def scrub(self, text: str) -> str:
        for p, rep in ((self.repo, "<project>"), (self.box / "home", "~"), (self.box, "<sandbox>")):
            bases = {str(p)}
            try:
                bases.add(str(Path(p).resolve()))   # macOS: /var/... is /private/var/...
            except OSError:
                pass
            forms = set()
            for native in bases:
                forms |= {native, native.replace("\\", "/"), native.replace("\\", "\\\\")}
                if WIN and re.match(r"^[A-Za-z]:", native):
                    forms.add("/" + native[0].lower() + native[2:].replace("\\", "/"))
            for f in sorted(forms, key=len, reverse=True):
                text = re.sub(re.escape(f), rep, text, flags=re.I if WIN else 0)
        return text

    # ---- judging what happened
    def judge(self, out: str, code, timed_out: bool, shown: str | None, b: dict) -> dict:
        u = {"exit": code, "status": None, "match": None, "diff": None, "note": None}
        if timed_out:
            u["status"] = "timed-out"
            u["note"] = "still running when the time ran out: it may be interactive or a server (mark it <!-- docs: background -->)"
            return u
        cmp = compare(shown, self.scrub(out)) if (shown is not None and not b.get("may_differ")) else None
        if code != 0:
            if cmp and cmp["ok"]:
                u.update(status="passed", match=cmp["match"], note=f"exits {code}, and prints what the doc shows")
                return u
            tool = missing_tool(out)
            if tool:
                u["status"], u["note"] = self.tool_status(tool)
                if u["status"] != "failed":
                    u["reason"] = u["note"]
            elif not self.network and OFFLINE_RE.search(out):
                u["status"], u["note"] = "network", "failed in a way that looks like the network being off"
                u["reason"] = u["note"]
            else:
                u["status"] = "failed"
            if cmp:
                u["match"], u["diff"] = cmp["match"], cmp.get("diff")
            return u
        if shown is None:
            u.update(status="passed", match="none shown")
        elif b.get("may_differ"):
            u.update(status="passed", match="not compared (output-may-differ)")
        else:
            u.update(status="passed" if cmp["ok"] else "differs", match=cmp["match"], diff=cmp.get("diff"))
            if cmp.get("note"):
                u["note"] = cmp["note"]
        return u

    def tool_status(self, tool: str) -> tuple[str, str | None]:
        name = tool.strip()
        if name in ("pip", "venv", "ensurepip"):
            return "no-tool", f"this sandbox's Python has no {name}"
        if "/" in name or "\\" in name or name.startswith("."):
            return "failed", f"{name} isn't there"
        if name in self.entry_names:
            if self.not_run:
                return "blocked", f"`{name}` isn't on the PATH, maybe because {', '.join(self.not_run[:3])} didn't run"
            return "failed", f"`{name}` isn't on the PATH after the steps before it"
        if shutil.which(name, path=self.env_path):
            return "failed", None
        return "no-tool", f"needs `{name}`, which isn't installed here"

    # ---- running
    def run(self, b: dict, timeout: float) -> dict:
        self.n += 1
        if b.get("background"):
            res = self.start_background(b)
        elif b["kind"] == "shell":
            res = self.run_shell(b, timeout)
        elif b["kind"] == "doctest":
            res = self.run_doctest(b, timeout)
        else:
            res = self.run_code(b, timeout)
        return res

    def base(self, b: dict) -> dict:
        r = {"id": b["id"], "key": b["key"], "pos": b.get("pos"), "doc": b["doc"], "line": b["line"],
             "label": block_label(b), "status": None, "notes": [], "after": list(self.not_run)}
        rel = self.rel_cwd()
        if rel:
            r["notes"].append(f"started in {fwd(rel)}/, where an earlier block left the terminal")
        return r

    def run_shell(self, b: dict, timeout: float) -> dict:
        r = self.base(b)
        cmd = self.script(b)
        res = kit.run_in(self.box, cmd, 1, timeout, log=False)
        run = res["runs"][0]
        if b["dialect"] in ("sh", "ps", "cmd"):
            if b["dialect"] == "cmd":
                self.after_cmd()
            self.read_cwd()
        r.update(exit=run["code"], secs=run["secs"], log=self.log(b, run))
        out = run["out"]
        if b.get("commands"):
            parts = split_marks(out, self.tok)
            units, stopped = [], False
            for k, c in enumerate(b["commands"], 1):
                part = parts.get(k)
                if stopped or part is None:
                    units.append({"cmd": c["cmd"], "status": "not reached", "exit": None})
                    stopped = True
                    continue
                if part["code"] is None:   # the shell ended, or was stopped, inside this command
                    u = self.judge(part["out"], run["code"], run["timed_out"], c["out"], b)
                    stopped = True
                else:
                    u = self.judge(part["out"], part["code"], False, c["out"], b)
                u["cmd"] = c["cmd"]
                u["out"] = tail(part["out"], 20)
                units.append(u)
            r["commands"] = units
            seen = [u["status"] for u in units if u["status"] in WORST]
            r["status"] = min(seen, key=WORST.index) if seen else ("timed-out" if run["timed_out"] else "failed")
            worst = next((u for u in units if u["status"] == r["status"]), None)
            if worst and worst.get("reason"):
                r["reason"] = worst["reason"]
            r["output"] = tail(self.scrub(re.sub(rf"^KDMARK_{self.tok}_.*$\n?", "", out, flags=re.M)))
            for u in units:
                if u.get("note") and u["status"] != "passed":
                    r["notes"].append(f"`{short(u['cmd'], 40)}`: {u['note']}")
            return r
        u = self.judge(out, run["code"], run["timed_out"], b.get("shown"), b)
        r.update(status=u["status"], match=u["match"], diff=u["diff"], output=tail(self.scrub(out)), reason=u.get("reason"))
        if u["note"]:
            r["notes"].append(u["note"])
        if b["dialect"] in ("sh", "ps", "cmd") and re.search(r"(^|[\s;&|(])(cd|Set-Location|pushd)\s", b["code"]):
            r["notes"].append("it changes folder; the blocks after it start there, as in a terminal")
        return r

    def py_argv(self) -> list[str]:
        return list(self.py)

    def run_code(self, b: dict, timeout: float) -> dict:
        r = self.base(b)
        runner_name = b["runner"]
        if b.get("example"):
            argv = {"python": self.py_argv(), "node": [self.tools.get("node", "node")],
                    "ts": [self.tools.get("node", "node"), "--experimental-strip-types", "--no-warnings"],
                    "ruby": [self.tools.get("ruby", "ruby")], "perl": [self.tools.get("perl", "perl")],
                    "php": [self.tools.get("php", "php")], "go": [self.tools.get("go", "go"), "run"]}[runner_name]
            res = kit.run_in(self.box, argv + [b["doc"]], 1, timeout, log=False)
            run = res["runs"][0]
            u = self.judge(run["out"], run["code"], run["timed_out"], None, b)
            r.update(status=u["status"], exit=run["code"], secs=run["secs"], output=tail(self.scrub(run["out"])),
                     match=u["match"], log=self.log(b, run), reason=u.get("reason"))
            if u["note"]:
                r["notes"].append(u["note"])
            return r
        code = b["code"]
        run, used = self.exec_code(b, code, timeout)
        if runner_name == "python" and run["code"] != 0 and not run["timed_out"]:
            m = NAME_ERROR.search(run["out"])
            earlier = [(i, c) for i, c in self.py_done]
            if m and earlier and re.search(rf"\b{re.escape(m[1])}\b", "\n".join(c for _, c in earlier)):
                pre = ["import contextlib as _kd_c, io as _kd_io"]
                for _, c in earlier:
                    pre.append(f"with _kd_c.redirect_stdout(_kd_io.StringIO()):\n    exec(compile({c!r}, '<an earlier block>', 'exec'))")
                run2, _ = self.exec_code(b, "\n".join(pre) + "\n" + code, timeout)
                if run2["code"] == 0 or not NAME_ERROR.search(run2["out"]):
                    run = run2
                    r["notes"].append(f"ran after the Python blocks before it ({', '.join(i for i, _ in earlier)}), "
                                      "as in one session: on its own it stops with a NameError")
        u = self.judge(run["out"], run["code"], run["timed_out"], b.get("shown"), b)
        r.update(status=u["status"], exit=run["code"], secs=run["secs"], output=tail(self.scrub(run["out"])),
                 match=u["match"], diff=u["diff"], log=self.log(b, run), reason=u.get("reason"))
        if u["note"]:
            r["notes"].append(u["note"])
        if runner_name == "python" and u["status"] in ("passed", "differs"):
            self.py_done.append((b["id"], code))
        return r

    def exec_code(self, b: dict, code: str, timeout: float):
        runner_name = b["runner"]
        cjs = runner_name == "node" and re.search(r"\brequire\(", code) and not re.search(r"^\s*(import|export)\b", code, re.M)
        ext = {"python": ".py", "node": ".cjs" if cjs else ".mjs", "ts": ".mts", "ruby": ".rb", "perl": ".pl",
               "php": ".php", "go": ".go"}[runner_name]
        if runner_name == "php" and not code.lstrip().startswith("<?"):
            code = "<?php\n" + code
        try:
            inside = Path(self.cwd).resolve().is_relative_to(self.box.resolve())
        except OSError:
            inside = False
        folder = self.cwd if inside else self.repo
        if runner_name == "go":
            folder = self.dir / f"go{self.n}"
            folder.mkdir(exist_ok=True)
        name = f"_docs_block_{self.n}{ext}"
        f = folder / name
        f.write_text(code + "\n", encoding="utf-8", newline="\n")
        node = self.tools.get("node", "node")
        argv = {"python": self.py_argv() + [name], "node": [node, name],
                "ts": [node, "--experimental-strip-types", "--no-warnings", name],
                "ruby": [self.tools.get("ruby", "ruby"), name], "perl": [self.tools.get("perl", "perl"), name],
                "php": [self.tools.get("php", "php"), name], "go": [self.tools.get("go", "go"), "run", str(f)]}[runner_name]
        try:
            rel = os.path.relpath(Path(folder).resolve(), self.repo.resolve()) if runner_name != "go" else self.rel_cwd()
        except (ValueError, OSError):
            rel = None
        try:
            res = kit.run_in(self.box, argv, 1, timeout, cwd=None if rel in (None, ".") else rel, log=False)
        finally:
            f.unlink(missing_ok=True)
        return res["runs"][0], argv

    def run_doctest(self, b: dict, timeout: float) -> dict:
        r = self.base(b)

        def go(text):
            f = self.dir / f"b{self.n}.txt"
            f.write_text(text + "\n", encoding="utf-8")
            argv = self.py_argv() + ["-m", "doctest", "-o", "NORMALIZE_WHITESPACE", "-o", "ELLIPSIS", str(f)]
            return kit.run_in(self.box, argv, 1, timeout, cwd=self.rel_cwd(), log=False)["runs"][0]
        run = go(b["code"])
        if run["code"] != 0 and self.pycon_done and NAME_ERROR.search(run["out"]):
            run2 = go("\n".join(c for _, c in self.pycon_done) + "\n" + b["code"])
            if run2["code"] == 0:
                run = run2
                r["notes"].append(f"passes after the sessions before it ({', '.join(i for i, _ in self.pycon_done)}), "
                                  "as one session; on its own it stops with a NameError")
        r.update(exit=run["code"], secs=run["secs"], output=tail(self.scrub(run["out"])), log=self.log(b, run),
                 match="doctest")
        if run["timed_out"]:
            r["status"] = "timed-out"
        elif run["code"] == 0:
            r["status"] = "passed"
            self.pycon_done.append((b["id"], b["code"]))
        else:
            tool = missing_tool(run["out"])
            if tool:
                r["status"], note = self.tool_status(tool)
                if note:
                    r["notes"].append(note)
                    r["reason"] = note
            elif "Exception raised" in run["out"] or "Traceback" in run["out"]:
                r["status"] = "failed"
            else:
                r["status"] = "differs"
                r["diff"] = [x for x in split_lines(run["out"]) if x.strip()][:20]
        return r

    def start_background(self, b: dict) -> dict:
        r = self.base(b)
        if b["kind"] != "shell" or b["dialect"] not in ("sh", "ps", "cmd"):
            r.update(status="skipped", skip={"why": "no-runner", "reason": "only shell blocks can run in the background"})
            return r
        cmd = self.script(b, background=True)
        env = runner().build_env(self.box)
        logf = self.dir / f"bg{self.n}.log"
        fh = open(logf, "wb")
        extra = {"start_new_session": True} if not WIN else {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        try:
            p = subprocess.Popen(runner().resolve(cmd, env), cwd=str(self.repo), env=env, stdin=subprocess.DEVNULL,
                                 stdout=fh, stderr=subprocess.STDOUT, **extra)
        except OSError as e:
            fh.close()
            r.update(status="failed", output=f"could not start: {e}")
            return r
        deadline = time.monotonic() + (b.get("settle") or 3)
        while time.monotonic() < deadline and p.poll() is None:
            time.sleep(0.1)
        r["background"] = True
        if p.poll() is not None:
            fh.close()
            out = logf.read_bytes().decode("utf-8", "replace")
            r.update(exit=p.returncode, output=tail(self.scrub(out)),
                     status="passed" if p.returncode == 0 else "failed")
            r["notes"].append("marked background, but it finished on its own")
        else:
            r.update(status="background")
            r["notes"].append(f"left running in the background; stopped after the doc's last block")
            self.bg.append({"proc": p, "fh": fh, "log": logf, "result": r})
        return r

    def stop_background(self) -> None:
        for bgr in self.bg:
            p = bgr["proc"]
            if p.poll() is None:
                runner().kill_tree(p)
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    pass
            bgr["fh"].close()
            out = bgr["log"].read_bytes().decode("utf-8", "replace")
            bgr["result"]["output"] = tail(self.scrub(out))
            if bgr["result"]["status"] == "background":
                bgr["result"]["status"] = "passed"
        self.bg = []


def split_marks(out: str, tok: str) -> dict:
    """The output of each command in a console block, split by the markers around it."""
    parts: dict = {}
    cur = None
    rx = re.compile(rf"^KDMARK_{tok}_(\d+)_(start|end)(?:_(-?\d+))?\s*$")
    for line in out.split("\n"):
        m = rx.match(line)
        if m:
            k = int(m[1])
            if m[2] == "start":
                cur = k
                parts[k] = {"out": [], "code": None}
            elif k in parts:
                parts[k]["code"] = int(m[3]) if m[3] is not None else None
                cur = None
            continue
        if cur is not None:
            parts[cur]["out"].append(line)
    for p in parts.values():
        if p["out"] and not p["out"][-1].strip():
            p["out"].pop()
        p["out"] = "\n".join(p["out"])
    return parts


def missing_tool(out: str) -> str | None:
    for rx in NOT_FOUND_RES:
        m = rx.search(out or "")
        if m:
            return m.group(1)
    return None


def norm_line(s: str) -> str:
    s = s.replace("\\", "/")
    for rx, rep in NORMS:
        s = rx.sub(rep, s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(<(?:time|duration|version|id)>)(?:[ ,]*\1)+", r"\1", s)
    return s


def norm_lines(text: str) -> list[str]:
    text = ANSI.sub("", text or "")
    out = []
    for line in split_lines(text):
        n = norm_line(line)
        if n:
            out.append(n)
    return out


def compare(shown: str, actual: str) -> dict:
    """Does what the doc shows match what it printed, once the noise is taken out?"""
    raw = split_lines(ANSI.sub("", shown or ""))
    elided = any(ELISION.match(x) for x in raw)
    want = [n for x in raw if not ELISION.match(x) and (n := norm_line(x))]
    got = norm_lines(actual)
    if not want:
        return {"ok": True, "match": "nothing to compare"}
    if want == got and not elided:
        return {"ok": True, "match": "exact"}
    it = iter(got)
    if all(any(w == g for g in it) for w in want):
        extra = len(got) - len(want)
        return {"ok": True, "match": "partial", "note": f"the doc shows part of the output ({plural(extra, 'more line')} in the real one)" if extra else None}
    diff = list(difflib.unified_diff(want, got, "the doc shows", "it printed", lineterm="", n=1))
    return {"ok": False, "match": "none" if not set(want) & set(got) else "some", "diff": diff[2:22]}


def run_group(cfg: dict, ws: Path, group: str, blocks: list[dict], first: list[dict], logdir: Path,
              timeout: float | None, keep: bool = False, upto: str | None = None) -> list[dict]:
    """Run one doc's blocks in order in one fresh sandbox."""
    runnable = [b for b in blocks if b["kind"] in RUNNABLE]
    todo = [b for b in runnable if not b["skip"]]
    results: list[dict] = []
    if not todo:
        return [skip_result(b) for b in runnable]
    name = fresh_name("blocks-" + PurePosixPath(group.rstrip("/")).name)
    box = make_box(cfg, ws, name, python=needs_python(todo + first))
    sess = Session(cfg, ws, box, logdir / re.sub(r"[^A-Za-z0-9._-]+", "-", group).strip("-"))
    try:
        for b in first:
            r = sess.run(b, b.get("timeout") or timeout or cfg["timeout"])
            if r["status"] not in ("passed", "differs", "background"):
                sess.not_run.append(f"{where(b)} (run first, {r['status']})")
        sess.cwd = sess.repo   # files and variables carry on from the docs run first; each doc starts in the project folder
        for b in runnable:
            if b["skip"]:
                results.append(skip_result(b))
                if b["skip"]["why"] not in ("platform", "marked", "history"):
                    sess.not_run.append(b["id"])
            else:
                r = sess.run(b, b.get("timeout") or timeout or cfg["timeout"])
                if b.get("setup"):
                    r["setup"] = True
                results.append(r)
                if r["status"] not in ("passed", "differs", "background"):
                    sess.not_run.append(b["id"])
            if upto and b["key"] == upto:
                break
    finally:
        sess.stop_background()
        if not keep:
            kit.drop_sandbox(ws, cfg, name)
        else:
            print(f"kept sandbox {name}: {box}")
    return results


def skip_result(b: dict) -> dict:
    return {"id": b["id"], "key": b["key"], "pos": b.get("pos"), "doc": b["doc"], "line": b["line"],
            "label": block_label(b), "status": "skipped", "skip": b["skip"], "notes": [], "after": []}


def describe(r: dict) -> str:
    s = r["status"]
    if s == "skipped":
        return f"not run: {r['skip']['reason']}"
    extra = ""
    if s == "passed":
        m = r.get("match")
        if r.get("commands"):
            shown = sum(1 for u in r["commands"] if u.get("match") not in (None, "none shown"))
            extra = f" ({plural(len(r['commands']), 'command')}" + (f", output matches for {shown}" if shown else "") + ")"
        elif m in ("exact", "partial"):
            extra = " (output matches" + (", in part" if m == "partial" else "") + ")"
        elif m == "doctest":
            extra = " (doctest)"
    elif s == "failed":
        bad = next((u for u in r.get("commands") or [] if u["status"] == "failed"), r)
        extra = f" (exit {bad.get('exit')})"
    elif s in ("no-tool", "network", "blocked"):
        extra = ": " + (r.get("reason") or "")
    elif s == "timed-out":
        extra = f" after {r.get('secs')} s"
    return s + extra


def block_findings(ws: Path, cfg: dict, data: dict, results: list[dict], p: int,
                   only: list[str] | None = None) -> tuple[list[str], list[str]]:
    """Failing and differing blocks become findings; blocks that pass now fix earlier ones."""
    by_key = {b["key"]: b for b in data["blocks"]}
    want = {d.replace("\\", "/") for d in only or []}
    ran_docs = {d for d, _ in data["docs"] if not want or d in want or any(d.startswith(w.rstrip("/") + "/") for w in want)}
    res_by_key = {r["key"]: r for r in results}
    res_by_pos = {tuple(r["pos"]): r for r in results if r.get("pos")}

    def fn(db):
        added, fixed = [], []
        for f in db["findings"]:
            blk = f.get("block")
            if not blk or f["status"] not in ("proven", "candidate"):
                continue
            if blk["doc"] not in ran_docs and (Path(cfg["repo"]) / blk["doc"]).exists():
                continue
            r = res_by_key.get(blk["key"]) or res_by_pos.get(tuple(blk.get("pos") or ()))
            if r and r["key"] != blk["key"]:
                old, new = blk.get("code", ""), (by_key.get(r["key"]) or {}).get("code", "")
                if difflib.SequenceMatcher(None, old, new).ratio() < 0.3:
                    r = None
            if r is None:
                f.update(status="fixed", fixed_by={"how": "removed", "pass": p, "note": "the block is no longer in the doc"})
                fixed.append(f["id"])
            elif r["status"] == "passed":
                f.update(status="fixed", fixed_by={"how": "rerun", "pass": p, "note": f"{where(r)} passes now"})
                fixed.append(f["id"])
            else:
                f["last_seen"] = p
        open_keys = {f["block"]["key"] for f in db["findings"] if f.get("block") and f["status"] in ("proven", "candidate", "dismissed")}
        for r in results:
            if r["status"] not in BAD or r["key"] in open_keys or r.get("setup"):
                continue
            b = by_key[r["key"]]
            first = f"{b.get('runner') or b.get('dialect')} {b['doc']}" if b.get("example") else                 (b.get("commands") or [{"cmd": b["code"]}])[0]["cmd"]
            if r.get("commands"):
                bad = next((u for u in r["commands"] if u["status"] in BAD), r["commands"][0])
                first = bad["cmd"]
            if r["status"] == "failed":
                title = f"`{short(first, 50)}` fails"
                kind = "wrong"
                sev = "high" if IMPORTANT_RE.search(b["doc"] + " " + " ".join(b["heading"])) else "medium"
                happened = f"exit {r.get('exit')}:\n{tail(r.get('output', ''), 6)}"
            elif r["status"] == "differs":
                title = f"`{short(first, 50)}` prints something else"
                kind = "stale"
                none = r.get("match") == "none" or any(u.get("match") == "none" for u in r.get("commands") or [])
                sev = "medium" if none else "low"
                diff = r.get("diff") or next((u.get("diff") for u in r.get("commands") or [] if u.get("diff")), None) or []
                happened = "\n".join(diff) or tail(r.get("output", ""), 6)
            else:
                title = f"`{short(first, 50)}` never finishes"
                kind, sev = "unclear", "low"
                happened = f"still running after {r.get('secs')} s; the doc doesn't say it's a server or waits for input"
            told = "The doc says to run:\n" + tail(b["code"], 12)
            if b.get("shown"):
                told += "\n\nand shows:\n" + tail(b["shown"], 8)
            status = "candidate" if (r["status"] == "failed" and r.get("after")) else "proven"
            notes = []
            if status == "candidate":
                notes.append(f"blocks before it didn't run ({', '.join(r['after'][:4])}), which may be why it fails")
            f = {"id": f"D{db['next']}", "title": title, "kind": kind, "severity": sev, "doc": b["doc"], "line": b["line"],
                 "heading": " > ".join(b["heading"]), "told": told, "happened": happened, "source": "script:run-blocks",
                 "status": status, "block": {"key": b["key"], "pos": b.get("pos"), "id": b["id"], "doc": b["doc"],
                                             "code": b["code"]},
                 "repro": {"type": "blocks", "doc": b["group"], "upto": b["key"]}, "notes": notes, "first_seen": p,
                 "last_seen": p, "result": r["status"]}
            db["next"] += 1
            db["findings"].append(f)
            added.append(f["id"])
        return added, fixed
    return mutate_findings(ws, fn)


SERVERISH = re.compile(r"\b(serve|server|runserver|uvicorn|gunicorn|flask run|rails s(erver)?|http\.server|"
                       r"(npm|yarn|pnpm|bun) (run )?(start|dev|serve)|--watch)\b", re.I)


def starts_server(blocks: list[dict]) -> bool:
    return any(b.get("background") or (b["kind"] == "shell" and SERVERISH.search(b["code"]))
               for b in blocks if b["kind"] in RUNNABLE and not b["skip"])


def run_all(cfg: dict, ws: Path, data: dict, docs: list[str] | None, first_docs: list[str], keep: bool,
            timeout: float | None, quiet: bool = False, jobs: int = 3) -> tuple[int, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for b in data["blocks"]:
        groups.setdefault(b["group"], []).append(b)
    if docs:
        want = {d.replace("\\", "/") for d in docs}
        unknown = want - set(groups) - {b["doc"] for b in data["blocks"]}
        if unknown:
            fail(f"no blocks in {', '.join(sorted(unknown))} (is it in scope? run extract to see the docs)")
        groups = {g: bs for g, bs in groups.items() if g in want or any(b["doc"] in want for b in bs)}
    first_blocks = []
    for d in first_docs:
        if d not in groups and d not in {b["group"] for b in data["blocks"]}:
            fail(f"--first {d}: no such doc in scope")
        first_blocks += [b for b in data["blocks"] if b["group"] == d and b["kind"] in RUNNABLE and not b["skip"]]
    p = latest_pass(ws, "blocks") + 1
    logdir = ws / "logs" / f"blocks-{p}"
    todo = [(g, bs) for g, bs in groups.items() if any(b["kind"] in RUNNABLE for b in bs)]

    def one(g, blocks):
        t0 = time.monotonic()
        pre = [] if g in first_docs else first_blocks
        return run_group(cfg, ws, g, blocks, pre, logdir, timeout, keep), round(time.monotonic() - t0, 1), bool(pre)
    # Docs run side by side, each in its own sandbox. Docs that start a server run one after
    # another, so two of them can't fight over the same port.
    servers = [(g, bs) for g, bs in todo if starts_server(bs + first_blocks)]
    others = [x for x in todo if x not in servers]
    done: dict = {}
    if jobs <= 1:
        for g, bs in todo:
            done[g] = one(g, bs)
    else:
        import concurrent.futures
        runner()   # load run.py here, once: kit.runner() isn't safe to call first from several threads

        def in_turn():
            return {g: one(g, bs) for g, bs in servers}
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs + (1 if servers else 0)) as ex:
            chain = ex.submit(in_turn) if servers else None
            futures = {g: ex.submit(one, g, bs) for g, bs in others}
            for g, f in futures.items():
                done[g] = f.result()
            if chain:
                done.update(chain.result())
    results: list[dict] = []
    for g, _ in todo:
        rs, secs, pre = done[g]
        results += rs
        if not quiet:
            ran = sum(1 for r in rs if r["status"] != "skipped")
            print(f"{g}: {plural(len(rs), 'block')}, {ran} run, {secs} s" + (f" (after {', '.join(first_docs)})" if pre else ""))
            for r in rs:
                print(f"  {r['id']:<5} {where(r):<28} {r['label']:<18} {describe(r)}")
                for u in r.get("commands") or []:
                    if u["status"] not in ("passed",):
                        print(f"        $ {short(u['cmd'], 60)}  -> {u['status']}" + (f" (exit {u['exit']})" if u.get("exit") else ""))
                for line in (r.get("diff") or [])[:8]:
                    print(f"        {line}")
                for u in r.get("commands") or []:
                    for line in (u.get("diff") or [])[:8]:
                        print(f"        {line}")
    kit.save_json(ws / "passes" / f"blocks-{p}.json", {"pass": p, "at": kit.now(), "head": git_head(cfg),
                                                        "network": cfg["network"], "first": first_docs,
                                                        "docs": sorted(groups), "results": results})
    return p, results


def git_head(cfg: dict) -> str | None:
    if not cfg["git"]:
        return None
    return kit.git(Path(cfg["repo"]), "rev-parse", "HEAD", check=False).strip() or None


def tally(results: list[dict]) -> dict:
    t: dict = {}
    for r in results:
        if r.get("setup"):
            key = "setup"
        elif r["status"] == "skipped":
            key = "skipped:" + r["skip"]["why"]
        elif r["status"] == "background":
            key = "passed"
        else:
            key = r["status"]
        t[key] = t.get(key, 0) + 1
    return t


SKIP_WORDS = {"lead": "left out in the side-effect check", "marked": "marked skip in the doc", "history": "in a changelog",
              "platform": "for another system", "no-runner": "with no runner here", "system": "that would change the system",
              "placeholder": "with placeholders", "network": "that need the network", "interactive": "interactive",
              "cap": "over the cap"}
NC_WORDS = {"no-tool": "needed a tool that isn't installed here", "network": "failed in a way that looks like the network being off",
            "blocked": "blocked by an earlier step that didn't run"}


def tally_line(results: list[dict]) -> str:
    t = tally(results)
    nc_keys = sorted(k for k in t if k.startswith("skipped:") or k in NC_WORDS)
    ran = sum(v for k, v in t.items() if k not in nc_keys and k != "setup")
    parts = [f"{t.get('passed', 0)} passed"]
    for k, one, many in (("differs", "differs", "differ"), ("failed", "failed", "failed"), ("timed-out", "timed out", "timed out")):
        if t.get(k):
            parts.append(f"{t[k]} {one if t[k] == 1 else many}")
    line = f"{plural(ran, 'block')} ran: " + ", ".join(parts)
    nc = sum(t[k] for k in nc_keys)
    if nc:
        bits = [f"{t[k]} {SKIP_WORDS.get(k.split(':', 1)[1], k) if k.startswith('skipped:') else NC_WORDS[k]}" for k in nc_keys]
        line += f". Not checked: {nc} ({'; '.join(bits)})"
    if t.get("setup"):
        line += f". {plural(t['setup'], 'setup block')} ran but {'is' if t['setup'] == 1 else 'are'}n't reported"
    return line + "."


# ---------------------------------------------------------------- help text against the docs

OPT_TOKEN = re.compile(r"(?<![\w/\\.-])(--?[A-Za-z0-9?][\w-]*)")
OTHER_TOOLS = {"pip", "pip3", "npm", "npx", "yarn", "pnpm", "git", "cargo", "go", "docker", "python", "python3", "py",
               "node", "curl", "wget", "make", "pytest", "brew", "apt", "conda", "uv", "poetry", "gh", "kubectl", "ssh",
               "tar", "ls", "rm", "cp", "mv", "grep", "sed", "awk", "find", "chmod", "mkdir", "cd", "echo", "cat",
               "powershell", "pwsh", "bash", "sh", "venv"}


def entry_argv(e: dict, py: list[str]) -> list[str]:
    if e["how"] == "python-entry":
        boot = (f"import sys, importlib, functools; sys.path[:0] = ['src', '']; sys.argv[0] = {e['name']!r}; "
                f"m = importlib.import_module({e['module']!r}); "
                f"f = functools.reduce(getattr, {e['attr']!r}.split('.'), m); sys.exit(f())")
        return py + ["-c", boot]
    if e["how"] == "python-script":
        return py + e["argv"]
    return list(e["argv"])


def parse_help(text: str) -> dict:
    groups: list[set] = []
    subs: list[str] = []

    def add(toks):
        toks = {t for t in toks if t not in ("-", "--")}
        if not toks:
            return
        for g in groups:
            if g & toks:
                g |= toks
                return
        groups.append(set(toks))
    section = None
    for line in split_lines(text):
        s = line.strip()
        if not s:
            continue
        if re.match(r"^usage\b", s, re.I):
            subs += [x for m in re.finditer(r"\{([\w,-]+)\}", s) for x in m[1].split(",")]
            for tok in OPT_TOKEN.findall(s):
                add({tok})
            section = "usage"
            continue
        if not line[:1].isspace() and s.endswith(":"):
            section = s[:-1].lower()
            continue
        if s.startswith("-"):
            col = re.split(r"\s{2,}|\t", s, maxsplit=1)[0]
            toks = OPT_TOKEN.findall(col)
            if toks:
                add(set(toks))
            continue
        m = re.match(r"^\s+\{([\w,-]+)\}", line)
        if m:
            subs += m[1].split(",")
            continue
        if section and "command" in section and not re.search(r"positional|argument|option", section):
            m = re.match(r"^\s+([a-z][\w:-]*)(?:\s*,\s*[a-z][\w-]*)*(?:\s{2,}|\s*$)", line)
            if m:
                subs.append(m[1])
        elif section == "usage" and line[:1].isspace():
            for tok in OPT_TOKEN.findall(s):
                add({tok})
    return {"groups": [sorted(g) for g in groups], "subs": list(dict.fromkeys(subs))}


def run_help(box: Path, argv: list[str]) -> dict:
    tried = []
    for flag in ("--help", "-h", "help"):
        r = kit.run_in(box, argv + [flag], 1, 60, log=False)["runs"][0]
        tried.append((flag, r))
        if r["code"] == 0 and r["out"].strip():
            return {"flag": flag, "ok": True, "exit": 0, "out": r["out"]}
    for flag, r in tried:
        if re.search(r"(?im)^\s*usage\b", r["out"]):
            return {"flag": flag, "ok": True, "exit": r["code"], "out": r["out"]}
    flag, r = tried[0]
    return {"flag": flag, "ok": False, "exit": r["code"], "out": r["out"]}


def invocation(words: list[str], e: dict) -> int | None:
    """If these words run entry point e, the index of its first argument."""
    i = 0
    while i < len(words) and (re.match(r"^[A-Za-z_]\w*=", words[i]) or cmd_word(words[i]) in ("sudo", "time", "exec", "env", "command", "nohup")):
        i += 1
    if i < len(words) and cmd_word(words[i]) in ("npx", "uvx", "bunx", "pnpx"):
        i += 1
    if i + 1 < len(words) and cmd_word(words[i]) in ("poetry", "uv", "pipx", "pdm", "hatch") and words[i + 1] == "run":
        i += 2
    if i + 1 < len(words) and cmd_word(words[i]) in ("bundle",) and words[i + 1] == "exec":
        i += 2
    if i >= len(words):
        return None
    w = words[i].replace("\\", "/")
    paths = [p.replace("\\", "/") for p in e.get("paths") or []]
    base = cmd_word(w)
    if base in (e["name"].lower(), re.sub(r"\.(py|js|sh)$", "", e["name"].lower())) and "/" not in w.lstrip("./"):
        return i + 1
    if any(w.lstrip("./") == p.lstrip("./") for p in paths):
        return i + 1
    if base in ("python", "python3", "py", "node", "sh", "bash", "deno", "bun"):
        j = i + 1
        while j < len(words) and words[j].startswith("-") and words[j] not in ("-m",):
            j += 2 if words[j] in ("-X", "-W") else 1
        if j < len(words) and words[j] == "-m" and j + 1 < len(words) and e.get("module") and words[j + 1] == e["module"]:
            return j + 2
        if j < len(words) and any(words[j].replace("\\", "/").lstrip("./") == p for p in paths):
            return j + 1
    return None


def roff_text(text: str) -> str:
    text = re.sub(r"\\f[BIRP]|\\f\(\w\w|\\&|\\e", "", text)
    return text.replace("\\-", "-").replace("\\(em", "—")


def scan_docs(cfg: dict, docs: list[list[str]], entries: list[dict]) -> dict:
    """Where the docs mention each command's options and subcommands."""
    top = Path(cfg["repo"])
    strong = {e["name"]: [] for e in entries}
    loose: list[tuple] = []
    anywhere: set = set()
    words_in_code: set = set()
    for rel, typ in docs:
        try:
            text = read_text(top / rel)
        except OSError:
            continue
        if typ == "man":
            text = roff_text(text)
        lines = split_lines(text)
        code_rows: set = set()
        if typ == "md":
            blocks, _ = parse_markdown(text)
            for b in blocks:
                code_rows.update(range(b["line"], b["end"] + 1))
        elif typ == "rst":
            blocks, _ = parse_rst(text)
            for b in blocks:
                code_rows.update(range(b["line"], b["end"] + 1))
        elif typ in ("txt", "man"):
            code_rows = set(range(1, len(lines) + 1))
        stem = PurePosixPath(rel).name.split(".")[0]
        for no, line in enumerate(lines, 1):
            anywhere.update(OPT_TOKEN.findall(line))
            spans = [m.group(2) for m in re.finditer(r"(`+)(.+?)\1", line)] if no not in code_rows else []
            texts = [line] if no in code_rows else spans
            for t in texts:
                t2 = SH_PROMPT.sub("", PS_PROMPT.sub("", t))
                words_in_code.update(re.findall(r"[\w:-]+", t2))
                for words in segments([t2]):
                    for e in entries:
                        k = invocation(words, e)
                        if k is None:
                            continue
                        rest = words[k:]
                        for j, tok in enumerate(rest):
                            if tok == "--":
                                break
                            if tok.startswith("-") and len(tok) > 1:
                                strong[e["name"]].append({"opt": tok.split("=", 1)[0], "doc": rel, "line": no,
                                                          "sub": rest[0] if rest and not rest[0].startswith("-") else None})
                        if rest and not rest[0].startswith("-"):
                            strong[e["name"]].append({"subcmd": rest[0], "doc": rel, "line": no})
            if no in code_rows and typ != "man":
                continue
            others = {w.lower() for w in re.findall(r"`([\w.-]+)", line)} | {w.lower() for w in re.findall(r"\b[\w.-]+\b", line)}
            for s in spans:
                if re.fullmatch(r"\s*-{1,2}[A-Za-z0-9][\w-]*(?:[ =]\S*)?(?:\s*,\s*-{1,2}[A-Za-z0-9][\w-]*(?:[ =]\S*)?)*\s*", s):
                    for tok in OPT_TOKEN.findall(s):
                        loose.append((tok, rel, no, others))
            m = re.match(r"^\s*(?:[-*+]|\d+[.)]|\||#{1,6})\s*(-{1,2}[A-Za-z0-9][\w-]*)", line)
            if m:
                loose.append((m[1], rel, no, others))
            if typ == "man":
                for e in entries:
                    if stem == e["name"]:
                        for tok in OPT_TOKEN.findall(line):
                            loose.append((tok, rel, no, {e["name"]}))
    return {"strong": strong, "loose": loose, "anywhere": anywhere, "words": words_in_code}


def cmd_help_drift(a) -> None:
    ws, cfg = load(a)
    if a.cli:
        extra = find_entry_points(Path(cfg["repo"]), [], a.cli)
        def add_cli(c):
            have = {e["name"]: e for e in c.get("entry_points", [])}
            for e in extra:
                have[e["name"]] = e
            c["entry_points"] = list(have.values())
        kit.update_cfg(ws, add_cli)
        ws, cfg = load(a)
    code = help_drift(cfg, ws)
    sys.exit(code)


def help_drift(cfg: dict, ws: Path, quiet: bool = False) -> int:
    entries = cfg.get("entry_points", [])
    p = latest_pass(ws, "help") + 1
    if not entries:
        if not quiet:
            print("No commands found to check. Name one with help-drift --cli NAME=COMMAND.")
        kit.save_json(ws / "passes" / f"help-{p}.json", {"pass": p, "at": kit.now(), "entries": [], "missing": [], "undocumented": []})
        return 0
    name = fresh_name("help")
    box = make_box(cfg, ws, name, python=any(e["how"].startswith("python") for e in entries))
    pyenv = kit.load_json(box / "pyenv.json", {"argv": [cfg["python"]["path"]]})
    results = []
    try:
        for e in entries:
            argv = entry_argv(e, pyenv["argv"])
            h = run_help(box, argv)
            rec = {"name": e["name"], "source": e["source"], "argv": argv, "flag": h["flag"], "ok": h["ok"],
                   "exit": h["exit"], "out": tail(h["out"], 200), "groups": [], "subs": {}}
            if h["ok"]:
                parsed = parse_help(h["out"])
                rec["groups"] = parsed["groups"]
                for sub in parsed["subs"][:25]:
                    if sub in ("help", "completion"):
                        rec["subs"][sub] = {"groups": [], "ok": True}
                        continue
                    hs = kit.run_in(box, argv + [sub, "--help"], 1, 60, log=False)["runs"][0]
                    rec["subs"][sub] = {"groups": parse_help(hs["out"])["groups"] if hs["code"] == 0 else [],
                                        "ok": hs["code"] == 0}
            results.append(rec)
    finally:
        kit.drop_sandbox(ws, cfg, name)
    scoped = find_docs(cfg)
    every = all_doc_paths(cfg)
    in_scope = scan_docs(cfg, [d for d in scoped if d[1] != "example"], entries)
    everywhere = scan_docs(cfg, every, entries)
    missing, undocumented = [], []
    ok_entries = [r for r in results if r["ok"]]
    all_opts = {r["name"]: {o for g in r["groups"] for o in g} | {o for s in r["subs"].values() for g in s["groups"] for o in g}
                for r in ok_entries}
    union = set().union(*all_opts.values()) if all_opts else set()

    def known(opt, opts):
        if opt in opts or opt[:2] in opts and len(opt) > 2 and not opt.startswith("--"):
            return True
        return bool(re.fullmatch(r"-[A-Za-z]{2,}", opt)) and all(f"-{c}" in opts for c in opt[1:])
    for r in ok_entries:
        opts = all_opts[r["name"]]
        seen = set()
        for m in in_scope["strong"].get(r["name"], []):
            if "subcmd" in m:
                if r["subs"] and m["subcmd"] not in r["subs"] and re.fullmatch(r"[a-z][\w-]*", m["subcmd"]) \
                        and ("sub", m["subcmd"]) not in seen:
                    seen.add(("sub", m["subcmd"]))
                    missing.append({"entry": r["name"], "what": "subcommand", "name": m["subcmd"], "doc": m["doc"], "line": m["line"]})
                continue
            o = m["opt"]
            if re.fullmatch(r"-\d+", o) or known(o, opts) or ("opt", o) in seen:
                continue
            seen.add(("opt", o))
            missing.append({"entry": r["name"], "what": "option", "name": o, "doc": m["doc"], "line": m["line"]})
        for g in r["groups"]:
            if set(g) & {"-h", "--help"}:
                continue
            if not set(g) & everywhere["anywhere"]:
                undocumented.append({"entry": r["name"], "what": "option", "name": ", ".join(g), "sub": None})
        for sub, s in r["subs"].items():
            if sub in ("help", "completion"):
                continue
            if sub not in everywhere["words"]:
                undocumented.append({"entry": r["name"], "what": "subcommand", "name": sub, "sub": None})
            for g in s["groups"]:
                if not set(g) & {"-h", "--help"} and not set(g) & everywhere["anywhere"]:
                    undocumented.append({"entry": r["name"], "what": "option", "name": ", ".join(g), "sub": sub})
    names = {r["name"].lower() for r in ok_entries}
    seen_loose = {(m["name"]) for m in missing}
    for tok, doc, line, others in in_scope["loose"]:
        if not ok_entries or tok in seen_loose or known(tok, union) or re.fullmatch(r"-\d+", tok):
            continue
        if (others & OTHER_TOOLS) and not (others & names):
            continue
        seen_loose.add(tok)
        entry = ok_entries[0]["name"] if len(ok_entries) == 1 else "any command"
        missing.append({"entry": entry, "what": "option", "name": tok, "doc": doc, "line": line})
    for m in missing:
        try:
            m["text"] = split_lines(read_text(Path(cfg["repo"]) / m["doc"]))[m["line"] - 1].strip()
        except (OSError, IndexError):
            pass
    kit.save_json(ws / "passes" / f"help-{p}.json", {"pass": p, "at": kit.now(), "head": git_head(cfg), "entries": results,
                                                      "missing": missing, "undocumented": undocumented})
    added, fixed = drift_findings(ws, p, missing, undocumented, results)
    if not quiet:
        for r in results:
            if r["ok"]:
                n = len(r["groups"])
                print(f"{r['name']} ({r['source']}): `{r['name']} {r['flag']}` lists {plural(n, 'option')}"
                      + (f" and {plural(len(r['subs']), 'subcommand')}" if r["subs"] else ""))
            else:
                print(f"{r['name']} ({r['source']}): couldn't read its help (exit {r['exit']}): {short(tail(r['out'], 2), 120)}")
        for m in missing:
            print(f"  documented, but `{m['entry']} --help` doesn't have it: {m['what']} {m['name']} ({m['doc']}:{m['line']})")
        for u in undocumented:
            print(f"  in `{u['entry']}{' ' + u['sub'] if u['sub'] else ''} --help`, but no doc mentions it: {u['what']} {u['name']}")
        print(f"Help drift: {len(missing)} documented but missing (high), {len(undocumented)} undocumented (low)."
              + (f" Findings: {', '.join(added)} added." if added else "") + (f" Fixed now: {', '.join(fixed)}." if fixed else ""))
    return 1 if (missing or undocumented) else 0


def drift_findings(ws: Path, p: int, missing: list, undocumented: list, results: list) -> tuple[list, list]:
    checked = {r["name"] for r in results if r["ok"]}
    now = {}
    for m in missing:
        now[f"drift:{m['entry']}:{m['what']}:{m['name']}:missing"] = ("high", m)
    for u in undocumented:
        now[f"drift:{u['entry']}:{u.get('sub') or ''}:{u['what']}:{u['name']}:undocumented"] = ("low", u)

    def fn(db):
        added, fixed = [], []
        have = {}
        for f in db["findings"]:
            if f.get("check_key", "").startswith("drift:"):
                have[f["check_key"]] = f
                ent = f["check_key"].split(":")[1]
                if f["status"] in ("proven", "candidate") and f["check_key"] not in now and (ent in checked or ent == "any command"):
                    f.update(status="fixed", fixed_by={"how": "rerun", "pass": p, "note": "help-drift no longer finds it"})
                    fixed.append(f["id"])
        for key, (sev, m) in now.items():
            if key in have:
                have[key]["last_seen"] = p
                continue
            if m in missing:
                title = f"the docs mention {m['what']} {m['name']}, which `{m['entry']} --help` doesn't have"
                told = f"{m['doc']}:{m['line']}: {short(m.get('text') or m['name'], 120)}"
                happened = f"`{m['entry']} --help` lists no {m['what']} {m['name']}"
                doc, line = m["doc"], m["line"]
            else:
                full = m["entry"] + (f" {m['sub']}" if m.get("sub") else "")
                title = f"`{full} --help` has {m['what']} {m['name']}, which no doc mentions"
                told, happened, doc, line = "Nothing: the docs never mention it", f"`{full} --help` lists {m['name']}", None, None
            f = {"id": f"D{db['next']}", "title": title, "kind": "drift", "severity": sev, "doc": doc, "line": line,
                 "heading": "", "told": told, "happened": happened, "source": "script:help-drift", "status": "proven",
                 "check_key": key, "notes": [], "first_seen": p, "last_seen": p,
                 "repro": {"type": "help", "entry": m["entry"]}}
            db["next"] += 1
            db["findings"].append(f)
            added.append(f["id"])
        return added, fixed
    return mutate_findings(ws, fn)


# ---------------------------------------------------------------- links

def heading_text(t: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("`", "")
    t = re.sub(r"[*~]", "", t)
    t = re.sub(r"(^|[^\w])_+|_+(?=[^\w]|$)", r"\1", t)
    return re.sub(r"&[a-z]+;|&#\d+;", "", t, flags=re.I)


def slugger():
    seen: dict = {}

    def slug(text: str) -> str:
        base = "".join(ch for ch in heading_text(text).strip().lower()
                       if ch in "- " or unicodedata.category(ch)[0] in "LMN" or unicodedata.category(ch) == "Pc").replace(" ", "-")
        s = base
        while s in seen:
            seen[base] = seen.get(base, 0) + 1
            s = f"{base}-{seen[base]}"
        seen[s] = 0
        return s
    return slug


_md_cache: dict = {}


def md_links(path: Path):
    key = str(path)
    if key in _md_cache:
        return _md_cache[key]
    raw = split_lines(read_text(path))
    clean, fence = [], None
    for line in raw:
        m = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            if m and m[1][0] == fence[0] and len(m[1]) >= len(fence) and not line.strip()[len(m[1]):].strip():
                fence = None
            clean.append("")
            continue
        if m:
            fence = m[1]
            clean.append("")
            continue
        clean.append(re.sub(r"(`+)(?:(?!\1).)*?\1", " ", line))
    slug = slugger()
    anchors, refs = set(), []
    for i, line in enumerate(clean):
        atx = re.match(r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if atx:
            anchors.add(slug(re.sub(r"\s+#+\s*$", "", re.sub(r"^ {0,3}#{1,6}\s+", "", raw[i]))))
        elif re.match(r"^ {0,3}(=+|-+)\s*$", line) and i > 0 and clean[i - 1].strip() \
                and not re.match(r"^\s*([|<>]|[-*+]\s|\d+\.\s|#)", clean[i - 1]):
            anchors.add(slug(raw[i - 1].strip()))
        for h in re.finditer(r"<h[1-6][^>]*>(.*?)</h[1-6]>", line, re.I):
            anchors.add(slug(h[1]))
        for h in re.finditer(r"\b(?:id|name)\s*=\s*[\"']([^\"']+)[\"']", line, re.I):
            anchors.add(h[1].lower())
        rest = line
        for rx, kind in ((r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)", "image"),
                         (r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\)", "link"),
                         (r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?=\s|$).*$", "link")):
            for m in re.finditer(rx, rest):
                refs.append({"line": i + 1, "url": m[1], "kind": kind})
            rest = re.sub(rx, " ", rest)
        for tag in re.finditer(r"<(img|source)\b[^>]*>", rest, re.I):
            for s in re.finditer(r"\bsrc\s*=\s*[\"']([^\"']+)[\"']", tag[0], re.I):
                refs.append({"line": i + 1, "url": s[1], "kind": "image"})
            for s in re.finditer(r"\bsrcset\s*=\s*[\"']([^\"']+)[\"']", tag[0], re.I):
                for part in s[1].split(","):
                    u = part.strip().split(" ")[0]
                    if u:
                        refs.append({"line": i + 1, "url": u, "kind": "image"})
        rest = re.sub(r"<(img|source)\b[^>]*>", " ", rest, flags=re.I)
        for m in re.finditer(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>", rest, re.I):
            refs.append({"line": i + 1, "url": m[1], "kind": "link"})
        rest = re.sub(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>", " ", rest, flags=re.I)
        for m in re.finditer(r"<(https?://[^>\s]+)>", rest):
            refs.append({"line": i + 1, "url": m[1], "kind": "link"})
        rest = re.sub(r"<(https?://[^>\s]+)>", " ", rest)
        for m in re.finditer(r"(?<![\w(\"'=</\[])https?://[^\s<>()\"'`\]]+", rest):
            refs.append({"line": i + 1, "url": re.sub(r"[.,;:!?*_]+$", "", m[0]), "kind": "link"})
    _md_cache[key] = (anchors, refs)
    return anchors, refs


def exact_case(top: Path, rel: str) -> bool:
    cur = top
    for part in PurePosixPath(rel).parts:
        try:
            names = os.listdir(cur)
        except OSError:
            return False
        if part not in names:
            return False
        cur = cur / part
    return True


def head_url(url: str) -> int | None:
    import ssl
    import urllib.error
    import urllib.request
    ctx = ssl.create_default_context()
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": "Mozilla/5.0 (killer-docs link check)"})
        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (400, 403, 405, 429, 501):
                continue
            return e.code
        except (urllib.error.URLError, OSError, ValueError):
            if method == "HEAD":
                continue
            return None
    return None


def check_links(cfg: dict, ws: Path, network: bool) -> dict:
    top = Path(cfg["repo"])
    docs = [d for d in find_docs(cfg) if d[1] in ("md",)]
    not_parsed = [d[0] for d in find_docs(cfg) if d[1] in ("rst", "txt", "man")]
    files = set(kit.all_files(top, cfg["git"]))
    dirs = {str(PurePosixPath(f).parents[i]) for f in files for i in range(len(PurePosixPath(f).parts) - 1)}
    broken, remote, local_n = [], {}, 0
    _md_cache.clear()

    def check_anchor(target: Path, frag: str, doc: str, line: int, url: str):
        a = urllib.parse.unquote(frag).lower()
        if re.fullmatch(r"l\d+(-l\d+)?", a) or not a:
            return
        try:
            anchors, _ = md_links(target)
        except OSError:
            return
        if a not in anchors and a.replace("user-content-", "") not in anchors:
            broken.append({"doc": doc, "line": line, "url": url, "kind": "anchor",
                           "problem": f"#{frag} matches no heading in {fwd(os.path.relpath(target, top))}"})
    for doc, _ in docs:
        try:
            _, refs = md_links(top / doc)
        except OSError:
            continue
        for ref in refs:
            url = ref["url"]
            if re.match(r"^(mailto|tel|javascript|data|ftp):", url, re.I):
                continue
            if re.match(r"^https?://", url, re.I):
                remote.setdefault(url, []).append((doc, ref["line"], ref["kind"]))
                continue
            if re.match(r"^[a-z][\w+.-]*:", url, re.I):
                continue
            local_n += 1
            path, _, frag = url.partition("#")
            path = urllib.parse.unquote(path.split("?", 1)[0])
            if not path:
                check_anchor(top / doc, frag, doc, ref["line"], url)
                continue
            target = (top / path.lstrip("/")) if path.startswith("/") else (top / doc).parent / path
            rel = fwd(os.path.normpath(os.path.relpath(target, top)))
            if rel.startswith(".."):
                broken.append({"doc": doc, "line": ref["line"], "url": url, "kind": ref["kind"], "problem": "points outside the project"})
                continue
            rel = "" if rel == "." else rel.rstrip("/")
            is_dir = rel == "" or rel in dirs
            if rel in files or is_dir:
                if frag and rel.lower().endswith((".md", ".markdown")):
                    check_anchor(top / rel, frag, doc, ref["line"], url)
                continue
            if (top / rel).exists():
                if any(f.lower() == rel.lower() for f in files) or not exact_case(top, rel):
                    problem = "has the wrong letter case (GitHub and Linux are case-sensitive)"
                else:
                    problem = "exists here, but git ignores it, so nobody else has it"
            else:
                problem = "not found"
            broken.append({"doc": doc, "line": ref["line"], "url": url, "kind": ref["kind"], "problem": problem})
    checked_remote, unreachable = 0, 0
    remote = {u: v for u, v in remote.items() if not LOCAL_HOSTS.match(urllib.parse.urlsplit(u).hostname or "")
              and not re.search(r"(^|\.)example\.(com|org|net)$", urllib.parse.urlsplit(u).hostname or "")}
    if network and remote:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for url, status in zip(remote, ex.map(head_url, list(remote))):
                checked_remote += 1
                if status in (404, 410):
                    for doc, line, kind in remote[url]:
                        broken.append({"doc": doc, "line": line, "url": url, "kind": kind, "problem": f"answers {status}"})
                elif status is None:
                    unreachable += 1
    return {"local": local_n, "remote": len(remote), "remote_checked": checked_remote, "unreachable": unreachable,
            "broken": broken, "not_parsed": not_parsed}


def cmd_links(a) -> None:
    ws, cfg = load(a)
    sys.exit(links(cfg, ws, a.network or cfg["network"]))


def links(cfg: dict, ws: Path, network: bool, quiet: bool = False) -> int:
    res = check_links(cfg, ws, network)
    data = extract(cfg, ws)
    p = latest_pass(ws, "links") + 1
    res.update(pass_=p, at=kit.now(), head=git_head(cfg), network=network, problems=data["problems"])
    kit.save_json(ws / "passes" / f"links-{p}.json", res)
    now = {f"link:{b['doc']}:{b['url']}": b for b in res["broken"]}
    for pr in data["problems"]:
        now[f"fence:{pr['doc']}:{pr['line']}"] = {"doc": pr["doc"], "line": pr["line"], "url": "", "kind": "fence", "problem": pr["problem"]}

    def fn(db):
        added, fixed = [], []
        have = {}
        for f in db["findings"]:
            k = f.get("check_key", "")
            if k.startswith(("link:", "fence:")):
                have[k] = f
                if f["status"] in ("proven", "candidate") and k not in now:
                    f.update(status="fixed", fixed_by={"how": "rerun", "pass": p, "note": "the link check passes now"})
                    fixed.append(f["id"])
        for k, b in now.items():
            if k in have:
                have[k]["last_seen"] = p
                continue
            if b["kind"] == "fence":
                title, kind, sev = "a code block is never closed", "wrong", "medium"
                told, happened = "A code block opens here", b["problem"]
            else:
                title = f"{b['kind']} {short(b['url'], 50)} is broken"
                kind, sev = "broken-link", "low" if b["kind"] == "anchor" else "medium"
                told, happened = f"The doc links to {b['url']}", b["problem"]
            f = {"id": f"D{db['next']}", "title": title, "kind": kind, "severity": sev, "doc": b["doc"], "line": b["line"],
                 "heading": "", "told": told, "happened": happened, "source": "script:links", "status": "proven",
                 "check_key": k, "notes": [], "first_seen": p, "last_seen": p, "repro": {"type": "links"}}
            db["next"] += 1
            db["findings"].append(f)
            added.append(f["id"])
        return added, fixed
    added, fixed = mutate_findings(ws, fn)
    if not quiet:
        for b in res["broken"]:
            print(f"  {b['doc']}:{b['line']}  {b['kind']} {b['url']}: {b['problem']}")
        for pr in data["problems"]:
            print(f"  {pr['doc']}:{pr['line']}  {pr['problem']}")
        web = (f"{res['remote_checked']} web links checked ({res['unreachable']} didn't answer, not counted as broken)"
               if network else f"{res['remote']} web links not checked (network off)")
        print(f"Links: {res['local']} relative links and anchors checked, {len(res['broken'])} broken; {web}."
              + (f" Not parsed for links: {', '.join(res['not_parsed'][:6])}." if res["not_parsed"] else "")
              + (f" Findings: {', '.join(added)} added." if added else "") + (f" Fixed now: {', '.join(fixed)}." if fixed else ""))
    return 1 if (res["broken"] or data["problems"]) else 0


# ---------------------------------------------------------------- commands

def cmd_init(a) -> None:
    scope = a.scope or "all"
    kind = scope.split(":", 1)[0]
    if kind not in ("all", "start", "cli", "path", "changes", "branch"):
        fail(f"unknown scope {scope!r}: use {SCOPES}")
    kit_scope = "repo" if kind in ("all", "start", "cli") else scope
    timeout = a.timeout or DEFAULT_TIMEOUT
    ws, cfg, _ = kit.init_run(SKILL, a.repo, scope=kit_scope, slug=a.slug or kind, target=a.target, network=a.network,
                              include=a.include or [], exclude=a.exclude or [], env=a.env or [], workspace=a.workspace,
                              force=a.force, quiet=True,
                              extra={"doc_scope": kind, "scope_spec": kit_scope, "after": a.after, "depth": a.depth, "timeout": timeout,
                                     "max_blocks": a.max_blocks, "first": a.first or [],
                                     "user_branch": None, "fix_branch": None})
    if a.force:
        for f in ("findings.json", "blocks.json", "skips.json", "journeys.json", "walks.json", "decisions.json",
                  "waiting.json", "commits.json", "claims.json", "guard.json"):
            (ws / f).unlink(missing_ok=True)
        kit.rmtree(ws / "passes")
    top = Path(cfg["repo"])
    python = find_python(a.python)
    tools = find_tools()
    files = kit.all_files(top, cfg["git"])
    entries = find_entry_points(top, files, a.cli or [])
    cfg.update(python=python, tools=tools, entry_points=entries, user_branch=cfg.get("branch"))
    docs = find_docs(cfg)
    cfg["docs_init"] = docs
    kit.save_json(ws / "config.json", cfg)
    kit.save_json(ws / "findings.json", {"next": 1, "findings": []})
    print(f"Run folder: {ws}")
    head = cfg.get("head") or ""
    print(f"Project: {top}" + (f" ({cfg['branch'] or 'detached'} at {head[:10]})" if head else ""))
    words = {"all": "all the docs", "start": "the README and getting-started docs", "cli": "CLI help text only",
             "path": f"{cfg['scope'].get('path')}", "changes": "docs with uncommitted changes",
             "branch": f"docs changed since {cfg['scope'].get('ref')}"}[kind]
    counts: dict = {}
    for _, t in docs:
        counts[t] = counts.get(t, 0) + 1
    if docs:
        names = [d[0] for d in docs if d[1] != "example"]
        print(f"Docs ({words}): {plural(len(names), 'file')}: {', '.join(names[:8])}" + (f", and {len(names) - 8} more" if len(names) > 8 else "")
              + (f"; {plural(counts['example'], 'example script')}" if counts.get("example") else "")
              + (f"; {plural(counts['man'], 'man page')} (read for option names, not run)" if counts.get("man") else ""))
    else:
        print(f"warning: no docs found in scope ({words})")
    if entries:
        print("Commands the docs may describe: " + "; ".join(f"{e['name']} ({e['source']})" for e in entries))
    else:
        print("Commands: none found (package.json bin, pyproject scripts, setup.cfg, Cargo, cmd/*/main.go, bin/). "
              "Name one with --cli NAME=COMMAND.")
    mentioned = readme_commands(top, docs, entries)
    if mentioned:
        print(f"The README runs {', '.join(f'`{m}`' for m in mentioned)}, and no way to run it was found: "
              f"name it with help-drift --cli NAME=COMMAND to check its help.")
    py = python
    print(f"Python for the sandboxes: {py['path']} ({py['version']})" + (
        ", with a fresh virtual environment in each sandbox, so pip installs stay there" if py["kind"] == "full" else
        "; it can't make virtual environments, so pip installs in the docs can't be checked here"))
    shells = [x for x in (("sh", tools.get("bash") or tools.get("sh")), ("PowerShell", tools.get("powershell")),
                          ("cmd", tools.get("cmd")), ("Node.js", tools.get("node"))) if x[1]]
    print("Shells and runners: " + (", ".join(f"{n} ({p})" for n, p in shells) or "none found"))
    print(f"Network in sandboxes: {'allowed: install steps really install, from the real registries' if a.network else 'off: blocks that fetch are skipped and listed'}")
    print(f"Each block may run for {timeout} s; at most {a.max_blocks} blocks a run.")


def readme_commands(top: Path, docs, entries) -> list[str]:
    readme = next((d[0] for d in docs if PurePosixPath(d[0]).name.upper().startswith("README") and "/" not in d[0]), None)
    if not readme:
        return []
    names = {e["name"] for e in entries} | {top.name.lower()}
    try:
        blocks, _ = parse_markdown(read_text(top / readme))
    except OSError:
        return []
    out = []
    for raw in blocks:
        b = classify(raw, readme, "md")
        if b["kind"] != "shell":
            continue
        for words in segments(code_lines(b)):
            w = cmd_word(words[0])
            if w == top.name.lower() and w not in {e["name"].lower() for e in entries} and w not in out:
                out.append(w)
    return out


def cmd_extract(a) -> None:
    ws, cfg = load(a)
    data = extract(cfg, ws)
    blocks = data["blocks"]
    runnable = [b for b in blocks if b["kind"] in RUNNABLE]
    will = [b for b in runnable if not b["skip"]]
    print(f"{plural(len(blocks), 'code block')} in {plural(len({b['doc'] for b in blocks}), 'file')}: "
          f"{len(runnable)} runnable, {len(will)} will run here.")
    for b in blocks:
        if b["kind"] not in RUNNABLE and not a.all:
            continue
        what = block_label(b) if b["kind"] in RUNNABLE else b["kind"]
        if b["kind"] in RUNNABLE:
            state = "runs" if not b["skip"] else f"won't run: {b['skip']['reason']}"
            bits = []
            if b.get("commands"):
                bits.append(plural(len(b["commands"]), "command"))
                shown = sum(1 for c in b["commands"] if c["out"] is not None)
                if shown:
                    bits.append(f"output shown for {shown}")
            elif b.get("shown"):
                bits.append(f"output shown at line {b['shown_line']}")
            for k in ("setup", "background", "may_differ"):
                if b.get(k):
                    bits.append({"may_differ": "output may differ"}.get(k, k))
            if b.get("timeout"):
                bits.append(f"timeout {b['timeout']} s")
            state += f" ({', '.join(bits)})" if bits and not b["skip"] else ""
        else:
            state = f"not run ({b['lang'] or 'untagged'})" + (f", the output of line {b['output_of']}" if b.get("output_of") else "")
        head = short(" > ".join(b["heading"][-2:]), 26)
        print(f"  {b['id']:<5} {where(b):<28} {what:<18} {head:<26} {state}")
    reasons: dict = {}
    for b in runnable:
        if b["skip"]:
            reasons[b["skip"]["why"]] = reasons.get(b["skip"]["why"], 0) + 1
    other = len(blocks) - len(runnable)
    if reasons:
        print("Won't run here: " + ", ".join(f"{v} {SKIP_WORDS.get(k, k)}" for k, v in sorted(reasons.items())) + ".")
    if other:
        kinds: dict = {}
        for b in blocks:
            if b["kind"] not in RUNNABLE:
                kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
        print(f"Not code to run: " + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) + (" (extract --all lists them)" if not a.all else "") + ".")
    for p in data["problems"]:
        print(f"warning: {p['doc']}:{p['line']}: {p['problem']}")
    for n in data["notes"]:
        print(f"warning: {n}")


def cmd_skip(a) -> None:
    ws, cfg = load(a)
    data = kit.load_json(ws / "blocks.json", None) or extract(cfg, ws)
    by_id = {b["id"]: b for b in data["blocks"]}
    if not a.undo and not a.reason:
        fail("say why with --reason, like --reason \"runs install.ps1, which writes to the registry\"")
    with kit.locked(ws):
        skips = kit.load_json(ws / "skips.json", {})
        for bid in a.ids:
            b = by_id.get(bid)
            if not b or b["kind"] not in RUNNABLE:
                fail(f"no runnable block {bid}; run extract to see the ids")
            if a.undo:
                skips.pop(b["key"], None)
            else:
                skips[b["key"]] = {"reason": a.reason, "id": bid, "doc": b["doc"], "line": b["line"], "at": kit.now()}
        kit.save_json(ws / "skips.json", skips)
    extract(cfg, ws)
    print(("No longer skipping " if a.undo else "Skipping ") + ", ".join(f"{i} ({where(by_id[i])})" for i in a.ids)
          + ("" if a.undo else f": {a.reason}"))


def cmd_journey(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    if a.action == "list":
        js = kit.load_json(ws / "journeys.json", [])
        if not js:
            print("No journeys yet. Add one with journey add <name> --goal ... --docs ...")
        for j in js:
            print(f"{j['name']}: {j['goal']}\n  docs: {', '.join(j['docs'])}" + (f"\n  goal check: {' '.join(j['check'])}" if j.get("check") else ""))
        return
    if not a.name or not a.goal or not a.docs:
        fail("journey add needs a name, --goal and --docs")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", a.name):
        fail("a journey name is letters, digits, dots, dashes and underscores")
    docs = []
    for d in a.docs:
        d = d.replace("\\", "/")
        if not (top / d.split("#")[0]).exists():
            fail(f"--docs {d}: no such file in the project")
        docs.append(d)
    check = shlex.split(a.check) if a.check else None
    with kit.locked(ws):
        js = [j for j in kit.load_json(ws / "journeys.json", []) if j["name"] != a.name]
        js.append({"name": a.name, "goal": a.goal, "docs": docs, "start": a.start, "check": check})
        kit.save_json(ws / "journeys.json", js)
    print(f"Journey {a.name}: {a.goal} ({plural(len(docs), 'doc')})" + (f"; goal check `{a.check}`" if check else ""))


def get_journey(ws: Path, name: str) -> dict:
    for j in kit.load_json(ws / "journeys.json", []):
        if j["name"] == name:
            return j
    fail(f"no journey {name}; add it with journey add, or see journey list")


def cmd_sandbox(a) -> None:
    ws, cfg = load(a)
    py = a.python if a.python is not None else (project_uses_python(cfg) or needs_python(
        [b for b in (kit.load_json(ws / "blocks.json", None) or {"blocks": []})["blocks"] if b["kind"] in RUNNABLE]))
    box = make_box(cfg, ws, a.name, python=py, network=True if a.net else None)
    pyenv = kit.load_json(box / "pyenv.json", {"kind": "none"})
    print(f"Sandbox {a.name} is ready.")
    print(kit.sandbox_text(box, cfg["network"] or a.net))
    if pyenv["kind"] == "venv":
        print("  python:       `python` and `pip` are a private virtual environment for this sandbox; installs stay in it")
    elif pyenv["kind"] == "shim":
        print("  python:       `python` runs here, but `pip` can't install anything (no private environment)")
    print("  installs:     pip --user, npm -g, cargo install and go install land in the sandbox's home")


def cmd_guard(a) -> None:
    ws, cfg = load(a)
    if not cfg["git"]:
        print("The project isn't a git repository, so there's no git status to compare.")
        return
    now = kit.git_status(Path(cfg["repo"]))
    saved = kit.load_json(ws / "guard.json", None)
    if a.save or saved is None:
        kit.save_json(ws / "guard.json", {"status": now, "at": kit.now()})
        print("Saved the project's git status" + (": clean." if not now.strip() else f": {plural(len(now.splitlines()), 'change')}."))
        return
    if now == saved["status"]:
        print("The project's git status is unchanged.")
        return
    before, after = set(saved["status"].splitlines()), set(now.splitlines())
    print("The project's git status changed since it was saved:")
    for line in sorted(after - before):
        print(f"  new:  {line}")
    for line in sorted(before - after):
        print(f"  gone: {line}")
    print("If you didn't do it, an agent did: undo it, and say so in the report.")
    sys.exit(1)


def cmd_run_blocks(a) -> None:
    ws, cfg = load(a)
    if cfg["doc_scope"] == "cli":
        print("The scope is CLI help only, so no blocks run. Use help-drift.")
        return
    if a.first is not None:
        kit.update_cfg(ws, lambda c: c.__setitem__("first", a.first))
        cfg["first"] = a.first
    data = extract(cfg, ws)
    p, results = run_all(cfg, ws, data, a.doc, cfg.get("first", []), a.keep, a.timeout, jobs=a.jobs)
    added, fixed = block_findings(ws, cfg, data, results, p, a.doc)
    print(f"Pass {p}: " + tally_line(results))
    if added:
        print(f"Findings: {', '.join(added)} added.")
    if fixed:
        print(f"Fixed now: {', '.join(fixed)}.")
    sys.exit(1 if any(r["status"] in BAD and not r.get("setup") for r in results) else 0)


def cmd_add(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    doc = rel_to(a.doc, top, cfg) if a.doc else None

    def fn(db):
        f = {"id": f"D{db['next']}", "title": a.title, "kind": a.kind, "severity": a.severity, "doc": doc, "line": a.line,
             "heading": heading_at(top, doc, a.line), "told": a.told or "", "happened": a.happened or "",
             "source": a.source, "status": "candidate", "notes": [], "first_seen": None}
        if a.command:
            f["repro"] = {"type": "command", "command": shlex.split(a.command), "dir": None}
        db["next"] += 1
        db["findings"].append(f)
        return f
    f = mutate_findings(ws, fn)
    print(f"added {f['id']}: {f['title']}")


def heading_at(top: Path, doc: str | None, line) -> str:
    """The section a line sits under, like 'Install > From source'."""
    if not doc or not line:
        return ""
    try:
        text = read_text(top / doc)
    except OSError:
        return ""
    line = as_int(line) or 0
    if doc.lower().endswith(".rst"):
        _, heads = parse_rst(text)
    elif doc.lower().endswith(tuple(MD_EXT)):
        _, heads = parse_markdown(text)
    else:
        return ""
    stack: list = []
    for h in heads:
        if h["line"] > line:
            break
        stack = [s for s in stack if s[0] < h["level"]] + [(h["level"], h["title"])]
    return " > ".join(t for _, t in stack)


def reply_source(a, cfg) -> tuple[Path, Path | None]:
    if bool(getattr(a, "reply", None)) == bool(a.sandbox):
        fail("give the reply file, or --sandbox <name> to read its reply.json")
    if a.sandbox:
        box = kit.find_sandbox(cfg, a.sandbox)
        return box / "scratch" / kit.REPLY, box
    return Path(a.reply), None


def rerun_repro(cfg: dict, ws: Path, fid: str, cmd: list[str], scratch: Path | None, quiet: bool = False) -> tuple[str, dict]:
    res = fresh_run(cfg, ws, cmd, scratch, f"verify-{fid}")
    kinds = [kit.classify(r) for r in res["runs"]]
    verdict = "shows" if "shows" in kinds else ("inconclusive" if "inconclusive" in kinds else "behaves")
    said = {"shows": "showed the problem", "behaves": "did not show it", "inconclusive": "was inconclusive (no OBSERVED: line, or it timed out)"}[verdict]
    print(f"{fid}: its reproduction, rerun in a fresh copy, {said}.")
    if not quiet and verdict != "shows":
        print("    " + kit.summary(res).replace("\n", "\n    "))
    return verdict, res


def cmd_collect(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    path, box = reply_source(a, cfg)
    data = kit.read_reply(path)
    role = a.role or ("fact-checker" if ("claims" in data and "outcome" not in data) else "newcomer")
    journey, walk = None, None
    if role == "newcomer":
        jname = a.journey or data.get("journey")
        if not jname:
            fail("which journey was this? give --journey <name>")
        journey = get_journey(ws, str(jname))
        outcome = data.get("outcome")
        if outcome not in OUTCOMES:
            fail(f"the reply's outcome is {outcome!r}; it must be reached, reached-with-help or stuck. "
                 "Send the same brief to a fresh agent" + (f" (the sandbox {a.sandbox} is kept)" if a.sandbox else ""))
        walks = kit.load_json(ws / "walks.json", [])
        mine = [w for w in walks if w["journey"] == journey["name"]]
        walk = a.walk or (max([w["walk"] for w in mine] or [0]) + 1)
        if walk > 1 and not any(w["walk"] < walk for w in mine):
            fail(f"walk {walk} of {journey['name']} needs an earlier walk recorded first")
        if any(w["walk"] == walk for w in mine) and not a.replace:
            fail(f"walk {walk} of {journey['name']} is already recorded; pass --replace to overwrite it")
        if walk > 1 and a.sandbox and any(w.get("sandbox") == a.sandbox for w in mine if w["walk"] < walk):
            fail("a second walk must be a fresh newcomer in a fresh sandbox, not the first walk's")
    steps = [s for s in kit.as_list(data.get("steps")) if isinstance(s, dict)]
    found = [f for f in kit.as_list(data.get("findings")) if isinstance(f, dict)]
    scratch_rel = None
    if box:
        scratch_rel = f"scratch/{a.sandbox}"
        kit.keep_scratch(box, ws / scratch_rel)
    check = None
    changed: list[str] = []
    if box and role == "newcomer":
        if journey.get("check"):
            r = kit.run_in(box, journey["check"], 1, 120, log=False)["runs"][0]
            check = {"command": journey["check"], "ok": not kit.failed(r), "exit": r["code"], "out": tail(r["out"], 8)}
        st = kit.git(box / "repo", "status", "--porcelain=v1", check=False)
        changed = [ln[3:] for ln in st.splitlines() if ln[:2].strip() and ln[:2] != "??"]
    source = f"newcomer:{journey['name']}#{walk}" if role == "newcomer" else (a.source or "fact-checker")
    readable = set(journey["docs"]) if journey else set()
    read = [rel_to(x, top, cfg) for x in kit.as_list(data.get("read"))]
    outside = [x for x in read if journey and x.split("#")[0] not in {d.split("#")[0] for d in readable}]

    def add(db):
        ids = []
        for f in found:
            happened = str(f.get("happened") or f.get("observed") or "").strip()
            title = str(f.get("title") or happened[:80]).strip()
            if not (title or happened):
                continue
            doc = rel_to(f.get("doc"), top, cfg) if f.get("doc") else None
            line = as_int(f.get("line"))
            rec = {"id": f"D{db['next']}", "title": title, "kind": f.get("kind") if f.get("kind") in KINDS else "unclear",
                   "severity": f.get("severity") if f.get("severity") in SEVERITY else "medium", "doc": doc, "line": line,
                   "heading": heading_at(top, doc, line), "told": str(f.get("told") or f.get("expected") or ""),
                   "happened": happened, "guess": f.get("guess"), "workaround": f.get("workaround"), "source": source,
                   "status": "candidate", "notes": [], "walk": walk, "journey": journey["name"] if journey else None}
            cmd = kit.as_cmd(f.get("command"))
            if cmd:
                rec["repro"] = {"type": "command", "command": cmd, "dir": scratch_rel}
            db["next"] += 1
            db["findings"].append(rec)
            ids.append(rec["id"])
        return ids
    ids = mutate_findings(ws, add)
    tally_v = {"shows": 0, "behaves": 0, "inconclusive": 0}
    if not a.no_verify:
        for fid in ids:
            f = get_finding(load_findings(ws), fid)
            if not f.get("repro"):
                continue
            verdict, _ = rerun_repro(cfg, ws, fid, f["repro"]["command"], ws / scratch_rel if scratch_rel else None, quiet=True)
            tally_v[verdict] += 1

            def apply(db, fid=fid, verdict=verdict):
                x = get_finding(db, fid)
                x["verified"] = verdict
                if verdict == "shows":
                    x["status"] = "proven"
                else:
                    x["notes"].append("its reproduction " + ("didn't show the problem" if verdict == "behaves"
                                      else "failed without an OBSERVED: line") + " in a fresh copy")
            mutate_findings(ws, apply)
    if role == "newcomer":
        claimed = data["outcome"]
        effective = claimed
        if check and not check["ok"] and claimed != "stuck":
            effective = "stuck"
        stuck = data.get("stuck_at") if isinstance(data.get("stuck_at"), dict) else None
        if stuck and stuck.get("doc"):
            stuck = dict(stuck, doc=rel_to(stuck["doc"], top, cfg))
        rec = {"journey": journey["name"], "walk": walk, "claimed": claimed, "outcome": effective, "check": check,
               "stuck_at": stuck, "guesses": [str(g) for g in kit.as_list(data.get("guesses"))][:20],
               "steps": len(steps), "ok_steps": sum(1 for s in steps if s.get("ok") is True),
               "sandbox": a.sandbox, "source": "reply", "at": kit.now(), "head": git_head(cfg),
               "docs_hash": docs_hash(top, journey["docs"]), "changed_files": changed[:30], "read_outside": outside[:20],
               "findings": ids}
        transcript = [dict(s, doc=rel_to(s.get("doc"), top, cfg) if s.get("doc") else None) for s in steps]
        for s in transcript:
            s["heading"] = heading_at(top, s.get("doc"), s.get("line"))
        kit.save_json(ws / "walks" / f"{journey['name']}-{walk}.json", {"walk": rec, "steps": transcript})
        with kit.locked(ws):
            walks = [w for w in kit.load_json(ws / "walks.json", []) if not (w["journey"] == journey["name"] and w["walk"] == walk)]
            walks.append(rec)
            kit.save_json(ws / "walks.json", walks)
        fixed = fixed_by_walk(ws, journey["name"], walk, effective, transcript, ids) if walk > 1 else []
    else:
        claims = [c for c in kit.as_list(data.get("claims")) if isinstance(c, dict)]
        if claims:
            with kit.locked(ws):
                allc = kit.load_json(ws / "claims.json", [])
                allc += [dict(c, source=source, doc=rel_to(c.get("doc"), top, cfg) if c.get("doc") else None) for c in claims]
                kit.save_json(ws / "claims.json", allc)
        fixed = []
    if box and not a.keep:
        kit.drop_sandbox(ws, cfg, a.sandbox)
    span = f"{ids[0]}-{ids[-1]}" if len(ids) > 1 else (ids[0] if ids else "no findings")
    if role == "newcomer":
        line = f"{journey['name']}, walk {walk}: {rec['outcome']}"
        if check:
            line += f" (goal check {'passed' if check['ok'] else 'FAILED: ' + ' '.join(check['command'])})"
        if rec["outcome"] != claimed:
            line += f"; it said {claimed}"
        print(line + f", {plural(len(steps), 'step')} recorded, {plural(len(rec['guesses']), 'guess', 'guesses')}; added {span}.")
        if changed:
            print(f"  It changed project files in its copy: {', '.join(changed[:8])}. Check whether the docs told it to.")
        if outside:
            print(f"  It says it read files outside its list: {', '.join(outside[:8])}.")
        if fixed:
            print(f"  Fixed by this walk (it passed those steps cleanly): {', '.join(fixed)}.")
    else:
        print(f"{source}: added {span}" + (f"; {plural(len(data.get('claims') or []), 'claim')} checked" if data.get("claims") else "") + ".")
    tried = sum(tally_v.values())
    if tried:
        print(f"Reproductions rerun in fresh copies: {tally_v['shows']} proven, {tally_v['behaves']} didn't show, "
              f"{tally_v['inconclusive']} inconclusive.")


def docs_hash(top: Path, docs: list[str]) -> str:
    h = hashlib.sha1()
    for d in docs:
        try:
            h.update((top / d.split("#")[0]).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


def fixed_by_walk(ws: Path, journey: str, walk: int, outcome: str, steps: list[dict], new_ids: list[str]) -> list[str]:
    """Earlier walks' findings on this journey count as fixed when this newcomer passed those steps cleanly."""
    db = load_findings(ws)
    new = [f for f in db["findings"] if f["id"] in new_ids]

    def place(x):
        return (x.get("doc") or "", x.get("heading") or "")
    trouble = {place(s) for s in steps if s.get("ok") is not True or s.get("guess") or s.get("workaround")}
    trouble |= {place(f) for f in new}
    clean = {place(s) for s in steps if s.get("ok") is True and not s.get("guess") and not s.get("workaround")}

    def fn(db):
        out = []
        for f in db["findings"]:
            if f.get("journey") != journey or (f.get("walk") or 0) >= walk or f["status"] != "candidate":
                continue
            if f.get("repro") and f.get("verified") == "shows":
                continue
            p = place(f)
            ok = outcome == "reached" and (p not in trouble) and (not f.get("doc") or p in clean)
            if ok:
                f.update(status="fixed", fixed_by={"how": "walk", "walk": walk, "note": f"walk {walk}'s newcomer passed this step cleanly"})
                out.append(f["id"])
            elif f.get("doc") and p not in clean and p not in trouble:
                if not any("didn't reach this part" in n for n in f["notes"]):
                    f["notes"].append(f"walk {walk} didn't reach this part of the docs")
        return out
    return mutate_findings(ws, fn)


def cmd_walk_result(a) -> None:
    ws, cfg = load(a)
    journey = get_journey(ws, a.journey)
    rec = {"journey": journey["name"], "walk": a.walk, "claimed": a.outcome, "outcome": a.outcome, "check": None,
           "stuck_at": {"doc": a.stuck_at.rsplit(":", 1)[0], "line": as_int(a.stuck_at.rsplit(":", 1)[-1])} if a.stuck_at else None,
           "guesses": a.guess or [], "steps": None, "ok_steps": None, "sandbox": None, "source": "lead", "at": kit.now(),
           "head": git_head(cfg), "docs_hash": docs_hash(Path(cfg["repo"]), journey["docs"]), "changed_files": [],
           "read_outside": [], "findings": [], "note": a.note}
    with kit.locked(ws):
        walks = [w for w in kit.load_json(ws / "walks.json", []) if not (w["journey"] == journey["name"] and w["walk"] == a.walk)]
        walks.append(rec)
        kit.save_json(ws / "walks.json", walks)
    print(f"{journey['name']}, walk {a.walk}: {a.outcome} (recorded by hand)")


def cmd_update(a) -> None:
    ws, _ = load(a)
    if a.status in ("dismissed", "proven") and not a.note:
        fail(f"say why it's {a.status} with --note")

    def fn(db):
        f = get_finding(db, a.id)
        if a.status:
            if a.status not in STATUSES:
                fail(f"--status is one of {', '.join(STATUSES)}")
            f["status"] = a.status
            if a.status == "fixed":
                f["fixed_by"] = {"how": "lead", "note": a.note or "marked fixed by hand; no check confirmed it"}
            if a.status == "dismissed":
                f["dismissed"] = a.note
            if a.status == "proven":
                f["notes"].append(f"marked proven by the lead: {a.note}")
                a.note = None
        if a.kind:
            f["kind"] = a.kind
        if a.severity:
            f["severity"] = a.severity
        if a.title:
            f["title"] = a.title
        if a.note:
            f["notes"].append(a.note)
        return f
    f = mutate_findings(ws, fn)
    print(f"{f['id']}: {f['status']} ({f['severity']}, {f['kind']}). {f['title']}")


def cmd_list(a) -> None:
    ws, _ = load(a)
    fs = load_findings(ws)["findings"]
    if a.status:
        want = set(a.status.split(","))
        fs = [f for f in fs if f["status"] in want]
    if not fs:
        print("No findings" + (f" with status {a.status}" if a.status else "") + ".")
        return
    order = {"proven": 0, "candidate": 1, "fixed": 2, "dismissed": 3}
    for f in sorted(fs, key=lambda f: (order.get(f["status"], 9), SEVERITY.index(f["severity"]), int(f["id"][1:]))):
        loc = f"{f['doc']}:{f['line']}" if f.get("doc") and f.get("line") else (f.get("doc") or "")
        print(f"{f['id']:<5} {f['status']:<10} {f['severity']:<7} {f['kind']:<12} {short(loc, 26):<26} {short(f['title'], 70)}")


def cmd_show(a) -> None:
    ws, _ = load(a)
    if re.fullmatch(r"[bB]\d+", a.id):
        p = load_pass(ws, "blocks")
        r = next((r for r in (p or {}).get("results", []) if r["id"] == a.id.lower()), None)
        b = next((b for b in (kit.load_json(ws / "blocks.json", {}) or {}).get("blocks", []) if b["id"] == a.id.lower()), None)
        if not b:
            fail(f"no block {a.id}")
        print(json.dumps({"block": b, "latest_result": r}, indent=2, ensure_ascii=False))
        return
    print(json.dumps(get_finding(load_findings(ws), a.id), indent=2, ensure_ascii=False))


def cmd_repro(a) -> None:
    ws, cfg = load(a)
    f = get_finding(load_findings(ws), a.id)
    rp = f.get("repro") or {}
    if rp.get("type") == "blocks":
        data = extract(cfg, ws)
        blocks = [b for b in data["blocks"] if b["group"] == rp["doc"]]
        key = rp["upto"]
        if key not in {b["key"] for b in blocks}:
            pos = tuple((f.get("block") or {}).get("pos") or ())
            match = next((b for b in blocks if tuple(b.get("pos") or ()) == pos), None)
            if not match:
                print(f"{f['id']}: the block is no longer in {rp['doc']}.")
                return
            key = match["key"]
        first = [b for d in cfg.get("first", []) if d != rp["doc"] for b in data["blocks"] if b["group"] == d and b["kind"] in RUNNABLE and not b["skip"]]
        results = run_group(cfg, ws, rp["doc"], blocks, first, ws / "logs" / f"repro-{f['id']}", None, a.keep, upto=key)
        r = next(r for r in results if r["key"] == key)
        print(f"{f['id']}: ran {rp['doc']} up to {where(r)}: {describe(r)}")
        for line in (r.get("output") or "").split("\n")[-12:]:
            print(f"    {line}")
        sys.exit(1 if r["status"] in BAD else 0)
    if rp.get("type") == "command":
        verdict, res = rerun_repro(cfg, ws, f["id"], rp["command"], ws / rp["dir"] if rp.get("dir") else None)
        sys.exit(1 if verdict == "shows" else 0)
    if rp.get("type") == "help":
        print(f"{f['id']}: rerun help-drift to check it.")
        return
    if rp.get("type") == "links":
        print(f"{f['id']}: rerun links to check it.")
        return
    print(f"{f['id']} has no reproduction.")


def cmd_branch(a) -> None:
    ws, cfg = load(a)
    if not cfg["git"] or not kit.has_head(Path(cfg["repo"])):
        fail("fixing on a branch needs a git repository with at least one commit")
    top = Path(cfg["repo"])
    cur = kit.git(top, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip()
    exists = lambda b: kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{b}")
    if a.branch:
        name = a.branch
        if cur != name:
            kit.git(top, "switch", name) if exists(name) else kit.git(top, "switch", "-c", name)
    else:
        if cfg.get("fix_branch") and cur == cfg["fix_branch"]:
            name = cur
        else:
            base = f"killer-docs/{kit.today()}"
            name, n = base, 2
            while exists(name):
                name, n = f"{base}-{n}", n + 1
            kit.git(top, "switch", "-c", name)
    dirty = [ln[3:] for ln in kit.git_status(top).splitlines() if ln[3:] in {d[0] for d in find_docs(cfg)}]

    def setb(c):
        c["fix_branch"] = name
        c["branch"] = name
        c.setdefault("fix_base", kit.git(top, "rev-parse", "HEAD").strip())
        c["dirty_at_branch"] = dirty
    kit.update_cfg(ws, setb)
    print(f"On branch {name}" + (" (named for this run)" if a.branch else "") + ". Doc fixes are committed here; nothing is pushed.")
    if dirty:
        print(f"warning: these docs had uncommitted changes of yours before the run: {', '.join(dirty)}. "
              "commit won't take them, so your changes stay out of the fix commits.")


DOCISH = re.compile(r"\.(md|markdown|mdx|rst|txt|adoc|png|jpe?g|gif|svg|webp)$|^(docs?|man|examples?)/|(^|/)README", re.I)


def cmd_commit(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    if not cfg.get("fix_branch"):
        fail("run branch first, so the fixes land on their own branch")
    cur = kit.git(top, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip()
    if cur != cfg["fix_branch"]:
        fail(f"you're on {cur or 'a detached HEAD'}, not {cfg['fix_branch']}; switch back before committing")
    files = [f.replace("\\", "/") for f in (a.files[1:] if a.files[:1] == ["--"] else a.files)]
    if not files:
        fail("name the doc files to commit, after --")
    for f in files:
        if not (top / f).exists() and not kit.git_ok(top, "ls-files", "--error-unmatch", "--", f):
            fail(f"{f}: no such file")
        if not DOCISH.search(f) and not a.allow_other:
            fail(f"{f} isn't a doc. killer-docs fixes the docs, not the code; a wrong program is a finding for "
                 "killer-bugs (update --kind code). Pass --allow-other if this file really is documentation.")
        if f in (cfg.get("dirty_at_branch") or []):
            fail(f"{f} had your own uncommitted changes before the run; commit those yourself first")
    msg = a.message.strip()
    allowed = {w.lower() for w in a.allow_word or []}
    if ATTRIBUTION.search(msg) or any(m.lower() not in allowed for m in AI_NAMES.findall(msg)):
        fail("the message mentions an AI tool or an attribution trailer; the project's history must read as its owner's own work")
    kit.git(top, "add", "--", *files)
    diff = kit.git(top, "diff", "--cached", "--", *files)
    added = "\n".join(x[1:] for x in diff.splitlines() if x.startswith("+") and not x.startswith("+++"))
    if ATTRIBUTION.search(added):
        kit.git(top, "reset", "-q", "--", *files)
        fail("the change adds an attribution line (Co-Authored-By, 'Generated with', a robot); take it out")
    if not diff.strip():
        fail("nothing to commit in those files")
    kit.git(top, "commit", "-q", "-m", msg, "--", *files)
    sha_ = kit.git(top, "rev-parse", "--short", "HEAD").strip()
    subject = msg.splitlines()[0]
    append_json(ws, "commits.json", {"sha": sha_, "subject": subject, "files": files, "findings": a.findings or [], "at": kit.now()})
    if a.findings:
        def fn(db):
            for fid in a.findings:
                get_finding(db, fid).setdefault("commits", []).append(sha_)
        mutate_findings(ws, fn)
    print(f"Committed {sha_} {subject} on {cur} ({plural(len(files), 'file')}).")


def cmd_recheck(a) -> None:
    ws, cfg = load(a)
    if not latest_pass(ws, "blocks") and cfg["doc_scope"] != "cli":
        fail("nothing to compare with: run run-blocks first")
    bad = False
    if cfg["doc_scope"] != "cli":
        data = extract(cfg, ws)
        p, results = run_all(cfg, ws, data, None, cfg.get("first", []), a.keep, a.timeout, quiet=True, jobs=a.jobs)
        added, fixed = block_findings(ws, cfg, data, results, p)
        first = load_pass(ws, "blocks", 1)
        print(f"Blocks, pass {p}: " + tally_line(results))
        print(f"  First run (pass 1): " + tally_line(first["results"]))
        changes = compare_passes(first["results"], results, data)
        for line in changes:
            print("  " + line)
        if not changes:
            print("  No block changed result.")
        if fixed:
            print(f"  Fixed now: {', '.join(fixed)}.")
        if added:
            print(f"  New findings: {', '.join(added)}.")
        bad |= any(r["status"] in BAD and not r.get("setup") for r in results)
    bad |= bool(help_drift(cfg, ws, quiet=False))
    bad |= bool(links(cfg, ws, a.network or cfg["network"], quiet=False))
    db = load_findings(ws)
    for f in db["findings"]:
        rp = f.get("repro") or {}
        if f["status"] in ("proven", "candidate") and rp.get("type") == "command":
            verdict, _ = rerun_repro(cfg, ws, f["id"], rp["command"], ws / rp["dir"] if rp.get("dir") else None, quiet=True)

            def apply(db, fid=f["id"], verdict=verdict):
                x = get_finding(db, fid)
                if verdict == "behaves" and x.get("verified") == "shows":
                    x.update(status="fixed", fixed_by={"how": "rerun", "note": "its reproduction no longer shows the problem"})
                x["recheck"] = verdict
            mutate_findings(ws, apply)
            bad |= verdict == "shows"
    sys.exit(1 if bad else 0)


def compare_passes(old: list[dict], new: list[dict], data: dict) -> list[str]:
    by_key = {r["key"]: r for r in new}
    by_pos = {tuple(r["pos"]): r for r in new if r.get("pos")}
    used, out = set(), []
    state = lambda r: "not run" if r["status"] == "skipped" else r["status"]
    for r in old:
        n = by_key.get(r["key"]) or (by_pos.get(tuple(r["pos"])) if r.get("pos") else None)
        if n is None:
            out.append(f"{where(r):<28} {r['label']:<16} gone from the doc (was {state(r)})")
            continue
        used.add(n["key"])
        changed_code = n["key"] != r["key"]
        if state(n) != state(r) or changed_code:
            out.append(f"{where(n):<28} {n['label']:<16} {state(r)} -> {state(n)}" + (" (the block changed)" if changed_code else ""))
    for n in new:
        if n["key"] not in used and n["key"] not in {r["key"] for r in old}:
            out.append(f"{where(n):<28} {n['label']:<16} new block: {state(n)}")
    return out


def cmd_decide(a) -> None:
    ws, _ = load(a)
    append_json(ws, "decisions.json", {"text": a.text, "at": kit.now()})
    print(f"Recorded: {a.text}")


def cmd_wait(a) -> None:
    ws, _ = load(a)
    append_json(ws, "waiting.json", {"text": a.text, "command": a.command, "at": kit.now()})
    print(f"Waiting for the user's OK: {a.text}" + (f" ({a.command})" if a.command else ""))


def cmd_clean(a) -> None:
    ws, cfg = load(a)
    n = kit.clean_sandboxes(ws, cfg)
    print(f"Deleted {plural(n, 'sandbox', 'sandboxes')}.")


# ---------------------------------------------------------------- report

def walk_text(w: dict | None) -> str:
    if not w:
        return "not walked"
    s = w["outcome"]
    if w.get("check"):
        s += ", goal check " + ("passed" if w["check"]["ok"] else "failed")
    elif w["source"] == "lead":
        s += " (recorded by hand)"
    else:
        s += " (its own account; no goal check)"
    if w["outcome"] != w["claimed"]:
        s += f"; it said {w['claimed']}"
    if w.get("stuck_at") and w["outcome"] != "reached":
        st = w["stuck_at"]
        s += f"; stuck at {st.get('doc')}:{st.get('line')}" if st.get("doc") else ""
    if w.get("guesses"):
        s += f"; {plural(len(w['guesses']), 'guess', 'guesses')}"
    return s


def finding_block(f: dict) -> list[str]:
    loc = f"{f['doc']}:{f['line']}" if f.get("doc") and f.get("line") else (f.get("doc") or "no single place")
    head = f"#### {f['id']}. {f['title']} ({f['severity']}, {f['kind']})"
    out = [head, "", f"- **Where:** {loc}" + (f", under \"{f['heading']}\"" if f.get("heading") else "")]
    if f.get("told"):
        out.append("- **The reader is told:** " + f["told"].replace("\n", "\n  "))
    if f.get("happened"):
        out.append("- **What happens:** " + f["happened"].replace("\n", "\n  "))
    if f.get("guess"):
        out.append(f"- **The newcomer had to guess:** {f['guess']}")
    if f.get("workaround"):
        out.append(f"- **Its workaround:** {f['workaround']}")
    rp = f.get("repro") or {}
    if rp.get("type") == "blocks":
        out.append(f"- **Reproduce:** `docs.py repro -w <run> {f['id']}` runs {rp['doc']}'s blocks in a fresh copy, up to this one")
    elif rp.get("type") == "command":
        out.append(f"- **Reproduce:** `{' '.join(rp['command'])}` in a fresh copy" + (f", with the files in `{rp['dir']}/`" if rp.get("dir") else ""))
    out.append(f"- **Found by:** {f['source']}" + (f"; its reproduction {'showed it' if f.get('verified') == 'shows' else 'did not show it'} in a fresh copy" if f.get("verified") else ""))
    if f.get("fixed_by"):
        fb = f["fixed_by"]
        how = {"rerun": "checked: a rerun passes now", "walk": f"checked: walk {fb.get('walk')}'s newcomer passed this step",
               "removed": "the block was taken out of the doc", "lead": "by the lead's word; no check confirmed it"}[fb["how"]]
        out.append(f"- **Fixed:** {how}" + (f" ({fb['note']})" if fb.get("note") and fb["how"] == "rerun" else ""))
    if f.get("commits"):
        out.append(f"- **Commits:** {', '.join(f['commits'])}")
    if f.get("dismissed"):
        out.append(f"- **Dismissed:** {f['dismissed']}")
    for n in f.get("notes", []):
        out.append(f"- *{n}*")
    return out + [""]


def cmd_report(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    data = kit.load_json(ws / "blocks.json", None) or extract(cfg, ws)
    findings = load_findings(ws)["findings"]
    journeys = kit.load_json(ws / "journeys.json", [])
    walks = kit.load_json(ws / "walks.json", [])
    first, last = load_pass(ws, "blocks", 1), load_pass(ws, "blocks")
    help_first, help_last = load_pass(ws, "help", 1), load_pass(ws, "help")
    links_last = load_pass(ws, "links")
    decisions = kit.load_json(ws / "decisions.json", [])
    waiting = kit.load_json(ws / "waiting.json", [])
    commits = kit.load_json(ws / "commits.json", [])
    claims = kit.load_json(ws / "claims.json", [])
    by = lambda *s: [f for f in findings if f["status"] in s and f["kind"] != "code"]
    rank = lambda f: (SEVERITY.index(f["severity"]), int(f["id"][1:]))
    proven = sorted(by("proven"), key=rank)
    after_skip = sorted([f for f in by("candidate") if f.get("block")], key=rank)
    candidates = sorted([f for f in by("candidate") if not f.get("block")], key=rank)
    fixed, dismissed = sorted(by("fixed"), key=rank), sorted(by("dismissed"), key=rank)
    for_bugs = [f for f in findings if f["kind"] == "code"]
    docs = [d for d in data.get("docs", []) if d[1] != "example"]
    t_first = tally(first["results"]) if first else {}
    t_last = tally(last["results"]) if last else {}
    bad_first = sum(t_first.get(k, 0) for k in BAD)
    bad_last = sum(t_last.get(k, 0) for k in BAD)
    ran_last = sum(v for k, v in t_last.items() if not k.startswith("skipped") and k not in ("no-tool", "network", "blocked", "setup"))
    nc_last = sum(v for k, v in t_last.items() if k.startswith("skipped") or k in ("no-tool", "network", "blocked"))
    latest_walk = {}
    first_walk = {}
    for w in sorted(walks, key=lambda w: w["walk"]):
        latest_walk[w["journey"]] = w
        first_walk.setdefault(w["journey"], w)
    reached_first = sum(1 for j in journeys if (first_walk.get(j["name"]) or {}).get("outcome") == "reached")
    reached_last = sum(1 for j in journeys if (latest_walk.get(j["name"]) or {}).get("outcome") == "reached")
    open_high = [f for f in proven if f["severity"] == "high"]
    if not first and not walks and not help_last:
        status = "blocked"
    elif journeys and (any(j["name"] not in latest_walk for j in journeys) or reached_last < len(journeys)):
        status = "partial"
    elif cfg.get("after") == "fix" and proven:
        status = "partial"
    else:
        status = "done"
    parts = []
    if last:
        parts.append(f"{ran_last} of {ran_last + nc_last} blocks ran, {bad_last} failing or differing"
                     + (f" (from {bad_first} at first)" if first and last["pass"] != first["pass"] else ""))
    if journeys:
        parts.append(f"newcomers reached {reached_last} of {len(journeys)} journeys"
                     + (f" (first walk: {reached_first})" if any(w["walk"] > 1 for w in walks) else ""))
    parts.append(f"{len(proven)} proven findings open, {len(fixed)} fixed")
    headline = f"{plural(len(docs), 'doc')}: " + "; ".join(parts) + "."
    L = [f"# Docs report: {cfg['target'] or top.name}", "",
         f"**Project:** `{top}`" + (f" at `{(git_head(cfg) or '')[:10]}`" if cfg["git"] else "")
         + (f" on `{cfg['fix_branch']}`" if cfg.get("fix_branch") else ""),
         f"**Docs checked:** " + (", ".join(f"`{d[0]}`" for d in docs[:12]) + (f" and {len(docs) - 12} more" if len(docs) > 12 else "") if docs else "none"),
         f"**Network in sandboxes:** {'on' if cfg['network'] else 'off'} · **Python in sandboxes:** {cfg['python']['path']} ({cfg['python']['version']})",
         "", f"**Result:** {headline}", ""]
    L += ["## Journeys", ""]
    if journeys:
        nmax = max([w["walk"] for w in walks] or [1])
        L += ["| Journey | Goal | First walk |" + (" Latest walk |" if nmax > 1 else ""), "|---|---|---|" + ("---|" if nmax > 1 else "")]
        for j in journeys:
            fw, lw = first_walk.get(j["name"]), latest_walk.get(j["name"])
            row = f"| {j['name']} | {j['goal']} | {walk_text(fw)} |"
            if nmax > 1:
                row += f" {walk_text(lw) if lw and fw and lw['walk'] != fw['walk'] else 'no second walk'}" + (f" (walk {lw['walk']})" if lw and fw and lw['walk'] != fw['walk'] else "") + " |"
            L.append(row.replace("\n", " "))
        L.append("")
        for w in sorted(walks, key=lambda w: (w["journey"], w["walk"])):
            if w.get("guesses"):
                L.append(f"- {w['journey']}, walk {w['walk']}, had to guess: " + "; ".join(w["guesses"][:6]))
            if w.get("changed_files"):
                L.append(f"- {w['journey']}, walk {w['walk']}, changed project files in its copy: {', '.join(w['changed_files'][:8])}")
            if w.get("read_outside"):
                L.append(f"- {w['journey']}, walk {w['walk']}, says it read files outside its list: {', '.join(w['read_outside'][:8])}")
        L.append("")
    else:
        L += ["No newcomer walked the docs in this run.", ""]
    L += ["## Blocks", ""]
    if first:
        L.append(f"**First run (pass {first['pass']}):** " + tally_line(first["results"]))
        if last and last["pass"] != first["pass"]:
            L.append("")
            L.append(f"**Latest run (pass {last['pass']}):** " + tally_line(last["results"]))
            changes = compare_passes(first["results"], last["results"], data)
            if changes:
                L += ["", "Block by block, first run to latest:", ""] + [f"- `{c.split()[0]}` " + " ".join(c.split()[1:]) for c in changes]
        L.append("")
        bad = [r for r in last["results"] if r["status"] in BAD and not r.get("setup")]
        if bad:
            L += ["Still failing or differing in the latest run:", ""]
            L += [f"- `{where(r)}` ({r['label']}): {describe(r)}" for r in bad] + [""]
    else:
        L += ["No blocks were run.", ""]
    L += ["## Findings", ""]
    L.append(f"{len(proven)} proven and open, " + (f"{len(after_skip)} failed after a step that didn't run, " if after_skip else "")
             + f"{len(candidates)} reported but not reproduced, {len(fixed)} fixed, "
             f"{len(dismissed)} dismissed, {len(for_bugs)} left for killer-bugs.")
    L.append("")
    if proven:
        L += ["### Proven, open", ""] + sum((finding_block(f) for f in proven), [])
    if after_skip:
        L += ["### Failed after a step that didn't run", "",
              "These blocks failed in a fresh copy, but a block before them didn't run here, which may be why.", ""]
        L += sum((finding_block(f) for f in after_skip), [])
    if candidates:
        L += ["### Reported, not reproduced", "",
              "A newcomer or a check reported these, but nothing reran them to show it. Judge them; they aren't counted as proven.", ""]
        L += sum((finding_block(f) for f in candidates), [])
    if fixed:
        L += ["### Fixed", ""] + sum((finding_block(f) for f in fixed), [])
    if for_bugs:
        L += ["### For killer-bugs: the docs are right, the code is wrong", "",
              "Nothing was changed for these. Run killer-bugs on them.", ""] + sum((finding_block(f) for f in for_bugs), [])
    if dismissed:
        L += ["### Dismissed", ""] + [f"- **{f['id']}. {f['title']}**: {f.get('dismissed') or ''}" for f in dismissed] + [""]
    L += ["## Help text", ""]
    if help_last and help_last.get("entries"):
        for e in help_last["entries"]:
            L.append(f"- `{e['name']}` ({e['source']}): " + (f"`{e['flag']}` lists {plural(len(e['groups']), 'option')}"
                     + (f" and {plural(len(e['subs']), 'subcommand')}" if e["subs"] else "") if e["ok"]
                     else f"couldn't read its help (exit {e['exit']}), so it wasn't compared"))
        L.append("")
        L.append(f"Latest check: {len(help_last['missing'])} documented but missing, {len(help_last['undocumented'])} not documented"
                 + (f" (first check: {len(help_first['missing'])} and {len(help_first['undocumented'])})" if help_first and help_first["pass"] != help_last["pass"] else "") + ".")
        L.append("")
    else:
        L += ["No command's help was checked" + (" (none found)" if help_last else "") + ".", ""]
    L += ["## Links", ""]
    if links_last:
        L.append(f"{links_last['local']} relative links and anchors checked, {len(links_last['broken'])} broken in the latest check. "
                 + (f"{links_last['remote_checked']} web links checked ({links_last['unreachable']} didn't answer)." if links_last["network"]
                    else f"{plural(links_last['remote'], 'web link')} not checked: the network was off." if links_last["remote"]
                    else "No web links."))
        L += [f"- `{b['doc']}:{b['line']}` {b['url']}: {b['problem']}" for b in links_last["broken"]]
        L.append("")
    else:
        L += ["Links weren't checked.", ""]
    L += ["## What wasn't checked", ""]
    skipped = [r for r in (last or {}).get("results", []) if r["status"] in NOT_CHECKED]
    if skipped:
        L.append(f"Blocks that didn't run, or ran but couldn't be judged ({len(skipped)}):")
        L.append("")
        for r in skipped:
            why = r["skip"]["reason"] if r["status"] == "skipped" else f"{r['status']}: " + (next((n for n in r["notes"] if n), "") or "")
            L.append(f"- `{where(r)}` ({r['label']}): {why}")
        L.append("")
    out_scope = [d for d in all_doc_paths(cfg) if d[0] not in {x[0] for x in data.get("docs", [])}]
    if out_scope:
        L.append(f"Docs out of scope: {', '.join(f'`{d[0]}`' for d in out_scope[:20])}" + (f" and {len(out_scope) - 20} more" if len(out_scope) > 20 else "") + ".")
    if links_last and links_last.get("not_parsed"):
        L.append(f"Links in these files aren't checked (not Markdown): {', '.join(f'`{x}`' for x in links_last['not_parsed'][:12])}.")
    mans = [d[0] for d in data.get("docs", []) if d[1] == "man"]
    if mans:
        L.append(f"Man pages are read for option names only; their examples aren't run: {', '.join(mans[:8])}.")
    if journeys:
        L.append("Newcomers are told not to read the code; nothing enforces that, so their blindness is by instruction. "
                 "A goal check, where one was set, is the only part of a walk's outcome the script confirmed.")
    if claims:
        wrong = sum(1 for c in claims if str(c.get("verdict")).lower() == "false")
        L.append(f"A fact checker checked {plural(len(claims), 'claim')} the blocks don't exercise against the code; "
                 f"{wrong} {'was' if wrong == 1 else 'were'} wrong. Claims it didn't list weren't checked.")
    else:
        L.append("No fact checker read the docs against the code" + (" (standard depth)" if cfg.get("depth") != "thorough" else "")
                 + ", so claims no block exercises (defaults, limits, file locations, environment variables) weren't checked.")
    L.append("")
    if decisions:
        L += ["## Decisions to check", ""] + [f"- {d['text']}" for d in decisions] + [""]
    if waiting:
        L += ["## Waiting for your OK", ""] + [f"- {w['text']}" + (f": `{w['command']}`" if w.get("command") else "") for w in waiting] + [""]
    if commits:
        L += [f"## Commits on `{cfg.get('fix_branch')}`", "", "Nothing was pushed.", ""] + [f"- `{c['sha']}` {c['subject']}" for c in commits] + [""]
    L += ["## Run details", "", f"Run folder: `{ws}`", f"Block logs: `{ws / 'logs'}`", ""]
    (ws / "report.md").write_text("\n".join(L), encoding="utf-8")
    numbers = {
        "docs": len(docs), "blocks_found": len([b for b in data.get("blocks", []) if b["kind"] in RUNNABLE]),
        "blocks_ran_first": sum(v for k, v in t_first.items() if not k.startswith("skipped") and k not in ("no-tool", "network", "blocked", "setup")),
        "blocks_bad_first": bad_first, "blocks_bad_latest": bad_last, "blocks_ran_latest": ran_last, "blocks_passed_latest": t_last.get("passed", 0),
        "blocks_differ_latest": t_last.get("differs", 0), "blocks_failed_latest": t_last.get("failed", 0),
        "blocks_timed_out_latest": t_last.get("timed-out", 0), "blocks_not_checked_latest": nc_last,
        "findings_proven_open": len(proven), "findings_after_skip_open": len(after_skip), "findings_reported_open": len(candidates), "findings_fixed": len(fixed),
        "findings_dismissed": len(dismissed), "findings_for_killer_bugs": len(for_bugs), "findings_high_open": len(open_high),
        "help_missing": len((help_last or {}).get("missing", [])), "help_undocumented": len((help_last or {}).get("undocumented", [])),
        "links_broken": len((links_last or {}).get("broken", [])), "journeys": len(journeys),
        "journeys_reached_first_walk": reached_first, "journeys_reached_latest_walk": reached_last,
    }
    summary = {"skill": "killer-docs", "status": status, "headline": headline, "report": str(ws / "report.md"),
               "branch": cfg.get("fix_branch"), "commits": [f"{c['sha']} {c['subject']}" for c in commits],
               "decisions": [d["text"] for d in decisions],
               "waiting": [w["text"] + (f": {w['command']}" if w.get("command") else "") for w in waiting], "numbers": numbers}
    kit.save_json(ws / "summary.json", summary)
    print(f"Wrote {ws / 'report.md'} and summary.json.")
    print(f"killer-docs: {status} · {headline} · {ws / 'report.md'}")


# ---------------------------------------------------------------- CLI

def main(argv=None) -> None:
    kit.utf8_stdout()
    p = argparse.ArgumentParser(prog="docs.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name, func, help_, ws=True):
        sp = sub.add_parser(name, help=help_, description=help_)
        if ws:
            sp.add_argument("--workspace", "-w", required=True, help="the run folder")
        sp.set_defaults(func=func)
        return sp

    s = cmd("init", cmd_init, "Create the run folder; list the docs and the commands they describe.", ws=False)
    s.add_argument("--repo", default=".", help="the project folder (default: here)")
    s.add_argument("--scope", default="all", help=f"which docs: {SCOPES} (default all)")
    s.add_argument("--target", default="", help="one line on what the project is")
    s.add_argument("--slug", help="short name for the run folder")
    s.add_argument("--network", action="store_true", help="let sandboxes use the network (install steps really install)")
    s.add_argument("--after", choices=["fix", "ask", "report"], default="fix", help="what happens once it's checked (default fix)")
    s.add_argument("--depth", choices=["standard", "thorough"], default="standard", help="thorough adds a fact checker")
    s.add_argument("--timeout", type=float, help=f"seconds each block may run (default {DEFAULT_TIMEOUT})")
    s.add_argument("--max-blocks", type=int, default=DEFAULT_CAP, help=f"at most this many blocks a run (default {DEFAULT_CAP})")
    s.add_argument("--first", nargs="*", help="docs whose blocks run first in every other doc's sandbox, like README.md")
    s.add_argument("--python", help="the Python the sandboxes use (default: the first that can make virtual environments)")
    s.add_argument("--cli", nargs="*", help="NAME=COMMAND for a command the docs describe that wasn't found")
    s.add_argument("--include", nargs="*", help="ignored paths every sandbox needs, like node_modules")
    s.add_argument("--exclude", nargs="*", help="leave these out, like vendor/ CHANGELOG.md")
    s.add_argument("--env", nargs="*", help="NAME=VALUE set in every sandbox")
    s.add_argument("--workspace", "-w", help="use this run folder instead of the default")
    s.add_argument("--force", action="store_true", help="start over in an existing run folder")

    s = cmd("extract", cmd_extract, "Pull the code blocks out of the docs and say which will run here.")
    s.add_argument("--all", action="store_true", help="list output, config and other blocks too")

    s = cmd("skip", cmd_skip, "Leave blocks out of every run (from the side-effect check), with the reason.")
    s.add_argument("ids", nargs="+", help="block ids from extract, like b7")
    s.add_argument("--reason", help="why, in a few words")
    s.add_argument("--undo", action="store_true", help="run them again")

    s = cmd("journey", cmd_journey, "Add or list the journeys a newcomer walks.")
    s.add_argument("action", choices=["add", "list"])
    s.add_argument("name", nargs="?", help="a short name, like install")
    s.add_argument("--goal", help="what the newcomer must reach, in a sentence")
    s.add_argument("--docs", nargs="*", help="the doc files it may read")
    s.add_argument("--start", help="where it starts, like README.md#install")
    s.add_argument("--check", help="a command that passes once the goal is reached, run in the newcomer's own sandbox")

    s = cmd("sandbox", cmd_sandbox, "Make an isolated copy of the project for one agent.")
    s.add_argument("name", help="a name for it, like walk-install-1")
    s.add_argument("--net", action="store_true", help="allow the network in this sandbox")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--python", dest="python", action="store_true", default=None, help="give it a private Python environment")
    g.add_argument("--no-python", dest="python", action="store_false", help="no private Python environment")

    s = cmd("guard", cmd_guard, "Save the project's git status, or compare it with the saved one (exit 1 if it changed).")
    s.add_argument("--save", action="store_true", help="save the current status (the first call saves too)")

    s = cmd("run-blocks", cmd_run_blocks, "Run each doc's blocks in order, in its own fresh sandbox.")
    s.add_argument("--doc", nargs="*", help="only these docs")
    s.add_argument("--first", nargs="*", help="docs whose blocks run first in every other doc's sandbox (saved for recheck)")
    s.add_argument("--timeout", type=float, help="seconds each block may run (default: the run's)")
    s.add_argument("--keep", action="store_true", help="keep the sandboxes afterwards, to look inside")
    s.add_argument("--jobs", type=int, default=3, help="docs run side by side, this many at once (default 3); "
                   "a doc that starts a server always runs on its own")

    s = cmd("help-drift", cmd_help_drift, "Compare each command's --help with what the docs say about it.")
    s.add_argument("--cli", nargs="*", help="NAME=COMMAND for a command to check (saved for the run)")

    s = cmd("links", cmd_links, "Check relative links, anchors and image paths.")
    s.add_argument("--network", action="store_true", help="check web links too (only 404 and 410 count as broken)")

    s = cmd("add", cmd_add, "Add a finding by hand.")
    s.add_argument("--title", required=True)
    s.add_argument("--kind", choices=KINDS, default="unclear")
    s.add_argument("--severity", choices=SEVERITY, default="medium")
    s.add_argument("--doc")
    s.add_argument("--line", type=int)
    s.add_argument("--told", help="what the reader is told")
    s.add_argument("--happened", help="what happens")
    s.add_argument("--command", help="a reproduction, run from the project copy")
    s.add_argument("--source", default="lead", help="user or lead")

    s = cmd("collect", cmd_collect, "Read a newcomer's or fact checker's reply; rerun its reproductions in fresh copies.")
    s.add_argument("reply", nargs="?", help="a reply saved to a file (or use --sandbox)")
    s.add_argument("--sandbox", help="read reply.json from this sandbox's scratch folder, then delete the sandbox")
    s.add_argument("--journey", help="the journey it walked")
    s.add_argument("--walk", type=int, help="1 for the first walk, 2 for the walk after fixes (default: the next one)")
    s.add_argument("--as", dest="role", choices=["newcomer", "fact-checker"], help="who wrote it (default: from the reply)")
    s.add_argument("--source", help="a name for a fact checker, like fact-checker#1")
    s.add_argument("--replace", action="store_true", help="overwrite a walk already recorded")
    s.add_argument("--no-verify", action="store_true", help="don't rerun its reproductions")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")

    s = cmd("walk-result", cmd_walk_result, "Record or correct how a journey went, by hand.")
    s.add_argument("--journey", required=True)
    s.add_argument("--walk", type=int, required=True)
    s.add_argument("--outcome", choices=OUTCOMES, required=True)
    s.add_argument("--stuck-at", help="doc:line where it got stuck")
    s.add_argument("--guess", nargs="*", help="what it had to guess")
    s.add_argument("--note")

    s = cmd("update", cmd_update, "Change a finding.")
    s.add_argument("id")
    s.add_argument("--status", choices=STATUSES)
    s.add_argument("--kind", choices=KINDS, help="code: the docs are right and the code is wrong (for killer-bugs)")
    s.add_argument("--severity", choices=SEVERITY)
    s.add_argument("--title")
    s.add_argument("--note", help="a note; the reason, for dismissed or proven")

    s = cmd("list", cmd_list, "List the findings.")
    s.add_argument("--status", help="only these statuses, comma-separated")

    s = cmd("show", cmd_show, "Show a finding (D3) or a block and its latest result (b7).")
    s.add_argument("id")

    s = cmd("repro", cmd_repro, "Rerun a finding's reproduction in a fresh sandbox.")
    s.add_argument("id")
    s.add_argument("--keep", action="store_true")

    s = cmd("branch", cmd_branch, "Make (or switch to) the local branch the doc fixes go on.")
    s.add_argument("--branch", help="use this branch (killer-goal names one); made if it doesn't exist")

    s = cmd("commit", cmd_commit, "Commit doc fixes on the run's branch.")
    s.add_argument("--message", "-m", required=True, help="what changed and why, in plain words")
    s.add_argument("--findings", nargs="*", help="the findings this fixes, like D1 D3")
    s.add_argument("--allow-other", action="store_true", help="the files include something that isn't a doc")
    s.add_argument("--allow-word", nargs="*", help="a tool name the message may mention because the project is about it")
    s.add_argument("files", nargs=argparse.REMAINDER, help="-- then the files")

    s = cmd("recheck", cmd_recheck, "After fixes: blocks, help, links and reproductions again, compared with the first run.")
    s.add_argument("--timeout", type=float)
    s.add_argument("--jobs", type=int, default=3, help="docs run side by side, this many at once (default 3)")
    s.add_argument("--network", action="store_true", help="check web links too")
    s.add_argument("--keep", action="store_true")

    s = cmd("decide", cmd_decide, "Record a call the user should check.")
    s.add_argument("text")

    s = cmd("wait", cmd_wait, "Record an outward-facing action that waits for the user's OK.")
    s.add_argument("text")
    s.add_argument("--command", help="the exact command, to run once they say yes")

    cmd("report", cmd_report, "Write report.md and summary.json.")
    cmd("clean", cmd_clean, "Delete this run's sandboxes.")

    a = p.parse_args(argv)
    try:
        a.func(a)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception:   # a crash is a setup error (2), never a failed check (1)
        import traceback
        traceback.print_exc()
        print(f"error: docs.py {a.cmd} stopped unexpectedly (above). Nothing it was doing counts as checked.", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
