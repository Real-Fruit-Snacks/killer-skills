#!/usr/bin/env python3
"""Bookkeeping for a killer-release run: get a release ready, and prove the built release installs.

Commands:
  init             read the project's release shape: version sites, last release, build and publish
  versions         every version site, and whether they agree
  changes          what changed since the last release: commits, files, the user-visible surface
  bump             the next version and the edits it takes; --apply commits them on a new local branch
  changelog-check  map the changelog's entries to the changes and back, for the lead and the reviewer
  commit           commit on the release branch, refusing a message with AI attribution
  sandbox          a sandbox for an agent: the project at the release branch, or --empty with the artifacts
  collect          read a release reviewer's or first-time installer's reply.json
  update           mark a finding fixed, or kept as it is
  build            build in a fresh sandbox from the release branch, and collect the artifacts
  smoke            install the artifacts in an empty sandbox with the network off, and run them
  notes            draft release notes from the changelog section
  check            the final gate, every item run by this script
  guard            what changed in the real project that this run didn't do (tags, pushes, your branch)
  publish-plan     write the exact commands that would publish to publish.md; runs nothing
  publish          run one step of that plan, after the user's OK in this session
  decide           record a call the user should check
  wait             record an outward action that's prepared but not done
  report           write report.md and summary.json
  clean            delete this run's sandboxes

Runs live in ~/.claude/killer-release/<project>/<date>-<slug>/ and sandboxes in the temp
folder; KILLER_RELEASE_HOME and KILLER_RELEASE_SANDBOXES move them. Nothing here tags,
pushes or uploads except `publish --step N --yes`. Exit 0 means fine, 1 means a check
failed, 2 means bad input or setup. Standard library only. Run `release.py <command> --help`
for options.
"""
from __future__ import annotations

import argparse
import difflib
import fnmatch
import functools
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = "release"


def _load_kit():
    spec = importlib.util.spec_from_file_location("killer_kit", HERE / "kit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kit = _load_kit()
fail = kit.fail
REPLY = kit.REPLY

SKIP_PARTS = {"node_modules", "vendor", "third_party", "third-party", ".venv", "venv", "dist", "build", "target",
              "tests", "test", "testing", "testdata", "fixtures", "__pycache__", "site-packages", ".tox", "examples"}
CHANGELOG_NAME = re.compile(r"^(change[s_-]?log|changes|history|news|releases?)(\.(md|markdown|txt|rst))?$", re.I)
ARCHIVES = (".whl", ".tar.gz", ".tgz", ".zip", ".tar.xz", ".tar.bz2", ".tar.zst", ".gem", ".nupkg", ".snupkg",
            ".crate", ".jar", ".war", ".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".apk", ".appimage", ".vsix",
            ".xpi", ".7z")
UNPACKABLE = (".zip", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar")
INTERPRETERS = {"python", "python3", "py", "pythonw", "node", "sh", "bash", "zsh", "dash", "pwsh", "powershell",
                "cmd", "java", "ruby", "perl", "deno", "bun", "php", "dotnet", "env"}
RECIPES = ("pip", "npm", "archive", "script", "binary")
VERDICTS = ("supported", "unsupported", "overstated", "unclear")
OUTCOMES = ("installed", "installed-with-help", "stuck")
BLOCKING = {"unsupported", "overstated", "missing", "stuck"}
RUN_COMMIT = re.compile(r"^Release \S+:")


# ---------------------------------------------------------------- versions

VER_IN_TEXT = (r"\d+\.\d+\.\d+(?:-(?:rc|alpha|beta|a|b|pre|preview|dev|next|canary)(?:[.-]?\d+)*"
               r"|\.?(?:a|b|c|rc|alpha|beta|pre|preview)\d*|\.dev\d*|\.post\d+)?")
VER_GROUP = rf"(?P<v>{VER_IN_TEXT})(?![0-9A-Za-z])"
_VER = re.compile(
    r"(?P<M>\d+)\.(?P<m>\d+)\.(?P<p>\d+)"
    r"(?:-(?P<spre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)"
    r"|\.?(?P<plab>a|b|c|rc|alpha|beta|pre|preview)(?P<pnum>\d*)"
    r"|\.dev(?P<dev>\d*)"
    r"|\.post(?P<post>\d+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?")
_RANK = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3, "pre": 3, "preview": 3}
_PEP = {"a": "a", "alpha": "a", "b": "b", "beta": "b", "c": "rc", "rc": "rc", "pre": "rc", "preview": "rc", "dev": "dev"}


@functools.total_ordering
class Ver:
    """A version: semver (1.2.0-rc.1) or PEP 440 (1.2.0rc1), compared the way both order them."""

    def __init__(self, M, m, p, pre=None, sep=".", post=None, build=None, style="semver"):
        self.M, self.m, self.p, self.pre, self.sep = M, m, p, pre, sep
        self.post, self.build, self.style = post, build, style

    @classmethod
    def parse(cls, text):
        if text is None:
            return None
        t = str(text).strip()
        if t[:1] in ("v", "V"):
            t = t[1:]
        mt = _VER.fullmatch(t)
        if not mt:
            return None
        g = mt.groupdict()
        pre, sep, style = None, ".", "semver"
        if g["spre"]:
            sm = re.fullmatch(r"(alpha|beta|rc|a|b|c|pre|preview|dev|next|canary)([.-]?)(\d*)", g["spre"], re.I)
            if sm:
                pre = (sm.group(1).lower(), int(sm.group(3)) if sm.group(3) else None)
                sep = sm.group(2) if sm.group(3) else "."
            else:
                pre = ("~" + g["spre"], None)
        elif g["plab"]:
            pre, style = (g["plab"].lower(), int(g["pnum"]) if g["pnum"] else 0), "pep440"
        elif g["dev"] is not None:
            pre, style = ("dev", int(g["dev"]) if g["dev"] else 0), "pep440"
        post = int(g["post"]) if g["post"] else None
        if post is not None:
            style = "pep440"
        return cls(int(g["M"]), int(g["m"]), int(g["p"]), pre, sep, post, g["build"], style)

    def key(self):
        if self.pre is None:
            pk = (1, 0, 0, "")
        else:
            lab, num = self.pre
            pk = (0, _RANK.get(lab, 2), num or 0, _PEP.get(lab, lab))
        return (self.M, self.m, self.p, pk, self.post or 0)

    def __eq__(self, o):
        return isinstance(o, Ver) and self.key() == o.key()

    def __lt__(self, o):
        return self.key() < o.key()

    def __hash__(self):
        return hash(self.key())

    def styled(self, style):
        return Ver(self.M, self.m, self.p, self.pre, self.sep, self.post, self.build, style)

    def __str__(self):
        core = f"{self.M}.{self.m}.{self.p}"
        if self.pre:
            lab, num = self.pre
            if lab.startswith("~"):
                core += "-" + lab[1:]
            elif self.style == "pep440":
                core += f".dev{num or 0}" if lab == "dev" else f"{_PEP.get(lab, lab)}{num or 0}"
            else:
                core += f"-{lab}" + ("" if num is None else f"{self.sep}{num}")
        if self.post is not None:
            core += f".post{self.post}"
        if self.build:
            core += "+" + self.build
        return core

    def __repr__(self):
        return f"Ver({self})"


def parse_pre(text: str):
    mt = re.fullmatch(r"([A-Za-z]+)([.-]?)(\d*)", text or "")
    if not mt:
        fail(f"--pre takes a label and an optional number, like rc, rc.1 or beta.2, not {text!r}")
    return mt.group(1).lower(), (int(mt.group(3)) if mt.group(3) else None), (mt.group(2) or ".")


def next_version(cur: Ver, kind: str, pre: str | None = None, style: str = "semver") -> Ver:
    """The version after cur: patch, minor or major the way npm does it, or an exact X.Y.Z."""
    explicit = None
    if kind in ("major", "minor", "patch"):
        M, m, p, is_pre = cur.M, cur.m, cur.p, cur.pre is not None
        if kind == "major":
            base = (M, 0, 0) if is_pre and m == 0 and p == 0 else (M + 1, 0, 0)
        elif kind == "minor":
            base = (M, m, 0) if is_pre and p == 0 else (M, m + 1, 0)
        else:
            base = (M, m, p) if is_pre else (M, m, p + 1)
    else:
        explicit = Ver.parse(kind)
        if explicit is None:
            fail(f"bump takes patch, minor, major or a version like 1.4.0, not {kind!r}")
        base = (explicit.M, explicit.m, explicit.p)
        style = explicit.style if explicit.pre else style
    new = Ver(*base, style=style)
    if pre:
        label, num, sep = parse_pre(pre)
        if num is None:
            same = cur.pre and (cur.M, cur.m, cur.p) == base and _PEP.get(cur.pre[0], cur.pre[0]) == _PEP.get(label, label)
            num = (cur.pre[1] or 0) + 1 if same else 1
        new.pre, new.sep = (label, num), sep
    elif explicit is not None:
        new.pre, new.sep, new.post = explicit.pre, explicit.sep, explicit.post
    return new


def version_forms(v: Ver) -> list[str]:
    return sorted({str(v), str(v.styled("semver")), str(v.styled("pep440"))}, key=len, reverse=True)


def version_seen(out: str, v: Ver) -> bool:
    """Does output show version v, as itself (0.2.0 isn't 10.2.0, 0.2.0rc1 or 0.2.0-rc.1)?"""
    for form in version_forms(v):
        pat = (rf"(?<![0-9A-Za-z.])v?{re.escape(form)}"
               r"(?![0-9A-Za-z]|\.[0-9]|[-+.]?(?:rc|alpha|beta|dev|pre|preview|post|next|canary|a|b|c)(?:[.-]?\d|\b))")
        if re.search(pat, out, re.I):
            return True
    return False


def versions_in(text: str) -> list[str]:
    seen = re.findall(rf"(?<![0-9A-Za-z.])v?({VER_IN_TEXT})(?![0-9A-Za-z])", text)
    return list(dict.fromkeys(seen))[:6]


# ---------------------------------------------------------------- TOML (tomllib on 3.11+, a small reader on 3.10)

class _Toml:
    """Enough TOML for manifests: tables, dotted keys, strings, arrays, inline tables, numbers."""
    _ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
    _BARE = re.compile(r"[A-Za-z0-9_-]+")
    _ATOM = re.compile(r"[0-9A-Za-z_:+.\-]+(?:[ T][0-9]{2}:[0-9:.+\-Z]+)?")

    def __init__(self, s):
        self.s, self.i = s, 0

    def err(self, msg):
        raise ValueError(f"{msg} at offset {self.i}")

    def peek(self, k=1):
        return self.s[self.i:self.i + k]

    def ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    def ws_nl(self):
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif c == "#":
                while self.i < len(self.s) and self.s[self.i] != "\n":
                    self.i += 1
            else:
                break

    def eol(self):
        self.ws()
        if self.peek() == "#":
            while self.i < len(self.s) and self.s[self.i] != "\n":
                self.i += 1
        if self.i < len(self.s) and self.s[self.i] not in "\r\n":
            self.err("expected the end of the line")

    def unescape(self, raw):
        def rep(m):
            e = m.group(1)
            if e[0] in "uU":
                return chr(int(e[1:], 16))
            if e[0].isspace():
                return ""
            return self._ESC.get(e, e)
        return re.sub(r"\\(u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|\s+|.)", rep, raw)

    def string(self):
        for q3 in ('"""', "'''"):
            if self.s.startswith(q3, self.i):
                end = self.s.find(q3, self.i + 3)
                if end < 0:
                    self.err("unclosed string")
                raw = self.s[self.i + 3:end]
                self.i = end + 3
                while self.peek() == q3[0]:
                    raw += q3[0]
                    self.i += 1
                raw = raw[1:] if raw.startswith("\n") else raw
                return self.unescape(raw) if q3[0] == '"' else raw
        q = self.s[self.i]
        j = self.i + 1
        while j < len(self.s):
            c = self.s[j]
            if c == "\\" and q == '"':
                j += 2
                continue
            if c == q:
                break
            if c == "\n":
                self.err("unclosed string")
            j += 1
        raw = self.s[self.i + 1:j]
        self.i = j + 1
        return self.unescape(raw) if q == '"' else raw

    def key(self):
        parts = []
        while True:
            self.ws()
            if self.peek() in ('"', "'"):
                parts.append(self.string())
            else:
                m = self._BARE.match(self.s, self.i)
                if not m:
                    self.err("expected a key")
                parts.append(m.group())
                self.i = m.end()
            self.ws()
            if self.peek() == ".":
                self.i += 1
                continue
            return parts

    def value(self):
        c = self.peek()
        if c in ('"', "'"):
            return self.string()
        if c == "[":
            self.i += 1
            out = []
            while True:
                self.ws_nl()
                if self.peek() == "]":
                    self.i += 1
                    return out
                out.append(self.value())
                self.ws_nl()
                if self.peek() == ",":
                    self.i += 1
                    continue
                if self.peek() == "]":
                    self.i += 1
                    return out
                self.err("expected , or ]")
        if c == "{":
            self.i += 1
            out = {}
            self.ws()
            if self.peek() == "}":
                self.i += 1
                return out
            while True:
                k = self.key()
                self.ws()
                if self.peek() != "=":
                    self.err("expected =")
                self.i += 1
                self.ws()
                self.put(out, k, self.value())
                self.ws()
                if self.peek() == ",":
                    self.i += 1
                    continue
                if self.peek() == "}":
                    self.i += 1
                    return out
                self.err("expected , or }")
        m = self._ATOM.match(self.s, self.i)
        if not m:
            self.err("expected a value")
        tok = m.group()
        self.i = m.end()
        if tok in ("true", "false"):
            return tok == "true"
        if re.fullmatch(r"[+-]?\d[\d_]*", tok):
            return int(tok.replace("_", ""))
        if re.fullmatch(r"[+-]?(\d[\d_]*)?\.?\d[\d_]*([eE][+-]?\d+)?", tok):
            return float(tok.replace("_", ""))
        return tok   # dates and times stay text

    @staticmethod
    def put(table, keys, val):
        for k in keys[:-1]:
            table = table.setdefault(k, {})
            if isinstance(table, list):
                table = table[-1]
        table[keys[-1]] = val

    def parse(self):
        root = {}
        cur = root
        while True:
            self.ws_nl()
            if self.i >= len(self.s):
                return root
            if self.peek() == "[":
                aot = self.peek(2) == "[["
                self.i += 2 if aot else 1
                keys = self.key()
                self.ws()
                close = "]]" if aot else "]"
                if not self.s.startswith(close, self.i):
                    self.err("expected " + close)
                self.i += len(close)
                t = root
                for k in keys[:-1]:
                    t = t.setdefault(k, {})
                    if isinstance(t, list):
                        t = t[-1]
                if aot:
                    lst = t.setdefault(keys[-1], [])
                    lst.append({})
                    cur = lst[-1]
                else:
                    cur = t.setdefault(keys[-1], {})
                    if isinstance(cur, list):
                        cur = cur[-1]
            else:
                keys = self.key()
                self.ws()
                if self.peek() != "=":
                    self.err("expected =")
                self.i += 1
                self.ws()
                self.put(cur, keys, self.value())
            self.eol()


def load_toml(text: str | None) -> dict:
    """A TOML file as a dict, or {} when it doesn't parse."""
    if not text:
        return {}
    try:
        import tomllib
    except ImportError:
        tomllib = None
    if tomllib is not None:
        try:
            return tomllib.loads(text)
        except Exception:
            return {}
    try:
        return _Toml(text.replace("\r\n", "\n")).parse()
    except (ValueError, IndexError):
        return {}


# ---------------------------------------------------------------- small helpers

def load(a) -> tuple[Path, dict]:
    return kit.load_run(a.workspace)


def rj(ws: Path, name: str, default=None):
    return kit.load_json(Path(ws) / name, default)


def sj(ws: Path, name: str, data) -> None:
    kit.save_json(Path(ws) / name, data)


def mutate(ws: Path, name: str, default, fn):
    with kit.locked(ws):
        data = kit.load_json(Path(ws) / name, default)
        out = fn(data)
        kit.save_json(Path(ws) / name, data)
    return out


def rev_sha(top: Path, ref: str | None) -> str | None:
    if not ref:
        return None
    return kit.git(top, "rev-parse", "--verify", "-q", ref + "^{commit}", check=False).strip() or None


def current_branch(top: Path) -> str | None:
    return kit.git(top, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip() or None


def platform_name() -> str:
    return "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")


def show(argv: list[str]) -> str:
    shown = ["python" if x == sys.executable else x for x in argv]
    return subprocess.list2cmdline(shown) if os.name == "nt" else shlex.join(shown)


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tail(text: str, n: int = 30) -> str:
    return "\n".join(text.rstrip("\n").splitlines()[-n:])


def indent(text: str, pad: str = "    ") -> str:
    return "\n".join(pad + ln for ln in text.splitlines())


def remotes(top: Path) -> list[str]:
    return [r for r in kit.git(top, "remote", check=False).split() if r]


def pick_remote(top: Path) -> str | None:
    rs = remotes(top)
    return "origin" if "origin" in rs else (rs[0] if rs else None)


def remote_url(top: Path, remote: str | None, push: bool = False) -> str | None:
    if not remote:
        return None
    args = ["remote", "get-url"] + (["--push"] if push else []) + [remote]
    return kit.git(top, *args, check=False).strip() or None


def github_of(url: str | None) -> tuple[str, str] | None:
    mt = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url or "")
    return (mt.group(1), mt.group(2)) if mt else None


def real_python(cmd: list[str], path: str | None = None) -> tuple[list[str], str]:
    """A bare `python` that finds nothing, or Windows' Microsoft Store stub, becomes the Python running this."""
    if not cmd or cmd[0].lower() not in ("python", "python3"):
        return cmd, ""
    found = shutil.which(cmd[0], path=path)
    if found and "windowsapps" not in found.lower():
        return cmd, ""
    why = "the Microsoft Store's stub" if found else "nothing"
    return [sys.executable, *cmd[1:]], f"`{cmd[0]}` on the PATH is {why}, so {sys.executable} runs it instead"


def empty_tree(top: Path) -> str:
    return kit.git(top, "hash-object", "-t", "tree", "--stdin", data="").strip()


# ---------------------------------------------------------------- the project's files, on disk or at a commit

class Tree:
    def __init__(self, top: Path, rev: str | None = None):
        self.top, self.rev = Path(top), rev
        self._files, self._set, self._text = None, None, {}

    def files(self) -> list[str]:
        if self._files is None:
            if self.rev:
                self._files = sorted(kit.split0(kit.git(self.top, "ls-tree", "-r", "-z", "--name-only", self.rev)))
            else:
                self._files = kit.all_files(self.top, True)
            self._set = set(self._files)
        return self._files

    def has(self, path: str) -> bool:
        self.files()
        return path in self._set

    def read(self, path: str) -> str | None:
        if not path or not self.has(path):
            return None
        if path not in self._text:
            if self.rev:
                r = subprocess.run(["git", "-C", str(self.top), "show", f"{self.rev}:{path}"], capture_output=True)
                data = r.stdout if r.returncode == 0 else None
            else:
                p = self.top / path
                data = p.read_bytes() if p.is_file() and p.stat().st_size <= kit.TEXT_LIMIT else None
            self._text[path] = None if data is None or b"\0" in data[:8192] else \
                data.decode("utf-8", "replace").lstrip("\ufeff")
        return self._text[path]


def lines_of(text: str) -> list[str]:
    return [ln.rstrip("\r") for ln in text.split("\n")]


def inner_dirs(f: str, pkg: str) -> list[str]:
    rel = f[len(pkg) + 1:] if pkg and f.startswith(pkg + "/") else f
    return rel.split("/")[:-1]


def skipped(f: str, pkg: str) -> bool:
    return any(d.lower() in SKIP_PARTS or d.startswith(".") for d in inner_dirs(f, pkg))


def within(f: str, pkg: str) -> bool:
    return not pkg or f.startswith(pkg + "/")


# ---------------------------------------------------------------- what the project is

def _j(pkg: str, name: str) -> str:
    return f"{pkg}/{name}" if pkg else name


def module_file(tree: Tree, pkg: str, dotted: str) -> str | None:
    """The file that defines a module like greet_tool or greet_tool.about."""
    parts = dotted.split(".")
    for root in ("src", ""):
        base = "/".join(x for x in (pkg, root, *parts) if x)
        for cand in (base + "/__init__.py", base + ".py"):
            if tree.has(cand):
                return cand
    return None


def project_info(tree: Tree, pkg: str = "") -> dict:
    info = {"package": pkg, "name": None, "ecosystem": None, "manifest": None, "version_from": None,
            "scripts": {}, "bins": {}, "backend": None, "private": False, "workspace": False}
    py = tree.read(_j(pkg, "pyproject.toml"))
    if py:
        d = load_toml(py)
        proj, tool = d.get("project") or {}, d.get("tool") or {}
        poetry = tool.get("poetry") or {}
        info.update(ecosystem="python", manifest=_j(pkg, "pyproject.toml"))
        info["name"] = proj.get("name") or poetry.get("name")
        scripts = {**(poetry.get("scripts") or {}), **(proj.get("scripts") or {})}
        info["scripts"] = {k: v for k, v in scripts.items() if isinstance(v, str)}
        bs = d.get("build-system") or {}
        info["backend"] = bs.get("build-backend")
        requires = " ".join(str(x) for x in bs.get("requires") or []).lower()
        if "version" in (proj.get("dynamic") or []) or (poetry and (tool.get("poetry-dynamic-versioning") or {}).get("enable")):
            hv = (tool.get("hatch") or {}).get("version") or {}
            sd = ((tool.get("setuptools") or {}).get("dynamic") or {}).get("version") or {}
            if "setuptools_scm" in tool or "setuptools-scm" in requires or "setuptools_scm" in requires:
                info["version_from"] = "tags (setuptools-scm)"
            elif hv.get("source") == "vcs" or "hatch-vcs" in requires:
                info["version_from"] = "tags (hatch-vcs)"
            elif ((tool.get("pdm") or {}).get("version") or {}).get("source") == "scm":
                info["version_from"] = "tags (pdm)"
            elif (tool.get("poetry-dynamic-versioning") or {}).get("enable"):
                info["version_from"] = "tags (poetry-dynamic-versioning)"
            elif hv.get("path"):
                info["version_from"] = "file:" + _j(pkg, hv["path"])
            elif isinstance(sd, dict) and sd.get("attr"):
                f = module_file(tree, pkg, sd["attr"].rsplit(".", 1)[0])
                info["version_from"] = f"file:{f}" if f else f"attr:{sd['attr']}"
            elif isinstance(sd, dict) and sd.get("file"):
                ff = sd["file"][0] if isinstance(sd["file"], list) else sd["file"]
                info["version_from"] = "file:" + _j(pkg, ff)
            elif "flit" in (info["backend"] or "") and info["name"]:
                f = module_file(tree, pkg, info["name"].replace("-", "_"))
                info["version_from"] = f"file:{f}" if f else "dynamic"
            else:
                info["version_from"] = "dynamic"
    pj = tree.read(_j(pkg, "package.json"))
    if pj:
        try:
            d = json.loads(pj)
        except json.JSONDecodeError:
            d = {}
        if isinstance(d, dict):
            if info["ecosystem"] is None:
                info.update(ecosystem="node", manifest=_j(pkg, "package.json"))
                info["name"] = d.get("name")
                info["private"] = bool(d.get("private"))
                info["workspace"] = bool(d.get("workspaces"))
                if str(d.get("version", "")).startswith("0.0.0-"):
                    info["version_from"] = "tags (semantic-release)"
            b = d.get("bin")
            if isinstance(b, str) and d.get("name"):
                info["bins"] = {str(d["name"]).split("/")[-1]: b}
            elif isinstance(b, dict):
                info["bins"] = {k: v for k, v in b.items() if isinstance(v, str)}
            info["npm_scripts"] = sorted((d.get("scripts") or {}).keys())
    cargo = tree.read(_j(pkg, "Cargo.toml"))
    if cargo:
        d = load_toml(cargo)
        if info["ecosystem"] is None:
            p = d.get("package") or {}
            info.update(ecosystem="rust", manifest=_j(pkg, "Cargo.toml"), name=p.get("name"))
            info["workspace"] = "workspace" in d
            bins = [b.get("name") for b in d.get("bin") or [] if isinstance(b, dict) and b.get("name")]
            info["bins"] = {b: f"target/release/{b}" for b in (bins or ([p["name"]] if p.get("name") else []))}
    if info["ecosystem"] is None:
        for f in tree.files():
            if not within(f, pkg) or len(inner_dirs(f, pkg)) > 0:
                continue
            base = f.rsplit("/", 1)[-1]
            if base == "setup.cfg" or base == "setup.py":
                info.update(ecosystem="python", manifest=f)
                text = tree.read(f) or ""
                mt = re.search(r"^\s*name\s*=\s*['\"]?([A-Za-z0-9._-]+)", text, re.M)
                info["name"] = info["name"] or (mt.group(1) if mt else None)
            elif base.endswith(".gemspec"):
                info.update(ecosystem="ruby", manifest=f, name=base[:-8])
            elif base.endswith((".csproj", ".fsproj", ".vbproj")):
                info.update(ecosystem="dotnet", manifest=f, name=base.rsplit(".", 1)[0])
            elif base == "go.mod":
                mt = re.search(r"^module\s+(\S+)", tree.read(f) or "", re.M)
                info.update(ecosystem="go", manifest=f, name=mt.group(1).rsplit("/", 1)[-1] if mt else None)
    return info


def other_packages(tree: Tree, pkg: str) -> list[str]:
    """Folders below the package with their own versioned manifest (a monorepo's other packages)."""
    out = []
    for f in tree.files():
        base = f.rsplit("/", 1)[-1]
        if base not in ("package.json", "pyproject.toml", "Cargo.toml") or "/" not in f:
            continue
        folder = f.rsplit("/", 1)[0]
        if folder == pkg or len(folder.split("/")) > 3 or skipped(f, ""):
            continue
        text = tree.read(f) or ""
        if re.search(r'^\s*"?version"?\s*[:=]', text, re.M):
            out.append(folder)
    return sorted(set(out))[:20]


# ---------------------------------------------------------------- version sites

def _site(out, path, n, line, mt, group, kind, gname="v"):
    v = mt.group(gname)
    if Ver.parse(v) is None:
        return
    out.append({"file": path, "line": n, "text": line.strip()[:200], "version": v,
                "span": [mt.start(gname), mt.end(gname)], "group": group, "kind": kind})


def toml_sites(out, path, text, tables, group="manifest"):
    table = None
    for n, line in enumerate(lines_of(text), 1):
        s = line.strip()
        if s.startswith("[["):
            table = None
            continue
        h = re.match(r"\[\s*([^\]]+?)\s*\]\s*(#.*)?$", s)
        if h:
            table = re.sub(r"[\"'\s]", "", h.group(1))
            continue
        if table in tables:
            mt = re.match(r"\s*version\s*=\s*([\"'])(?P<v>[^\"']*)\1", line)
            if mt:
                _site(out, path, n, line, mt, group, f"[{table}] version")


def json_version_sites(out, path, text, group, kind, lock_name=None):
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return
    if not isinstance(d, dict):
        return
    if lock_name is not None and d.get("name") != lock_name:
        return
    ls = lines_of(text)
    want = d.get("version")
    if isinstance(want, str):
        best = None
        for n, line in enumerate(ls, 1):
            mt = re.match(r'(?P<ind>\s*)"version"\s*:\s*"(?P<v>[^"]*)"', line)
            if mt and mt.group("v") == want and (best is None or len(mt.group("ind")) < best[2]):
                best = (n, mt, len(mt.group("ind")))
        if best:
            _site(out, path, best[0], ls[best[0] - 1], best[1], group, kind)
    root_pkg = ((d.get("packages") or {}).get("") or {}) if lock_name is not None else {}
    if isinstance(root_pkg.get("version"), str):
        start = next((i for i, ln in enumerate(ls) if re.match(r'\s*"":\s*\{', ln)), None)
        if start is not None:
            for n in range(start + 1, min(start + 60, len(ls))):
                mt = re.match(r'\s*"version"\s*:\s*"(?P<v>[^"]*)"', ls[n])
                if mt:
                    _site(out, path, n + 1, ls[n], mt, group, 'packages[""].version')
                    break


def regex_sites(out, path, text, regex, group, kind, first_only=False):
    for n, line in enumerate(lines_of(text), 1):
        for mt in regex.finditer(line):
            _site(out, path, n, line, mt, group, kind)
            if first_only:
                return


def name_forms(name: str | None) -> list[str]:
    if not name:
        return []
    forms = {name}
    if "/" not in name:
        forms |= {name.replace("-", "_"), name.replace("_", "-")}
    return sorted(forms, key=len, reverse=True)


def pin_regexes(name, gh):
    out = []
    if name:
        alt = "|".join(re.escape(f) for f in name_forms(name))
        out += [
            (re.compile(rf"(?<![\w./@-])(?:{alt})(?:\[[^\]\s]*\])?\s*===?\s*v?{VER_GROUP}", re.I), "a pip pin"),
            (re.compile(rf"(?<![\w./-])(?:{alt})@v?{VER_GROUP}", re.I), "an @version pin"),
            (re.compile(rf"\bcargo\s+install\s+(?:{alt})\b[^\n`]*?--version[= ]v?{VER_GROUP}"), "a cargo install pin"),
            (re.compile(rf"\bgem\s+install\s+(?:{alt})\b[^\n`]*?(?:-v|--version)[= ]['\"]?{VER_GROUP}"), "a gem install pin"),
        ]
    if gh:
        o, r = (re.escape(x) for x in gh)
        out += [
            (re.compile(rf"github\.com/{o}/{r}/releases/download/[^/\s]*?v?{VER_GROUP}/", re.I), "a release download URL"),
            (re.compile(rf"\buses:\s*{o}/{r}@v?{VER_GROUP}", re.I), "an Actions pin"),
        ]
    return out


EXAMPLE_RE = re.compile(rf"\b[A-Z][A-Z0-9_]*_VERSION\b[^\n]{{0,60}}?v?{VER_GROUP}")


def changelog_file(tree: Tree, pkg: str) -> str | None:
    for folder in dict.fromkeys([pkg, ""]):
        cands = [f for f in tree.files() if (f.rsplit("/", 1)[0] if "/" in f else "") == folder
                 and CHANGELOG_NAME.match(f.rsplit("/", 1)[-1])]
        if cands:
            return sorted(cands, key=lambda f: (not f.lower().endswith(".md"), len(f)))[0]
    return None


def doc_files(tree: Tree, pkg: str, changelog: str | None) -> list[str]:
    out = []
    for f in tree.files():
        if f == changelog:
            continue
        folder = f.rsplit("/", 1)[0] if "/" in f else ""
        low = f.rsplit("/", 1)[-1].lower()
        if folder in ("", pkg) and low.startswith(("readme", "install")) and \
                (low in ("readme", "install") or low.endswith((".md", ".rst", ".txt", ".markdown"))):
            out.append(f)
        elif re.match(r"docs?/", f[len(pkg) + 1:] if pkg and f.startswith(pkg + "/") else f) \
                and low.endswith((".md", ".mdx", ".rst", ".txt", ".markdown")):
            out.append(f)
    return out[:300]


def find_sites(tree: Tree, info: dict, gh=None) -> list[dict]:
    """Every place the project's own version is written down."""
    pkg = info.get("package") or ""
    out: list[dict] = []
    tags_only = (info.get("version_from") or "").startswith("tags")
    from_file = (info.get("version_from") or "")[5:] if (info.get("version_from") or "").startswith("file:") else None
    if not tags_only:
        t = tree.read(_j(pkg, "pyproject.toml"))
        if t:
            toml_sites(out, _j(pkg, "pyproject.toml"), t, ("project", "tool.poetry"))
        t = tree.read(_j(pkg, "package.json"))
        if t:
            json_version_sites(out, _j(pkg, "package.json"), t, "manifest", "version")
        t = tree.read(_j(pkg, "Cargo.toml"))
        if t:
            toml_sites(out, _j(pkg, "Cargo.toml"), t, ("package", "workspace.package"))
        t = tree.read(_j(pkg, "setup.cfg"))
        if t:
            sect = None
            for n, line in enumerate(lines_of(t), 1):
                h = re.match(r"\s*\[([^\]]+)\]", line)
                if h:
                    sect = h.group(1).strip()
                    continue
                mt = re.match(r"\s*version\s*=\s*(?P<v>[^\s#;]+)\s*$", line)
                if sect == "metadata" and mt:
                    _site(out, _j(pkg, "setup.cfg"), n, line, mt, "manifest", "[metadata] version")
        t = tree.read(_j(pkg, "setup.py"))
        if t:
            regex_sites(out, _j(pkg, "setup.py"), t, re.compile(r"\bversion\s*=\s*['\"](?P<v>[^'\"]+)['\"]"),
                        "manifest", "setup(version=...)", first_only=True)
        for name in ("VERSION", "VERSION.txt", "version.txt"):
            t = tree.read(_j(pkg, name))
            if t and len([ln for ln in lines_of(t) if ln.strip()]) == 1:
                regex_sites(out, _j(pkg, name), t, re.compile(rf"^\s*v?{VER_GROUP}\s*$"), "manifest", "the version file")
        for f in tree.files():
            if not within(f, pkg) or skipped(f, pkg):
                continue
            base = f.rsplit("/", 1)[-1]
            depth = len(inner_dirs(f, pkg))
            if base.endswith((".csproj", ".fsproj", ".vbproj")) or base == "Directory.Build.props":
                if depth <= 3:
                    regex_sites(out, f, tree.read(f) or "",
                                re.compile(r"<(?P<t>Version|VersionPrefix|PackageVersion)>(?P<v>[^<]+)</(?P=t)>"),
                                "manifest", "<Version>")
            elif base.endswith(".gemspec") and depth == 0:
                regex_sites(out, f, tree.read(f) or "", re.compile(r"\.version\s*=\s*['\"](?P<v>[^'\"]+)['\"]"),
                            "manifest", "spec.version")
            elif base.endswith(".nuspec") and depth <= 1:
                regex_sites(out, f, tree.read(f) or "", re.compile(r"<version>(?P<v>[^<]+)</version>"),
                            "manifest", "<version>")
            elif base == "Chart.yaml" and depth <= 1:
                regex_sites(out, f, tree.read(f) or "", re.compile(r"^version:\s*['\"]?(?P<v>[^'\"\s]+)"),
                            "manifest", "chart version", first_only=True)
    for f in tree.files():
        if not within(f, pkg) or skipped(f, pkg):
            continue
        base = f.rsplit("/", 1)[-1]
        if base.endswith(".py"):
            text = tree.read(f) or ""
            if "__version__" in text:
                group = "manifest" if f == from_file else "mirror"
                regex_sites(out, f, text, re.compile(r"^__version__\s*(?::\s*str\s*)?=\s*(['\"])(?P<v>[^'\"]+)\1"),
                            group, "__version__", first_only=True)
        elif base == "version.rb":
            regex_sites(out, f, tree.read(f) or "", re.compile(r"\bVERSION\s*=\s*['\"](?P<v>[^'\"]+)['\"]"),
                        "manifest" if f == from_file else "mirror", "VERSION")
        elif base.startswith("Dockerfile") and len(inner_dirs(f, pkg)) == 0:
            regex_sites(out, f, tree.read(f) or "",
                        re.compile(r"^\s*LABEL\b.*?\b(?:org\.opencontainers\.image\.)?version\s*=\s*['\"]?(?P<v>[^'\"\s]+)", re.I),
                        "mirror", "LABEL version")
    if from_file and tree.has(from_file) and not any(s["file"] == from_file for s in out):
        t = tree.read(from_file) or ""
        if len([ln for ln in lines_of(t) if ln.strip()]) == 1:
            regex_sites(out, from_file, t, re.compile(rf"^\s*v?{VER_GROUP}\s*$"), "manifest", "the version file")
    name = info.get("name")
    for lock in ("package-lock.json", "npm-shrinkwrap.json"):
        t = tree.read(_j(pkg, lock))
        if t and name:
            json_version_sites(out, _j(pkg, lock), t, "lock", "version", lock_name=name)
    for lock in dict.fromkeys([_j(pkg, "Cargo.lock"), "Cargo.lock", _j(pkg, "uv.lock"), "uv.lock"]):
        t = tree.read(lock)
        if not t or not name:
            continue
        cur = None
        norm = lambda s: re.sub(r"[-_.]+", "-", s).lower()
        for n, line in enumerate(lines_of(t), 1):
            if line.strip() == "[[package]]":
                cur = None
            mt = re.match(r'name\s*=\s*"([^"]+)"', line)
            if mt:
                cur = mt.group(1)
            mt = re.match(r'version\s*=\s*"(?P<v>[^"]+)"', line)
            if mt and cur and norm(cur) == norm(name):
                _site(out, lock, n, line, mt, "lock", "the lock file's own entry")
                cur = None
    cl = changelog_file(tree, pkg)
    if cl:
        parsed = parse_changelog(tree.read(cl) or "")
        for sec in parsed["sections"]:
            if sec["version"]:
                line = parsed["lines"][sec["line"]]
                mt = re.compile(rf"(?<![\w.])v?{VER_GROUP}").search(line)
                if mt:
                    _site(out, cl, sec["line"] + 1, line, mt, "changelog", "the newest section")
                    out[-1]["dated"] = bool(sec["date"])
                    out[-1]["unreleased"] = sec["unreleased"]
                break
    pins = pin_regexes(name, gh)
    seen = {(s["file"], s["line"]) for s in out}
    for f in doc_files(tree, pkg, cl):
        text = tree.read(f) or ""
        for n, line in enumerate(lines_of(text), 1):
            hit = False
            for rx, kind in pins:
                for mt in rx.finditer(line):
                    _site(out, f, n, line, mt, "pin", kind)
                    hit = True
            if not hit and (f, n) not in seen:
                for mt in EXAMPLE_RE.finditer(line):
                    _site(out, f, n, line, mt, "example", "an example")
    for f in tree.files():
        folder = f.rsplit("/", 1)[0] if "/" in f else ""
        if folder in ("", pkg) and f.lower().endswith((".sh", ".ps1", ".bat", ".cmd")):
            regex_sites(out, f, tree.read(f) or "", EXAMPLE_RE, "example", "an example")
    return out


SOURCE_ORDER = ("pyproject.toml", "package.json", "Cargo.toml", "setup.cfg", "setup.py")


def source_site(sites: list[dict]) -> dict | None:
    """The site the version comes from: the manifest, in the order build tools read them."""
    man = [s for s in sites if s["group"] == "manifest"]
    if not man:
        return None
    def rank(s):
        base = s["file"].rsplit("/", 1)[-1]
        return SOURCE_ORDER.index(base) if base in SOURCE_ORDER else len(SOURCE_ORDER)
    return sorted(man, key=rank)[0]


def site_verb(s: dict) -> str:
    return {"pin": "pins", "changelog": "has its newest section at", "lock": "records"}.get(s["group"], "says")


def compare_sites(sites: list[dict], want: Ver | None, want_from: str) -> tuple[list[str], list[str]]:
    """(problems, notes) for every site that has to agree with want."""
    problems, notes = [], []
    if want is None:
        return problems, notes
    for s in sites:
        if s["group"] == "example":
            continue
        v = Ver.parse(s["version"])
        if v == want:
            continue
        where = f"{s['file']}:{s['line']}"
        if s["group"] == "changelog" and v and v > want and (s.get("unreleased") or not s.get("dated")):
            notes.append(f"{where} already has a section for {s['version']}, not released yet")
            continue
        problems.append(f"{where} {site_verb(s)} {s['version']}, {want_from} {want}")
    return problems, notes


# ---------------------------------------------------------------- changelogs

HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DATE_RE = re.compile(rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d{{1,2}} {MONTHS} \d{{4}}\b|\b{MONTHS} \d{{1,2}},? \d{{4}}\b", re.I)
UNRELEASED = re.compile(r"\b(unreleased|upcoming|not yet released|tbd|tba|in progress|work in progress)\b", re.I)
BULLET = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
TITLE_VER = re.compile(rf"(?<![\w.])v?{VER_GROUP}")


def parse_changelog(text: str) -> dict:
    lines = lines_of(text)
    heads, fence = [], False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        mt = HEADING.match(ln)
        if mt:
            heads.append((i, len(mt.group(1)), mt.group(2)))
    level = None
    for i, lvl, title in heads:
        if TITLE_VER.search(title) or re.match(r"\[?\s*unreleased\b", title, re.I):
            level = lvl
            break
    sections = []
    if level is not None:
        for i, lvl, title in heads:
            if lvl != level:
                continue
            mt = TITLE_VER.search(title)
            unrel = bool(UNRELEASED.search(title))
            if not mt and not re.match(r"\[?\s*unreleased\b", title, re.I):
                continue
            end = next((h[0] for h in heads if h[0] > i and h[1] <= level), len(lines))
            dm = DATE_RE.search(title)
            sections.append({"line": i, "end": end, "title": title, "level": level,
                             "version": mt.group("v") if mt else None,
                             "date": dm.group(0) if dm else None, "unreleased": unrel or not mt})
    links = [i for i, ln in enumerate(lines) if re.match(r"^\[[^\]]+\]:\s*\S+", ln)]
    return {"lines": lines, "sections": sections, "level": level, "links": links}


def section_for(parsed: dict, v: Ver | None):
    if v is None:
        return None
    for s in parsed["sections"]:
        if s["version"] and Ver.parse(s["version"]) == v:
            return s
    return None


def unreleased_section(parsed: dict):
    return next((s for s in parsed["sections"] if not s["version"]), None)


def entries_of(parsed: dict, sec: dict) -> tuple[list[dict], str]:
    lines = parsed["lines"]
    entries, summary, cat, cur, base, blank = [], [], None, None, None, False
    for k in range(sec["line"] + 1, sec["end"]):
        ln = lines[k]
        if re.match(r"^\[[^\]]+\]:\s*\S+", ln):
            continue
        h = HEADING.match(ln)
        if h:
            cat, cur, base = h.group(2).strip(), None, None
            continue
        b = BULLET.match(ln)
        if b and (base is None or len(b.group(1)) <= base):
            base = len(b.group(1)) if base is None else base
            cur = {"id": f"E{len(entries) + 1}", "text": b.group(3).strip(), "line": k + 1, "category": cat}
            entries.append(cur)
            blank = False
            continue
        if not ln.strip():
            blank = True
            continue
        if cur and (ln[:1] in (" ", "\t") or not blank):
            cur["text"] += " " + re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", ln).strip()
            continue
        if not entries and cat is None:
            summary.append(ln.strip())
        else:
            cur = None
        blank = False
    return entries, " ".join(summary).strip()


def section_text(parsed: dict, sec: dict) -> str:
    body = [ln for ln in parsed["lines"][sec["line"] + 1:sec["end"]] if not re.match(r"^\[[^\]]+\]:\s*\S+", ln)]
    return "\n".join(body).strip("\n")


def changelog_edit(text: str, target: Ver, today: str, tag: str) -> tuple[str, list[str]]:
    """Date the target's section, or turn the Unreleased section into it. Never invents entries."""
    cr = "\r" if text.count("\r\n") * 2 > text.count("\n") else ""
    raw = text.split("\n")
    parsed = parse_changelog(text)
    notes = []
    sec = section_for(parsed, target)
    dated_before = any(s["date"] for s in parsed["sections"])
    if sec:
        if sec["date"] or (not sec["unreleased"] and not dated_before):
            return text, notes
        line = raw[sec["line"]].rstrip("\r")
        if UNRELEASED.search(line):
            line = UNRELEASED.sub(today, line, count=1)
        else:
            line += f" ({today})" if "(" in "".join(s["title"] for s in parsed["sections"]) else f" - {today}"
        raw[sec["line"]] = line + cr
        return "\n".join(raw), notes
    unrel = unreleased_section(parsed)
    if not unrel:
        return text, [f"there's no section for {target} and no Unreleased section to turn into one; write it, "
                      "then run changelog-check"]
    prev = next((s for s in parsed["sections"] if s["version"]), None)
    hashes = "#" * unrel["level"]
    if prev:
        pl = parsed["lines"][prev["line"]]
        mt = TITLE_VER.search(pl)
        head = pl[:mt.start("v")] + str(target) + pl[mt.end("v"):]
        dm = DATE_RE.search(head)
        if dm:
            head = head[:dm.start()] + today + head[dm.end():]
        elif dated_before:
            head += f" - {today}"
        if UNRELEASED.search(head):
            head = UNRELEASED.sub(today, head, count=1)
    else:
        head = f"{hashes} [{target}] - {today}" if "[" in unrel["title"] else f"{hashes} {target} - {today}"
    kac = unrel["title"].lstrip().startswith("[")
    new = ([raw[unrel["line"]].rstrip("\r") + cr, cr] if kac else []) + [head + cr]
    raw[unrel["line"]:unrel["line"] + 1] = new
    for i, ln in enumerate(raw):
        mt = re.match(r"^\[unreleased\]:\s*(\S+?/compare/)(\S+?)\.\.\.(\S+?)\s*$", ln.rstrip("\r"), re.I)
        if mt:
            base, old, head_ref = mt.groups()
            raw[i] = f"{ln[:ln.index(']') + 1]}: {base}{tag}...{head_ref}" + cr
            raw.insert(i + 1, f"[{target}]: {base}{old}...{tag}" + cr)
            break
    if not entries_of(parse_changelog("\n".join(raw)), section_for(parse_changelog("\n".join(raw)), target))[0]:
        notes.append(f"the section for {target} has no entries yet: write them before the build")
    return "\n".join(raw), notes


# ---------------------------------------------------------------- tags, workflows, the release shape

TAG_RE = re.compile(r"(?P<prefix>.*?)(?P<v>\d+\.\d+\.\d+\S*)")


def version_tags(top: Path, name: str | None) -> list[dict]:
    out = kit.git(top, "for-each-ref", "--format=%(refname:short)%09%(objectname)%09%(*objectname)%09%(creatordate:short)",
                  "refs/tags", check=False)
    merged = set(kit.git(top, "tag", "--merged", "HEAD", check=False).split()) if kit.has_head(top) else set()
    names = [n.lower() for n in name_forms(name.split("/")[-1] if name else None)]
    tags = []
    for line in out.splitlines():
        parts = (line.split("\t") + ["", "", "", ""])[:4]
        tname, obj, peeled, date = parts
        mt = TAG_RE.fullmatch(tname)
        v = Ver.parse(mt.group("v")) if mt else None
        if not v:
            continue
        prefix = mt.group("prefix")
        generic = prefix.lower() in ("", "v", "release-", "release-v", "release/", "release/v", "rel-")
        named = any(prefix.lower() in (f"{n}@", f"{n}@v", f"{n}-v", f"{n}/v", f"{n}-", f"{n}/") for n in names)
        if not (generic or named):
            continue
        tags.append({"tag": tname, "version": str(v), "prefix": prefix, "date": date, "sha": peeled or obj,
                     "merged": tname in merged, "named": named})
    if any(t["named"] for t in tags):
        tags = [t for t in tags if t["named"]]
    return sorted(tags, key=lambda t: Ver.parse(t["version"]), reverse=True)


def tag_signed(top: Path, tag: str) -> bool:
    if kit.git(top, "cat-file", "-t", tag, check=False).strip() != "tag":
        return False
    body = kit.git(top, "cat-file", "-p", tag, check=False)
    return "-----BEGIN PGP SIGNATURE-----" in body or "-----BEGIN SSH SIGNATURE-----" in body


PUBLISHERS = [
    (r"\bgh\s+release\s+create\b|softprops/action-gh-release|ncipollo/release-action", "github", "creates the GitHub release"),
    (r"\bnpm\s+publish\b|JS-DevTools/npm-publish", "npm", "publishes to npm"),
    (r"\btwine\s+upload\b|pypa/gh-action-pypi-publish|\buv\s+publish\b|\bpoetry\s+publish\b|\bflit\s+publish\b|\bhatch\s+publish\b",
     "pypi", "publishes to PyPI"),
    (r"\bcargo\s+publish\b", "crates", "publishes to crates.io"),
    (r"\bgem\s+push\b", "rubygems", "publishes to RubyGems"),
    (r"\bdotnet\s+nuget\s+push\b|\bnuget\s+push\b", "nuget", "publishes to NuGet"),
    (r"goreleaser", "goreleaser", "runs GoReleaser"),
    (r"\bdocker\s+push\b|docker/build-push-action", "docker", "pushes a container image"),
]


def read_workflows(tree: Tree) -> list[dict]:
    out = []
    for f in tree.files():
        if not re.fullmatch(r"\.github/workflows/[^/]+\.ya?ml", f):
            continue
        text = tree.read(f) or ""
        ls = lines_of(text)
        on, inside, inline = [], False, ""   # the `on:` block only: a job called `release` isn't a trigger
        for ln in ls:
            mt = re.match(r"^(on|\"on\"|'on'|true)\s*:(.*)$", ln)
            if mt and not inside:
                inside = True
                inline = re.sub(r"#.*", "", mt.group(2)).strip()
                continue
            if inside:
                if ln and not ln[0].isspace() and not ln.startswith("#"):
                    break
                on.append(ln)
        block = "\n".join(on)
        tag_pats = []
        for mt in re.finditer(r"^\s*tags:\s*\[([^\]]*)\]", block, re.M):
            tag_pats += [x.strip().strip("'\"") for x in mt.group(1).split(",") if x.strip()]
        for i, ln in enumerate(on):
            if re.match(r"^\s*tags:\s*$", ln):
                for nxt in on[i + 1:]:
                    m2 = re.match(r"^\s*-\s*['\"]?([^'\"\s#]+)", nxt)
                    if not m2:
                        break
                    tag_pats.append(m2.group(1))
        on_release = bool(re.search(r"^\s{1,8}release\s*:", block, re.M) or re.search(r"\brelease\b", inline))
        runs = []
        for i, ln in enumerate(ls):
            mt = re.match(r"^(\s*)-?\s*run:\s*(.*)$", ln)
            if not mt:
                continue
            first = mt.group(2).strip()
            if first in ("|", ">", "|-", ">-"):
                ind, cont = None, ""
                for nxt in ls[i + 1:]:
                    if not nxt.strip():
                        continue
                    lead = len(nxt) - len(nxt.lstrip())
                    if ind is None:
                        ind = lead
                    if lead < ind:
                        break
                    piece = nxt.strip()
                    if piece.endswith("\\"):
                        cont += piece[:-1].rstrip() + " "
                        continue
                    runs.append(cont + piece)
                    cont = ""
            elif first:
                runs.append(first)
        uses = re.findall(r"uses:\s*([^\s#]+)", text)
        blob = "\n".join(runs + uses)
        publishes = [(key, what) for rx, key, what in PUBLISHERS if re.search(rx, blob, re.I)]
        builds = [r for r in runs if re.search(r"\b(build|pack|package|dist|wheel|sdist)\b", r, re.I)
                  and not re.search(r"publish|upload|release create|\bpush\b", r, re.I)
                  and not re.match(r"(cd|ls|cat|echo|mkdir|rm|test|\[)\b", r)]
        base = f.rsplit("/", 1)[-1]
        if not (tag_pats or on_release or publishes):
            continue
        out.append({"file": f, "tags": tag_pats, "on_release": on_release, "publishes": publishes,
                    "builds": builds[:6], "reads_changelog": "changelog" in text.lower(),
                    "reusable": [u for u in uses if u.startswith("./")]})
    return out


def build_suggestions(tree: Tree, info: dict, workflows: list[dict]) -> list[dict]:
    out = []
    for w in workflows:
        for b in w["builds"]:
            out.append({"cmd": b, "why": f"what {w['file']} runs"})
    eco, pkg = info.get("ecosystem"), info.get("package") or ""
    if eco == "python":
        has_build = importlib.util.find_spec("build") is not None
        out.append({"cmd": "python -m build" if has_build else "python -m pip wheel . --no-deps -w dist",
                    "why": f"the Python build ({info.get('backend') or 'setuptools'}); offline it needs that backend "
                           "installed, else pass --net"})
    elif eco == "node":
        out.append({"cmd": "npm pack", "why": "npm's own archive (runs prepack and prepare)"})
    elif eco == "rust":
        out.append({"cmd": "cargo build --release", "why": "the release binaries in target/release"})
    elif eco == "go":
        out.append({"cmd": "go build -o dist/ ./...", "why": "the Go binaries"})
    for f in tree.files():
        base = f.rsplit("/", 1)[-1]
        folder = f.rsplit("/", 1)[0] if "/" in f else ""
        if base in (".goreleaser.yml", ".goreleaser.yaml") and folder == "":
            out.append({"cmd": "goreleaser release --snapshot --clean", "why": "GoReleaser, without publishing"})
        if base in ("Makefile", "makefile", "GNUmakefile") and folder in ("", pkg):
            targets = re.findall(r"^(dist|package|release|build|wheel)\s*:", tree.read(f) or "", re.M)
            for t in targets[:2]:
                out.append({"cmd": f"make {t}" if folder == "" else f"make -C {folder} {t}", "why": f"the {t} target in {f}"})
        if re.fullmatch(r"(tools/|scripts/)?(build|package|pack|release)\.(py|sh|ps1|mjs|js)", f):
            runner = {"py": "python", "sh": "sh", "ps1": "pwsh -File", "mjs": "node", "js": "node"}[f.rsplit(".", 1)[-1]]
            out.append({"cmd": f"{runner} {f}", "why": "a build script in the project"})
    seen, uniq = set(), []
    for s in out:
        if s["cmd"] not in seen:
            seen.add(s["cmd"])
            uniq.append(s)
    return uniq[:8]


def smoke_suggestions(tree: Tree, info: dict) -> list[str]:
    out = []
    name = info.get("name")
    first_bin = next(iter(info.get("scripts") or info.get("bins") or {}), None)
    vcmd = f"{first_bin} --version" if first_bin else "<the tool> --version"
    eco = info.get("ecosystem")
    if eco == "python":
        out.append(f"--install pip --name {name} -- {vcmd}")
    if eco == "node" and not info.get("private"):
        out.append(f"--install npm -- {vcmd}")
    if eco == "rust":
        out.append(f"--install binary --artifact {first_bin or '<binary>'} -- {vcmd}")
    if eco == "go":
        out.append(f"--install binary -- {vcmd}")
    for f in ("install.sh", "install.ps1", "install.py"):
        if tree.has(f):
            out.append(f"--install script --script {f} --env <its test settings> -- <what a user runs next>")
    out.append("--install archive -- <the unpacked tool> --version   (for a zip or tarball)")
    return out


def detect_shape(top: Path, pkg: str) -> dict:
    tree = Tree(top)
    info = project_info(tree, pkg)
    remote = pick_remote(top)
    gh = github_of(remote_url(top, remote))
    sites = find_sites(tree, info, gh)
    src = source_site(sites)
    tags = version_tags(top, info.get("name") if pkg else None)
    merged = [t for t in tags if t["merged"]]
    last = merged[0] if merged else None
    newest = tags[0] if tags else None
    if last:
        last = dict(last, commits_since=int(kit.git(top, "rev-list", "--count", f"{last['tag']}..HEAD").strip() or 0))
    total = int(kit.git(top, "rev-list", "--count", "HEAD").strip() or 0)
    workflows = read_workflows(tree)
    sign_cfg = kit.git(top, "config", "--get", "tag.gpgSign", check=False).strip().lower() == "true"
    commit_sign = kit.git(top, "config", "--get", "commit.gpgSign", check=False).strip().lower() == "true"
    return {
        "info": info, "sites": sites, "source": src, "remote": remote, "github": list(gh) if gh else None,
        "push_url": remote_url(top, remote, push=True),
        "last": last, "newest_tag": newest if newest and newest is not (merged[0] if merged else None) else None,
        "total_commits": total, "tag_prefix": last["prefix"] if last else (tags[0]["prefix"] if tags else "v"),
        "workflows": workflows, "builds": build_suggestions(tree, info, workflows),
        "smokes": smoke_suggestions(tree, info), "changelog": changelog_file(tree, pkg),
        "signing": {"tags": sign_cfg or bool(last and tag_signed(top, last["tag"])), "commits": commit_sign},
        "others": other_packages(tree, pkg),
    }


def current_version(shape: dict) -> Ver | None:
    src = shape.get("source")
    if src:
        return Ver.parse(src["version"])
    return Ver.parse(shape["last"]["version"]) if shape.get("last") else None


def ci_publishers(shape: dict) -> list[dict]:
    return [w for w in shape.get("workflows") or [] if (w["tags"] or w["on_release"]) and w["publishes"]]


# ---------------------------------------------------------------- the run's own state

def bump_state(ws: Path) -> dict:
    return rj(ws, "bump.json", {}) or {}


def release_ref(ws: Path, cfg: dict) -> str:
    b = bump_state(ws)
    return b["branch"] if b.get("applied") else "HEAD"


def target_version(ws: Path, cfg: dict, shape: dict | None = None) -> Ver | None:
    b = bump_state(ws)
    if b.get("to"):
        return Ver.parse(b["to"])
    shape = shape or rj(ws, "shape.json", {})
    src = shape.get("source")
    return Ver.parse(src["version"]) if src else None


def tag_for(ws: Path, shape: dict, v: Ver) -> str:
    b = bump_state(ws)
    return b.get("tag") or f"{shape.get('tag_prefix', 'v')}{v}"


def repo_state(top: Path) -> dict:
    refs = {}
    for line in kit.git(top, "for-each-ref", "--format=%(refname)%09%(objectname)", "refs/tags", "refs/remotes",
                        "refs/heads", check=False).splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            refs[k] = v
    return {"head": rev_sha(top, "HEAD"), "branch": current_branch(top), "refs": refs, "status": kit.git_status(top)}


def save_guard(ws: Path, top: Path, why: str) -> None:
    sj(ws, "guard.json", {"at": kit.now(), "why": why, "state": repo_state(top)})


def guard_findings(ws: Path, cfg: dict) -> tuple[list[str], list[str]]:
    """(problems, notes): what changed in the real project since this run last changed it."""
    top = Path(cfg["repo"])
    old = (rj(ws, "guard.json", {}) or {}).get("state")
    if not old:
        return [], ["no starting state recorded"]
    new = repo_state(top)
    b = bump_state(ws)
    rel = b.get("branch") if b.get("applied") else None
    orig = cfg.get("branch")
    problems, notes = [], []
    for ref in sorted(set(old["refs"]) | set(new["refs"])):
        a, z = old["refs"].get(ref), new["refs"].get(ref)
        if a == z:
            continue
        short = ref.split("/", 2)[-1]
        if ref.startswith("refs/tags/"):
            what = "made" if a is None else ("deleted" if z is None else "moved")
            problems.append(f"tag {short} was {what} (only publish makes tags)")
        elif ref.startswith("refs/remotes/"):
            problems.append(f"{short} changed from {(a or 'nothing')[:10]} to {(z or 'nothing')[:10]}: something pushed or fetched")
        elif ref == f"refs/heads/{orig}" and orig != rel:
            problems.append(f"your branch {orig} moved from {(a or 'nothing')[:10]} to {(z or 'nothing')[:10]}: "
                            "something committed to it")
        elif rel and ref == f"refs/heads/{rel}":
            if a and z and kit.git_ok(top, "merge-base", "--is-ancestor", a, z):
                n = kit.git(top, "rev-list", "--count", f"{a}..{z}").strip()
                notes.append(f"the release branch {rel} moved forward by {n} commit(s)")
            else:
                problems.append(f"the release branch {rel} was rewritten or deleted")
        else:
            notes.append(f"branch {short} {'was made' if a is None else ('was deleted' if z is None else 'moved')}")
    if old["branch"] != new["branch"]:
        notes.append(f"the checkout moved from {old['branch'] or 'a detached HEAD'} to {new['branch'] or 'a detached HEAD'}")
    if old["status"] != new["status"]:
        changed = [ln for ln in new["status"].splitlines() if ln not in old["status"].splitlines()]
        notes.append("uncommitted changes differ from before" + (": " + ", ".join(ln[3:] for ln in changed[:8]) if changed else ""))
    return problems, notes


# ---------------------------------------------------------------- AI attribution

ALWAYS = [
    (re.compile(r"co-authored-by\s*:", re.I), "a Co-Authored-By trailer"),
    (re.compile(r"noreply@anthropic\.com", re.I), "Anthropic's noreply address"),
    (re.compile("\U0001F916"), "the robot emoji"),
    (re.compile(r"\bgenerated\s+(?:with|by|using)\b[^\n]{0,40}?\b(?:claude|anthropic|chatgpt|openai|gpt-?\d\w*|copilot|"
                r"gemini|codex|cursor|llms?|an?\s+ai|ai(?:\s+assistant)?)\b", re.I), "a 'generated with' credit"),
    (re.compile(r"\b(?:written|drafted|authored|created|made|produced|assisted|co-written)\s+(?:with|by|using)\s+"
                r"(?:the\s+help\s+of\s+)?(?:claude|chatgpt|gpt-?\d\w*|copilot|gemini|an?\s+ai\b|ai\s+assistant|an?\s+llm)",
                re.I), "an AI credit"),
    (re.compile(r"claude\.ai/code|claude\.com/claude-code", re.I), "a Claude Code link"),
]
NAMES = re.compile(r"\b(claude|anthropic)\b", re.I)


def attribution_hits(text: str, where: str, names_ok: bool = False) -> list[dict]:
    """Lines that credit an AI. Claude or Anthropic by name counts too, unless the project is about them."""
    hits = []
    for n, line in enumerate(lines_of(text or ""), 1):
        for rx, why in ALWAYS:
            if rx.search(line):
                hits.append({"where": where, "line": n, "text": line.strip()[:200], "why": why})
                break
        else:
            if not names_ok and NAMES.search(line):
                hits.append({"where": where, "line": n, "text": line.strip()[:200], "why": "names Claude or Anthropic"})
    return hits


def names_allowed(tree: Tree) -> bool:
    """A project whose README already names Claude or Anthropic is about them: the names alone aren't a credit."""
    for f in tree.files():
        if "/" not in f and f.lower().startswith("readme") and NAMES.search(tree.read(f) or ""):
            return True
    return False


# ---------------------------------------------------------------- changes since the last release

def commits_between(top: Path, since: str | None, to: str) -> list[dict]:
    rng = [f"{since}..{to}"] if since else [to]
    out = kit.git(top, "log", "--format=%x1e%H%x1f%h%x1f%an%x1f%ad%x1f%P%x1f%s%x1f%b%x1f", "--date=short",
                  "--name-only", "--no-renames", *rng)
    commits = []
    for rec in out.split("\x1e")[1:]:
        parts = rec.split("\x1f")
        if len(parts) < 8:
            continue
        sha, short, author, date, parents, subject, body = parts[:7]
        files = [f.strip() for f in parts[7].splitlines() if f.strip()]
        commits.append({"sha": sha, "short": short, "author": author, "date": date, "subject": subject.strip(),
                        "body": body.strip(), "files": files, "merge": len(parents.split()) > 1})
    return commits


def diff_tokens(top: Path, since: str | None, to: str, limit: int = 400) -> dict:
    rng = [f"{since}..{to}"] if since else [to]
    r = subprocess.run(["git", "-C", str(top), "log", "-p", "--no-color", "--no-ext-diff", "--format=%x1e%H",
                        f"--max-count={limit}", *rng], capture_output=True)
    text = r.stdout.decode("utf-8", "replace")
    out = {}
    for rec in text.split("\x1e")[1:]:
        sha, _, patch = rec.partition("\n")
        toks, size, skip = set(), 0, False
        for ln in patch.split("\n"):
            if ln.startswith("diff --git "):   # the changelog's own lines can't be the evidence for its entries
                skip = bool(CHANGELOG_NAME.match(ln.rsplit("/", 1)[-1].strip()))
                continue
            if skip or ln.startswith(("+++", "---")) or not ln.startswith(("+", "-")):
                continue
            size += len(ln)
            if size > 200_000:
                break
            toks.update(t.lower() for t in re.findall(r"--?[A-Za-z][\w-]+|[A-Za-z_][\w.]{3,}", ln))
        out[sha.strip()] = sorted(toks)[:4000]
    return out


def area(path: str) -> str:
    low = path.lower()
    parts = low.split("/")
    base = parts[-1]
    if parts[0] in ("tests", "test", "spec", "testing") or re.search(r"(^|/)(test_[^/]+|[^/]+_test\.\w+|[^/]+\.test\.\w+)$", low):
        return "tests"
    if parts[0] in (".github", ".circleci", ".gitlab") or base in (".gitlab-ci.yml", "azure-pipelines.yml", ".travis.yml"):
        return "ci"
    if CHANGELOG_NAME.match(base) and len(parts) <= 2:
        return "changelog"
    if parts[0] in ("docs", "doc") or base.endswith((".md", ".rst")) or base in ("license", "license.txt", "license.md"):
        return "docs"
    if base in ("pyproject.toml", "setup.py", "setup.cfg", "package.json", "package-lock.json", "cargo.toml",
                "cargo.lock", "makefile", "go.mod", "go.sum", "uv.lock", "poetry.lock", "yarn.lock",
                "pnpm-lock.yaml", "requirements.txt") or base.endswith((".csproj", ".gemspec")):
        return "build"
    return "source"


def split_req(spec: str) -> tuple[str, str]:
    mt = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)", str(spec))
    if not mt:
        return str(spec).strip(), ""
    return re.sub(r"[-_.]+", "-", mt.group(1)).lower(), mt.group(3).strip()


def deps_of(tree: Tree | None, info: dict) -> dict:
    if tree is None:
        return {}
    pkg = info.get("package") or ""
    out = {}
    py = load_toml(tree.read(_j(pkg, "pyproject.toml")))
    proj = py.get("project") or {}
    for spec in proj.get("dependencies") or []:
        n, rest = split_req(spec)
        out[n] = rest or "*"
    for extra, specs in (proj.get("optional-dependencies") or {}).items():
        for spec in specs or []:
            n, rest = split_req(spec)
            out[f"{n} (extra {extra})"] = rest or "*"
    for n, v in ((py.get("tool") or {}).get("poetry", {}).get("dependencies") or {}).items():
        if n.lower() != "python":
            out[n.lower()] = str(v.get("version", v) if isinstance(v, dict) else v)
    try:
        pj = json.loads(tree.read(_j(pkg, "package.json")) or "{}")
    except json.JSONDecodeError:
        pj = {}
    for field, tag in (("dependencies", ""), ("peerDependencies", " (peer)"), ("optionalDependencies", " (optional)"),
                       ("devDependencies", " (dev)")):
        for n, v in (pj.get(field) or {}).items() if isinstance(pj, dict) else []:
            out[n + tag] = str(v)
    cargo = load_toml(tree.read(_j(pkg, "Cargo.toml")))
    for field, tag in (("dependencies", ""), ("dev-dependencies", " (dev)")):
        for n, v in (cargo.get(field) or {}).items():
            out[n + tag] = str(v.get("version", "path") if isinstance(v, dict) else v)
    req = tree.read(_j(pkg, "requirements.txt"))
    for ln in lines_of(req or ""):
        ln = ln.split("#")[0].strip()
        if ln and not ln.startswith("-"):
            n, rest = split_req(ln)
            out.setdefault(n, rest or "*")
    gomod = tree.read(_j(pkg, "go.mod")) or ""
    for mt in re.finditer(r"^\s*(?:require\s+)?([\w.-]+\.[\w./-]+)\s+(v\d\S*)", gomod, re.M):
        out[mt.group(1)] = mt.group(2)
    return out


def env_names(tree: Tree | None, info: dict) -> set:
    if tree is None:
        return set()
    out = set()
    cl = changelog_file(tree, info.get("package") or "")
    for f in doc_files(tree, info.get("package") or "", cl):
        out.update(re.findall(r"`([A-Z][A-Z0-9]*_[A-Z0-9_]+)`", tree.read(f) or ""))
    return out


def entry_commands(info: dict, override: list[str] | None) -> list[dict]:
    if override:
        return [{"name": kit.as_cmd(c)[0], "argv": kit.as_cmd(c)} for c in override if kit.as_cmd(c)]
    pkg = info.get("package") or ""
    out = []
    for name, target in (info.get("scripts") or {}).items():
        mod, _, func = str(target).partition(":")
        mod, func = mod.strip(), (func.strip() or "main").split()[0]
        roots = [_j(pkg, "src"), pkg or "."]
        code = ("import importlib, os, sys\n"
                f"sys.path[:0] = [os.path.abspath(p) for p in {roots!r}]\n"
                f"sys.argv[0] = {name!r}\n"
                f"obj = importlib.import_module({mod!r})\n"
                f"for part in {func!r}.split('.'):\n    obj = getattr(obj, part)\n"
                "rc = obj()\n"
                "sys.exit(rc if isinstance(rc, int) else 0)\n")
        out.append({"name": name, "argv": [sys.executable, "-c", code]})
    if shutil.which("node"):
        for name, path in (info.get("bins") or {}).items():
            if info.get("ecosystem") == "node":
                out.append({"name": name, "argv": ["node", _j(pkg, path.lstrip("./"))]})
    return out[:5]


OPT_RE = re.compile(r"(?<![\w-])(--[A-Za-z0-9][A-Za-z0-9_-]*|-[A-Za-z0-9])(?![\w-])")


def help_surface(root: Path, argv: list[str]) -> dict:
    r = kit.run_in(root, argv + ["--help"], timeout=60, log=False)["runs"][0]
    text = r["out"]
    res = {"ok": not kit.failed(r) or bool(OPT_RE.search(text)), "options": sorted(set(OPT_RE.findall(text))),
           "subs": {}, "error": "" if not kit.failed(r) else tail(text, 6)}
    subs = []
    mt = re.search(r"\{([A-Za-z][\w-]*(?:,[A-Za-z][\w-]*)+)\}", text)
    if mt:
        subs = mt.group(1).split(",")
    else:
        block = re.search(r"^(?:commands|subcommands|available commands)\s*:\s*\n((?:[ \t]+\S.*\n?)+)", text, re.I | re.M)
        if block:
            subs = re.findall(r"^[ \t]{2,}([a-z][\w-]*)\b", block.group(1), re.M)
    for sub in subs[:15]:
        rr = kit.run_in(root, argv + [sub, "--help"], timeout=60, log=False)["runs"][0]
        res["subs"][sub] = sorted(set(OPT_RE.findall(rr["out"])))
    return res


def cli_diff(cfg: dict, ws: Path, info_old: dict | None, info_new: dict, since: str | None, to: str,
             override: list[str] | None) -> list[dict]:
    cmds_new = entry_commands(info_new, override)
    if not cmds_new:
        return []
    cmds_old = {c["name"]: c for c in entry_commands(info_old, override)} if info_old else {}
    out = []
    surfaces = {}
    name = f"help-{secrets.token_hex(3)}"
    root = kit.make_sandbox(cfg, ws, name, rev=to, network=False)
    try:   # one sandbox: the release first, then the same copy checked out at the last release
        for c in cmds_new:
            surfaces[("new", c["name"])] = help_surface(root, c["argv"])
        old_sha = rev_sha(Path(cfg["repo"]), since) if since else None
        if old_sha and cmds_old:
            kit.git(root / "repo", "checkout", "-q", "-f", "--detach", old_sha)
            kit.git(root / "repo", "clean", "-q", "-fdx")
            for c in cmds_old.values():
                surfaces[("old", c["name"])] = help_surface(root, c["argv"])
    finally:
        kit.drop_sandbox(ws, cfg, name)
    for c in cmds_new:
        new = surfaces.get(("new", c["name"]))
        old = surfaces.get(("old", c["name"]))
        rec = {"command": c["name"], "argv": show(c["argv"])[:120], "new_ok": bool(new and new["ok"]),
               "old_ok": bool(old and old["ok"]), "existed": c["name"] in cmds_old, "added": [], "removed": [],
               "error": (new or {}).get("error") if not (new and new["ok"]) else ""}
        if new and new["ok"]:
            if old and old["ok"]:
                rec["added"] = sorted(set(new["options"]) - set(old["options"]))
                rec["removed"] = sorted(set(old["options"]) - set(new["options"]))
                for sub in sorted(set(new["subs"]) | set(old["subs"])):
                    a, z = set(old["subs"].get(sub, [])), set(new["subs"].get(sub, []))
                    if sub not in old["subs"]:
                        rec["added"].append(f"subcommand {sub}")
                    elif sub not in new["subs"]:
                        rec["removed"].append(f"subcommand {sub}")
                    else:
                        rec["added"] += [f"{sub} {o}" for o in sorted(z - a)]
                        rec["removed"] += [f"{sub} {o}" for o in sorted(a - z)]
        out.append(rec)
    return out


def recommend(cur: Ver | None, commits: list[dict], surface: dict) -> dict:
    breaking, features = [], []
    for c in commits:
        text = c["subject"] + "\n" + c["body"]
        if re.search(r"BREAKING[ -]CHANGE", text) or re.match(r"\w+(\([^)]*\))?!:", c["subject"]):
            breaking.append(f"commit {c['short']}: {c['subject']}")
        elif re.match(r"(feat|add|adds|new|support|introduce|allow)\b", c["subject"], re.I):
            features.append(f"commit {c['short']}: {c['subject']}")
    for rec in surface.get("cli", []):
        for o in rec["removed"]:
            breaking.append(f"{rec['command']} --help lost {o}")
        for o in rec["added"]:
            features.append(f"{rec['command']} --help gained {o}")
    for e in surface.get("entry_points", {}).get("removed", []):
        breaking.append(f"the {e} command is gone")
    for e in surface.get("entry_points", {}).get("added", []):
        features.append(f"a new {e} command")
    for f in surface.get("public_files", {}).get("removed", []):
        breaking.append(f"{f} was removed (breaking if users import or run it)")
    kind = "major" if breaking else ("minor" if features else "patch")
    note = ""
    if cur and cur.M == 0:
        if kind == "major":
            kind = "minor"
            note = ("on 0.x a minor bump is the breaking one: ranges like ^0.1.0 won't pick up 0.2.0 on their own. "
                    "Use major only to leave 0.x for 1.0.0")
        elif kind == "minor":
            note = "on 0.x a minor bump also counts as breaking for ^0.x ranges, so users opt in to it"
    return {"kind": kind, "breaking": breaking[:12], "features": features[:12], "note": note}


# ---------------------------------------------------------------- the changelog against the changes

STOP = set("""a an the and or but of to in on for with without by from at as is are was were be been being it its this
that these those now new add added adds adding fix fixed fixes fixing change changed changes changing update updated
updates updating remove removed removes removing support supports supported use uses used using make makes made when
which what who into than then there their they them can could will would should may might also more less not no yes so
if else only just any all some each every other our your you we release version via per one two first last before
after does doing done get gets got set sets work works way still""".split())


def stem(w: str) -> str:
    w = w.lower().strip("'-")
    if w.endswith("'s"):
        w = w[:-2]
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def words(text: str) -> set:
    return {stem(t) for t in re.findall(r"[A-Za-z][A-Za-z0-9_'-]{2,}", text) if t.lower() not in STOP} - STOP


def code_tokens(text: str) -> set:
    toks = set()
    for span in re.findall(r"`([^`]+)`", text):
        parts = span.split()
        if len(parts) == 1 and len(span) >= 4 and span.lower() not in STOP:
            toks.add(span.lower().strip("()"))
        toks.update(t.lower() for t in re.findall(r"--?[A-Za-z][\w-]*", span))
        toks.update(t.lower() for t in re.findall(r"[A-Za-z_][\w]*[._/][\w./]+", span))
    toks.update(t.lower() for t in re.findall(r"(?<![\w-])--[A-Za-z][\w-]+", text))
    toks.update(t.lower() for t in re.findall(
        r"\b[\w-]+\.(?:py|js|ts|mjs|cjs|rs|go|rb|java|cs|json|toml|ya?ml|md|sh|ps1|txt|cfg|ini)\b", text))
    return toks


def surface_items(changes: dict) -> list[dict]:
    s = changes.get("surface", {})
    items = []
    for rec in s.get("cli", []):
        for o in rec["added"]:
            items.append({"what": f"{rec['command']} --help: {o} added", "keys": [o.split()[-1]]})
        for o in rec["removed"]:
            items.append({"what": f"{rec['command']} --help: {o} removed", "keys": [o.split()[-1]]})
    for kind in ("added", "removed"):
        for e in s.get("entry_points", {}).get(kind, []):
            items.append({"what": f"command {e} {kind}", "keys": [e]})
        for f in s.get("public_files", {}).get(kind, []):
            items.append({"what": f"file {f} {kind}", "keys": [f, f.rsplit("/", 1)[-1]]})
        for v in s.get("env_vars", {}).get(kind, []):
            items.append({"what": f"setting {v} {kind} in the docs", "keys": [v]})
    deps = s.get("deps", {})
    for n, spec in deps.get("added", {}).items():
        items.append({"what": f"dependency added: {n} {spec}".strip(), "keys": [n.split()[0]]})
    for n, spec in deps.get("removed", {}).items():
        items.append({"what": f"dependency removed: {n}", "keys": [n.split()[0]]})
    for n, pair in deps.get("changed", {}).items():
        items.append({"what": f"dependency changed: {n} {pair[0]} -> {pair[1]}", "keys": [n.split()[0]]})
    return items


def map_changelog(entries: list[dict], changes: dict) -> dict:
    commits = [c for c in changes.get("commits", []) if not c.get("merge")]
    dtoks = changes.get("diff_tokens", {})
    cwords = {c["sha"]: words(c["subject"] + " " + c["body"]) for c in commits}
    df: dict = {}
    for ws_ in cwords.values():
        for w in ws_:
            df[w] = df.get(w, 0) + 1
    items = surface_items(changes)
    links = []
    for e in entries:
        etext = e["text"]
        ew, ec = words(etext), code_tokens(etext)
        prs = set(re.findall(r"#(\d+)\b", etext))
        for c in commits:
            ctext = (c["subject"] + "\n" + c["body"]).lower()
            why = None
            cprs = set(re.findall(r"#(\d+)\b", ctext))
            if prs & cprs:
                why = f"#{sorted(prs & cprs)[0]}"
            if not why:
                files = {f.lower() for f in c["files"]} | {f.rsplit("/", 1)[-1].lower() for f in c["files"]}
                toks = set(dtoks.get(c["sha"], []))
                for t in sorted(ec):
                    if t in files or t in toks or re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", ctext):
                        why = f"mentions {t}"
                        break
            if not why:
                shared = ew & cwords[c["sha"]]
                rare = [w for w in shared if len(w) >= 6 and df.get(w, 0) <= 1]
                if len(shared) >= 2 or rare:
                    why = "shares the words " + ", ".join(sorted(shared)[:4])
            if why:
                links.append({"entry": e["id"], "commit": c["sha"], "why": why})
        low = etext.lower()
        for k, it in enumerate(items):
            if any(re.search(rf"(?<![\w-]){re.escape(key.lower())}(?![\w-])", low) for key in it["keys"] if key):
                links.append({"entry": e["id"], "surface": k, "why": "names it"})
    by_entry = {e["id"]: [ln for ln in links if ln["entry"] == e["id"]] for e in entries}
    internal, unmatched_commits = [], []
    for c in commits:
        if any(ln.get("commit") == c["sha"] for ln in links):
            continue
        areas = {area(f) for f in c["files"]} or {"none"}
        if areas <= {"tests", "ci", "changelog", "none"}:
            internal.append({"sha": c["sha"], "short": c["short"], "subject": c["subject"],
                             "why": "only " + " and ".join(sorted(a for a in areas if a != "none") or ["nothing"])})
        else:
            unmatched_commits.append(c)
    unmatched_surface = [it for k, it in enumerate(items) if not any(ln.get("surface") == k for ln in links)]
    return {"links": links, "by_entry": by_entry, "items": items, "internal": internal,
            "entries_without_change": [e for e in entries if not by_entry[e["id"]]],
            "commits_without_entry": unmatched_commits, "surface_without_entry": unmatched_surface}


# ---------------------------------------------------------------- sandboxes with no project in them

def empty_box(cfg: dict, ws: Path, name: str, network: bool, env: dict | None = None) -> Path:
    """A sandbox like the kit's, but repo/ is an empty folder: no project source, no git.

    It also hides what a clean machine wouldn't have: the user's Python user site and
    PYTHONPATH, virtualenv and conda settings, pip and npm configuration, and global
    install folders, so the installed release is the only copy of the project it can find."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        fail("a sandbox name is letters, digits, dots, dashes and underscores")
    root = kit.box_path(cfg, name)
    if root.exists():
        fail(f"sandbox {name} already exists at {root}; pick another name, or run clean")
    root.mkdir(parents=True)
    kit.update_cfg(ws, lambda c: c.setdefault("sandboxes", {}).__setitem__(name, str(root)))
    try:
        for d in ("repo", "scratch", "logs", "artifacts"):
            (root / d).mkdir(exist_ok=True)
        spec = kit.sandbox_env(root, network, {**cfg.get("env", {}), **(env or {})})
        home = root / "home"
        spec["set"].update({
            "PYTHONNOUSERSITE": "1", "PYTHONUSERBASE": str(home / ".local"), "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_CONFIG_FILE": os.devnull, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1",
            "npm_config_prefix": str(home / ".npm-global"), "npm_config_cache": str(home / ".npm"),
            "npm_config_userconfig": str(home / ".npmrc"), "npm_config_update_notifier": "false",
            "CARGO_INSTALL_ROOT": str(home / ".cargo-install"), "GOBIN": str(home / "go-bin"),
            "GEM_HOME": str(home / ".gem"), "DOTNET_CLI_HOME": str(home),
        })
        spec["drop"] = list(spec["drop"]) + ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV", "CONDA_PREFIX",
                                             "CONDA_DEFAULT_ENV", "NODE_PATH", "PIP_FIND_LINKS", "PIP_INDEX_URL",
                                             "PIP_EXTRA_INDEX_URL", "PIP_TARGET", "PIP_PREFIX", "PIP_USER",
                                             "PIP_REQUIRE_VIRTUALENV", "npm_config_globalconfig"]
        kit.save_json(root / "env.json", spec)
        shutil.copy2(HERE / "run.py", root / "run.py")
    except BaseException:
        kit.drop_sandbox(ws, cfg, name)
        raise
    return root


def set_path(root: Path, dirs: list[Path], extra: dict | None = None) -> None:
    spec = kit.load_json(root / "env.json", {})
    current = os.environ.get("PATH") or os.environ.get("Path") or ""
    spec["set"]["PATH"] = os.pathsep.join([str(d) for d in dirs] + ([current] if current else []))
    spec["set"].update(extra or {})
    kit.save_json(root / "env.json", spec)


def safe_extract(archive: Path, dest: Path) -> None:
    low = archive.name.lower()
    if low.endswith((".zip", ".whl", ".jar", ".nupkg", ".vsix")):
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if n.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", n) or ".." in re.split(r"[\\/]", n):
                    fail(f"{archive.name} has an unsafe path in it: {n}")
            z.extractall(dest)
            if os.name != "nt":
                for zi in z.infolist():
                    mode = (zi.external_attr >> 16) & 0o777
                    if mode & 0o111 and not zi.is_dir():
                        os.chmod(dest / zi.filename, mode)
        return
    if not tarfile.is_tarfile(archive):
        fail(f"{archive.name} isn't a zip or tar archive")
    with tarfile.open(archive) as t:
        if hasattr(tarfile, "data_filter"):
            t.extractall(dest, filter="data")
            return
        members = []
        for m in t.getmembers():
            if m.name.startswith(("/", "\\")) or ".." in re.split(r"[\\/]", m.name) or m.issym() or m.islnk() or m.isdev():
                fail(f"{archive.name} has an unsafe entry in it: {m.name}")
            members.append(m)
        t.extractall(dest, members=members)


def script_argv(path: Path) -> list[str]:
    s = path.suffix.lower()
    if s == ".py":
        return [sys.executable, path.name]
    if s == ".sh":
        return ["sh", path.name]
    if s == ".ps1":
        shell = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"] if os.name == "nt" else ["pwsh", "-NoProfile", "-File"]
        return shell + [path.name]
    if s in (".cmd", ".bat"):
        return ["cmd", "/c", path.name]
    return ["./" + path.name if os.name != "nt" else path.name]


def pick(names: list[str], patterns: list[str] | None, suffixes: tuple) -> list[str]:
    if patterns:
        got = [n for n in names if any(fnmatch.fnmatch(n, p) or n == p for p in patterns)]
        if not got:
            fail(f"no artifact matches {', '.join(patterns)}; the build has: {', '.join(names)}")
        return got
    return [n for n in names if n.lower().endswith(suffixes)]


def pip_version(exe: str) -> tuple[int, int] | None:
    r = subprocess.run([exe, "-m", "pip", "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1"))
    mt = re.match(r"pip (\d+)\.(\d+)", r.stdout or "")
    return (int(mt.group(1)), int(mt.group(2))) if r.returncode == 0 and mt else None


def python_has(exe: str, module: str) -> bool:
    r = subprocess.run([exe, "-c", f"import importlib.util, sys; sys.exit(0 if importlib.util.find_spec({module!r}) else 1)"],
                       capture_output=True)
    return r.returncode == 0


# ---------------------------------------------------------------- commands: init, versions, changes

def print_shape(shape: dict, ws: Path) -> None:
    info = shape["info"]
    sites = shape["sites"]
    name = info.get("name") or Path(rj(ws, "config.json", {}).get("repo", ".")).name
    eco = info.get("ecosystem") or "no package manifest found"
    print(f"Package: {name} ({eco}" + (f", {info['manifest']}" if info.get("manifest") else "") + ")"
          + (f", in {info['package']}" if info.get("package") else ""))
    src = shape.get("source")
    vf = info.get("version_from") or ""
    if vf.startswith("tags"):
        print(f"Version: from the git tags, {vf[5:].strip('()') or 'tooling'}: the tag is the version, so there "
              "are no manifest sites to edit")
    elif src:
        print(f"Version: {src['version']}, from {src['file']}")
    elif shape.get("last"):
        print(f"Version: no manifest holds it; the last tag ({shape['last']['tag']}) is the version")
    else:
        print("Version: none found yet (a first release with no manifest version)")
    must = [s for s in sites if s["group"] != "example"]
    print(f"Version sites ({len(must)}):")
    for s in must:
        extra = ""
        if s["group"] == "changelog":
            extra = " (dated)" if s.get("dated") else " (not dated: unreleased)"
        print(f"  {s['file']}:{s['line']}  {s['version']}  {s['group']}, {s['kind']}{extra}  | {s['text'][:90]}")
    if not must:
        print("  none")
    ex = [s for s in sites if s["group"] == "example"]
    if ex:
        print(f"Examples, not bumped unless you pass --include-examples ({len(ex)}):")
        for s in ex[:10]:
            print(f"  {s['file']}:{s['line']}  {s['version']}  | {s['text'][:90]}")
    last = shape.get("last")
    if last:
        print(f"Last release: {last['tag']} ({last['date']}), {plural(last['commits_since'], 'commit')} since")
    else:
        print(f"Last release: none, so this is the first release; changes covers all {plural(shape['total_commits'], 'commit')}")
    if shape.get("newest_tag") and (not last or shape["newest_tag"]["tag"] != last["tag"]):
        print(f"  note: {shape['newest_tag']['tag']} is newer but isn't in this branch's history")
    if shape.get("changelog"):
        print(f"Changelog: {shape['changelog']}")
    else:
        print("Changelog: none found")
    eps = {**(info.get("scripts") or {}), **(info.get("bins") or {})}
    if eps:
        print("Entry points: " + ", ".join(f"{k} ({v})" for k, v in list(eps.items())[:6]))
    print("Build, likely commands:")
    for b in shape["builds"] or [{"cmd": "none found", "why": "pass yours to build"}]:
        print(f"  {b['cmd']}   ({b['why']})")
    ci = ci_publishers(shape)
    if ci:
        for w in ci:
            on = ("a tag push (" + ", ".join(w["tags"]) + ")") if w["tags"] else "a published release"
            print(f"Publish: CI does it. {w['file']} runs on {on} and " + "; ".join(x[1] for x in w["publishes"])
                  + (". It reads the changelog" if w["reads_changelog"] else "")
                  + (". It also runs " + ", ".join(r.rsplit("/", 1)[-1] for r in w["reusable"]) if w["reusable"] else "") + ".")
    else:
        other = [w["file"] for w in shape["workflows"]]
        print("Publish: by hand; no workflow publishes on a tag" + (f" ({', '.join(other)} found)" if other else ""))
    print("Smoke recipes that fit:")
    for s in shape["smokes"]:
        print(f"  smoke -w <run> {s}")
    sign = shape["signing"]
    print(f"Signing: tags {'are signed (the plan uses git tag -s)' if sign['tags'] else 'are not signed'}"
          + ("; commits are signed by your git config" if sign["commits"] else ""))
    if shape.get("others"):
        print("Other packages here (pick one with init --package <folder>): " + ", ".join(shape["others"][:10]))
    print(f"Remote: {shape['remote'] or 'none'}" + (f" (GitHub {'/'.join(shape['github'])})" if shape.get("github") else ""))


def cmd_init(a) -> None:
    path = Path(a.repo).expanduser().resolve()
    if not path.is_dir():
        fail(f"no folder {path}")
    top, is_git = kit.find_top(path)
    if not is_git or not kit.has_head(top):
        fail("killer-release needs a git repository with at least one commit: releases are cut from commits and tags")
    pkg = ""
    if a.package:
        p = (top / a.package).resolve()
        try:
            pkg = p.relative_to(top).as_posix()
        except ValueError:
            fail(f"{a.package} isn't inside {top}")
        pkg = "" if pkg == "." else pkg
        if not p.is_dir():
            fail(f"no folder {p}")
    extra = {"package": pkg, "landing": a.landing, "notes_mode": a.notes, "test_cmd": a.test or None,
             "goal_branch": a.branch or None}
    ws, cfg, _ = kit.init_run(SKILL, str(top), slug=a.slug or "release", target=a.target, network=a.network,
                              include=a.include or [], env=a.env or [], workspace=a.workspace, force=a.force,
                              extra=extra, quiet=True)
    if a.force:
        for f in ("shape.json", "bump.json", "changes.json", "changelog.json", "build.json", "smoke.json", "check.json",
                  "publish.json", "publish.md", "notes.md", "findings.json", "review.json", "installer.json",
                  "decisions.json", "waiting.json", "report.md", "summary.json"):
            (ws / f).unlink(missing_ok=True)
        kit.rmtree(ws / "artifacts")
    shape = detect_shape(top, pkg)
    info = shape["info"]
    if not pkg and not info.get("manifest") and len(shape["others"]) > 1:
        print("\n".join(f"  {o}" for o in shape["others"]))
        fail("there's no manifest at the top but several packages below it; pick one with --package <folder>")
    sj(ws, "shape.json", shape)
    save_guard(ws, top, "init")
    print(f"Run folder: {ws}")
    print(f"Project: {top} ({cfg['branch'] or 'detached'} at {(cfg['head'] or '')[:10]})")
    print_shape(shape, ws)
    print(f"Landing: {a.landing} · notes: {a.notes} · network in sandboxes: {'allowed' if a.network else 'off'}"
          + (f" · branch from killer-goal: {a.branch}" if a.branch else ""))
    print(f"Next: release.py changes -w \"{ws}\"")


def cmd_versions(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    shape = rj(ws, "shape.json", {})
    rev = a.rev
    tree = Tree(top, rev)
    info = project_info(tree, cfg.get("package") or "")
    gh = tuple(shape["github"]) if shape.get("github") else None
    sites = find_sites(tree, info, gh)
    b = bump_state(ws)
    if b.get("to"):
        want, want_from = Ver.parse(b["to"]), "the release is"
    else:
        src = source_site(sites)
        if src:
            want, want_from = Ver.parse(src["version"]), f"{src['file']} says"
        elif shape.get("last"):
            want, want_from = Ver.parse(shape["last"]["version"]), f"the last release ({shape['last']['tag']}) is"
        else:
            want, want_from = None, ""
    problems, notes = compare_sites(sites, want, want_from)
    must = [s for s in sites if s["group"] != "example"]
    on = current_branch(top)
    if not rev and b.get("applied") and on != b["branch"]:
        notes.append(f"you're on {on or 'a detached HEAD'}, not the release branch; versions --rev {b['branch']} reads that")
    print(f"Version sites in {'the working tree' if not rev else rev}: {len(must)}" + (f", checked against {want}" if want else ""))
    for s in must:
        ok = Ver.parse(s["version"]) == want
        print(f"  {'ok  ' if ok else 'DIFF'}  {s['file']}:{s['line']}  {s['version']}  ({s['group']})")
    for s in [s for s in sites if s["group"] == "example"]:
        print(f"  ex    {s['file']}:{s['line']}  {s['version']}  (an example; not compared)")
    for n in notes:
        print(f"note: {n}")
    if b.get("to") and not b.get("applied"):
        print(f"note: the bump to {b['to']} is planned, not applied yet")
    sj(ws, "versions.json", {"at": kit.now(), "rev": rev, "want": str(want) if want else None, "sites": sites,
                             "problems": problems})
    if problems:
        print(f"FAIL: {plural(len(problems), 'site')} disagree{'s' if len(problems) == 1 else ''}:")
        for p in problems:
            print(f"  {p}")
        sys.exit(1)
    print("PASS: every version site agrees." if want else "No version to compare against yet.")


def cmd_changes(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    shape = rj(ws, "shape.json", {})
    since = a.since or ((shape.get("last") or {}).get("tag"))
    if since and not rev_sha(top, since):
        fail(f"{since} isn't a commit or tag here")
    to = a.to or release_ref(ws, cfg)
    commits = commits_between(top, since, to)
    b = bump_state(ws)
    site_files = {s["file"] for s in shape.get("sites", [])} | ({shape["changelog"]} if shape.get("changelog") else set())
    ours = []
    if b.get("applied") and b.get("base"):
        mine = {c["sha"] for c in commits_between(top, b["base"], to)}
        ours = [c for c in commits if c["sha"] in mine and (RUN_COMMIT.match(c["subject"]) or set(c["files"]) <= site_files)]
    commits = [c for c in commits if c not in ours]
    toks = diff_tokens(top, since, to)
    base_tree = since or empty_tree(top)
    ns = kit.git(top, "diff", "--name-status", "--no-renames", "-z", base_tree, to)
    parts = kit.split0(ns)
    files = [{"status": parts[i], "path": parts[i + 1]} for i in range(0, len(parts) - 1, 2)]
    areas: dict = {}
    for f in files:
        areas.setdefault(area(f["path"]), []).append(f["path"])
    pkg = cfg.get("package") or ""
    old_tree = Tree(top, since) if since else None
    new_tree = Tree(top, to)
    info_new = project_info(new_tree, pkg)
    info_old = project_info(old_tree, pkg) if old_tree else None
    d_old, d_new = deps_of(old_tree, info_old or info_new), deps_of(new_tree, info_new)
    deps = {"added": {k: v for k, v in d_new.items() if k not in d_old},
            "removed": {k: v for k, v in d_old.items() if k not in d_new},
            "changed": {k: [d_old[k], d_new[k]] for k in d_new if k in d_old and d_old[k] != d_new[k]}}
    ep_old = set((info_old or {}).get("scripts", {})) | set((info_old or {}).get("bins", {}))
    ep_new = set(info_new.get("scripts", {})) | set(info_new.get("bins", {}))
    pub = {"added": [f["path"] for f in files if f["status"] == "A" and area(f["path"]) == "source"][:40],
           "removed": [f["path"] for f in files if f["status"] == "D" and area(f["path"]) == "source"][:40]}
    e_old, e_new = env_names(old_tree, info_old or info_new), env_names(new_tree, info_new)
    surface = {"deps": deps, "entry_points": {"added": sorted(ep_new - ep_old) if since else [],
                                              "removed": sorted(ep_old - ep_new)},
               "public_files": pub if since else {"added": [], "removed": []},
               "env_vars": {"added": sorted(e_new - e_old) if since else [], "removed": sorted(e_old - e_new)}}
    surface["cli"] = [] if a.no_help else cli_diff(cfg, ws, info_old, info_new, since, to, a.help_cmd)
    rec = recommend(current_version(shape), commits, surface)
    data = {"at": kit.now(), "since": since, "to": to, "to_sha": rev_sha(top, to), "first_release": since is None,
            "commits": commits, "run_commits": [c["sha"] for c in ours], "files": files, "areas": areas,
            "surface": surface, "recommend": rec, "diff_tokens": toks}
    sj(ws, "changes.json", data)
    head = (f"Changes since {since}" + (f" ({shape['last']['date']})" if shape.get("last") and since == shape["last"]["tag"] else "")
            if since else "Changes in all of history (first release)")
    print(f"{head}: {plural(len(commits), 'commit')}, {plural(len(files), 'file')} changed")
    for c in commits[:a.limit]:
        print(f"  {c['short']}  {c['date']}  {c['subject']}" + ("  (merge)" if c["merge"] else ""))
    if len(commits) > a.limit:
        print(f"  ... and {len(commits) - a.limit} more (all in changes.json)")
    if ours:
        print(f"  (left out: {plural(len(ours), 'commit')} this run made)")
    if areas:
        print("Files by area: " + ", ".join(f"{k} {len(v)}" for k, v in sorted(areas.items())))
        for k in ("source", "build"):
            if areas.get(k):
                print(f"  {k}: " + ", ".join(areas[k][:8]) + (f", and {len(areas[k]) - 8} more" if len(areas[k]) > 8 else ""))
    print("User-visible surface:")
    shown = 0
    for r in surface["cli"]:
        if r["added"] or r["removed"]:
            print(f"  {r['command']} --help: " + "; ".join([f"added {x}" for x in r["added"]] + [f"removed {x}" for x in r["removed"]]))
            shown += 1
        elif not r["new_ok"]:
            print(f"  {r['command']} --help didn't run at {to}: {r['error'].splitlines()[-1] if r['error'] else 'no output'}")
            shown += 1
    if not a.no_help and not surface["cli"]:
        print("  CLI options: not compared (no entry point found; name one with --help-cmd \"<command>\")")
    for k, label in (("added", "dependency added"), ("removed", "dependency removed")):
        for n, v in deps[k].items():
            print(f"  {label}: {n} {v}".rstrip())
            shown += 1
    for n, (x, y) in deps["changed"].items():
        print(f"  dependency changed: {n} {x} -> {y}")
        shown += 1
    for k in ("added", "removed"):
        for e in surface["entry_points"][k]:
            print(f"  command {e} {k}")
            shown += 1
        for f in surface["public_files"][k][:10]:
            print(f"  file {k}: {f}")
            shown += 1
        for v in surface["env_vars"][k]:
            print(f"  setting {k} in the docs: {v}")
            shown += 1
    if not shown:
        print("  nothing the script could detect")
    dirty = [ln for ln in kit.git_status(top).splitlines() if ln.strip()]
    if not commits:
        print(f"Suggested release: none yet: nothing is committed since {since or 'the start'}")
    else:
        print(f"Suggested release: {rec['kind']}" + (f" ({rec['breaking'][0]})" if rec["breaking"] else
                                                     (f" ({rec['features'][0]})" if rec["features"] else
                                                      " (no new options, commands or feature commits found)")))
    if rec["note"] and commits:
        print(f"note: {rec['note']}")
    if dirty and to == "HEAD":
        print(f"note: {plural(len(dirty), 'uncommitted change')} in the working tree aren't part of any release until "
              "they're committed")
    print(f"Saved {ws / 'changes.json'}")


# ---------------------------------------------------------------- bump

def free_branch(top: Path, name: str) -> str:
    if not kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{name}"):
        return name
    n = 2
    while kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{name}-{n}"):
        n += 1
    return f"{name}-{n}"


def plan_bump(top: Path, cfg: dict, shape: dict, kind: str, pre: str | None, examples: bool) -> dict:
    tree = Tree(top)
    pkg = cfg.get("package") or ""
    info = project_info(tree, pkg)
    gh = tuple(shape["github"]) if shape.get("github") else None
    sites = find_sites(tree, info, gh)
    src = source_site(sites)
    cur_src = Ver.parse(src["version"]) if src else None
    last = Ver.parse(shape["last"]["version"]) if shape.get("last") else None
    base = last or cur_src or Ver(0, 0, 0)
    style = (cur_src.style if cur_src and (cur_src.pre or cur_src.post is not None) else
             ("pep440" if info.get("ecosystem") == "python" else "semver"))
    new = next_version(base, kind, pre, style)
    if last and not new > last:
        fail(f"{new} isn't newer than the last release, {last} ({shape['last']['tag']})")
    if cur_src and new < cur_src:
        fail(f"{src['file']} already says {cur_src}, which is newer than {new}; to finish that bump, run bump {cur_src}")
    tag = f"{shape.get('tag_prefix', 'v')}{new}"
    notes = []
    if base.M == 0 and kind == "minor":
        notes.append("on 0.x a minor bump is the breaking one: ranges like ^0.1.0 won't pick it up on their own")
    if base.M == 0 and new.M == 1:
        notes.append("1.0.0 leaves 0.x: from now on a major bump is the breaking one")
    if (info.get("version_from") or "").startswith("tags"):
        notes.append(f"the version comes from the git tags ({info['version_from'][5:].strip('()')}): the bump is the "
                     f"tag {tag} itself, made by publish; build with --tag-in-sandbox so the build sees it")
    groups = {"manifest", "mirror", "pin"} | ({"example"} if examples else set())
    edits: dict = {}
    for s in sites:
        if s["group"] in groups and Ver.parse(s["version"]) != new:
            edits.setdefault(s["file"], []).append(s)
    new_text, old_text = {}, {}
    for f, ss in edits.items():
        raw = (top / f).read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw[3 if bom else 0:].decode("utf-8", "surrogateescape")
        ls = text.split("\n")
        for s in sorted(ss, key=lambda s: (s["line"], -s["span"][0])):
            ln = ls[s["line"] - 1]
            cr = "\r" if ln.endswith("\r") else ""
            body = ln[:-1] if cr else ln
            if body[s["span"][0]:s["span"][1]] != s["version"]:
                fail(f"{f}:{s['line']} changed while planning; run bump again")
            ls[s["line"] - 1] = body[:s["span"][0]] + str(new) + body[s["span"][1]:] + cr
        old_text[f], new_text[f] = (bom, text), (bom, "\n".join(ls))
    cl = changelog_file(tree, pkg)
    if cl:
        bom0, text0 = old_text.get(cl, (False, None))
        if text0 is None:
            raw = (top / cl).read_bytes()
            bom0 = raw.startswith(b"\xef\xbb\xbf")
            text0 = raw[3 if bom0 else 0:].decode("utf-8", "surrogateescape")
        edited, cn = changelog_edit(new_text.get(cl, (bom0, text0))[1], new, kit.today(), tag)
        notes += [f"{cl}: {n}" for n in cn]
        if edited != text0:
            old_text[cl], new_text[cl] = (bom0, text0), (bom0, edited)
    locks = [s for s in sites if s["group"] == "lock" and Ver.parse(s["version"]) != new]
    skipped_ex = [s for s in sites if s["group"] == "example" and not examples]
    return {"from": str(base), "from_source": str(cur_src) if cur_src else None, "to": str(new), "kind": kind,
            "pre": pre, "tag": tag, "notes": notes, "files": sorted(new_text), "locks": locks, "examples": skipped_ex,
            "old": old_text, "new": new_text, "info": info}


def print_diff(plan: dict) -> int:
    n = 0
    for f in plan["files"]:
        a = [ln.rstrip("\r") + "\n" for ln in plan["old"][f][1].split("\n")]
        b = [ln.rstrip("\r") + "\n" for ln in plan["new"][f][1].split("\n")]
        for line in difflib.unified_diff(a, b, f"a/{f}", f"b/{f}", n=1):
            sys.stdout.write(line if line.endswith("\n") else line + "\n")
            n += 1
    return n


def update_locks(cfg: dict, ws: Path, top: Path, plan: dict) -> tuple[list[str], list[str]]:
    """Lock files that record the project's own version, rewritten by their own tool in a sandbox."""
    done, left = [], []
    pkg = cfg.get("package") or ""
    tools = {"package-lock.json": ("npm", ["npm", "version", plan["to"], "--no-git-tag-version", "--allow-same-version",
                                           "--ignore-scripts"], "npm install --package-lock-only"),
             "npm-shrinkwrap.json": ("npm", ["npm", "version", plan["to"], "--no-git-tag-version", "--allow-same-version",
                                             "--ignore-scripts"], "npm install --package-lock-only"),
             "Cargo.lock": ("cargo", ["cargo", "update", "--workspace", "--offline"], "cargo update --workspace"),
             "uv.lock": ("uv", ["uv", "lock", "--offline"], "uv lock")}
    for f in sorted({s["file"] for s in plan["locks"]}):
        base = f.rsplit("/", 1)[-1]
        tool, argv, manual = tools.get(base, (None, None, None))
        if not tool or not shutil.which(tool):
            left.append(f"{f} still records {', '.join(sorted({s['version'] for s in plan['locks'] if s['file'] == f}))}; "
                        f"update it with: {manual or 'its tool'}")
            continue
        name = f"lock-{secrets.token_hex(3)}"
        root = kit.make_sandbox(cfg, ws, name, network=False)
        try:
            cwd = f.rsplit("/", 1)[0] if "/" in f else None
            r = kit.run_in(root, argv, timeout=300, cwd=cwd, log=False)["runs"][0]
            got = root / "repo" / f
            if kit.failed(r) or not got.is_file():
                left.append(f"{f}: {show(argv)} failed in a sandbox: {tail(r['out'], 3)}; update it with: {manual}")
                continue
            text = got.read_text(encoding="utf-8", errors="replace")
            check_sites: list[dict] = []
            if base.endswith(".json"):
                json_version_sites(check_sites, f, text, "lock", "version", lock_name=plan["info"].get("name"))
            else:
                check_sites = [s for s in find_sites(Tree(root / "repo"), plan["info"]) if s["file"] == f]
            if not check_sites or any(Ver.parse(s["version"]) != Ver.parse(plan["to"]) for s in check_sites):
                left.append(f"{f}: {show(argv)} ran but the file still doesn't record {plan['to']}; update it with: {manual}")
                continue
            (top / f).write_bytes(got.read_bytes())
            done.append(f)
        finally:
            kit.drop_sandbox(ws, cfg, name)
    return done, left


def cmd_bump(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    shape = rj(ws, "shape.json", {})
    prior = bump_state(ws)
    if a.apply and cfg.get("landing") == "check":
        fail("this run is check-only (init --landing check), so it changes nothing; start a new run to prepare the release")
    if prior.get("applied"):
        on = current_branch(top)
        if on != prior["branch"]:
            fail(f"this run already cut {prior['to']} on {prior['branch']}; switch to it (git switch {prior['branch']}) "
                 "or start a new run")
    plan = plan_bump(top, cfg, shape, a.kind, a.pre, a.include_examples)
    if prior.get("applied") and plan["to"] != prior["to"]:
        fail(f"this run already cut {prior['to']} on {prior['branch']}; start a new run for {plan['to']}")
    branch = prior.get("branch") or a.branch or cfg.get("goal_branch") or f"killer-release/v{plan['to']}"
    if cfg.get("package") and not (a.branch or cfg.get("goal_branch") or prior.get("branch")):
        branch = f"killer-release/{Path(cfg['package']).name}-v{plan['to']}"
    print(f"Version: {plan['from']} -> {plan['to']} ({a.kind}" + (f", pre-release {a.pre}" if a.pre else "") + ")"
          + (f"; {shape['info'].get('manifest') or 'the manifest'} says {plan['from_source']}" if plan["from_source"]
             and plan["from_source"] != plan["from"] else ""))
    print(f"Tag: {plan['tag']}")
    for n in plan["notes"]:
        print(f"note: {n}")
    if plan["files"]:
        print(f"Edits ({plural(len(plan['files']), 'file')}):")
        print_diff(plan)
    else:
        print("Edits: none; every site already says " + plan["to"])
    for s in plan["examples"]:
        print(f"Not changed: {s['file']}:{s['line']} {s['version']} is an example (--include-examples bumps it)")
    for s in plan["locks"]:
        print(f"Lock file: {s['file']}:{s['line']} records {s['version']}; --apply updates it with its own tool, or lists it")
    record = {k: plan[k] for k in ("from", "from_source", "to", "kind", "pre", "tag", "notes", "files")}
    record["branch_name"] = branch
    if not a.apply:
        if not prior.get("applied"):
            sj(ws, "bump.json", {**record, "applied": False, "at": kit.now()})
        print(f"Preview only: nothing changed. Run again with --apply to commit this on {branch}.")
        return
    if prior.get("applied") and not plan["files"]:
        print(f"Already at {plan['to']} on {prior['branch']}; nothing to do.")
        return
    dirty = [ln for ln in kit.git(top, "status", "--porcelain=v1", "--untracked-files=no").splitlines() if ln.strip()]
    if dirty:
        fail("these tracked files have uncommitted changes: " + ", ".join(ln[3:] for ln in dirty[:10])
             + ". A release is cut from commits: commit them where they belong (or stash them), then run bump --apply again")
    orig = current_branch(top)
    base = rev_sha(top, "HEAD")
    made = False
    touched = plan["files"] + sorted({s["file"] for s in plan["locks"]})

    def undo():
        kit.git(top, "reset", "-q", "--", *touched, check=False)
        kit.git(top, "checkout", "-q", "--", *touched, check=False)
        if made:
            kit.git(top, "switch", "-q", *([orig] if orig else ["--detach", base]), check=False)
            kit.git(top, "branch", "-q", "-D", branch, check=False)
    if branch != orig:
        if (a.branch or cfg.get("goal_branch")) and kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{branch}"):
            fail(f"branch {branch} exists and isn't the current one; switch to it first (git switch {branch}), or name another")
        if not kit.git_ok(top, "check-ref-format", "--branch", branch):
            fail(f"{branch} isn't a valid branch name")
        if not (a.branch or cfg.get("goal_branch")) and not prior.get("branch"):
            branch = free_branch(top, branch)
        kit.git(top, "switch", "-q", "-c", branch)
        made = True
    try:
        for f in plan["files"]:
            bom, text = plan["new"][f]
            (top / f).write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8", "surrogateescape"))
        done, left = update_locks(cfg, ws, top, plan) if plan["locks"] else ([], [])
        files = plan["files"] + done
        if not files:
            fail("nothing to commit: every site already says " + plan["to"])
        kit.git(top, "add", "--", *files)
        r = subprocess.run(["git", "-C", str(top), "commit", "-q", "-m", f"Release {plan['to']}: bump the version"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout).strip())
    except SystemExit:
        undo()
        print("error: nothing was changed", file=sys.stderr)
        raise
    except (RuntimeError, OSError) as e:
        undo()
        fail(f"the bump commit failed, so nothing was changed: {e}")
    sha = rev_sha(top, "HEAD")
    for item in left:
        print(f"note: {item}")
        mutate(ws, "decisions.json", [], lambda d, t=item: d.append({"text": f"Left for you: {t}", "at": kit.now()}))
    sj(ws, "bump.json", {**record, "applied": True, "branch": branch, "base": base, "orig": orig, "commit": sha,
                         "locks_done": done, "locks_left": left, "at": kit.now()})
    save_guard(ws, top, "bump --apply")
    print(f"Committed {sha[:10]} on {branch}: Release {plan['to']}: bump the version."
          + (f" Your branch {orig} is unchanged at {base[:10]}." if made and orig else ""))


# ---------------------------------------------------------------- changelog-check, commit

def changelog_target(ws: Path, cfg: dict, shape: dict, a) -> tuple[str, dict, dict, Ver | None]:
    top = Path(cfg["repo"])
    f = a.file or shape.get("changelog")
    if not f:
        fail("no changelog found; name it with --file")
    p = top / f
    if not p.is_file():
        fail(f"no file {p}")
    parsed = parse_changelog(p.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff"))
    v = Ver.parse(a.version) if getattr(a, "version", None) else target_version(ws, cfg, shape)
    sec = section_for(parsed, v)
    if not sec:
        sec = unreleased_section(parsed)
        if not sec:
            fail(f"{f} has no section for {v or 'this release'} and no Unreleased section")
        print(f"note: {f} has no section for {v} yet; reading the Unreleased section")
    return f, parsed, sec, v


def cmd_changelog_check(a) -> None:
    ws, cfg = load(a)
    shape = rj(ws, "shape.json", {})
    changes = rj(ws, "changes.json")
    if not changes:
        fail("run changes first: the map needs its commits and surface changes")
    f, parsed, sec, v = changelog_target(ws, cfg, shape, a)
    entries, summary = entries_of(parsed, sec)
    m = map_changelog(entries, changes)
    commits = {c["sha"]: c for c in changes["commits"]}
    print(f"{f}, section {sec['title']}: {plural(len(entries), 'entry', 'entries')}"
          + ("" if sec["date"] else " (not dated yet)"))
    print("Entries -> what supports them:")
    for e in entries:
        print(f"  {e['id']} (line {e['line']}{', ' + e['category'] if e['category'] else ''}) {e['text'][:110]}")
        links = m["by_entry"][e["id"]]
        for ln in links[:6]:
            if "commit" in ln:
                c = commits[ln["commit"]]
                print(f"       <- {c['short']} {c['subject'][:80]}  ({ln['why']})")
            else:
                print(f"       <- {m['items'][ln['surface']]['what']}  ({ln['why']})")
        if not links:
            print("       NOTHING BEHIND IT: no commit or surface change matches")
    print("Changes -> entries:")
    for c in changes["commits"]:
        if c.get("merge"):
            continue
        ids = sorted({ln["entry"] for ln in m["links"] if ln.get("commit") == c["sha"]})
        internal = next((x for x in m["internal"] if x["sha"] == c["sha"]), None)
        state = ", ".join(ids) if ids else (f"internal ({internal['why']})" if internal else "NO ENTRY")
        print(f"  {c['short']} {c['subject'][:80]}  -> {state}")
    for k, it in enumerate(m["items"]):
        ids = sorted({ln["entry"] for ln in m["links"] if ln.get("surface") == k})
        print(f"  {it['what']}  -> {', '.join(ids) if ids else 'NO ENTRY'}")
    nw, nc, ns = len(m["entries_without_change"]), len(m["commits_without_entry"]), len(m["surface_without_entry"])
    print(f"Findings: {plural(nw, 'entry', 'entries')} with no change behind {'it' if nw == 1 else 'them'}, "
          f"{plural(nc, 'commit')} and {plural(ns, 'surface change')} with no entry.")
    print("This map is for you and the release reviewer, not a verdict: matching is by words, file names and options.")
    sj(ws, "changelog.json", {"at": kit.now(), "file": f, "section": sec["title"], "dated": bool(sec["date"]),
                              "version": str(v) if v else None, "entries": entries, "summary": summary,
                              "links": m["links"], "items": m["items"],
                              "entries_without_change": [e["id"] for e in m["entries_without_change"]],
                              "commits_without_entry": [c["sha"] for c in m["commits_without_entry"]],
                              "surface_without_entry": [it["what"] for it in m["surface_without_entry"]],
                              "internal": m["internal"]})


def cmd_commit(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    b = bump_state(ws)
    if not b.get("applied"):
        fail("there's no release branch yet: run bump --apply first")
    on = current_branch(top)
    if on != b["branch"]:
        fail(f"you're on {on or 'a detached HEAD'}, not the release branch {b['branch']}: git switch {b['branch']}")
    hits = attribution_hits(a.message, "the message", names_allowed(Tree(top, b.get("base"))))
    if hits:
        fail("the message credits an AI, which the project's history mustn't: "
             + "; ".join(f"line {h['line']} ({h['why']}): {h['text']}" for h in hits))
    if a.paths:
        kit.git(top, "add", "--", *a.paths)
    else:
        kit.git(top, "add", "-u")
    if not kit.git(top, "diff", "--cached", "--name-only").strip():
        fail("nothing staged to commit")
    r = subprocess.run(["git", "-C", str(top), "commit", "-q", "-m", a.message], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode:
        fail(f"git commit failed: {(r.stderr or r.stdout).strip()}")
    save_guard(ws, top, "commit")
    print(f"Committed {rev_sha(top, 'HEAD')[:10]} on {b['branch']}: {a.message.splitlines()[0]}")


# ---------------------------------------------------------------- agents: sandbox, collect, update

def cmd_sandbox(a) -> None:
    ws, cfg = load(a)
    net = bool(a.net)
    if a.empty:
        build = rj(ws, "build.json")
        if not build or not build.get("artifacts"):
            fail("no artifacts yet: run build first")
        root = empty_box(cfg, ws, a.name, net, kit.parse_env(a.env))
        for art in build["artifacts"]:
            shutil.copy2(ws / "artifacts" / art["name"], root / "repo" / art["name"])
        print(f"Sandbox {a.name} is ready. It holds the release's files and nothing of the project's source.")
        print(f"  folder:       {root / 'repo'}   ({', '.join(x['name'] for x in build['artifacts'])})")
        print(f"  run things:   {kit.run_command_line(root)}")
        print(f"  scratch:      {root / 'scratch'}   (the agent writes its reply to {REPLY} here)")
        print(f"  network:      {'allowed' if net else kit.OFFLINE_NOTE}")
        print("  home, temp and app-data folders are the sandbox's own; tokens, keys and your Python and npm settings are left out")
        return
    ref = a.rev or release_ref(ws, cfg)
    sha = rev_sha(Path(cfg["repo"]), ref) or fail(f"{ref} isn't a commit here")
    root = kit.make_sandbox(cfg, ws, a.name, rev=ref, network=net or None)
    print(f"Sandbox {a.name} is ready.")
    print(kit.sandbox_text(root, net or cfg["network"], ref))
    since = (rj(ws, "shape.json", {}).get("last") or {}).get("tag")
    print(f"  history:      the release is {sha[:10]}; " + (f"git log -p {since}..{sha[:10]} shows what changed since {since}. "
                                                          if since else "") + "The top commit, 'sandbox base', is the sandbox's own.")


def add_findings(ws: Path, source: str, items: list[dict]) -> list[str]:
    def add(db):
        ids = []
        for it in items:
            fid = f"F{db['next']}"
            db["next"] += 1
            db["items"].append({"id": fid, "source": source, "status": "open", "note": "", **it})
            ids.append(fid)
        return ids
    return mutate(ws, "findings.json", {"next": 1, "items": []}, add)


def cmd_collect(a) -> None:
    ws, cfg = load(a)
    if bool(a.reply) == bool(a.sandbox):
        fail("give the reply file, or --sandbox <name> to read its reply.json")
    box = kit.find_sandbox(cfg, a.sandbox) if a.sandbox else None
    data = kit.read_reply(box / "scratch" / REPLY if box else Path(a.reply))
    source = a.source or (f"{a.role}:{a.sandbox}" if a.sandbox else a.role)
    if a.role == "reviewer":
        entries = data.get("entries")
        if not isinstance(entries, list) or not entries:
            fail("a reviewer's reply needs an \"entries\" list with a verdict for each changelog entry")
        bad = [f"entry {i + 1}: {e.get('verdict')!r}" for i, e in enumerate(entries)
               if not isinstance(e, dict) or e.get("verdict") not in VERDICTS]
        if bad:
            fail("verdicts are supported, unsupported, overstated or unclear; got " + ", ".join(bad))
        missing = [m for m in kit.as_list(data.get("missing")) if isinstance(m, dict) and (m.get("change") or m.get("text"))]
        items = [{"kind": e["verdict"], "text": str(e.get("entry", ""))[:300], "evidence": str(e.get("evidence", ""))[:600]}
                 for e in entries if e["verdict"] != "supported"]
        items += [{"kind": "missing", "text": str(m.get("change") or m.get("text"))[:300],
                   "evidence": str(m.get("evidence", ""))[:600], "suggestion": str(m.get("suggested_entry", ""))[:300]}
                  for m in missing]
        for br in kit.as_list(data.get("breaking_not_called_breaking")):
            items.append({"kind": "missing", "text": f"breaking, not called breaking: {br}"[:300], "evidence": ""})
        ids = add_findings(ws, source, items)
        counts = {v: sum(1 for e in entries if e["verdict"] == v) for v in VERDICTS}
        sj(ws, "review.json", {"at": kit.now(), "source": source, "counts": counts, "missing": len(missing), "data": data})
        print(f"Reviewer: {plural(len(entries), 'entry', 'entries')} (" + ", ".join(f"{n} {v}" for v, n in counts.items() if n)
              + f"), {plural(len(missing), 'missing change')}." + (f" Findings {ids[0]}-{ids[-1]}." if len(ids) > 1 else
                                                                (f" Finding {ids[0]}." if ids else " No findings.")))
    else:
        outcome = data.get("outcome")
        if outcome not in OUTCOMES:
            fail(f"an installer's outcome is installed, installed-with-help or stuck, not {outcome!r}")
        stuck = [s for s in kit.as_list(data.get("stuck_points")) if isinstance(s, dict)]
        items = [{"kind": "stuck", "text": str(s.get("what", ""))[:300], "evidence": str(s.get("where", ""))[:300],
                  "suggestion": str(s.get("suggestion", ""))[:300]} for s in stuck]
        guesses = [st for st in kit.as_list(data.get("steps")) if isinstance(st, dict) and st.get("guess")]
        items += [{"kind": "guess", "text": f"had to guess: {st.get('guess')}"[:300],
                   "evidence": f"{st.get('doc', '')}:{st.get('line', '')} {st.get('action', '')}"[:300]} for st in guesses]
        ids = add_findings(ws, source, items)
        sj(ws, "installer.json", {"at": kit.now(), "source": source, "outcome": outcome,
                                  "version_seen": data.get("version_seen"), "data": data})
        print(f"First-time installer: {outcome}" + (f", saw {data.get('version_seen')}" if data.get("version_seen") else "")
              + f"; {plural(len(stuck), 'stuck point')}, {plural(len(guesses), 'guess', 'guesses')}."
              + (f" Findings {', '.join(ids)}." if ids else ""))
    if box:
        kit.keep_scratch(box, ws / "scratch" / a.sandbox)
        if not a.keep:
            kit.drop_sandbox(ws, cfg, a.sandbox)


def cmd_update(a) -> None:
    ws, cfg = load(a)

    def upd(db):
        for it in db["items"]:
            if it["id"].lower() == a.id.lower():
                it["status"] = a.status
                if a.note:
                    it["note"] = a.note
                return it
        fail(f"no finding {a.id}")
    it = mutate(ws, "findings.json", {"next": 1, "items": []}, upd)
    if a.status == "kept":
        mutate(ws, "decisions.json", [], lambda d: d.append(
            {"text": f"Kept {it['id']} as it is ({it['kind']}: {it['text'][:120]}): {a.note or 'no reason given'}", "at": kit.now()}))
    print(f"{it['id']}: {it['status']}. {it['kind']}: {it['text'][:100]}")


# ---------------------------------------------------------------- build

def is_artifact(rel: str) -> bool:
    parts = rel.split("/")
    low = rel.lower()
    if any(p in ("node_modules", ".git", "__pycache__") or p.endswith(".egg-info") for p in parts):
        return False
    if low.endswith(ARCHIVES):
        return True
    if low.startswith("target/release/") and len(parts) == 3:
        return "." not in parts[2] or low.endswith(".exe")
    return False


def collect_artifacts(repo: Path, globs: list[str] | None) -> tuple[list[str], list[str]]:
    st = kit.split0(kit.git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored"))
    new = [e[3:] for e in st if e[:2] in ("??", "!!")]
    modified = [e[3:] for e in st if e[:2] not in ("??", "!!") and len(e) > 3]
    new = [p for p in new if not re.search(r"(^|/)(__pycache__|\.pytest_cache|\.mypy_cache)(/|$)|\.pyc$", p)]
    if globs:
        found = []
        for g in globs:
            hits = sorted(p.relative_to(repo).as_posix() for p in repo.glob(g) if p.is_file() and ".git" not in p.parts)
            found += hits
        return list(dict.fromkeys(found)), modified
    arts = [p for p in new if is_artifact(p)]
    if not arts:
        arts = [p for p in new if p.split("/")[0] == "dist"]
    return arts, modified


def run_build(cfg: dict, ws: Path, cmd: list[str], rev: str, net: bool, globs, timeout: float, tag: str | None,
              keep_to: Path | None) -> dict:
    top = Path(cfg["repo"])
    sha = rev_sha(top, rev) or fail(f"{rev} isn't a commit here")
    name = f"build-{secrets.token_hex(3)}"
    root = kit.make_sandbox(cfg, ws, name, rev=rev, network=net)
    try:
        repo = root / "repo"
        if tag:
            kit.git(repo, "tag", "-f", tag)
        r = kit.run_in(root, cmd, timeout=timeout, log=True)["runs"][0]
        found, modified = collect_artifacts(repo, globs)
        arts = []
        if keep_to is not None:
            kit.rmtree(keep_to)
            keep_to.mkdir(parents=True)
        names = set()
        for rel in found:
            nm = rel.rsplit("/", 1)[-1]
            if nm in names:
                nm = rel.replace("/", "-")
            names.add(nm)
            src = repo / rel
            if keep_to is not None:
                shutil.copy2(src, keep_to / nm)
            arts.append({"name": nm, "path": rel, "size": src.stat().st_size, "sha256": sha256(src)})
        log = None
        if r.get("log"):
            (ws / "logs").mkdir(exist_ok=True)
            log = ws / "logs" / f"{name}.log"
            shutil.copy2(r["log"], log)
        return {"ok": not kit.failed(r), "code": r["code"], "timed_out": r["timed_out"], "secs": r["secs"], "cmd": cmd,
                "rev": rev, "sha": sha, "network": net, "artifacts": arts, "tail": tail(r["out"], 40),
                "log": str(log) if log else None, "modified": modified[:40], "tag_in_sandbox": tag, "at": kit.now()}
    finally:
        kit.drop_sandbox(ws, cfg, name)


def cmd_build(a) -> None:
    ws, cfg = load(a)
    cmd = kit.strip_dashes(a.cmd)
    if not cmd:
        fail("give the build command after --, like: build -w <run> -- python -m build")
    cmd, why = real_python(cmd)
    if why:
        print(f"note: {why}")
    shape = rj(ws, "shape.json", {})
    rev = a.rev or release_ref(ws, cfg)
    a.net = bool(a.net or cfg.get("network"))
    v = target_version(ws, cfg, shape)
    tag = tag_for(ws, shape, v) if a.tag_in_sandbox and v else None
    if a.tag_in_sandbox and not v:
        fail("--tag-in-sandbox needs a version: run bump first")
    print(f"Building {rev} in a fresh sandbox: {show(cmd)}" + (f" (tagged {tag} in the sandbox only)" if tag else "")
          + ("" if a.net else "; network off"))
    res = run_build(cfg, ws, cmd, rev, a.net, a.artifacts, a.timeout, tag, ws / "artifacts")
    if res["artifacts"]:
        (ws / "SHA256SUMS.txt").write_text("".join(f"{x['sha256']}  {x['name']}\n" for x in res["artifacts"]),
                                           encoding="utf-8", newline="\n")
    if a.twice and res["ok"]:
        again = run_build(cfg, ws, cmd, rev, a.net, a.artifacts, a.timeout, tag, None)
        first = {x["name"]: x["sha256"] for x in res["artifacts"]}
        second = {x["name"]: x["sha256"] for x in again["artifacts"]}
        differ = sorted(n for n in set(first) | set(second) if first.get(n) != second.get(n))
        res["twice"] = {"reproducible": not differ and again["ok"], "differ": differ}
    if v and res["artifacts"]:
        stale = [x["name"] for x in res["artifacts"] if versions_in(x["name"]) and not version_seen(x["name"], v)]
        res["stale_names"] = stale
    sj(ws, "build.json", res)
    state = "built" if res["ok"] else ("timed out" if res["timed_out"] else f"FAILED (exit {res['code']})")
    print(f"Build {state} in {res['secs']} s from {rev} at {res['sha'][:10]}.")
    if not res["ok"]:
        print(indent(res["tail"]))
        if not a.net and re.search(r"proxy|network|resolve|getaddrinfo|could not find a version|no matching distribution|"
                                   r"ENOTFOUND|EAI_AGAIN|offline", res["tail"], re.I):
            print("hint: the build looks like it needs the network (build isolation fetches the backend). Install the "
                  "backend and build without isolation, or run build again with --net and say so in the report.")
        sys.exit(1)
    if not res["artifacts"]:
        print("FAIL: the build made nothing that looks like a release file. Name them with --artifacts \"dist/*\".")
        if res["modified"]:
            print("  it changed tracked files: " + ", ".join(res["modified"][:8]))
        sys.exit(1)
    print(f"Artifacts ({len(res['artifacts'])}), copied to {ws / 'artifacts'} with SHA256SUMS.txt beside them:")
    for x in res["artifacts"]:
        print(f"  {x['name']}  {x['size']:,} bytes  sha256 {x['sha256'][:16]}...")
    if res.get("stale_names"):
        print(f"WARNING: these names don't mention {v}: {', '.join(res['stale_names'])}. The build may be reading an old "
              "version from somewhere.")
    if res["modified"]:
        print("note: the build changed tracked files (generated during the build?): " + ", ".join(res["modified"][:8]))
    if "twice" in res:
        t = res["twice"]
        print("Built twice in fresh sandboxes: " + ("reproducible, every artifact the same bytes." if t["reproducible"]
                                                    else "these differ: " + ", ".join(t["differ"]) + " (informational)."))


# ---------------------------------------------------------------- smoke: the core check

def cmd_smoke(a) -> None:
    ws, cfg = load(a)
    cmd0 = kit.strip_dashes(a.cmd)
    if not cmd0:
        fail("give the first command a new user runs after --, like: smoke -w <run> --install pip -- mytool --version")
    build = rj(ws, "build.json")
    if not build or not build.get("ok") or not build.get("artifacts"):
        fail("there's no successful build in this run: run build first")
    shape = rj(ws, "shape.json", {})
    v = Ver.parse(a.version) if a.version else target_version(ws, cfg, shape)
    if v is None and not a.no_version_check:
        fail("no version to look for: run bump first, or pass --version")
    if a.no_version_check is not None and not a.no_version_check.strip():
        fail("--no-version-check needs the reason, like --no-version-check \"the installer prints no version\"")
    names = [x["name"] for x in build["artifacts"]]
    recipe = a.install
    if recipe == "pip":
        chosen = pick(names, a.artifact, (".whl",)) or pick(names, None, (".tar.gz",))   # a wheel, else an sdist
    elif recipe == "npm":
        chosen = pick(names, a.artifact, (".tgz",))
    elif recipe == "archive":
        chosen = pick(names, a.artifact, UNPACKABLE)
    elif recipe == "binary":
        chosen = pick(names, a.artifact, ()) if a.artifact else ([names[0]] if len(names) == 1 else [])
    else:
        chosen = pick(names, a.artifact, ()) if a.artifact else names
    if not chosen:
        fail(f"none of the artifacts suits the {recipe} recipe ({', '.join(names)}); pick with --artifact <name>")
    if recipe == "script" and not a.script:
        fail("the script recipe needs --script <the installer among the artifacts>, like --script install.sh")
    if recipe == "script" and a.script not in names:
        fail(f"{a.script} isn't among the artifacts ({', '.join(names)}); add it to the build with --artifacts")
    label = a.label or f"{recipe} on {platform_name()}"
    name = f"smoke-{recipe}-{secrets.token_hex(3)}"
    planned = kit.box_path(cfg, name)

    def subst(s: str) -> str:
        return (s.replace("{sandbox}", str(planned)).replace("{home}", str(planned / "home"))
                .replace("{work}", str(planned / "repo")))
    cmd0 = [subst(x) for x in cmd0]
    thens = [[subst(x) for x in kit.as_cmd(t)] for t in a.then or []]
    root = empty_box(cfg, ws, name, a.net, {k: subst(v) for k, v in kit.parse_env(a.env).items()})
    work = root / "repo"
    reasons, install, commands, notes_, install_out = [], [], [], [], []
    variant = recipe
    try:
        for n in chosen:
            shutil.copy2(ws / "artifacts" / n, root / "artifacts" / n)
        bins: list[Path] = []
        extra_env = {}
        steps: list[tuple[str, list[str], str | None]] = []
        if recipe == "pip":
            py = a.python or sys.executable
            links = ["--find-links", str(root / "artifacts")]
            for wh in a.wheelhouse or []:
                links += ["--find-links", str(Path(wh).resolve())]
            wheels = [n for n in chosen if n.endswith(".whl")]
            if len(wheels) == 1:
                spec = [str(root / "artifacts" / wheels[0])]
            elif not wheels and len(chosen) == 1:
                spec = [str(root / "artifacts" / chosen[0])]
            else:   # several wheels (one per platform, say): pip picks the one that fits
                spec = [a.name or (shape.get("info") or {}).get("name") or fail("pass --name <the package to install>")]
            index = [] if a.net else ["--no-index"]
            venv = root / "venv"
            vpy = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            has_venv, pv = python_has(py, "venv"), pip_version(py)
            if has_venv and pv and pv >= (22, 3):
                # An empty environment filled by this Python's pip: the same install, without the
                # 20 seconds ensurepip takes to put a second copy of pip inside it.
                variant = f"pip {pv[0]}.{pv[1]} into a new, empty virtual environment"
                steps.append(("create an empty virtual environment", [py, "-m", "venv", "--without-pip", str(venv)], None))
                steps.append(("install with pip", [py, "-m", "pip", "--python", str(vpy), "install", *index, *links, *spec], None))
                bins = [venv / ("Scripts" if os.name == "nt" else "bin")]
            elif has_venv and python_has(py, "ensurepip"):
                variant = "pip inside a new virtual environment"
                steps.append(("create a virtual environment", [py, "-m", "venv", str(venv)], None))
                steps.append(("install with pip", [str(vpy), "-m", "pip", "install", *index, *links, *spec], None))
                bins = [venv / ("Scripts" if os.name == "nt" else "bin")]
            elif pv:
                variant = "pip --target, with PYTHONPATH (this Python can't make a virtual environment)"
                site_dir = root / "site"
                steps.append(("install with pip --target", [py, "-m", "pip", "install", "--target", str(site_dir),
                                                            *index, *links, *spec], None))
                bins = [site_dir / "bin"]
                extra_env = {"PYTHONPATH": str(site_dir)}
            else:
                fail(f"{py} has no venv module and no pip, so it can't install a wheel the way a user would. Run smoke "
                     "with --python <a full Python 3 install>, or use the archive recipe")
        elif recipe == "npm":
            npm = shutil.which("npm") or fail("the npm recipe needs npm on the PATH")
            (work / "package.json").write_text(json.dumps({"name": "smoke-install", "version": "1.0.0", "private": True}),
                                               encoding="utf-8")
            steps.append(("install with npm", [npm, "install", "--no-audit", "--no-fund"] + ([] if a.net else ["--offline"])
                          + [str(root / "artifacts" / chosen[0])], None))
            bins = [work / "node_modules" / ".bin"]
        elif recipe == "archive":
            for n in chosen:
                safe_extract(root / "artifacts" / n, work)
            install.append({"title": f"unpacked {', '.join(chosen)}", "cmd": "", "code": 0, "tail": ""})
            tops = [p for p in work.iterdir() if p.is_dir()]
            bins = [work, work / "bin"] + [d for t in tops for d in (t, t / "bin")]
        elif recipe == "script":
            for n in chosen:
                shutil.copy2(ws / "artifacts" / n, work / n)
            argv = script_argv(work / a.script) + [subst(x) for x in kit.as_cmd(a.script_args or "")]
            steps.append((f"run {a.script}", argv, None))
            bins = [work]
        else:
            (work / "bin").mkdir()
            for n in chosen:
                dst = work / "bin" / n
                shutil.copy2(ws / "artifacts" / n, dst)
                if os.name != "nt":
                    dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            bins = [work / "bin"]
        bins = [Path(subst(p)) for p in (a.bin_dir or [])] + bins
        set_path(root, bins, extra_env)
        ok = True
        for title, argv, cwd in steps:
            res = kit.run_in(root, argv, timeout=a.install_timeout, cwd=cwd, log=True)["runs"][0]
            install.append({"title": title, "cmd": show(argv), "code": res["code"], "tail": tail(res["out"], 25)})
            install_out.append(res["out"][-20000:])
            if kit.failed(res):
                ok = False
                why = f"{title} failed ({'timed out' if res['timed_out'] else 'exit ' + str(res['code'])})"
                mt = re.search(r"No matching distribution found for (\S+)|ETARGET|ENOTCACHED|notarget|could not find", res["out"], re.I)
                if mt and not a.net:
                    why += (": a dependency isn't available offline. Pass --wheelhouse <folder of wheels> (or the npm "
                            "cache), or --net, and say which in the report")
                reasons.append(why)
                break
        if ok:
            run_env = kit.runner().build_env(root)
            path_var = run_env.get("PATH") or run_env.get("Path")
            for cmd in [cmd0] + thens:
                if not cmd:
                    continue
                cmd, why = real_python(cmd, path_var)
                if why and why not in notes_:
                    notes_.append(why)
                exe = cmd[0]
                cwd_dir = work / a.cwd if a.cwd else work
                if "/" in exe or "\\" in exe:
                    found = (cwd_dir / exe).resolve() if not Path(exe).is_absolute() else Path(exe)
                    found = str(found) if found.exists() else None
                else:
                    found = shutil.which(exe, path=path_var)
                stem_ = Path(exe).name.lower()
                stem_ = re.sub(r"(\.exe|\.cmd|\.bat)$", "", stem_)
                interp = stem_ in INTERPRETERS or re.fullmatch(r"python\d[\d.]*", stem_) is not None
                inside = bool(found) and Path(found).resolve().is_relative_to(root.resolve())
                rec = {"cmd": show(cmd), "resolved": found, "inside": inside, "interpreter": interp}
                if found and not interp and not inside:
                    rec.update(code=None, tail="")
                    commands.append(rec)
                    reasons.append(f"`{exe}` ran {found}, which is outside the sandbox: the installed release didn't "
                                   "put that command where it can be found, so this would test something else")
                    ok = False
                    break
                if interp and "-m" in cmd[1:] and stem_.startswith("py"):
                    mod = cmd[cmd.index("-m") + 1] if cmd.index("-m") + 1 < len(cmd) else None
                    if mod:
                        probe = kit.run_in(root, [cmd[0], "-c", "import importlib.util, sys; s = importlib.util.find_spec("
                                                  "sys.argv[1]); print('ORIGIN=' + ((s.origin or '') if s else ''))", mod],
                                           timeout=60, cwd=a.cwd, log=False)["runs"][0]
                        mo = re.search(r"ORIGIN=(.*)", probe["out"])
                        origin = mo.group(1).strip() if mo else ""
                        if origin and not Path(origin).resolve().is_relative_to(root.resolve()):
                            rec.update(code=None, tail="", module_origin=origin)
                            commands.append(rec)
                            reasons.append(f"`-m {mod}` imports from {origin}, outside the sandbox, not from the release")
                            ok = False
                            break
                        rec["module_origin"] = origin
                res = kit.run_in(root, cmd, timeout=a.timeout, cwd=a.cwd, log=True)["runs"][0]
                rec.update(code=res["code"], tail=tail(res["out"], 25), out=res["out"][-20000:])
                commands.append(rec)
                if kit.failed(res):
                    reasons.append(f"`{rec['cmd']}` {'timed out' if res['timed_out'] else 'exited ' + str(res['code'])}")
                    ok = False
                    break
        version = {"expected": str(v) if v else None, "found": None, "seen": []}
        if ok and commands:
            first_out = commands[0].get("out", "")
            version["seen"] = versions_in(first_out)
            if a.no_version_check:
                version["skipped"] = a.no_version_check
            else:
                version["found"] = version_seen(first_out, v)
                if not version["found"]:
                    reasons.append(f"`{commands[0]['cmd']}` didn't print {v}"
                                   + (f"; it printed {', '.join(version['seen'])}" if version["seen"] else "; it printed no version"))
                    ok = False
            alltext = "\n".join(install_out + [c.get("out", "") for c in commands])
            for want in a.expect or []:
                if want not in alltext:
                    reasons.append(f"the output doesn't contain {want!r}")
                    ok = False
        (ws / "smoke-logs").mkdir(exist_ok=True)
        logdir = ws / "smoke-logs" / kit.slugify(label)
        kit.rmtree(logdir)
        shutil.copytree(root / "logs", logdir)
        if a.keep:
            print(f"Kept the sandbox: {root}")
    finally:
        if not a.keep:
            kit.drop_sandbox(ws, cfg, name)
    verdict = "PASS" if ok and not reasons else "FAIL"
    arts = [{"name": n, "sha256": next(x["sha256"] for x in build["artifacts"] if x["name"] == n)} for n in chosen]
    entry = {"label": label, "recipe": recipe, "variant": variant, "platform": platform_name(), "network": bool(a.net),
             "wheelhouse": a.wheelhouse or [], "artifacts": arts, "build_sha": build["sha"], "install": install,
             "commands": [{k: c[k] for k in c if k != "out"} for c in commands], "version": version,
             "verdict": verdict, "reasons": reasons, "logs": str(logdir), "at": kit.now()}

    def put(lst):
        lst[:] = [x for x in lst if x["label"] != label] + [entry]
    mutate(ws, "smoke.json", [], put)
    print(f"Smoke: {label}, " + ("network on" if a.net else "network off") + ", in an empty sandbox with no project source")
    if variant != recipe:
        print(f"  note: {variant}")
    for n in notes_:
        print(f"  note: {n}")
    for st in install:
        print(f"  {st['title']}" + (f": exit {st['code']}" if st["cmd"] else ""))
        if st["tail"]:
            shown = st["tail"] if st["code"] != 0 else "\n".join(st["tail"].splitlines()[-4:])
            print(indent(shown, "      "))
    for c in commands:
        where = ("" if not c["resolved"] else f", ran {c['resolved']}" + (" (installed by the release)" if c["inside"] and not c["interpreter"] else ""))
        print(f"  $ {c['cmd']}  -> {'exit ' + str(c['code']) if c.get('code') is not None else 'not run'}{where}")
        if c.get("tail"):
            print(indent("\n".join(c["tail"].splitlines()[:12]), "      "))
    if verdict == "PASS":
        what = f"printed {v}" if version.get("found") else f"ran (version not checked: {version.get('skipped')})"
        print(f"PASS: the release installed from scratch and {what}.")
    else:
        print("FAIL: " + "; ".join(reasons))
        sys.exit(1)


# ---------------------------------------------------------------- notes

INSTALL_LINE = re.compile(r"\b(pip3?|pipx|uv)\s+(install|add|tool install)\b|\bnpm\s+(i|install)\b|\byarn\s+add\b|"
                          r"\bpnpm\s+add\b|\bcargo\s+install\b|\bgo\s+install\b|\bgem\s+install\b|\bbrew\s+install\b|"
                          r"\bdotnet\s+tool\s+install\b|\bdocker\s+pull\b|install\.(sh|ps1)\b|\|\s*(sh|bash|iex)\b")


def readme_install_lines(tree: Tree, old: list[Ver], new: Ver) -> list[str]:
    out = []
    for f in [f for f in tree.files() if "/" not in f and f.lower().startswith("readme")][:1]:
        fence = False
        for ln in lines_of(tree.read(f) or ""):
            if ln.lstrip().startswith(("```", "~~~")):
                fence = not fence
                continue
            if (fence or ln.startswith("    ")) and INSTALL_LINE.search(ln):
                line = ln.strip()
                for o in old:
                    for form in version_forms(o):
                        line = re.sub(rf"(?<![0-9A-Za-z.]){re.escape(form)}(?![0-9A-Za-z])", str(new), line)
                out.append(line)
    return list(dict.fromkeys(out))[:8]


def cmd_notes(a) -> None:
    ws, cfg = load(a)
    shape = rj(ws, "shape.json", {})
    out = ws / "notes.md"
    if out.exists() and not a.force:
        fail(f"{out} exists (you may have edited it); pass --force to draft it again")
    top = Path(cfg["repo"])
    f, parsed, sec, v = changelog_target(ws, cfg, shape, a)
    v = v or Ver.parse(sec["version"] or "")
    if v is None:
        fail("no version for the notes: run bump first")
    name = (shape.get("info") or {}).get("name") or top.name
    body = section_text(parsed, sec)
    tag = tag_for(ws, shape, v)
    if a.as_is:
        text = body.strip() + "\n"
    else:
        lines = [f"# {name} {v}", "", body.strip(), ""]
        olds = [x for x in (Ver.parse((shape.get("last") or {}).get("version")), current_version(shape)) if x]
        inst = readme_install_lines(Tree(top), olds, v)
        eco = (shape.get("info") or {}).get("ecosystem")
        if not inst:
            inst = {"python": [f"pip install --upgrade {name}=={v}"], "node": [f"npm install {name}@{v}"],
                    "rust": [f"cargo install {name} --version {v}"]}.get(eco, [])
        if inst:
            lines += ["## Install", "", "```sh", *inst, "```", ""]
        last = (shape.get("last") or {}).get("tag")
        if shape.get("github") and last:
            o, r = shape["github"]
            lines += [f"**Full changelog:** https://github.com/{o}/{r}/compare/{last}...{tag}", ""]
        text = "\n".join(lines)
    out.write_text(text, encoding="utf-8", newline="\n")
    hits = attribution_hits(text, "notes.md", names_allowed(Tree(top)))
    print(f"Drafted {out} from {f}, section {sec['title']}. Edit it in the project's voice; check reads it.")
    print(indent(text.rstrip("\n"), "  | "))
    if hits:
        print("WARNING: it credits an AI: " + "; ".join(f"line {h['line']}: {h['text']}" for h in hits))


# ---------------------------------------------------------------- check: the final gate

def cmd_check(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    shape = rj(ws, "shape.json", {})
    if a.test:
        kit.update_cfg(ws, lambda c: c.__setitem__("test_cmd", a.test))
        cfg["test_cmd"] = a.test
    b = bump_state(ws)
    ref = release_ref(ws, cfg)
    sha = rev_sha(top, ref) or fail(f"{ref} isn't a commit here")
    v = target_version(ws, cfg, shape)
    if v is None:
        fail("no version to check: run bump first")
    tag = tag_for(ws, shape, v)
    items = []

    def item(name, status, detail, lines=()):
        items.append({"name": name, "status": status, "detail": detail, "lines": list(lines)})
        print(f"{status:<4}  {name}: {detail}")
        for ln in list(lines)[:12]:
            print(f"        {ln}")

    print(f"Gate for {(shape.get('info') or {}).get('name') or top.name} {v} on {ref} at {sha[:10]}")
    tree = Tree(top, sha)
    pkg = cfg.get("package") or ""
    info = project_info(tree, pkg)
    sites = find_sites(tree, info, tuple(shape["github"]) if shape.get("github") else None)
    problems, notes = compare_sites(sites, v, "the release is")
    must = [s for s in sites if s["group"] != "example"]
    if problems:
        item("versions", "FAIL", f"{plural(len(problems), 'site')} of {len(must)} disagree", problems)
    elif (info.get("version_from") or "").startswith("tags"):
        item("versions", "PASS", f"the version is the tag {tag} ({info['version_from']}); {plural(len(must), 'other site')} agree")
    else:
        item("versions", "PASS", f"every version site says {v} ({len(must)})", notes)
    cl = changelog_file(tree, pkg)
    if not cl:
        item("changelog", "FAIL", "no changelog found")
    else:
        parsed = parse_changelog(tree.read(cl) or "")
        sec = section_for(parsed, v)
        dated_any = any(s["date"] for s in parsed["sections"] if s is not sec)
        if not sec:
            item("changelog", "FAIL", f"{cl} has no section for {v}")
        else:
            n = len(entries_of(parsed, sec)[0])
            if sec["unreleased"] or (not sec["date"] and dated_any):
                item("changelog", "FAIL", f"{cl}'s section for {v} isn't dated: {sec['title']}")
            elif n == 0:
                item("changelog", "FAIL", f"{cl}'s section for {v} has no entries")
            else:
                item("changelog", "PASS", f"{cl} has a section for {v}" + (f" dated {sec['date']}" if sec["date"] else
                                                                         " (undated, like the earlier ones)")
                     + f" with {plural(n, 'entry', 'entries')}")
    local = kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/tags/{tag}")
    remote = shape.get("remote")
    if local:
        item("tag", "FAIL", f"{tag} already exists here, at {rev_sha(top, tag)[:10]}")
    elif not remote:
        item("tag", "PASS", f"{tag} isn't a tag here yet (no remote to check)")
    elif not (a.net or cfg["network"]):
        item("tag", "PASS", f"{tag} isn't a tag here yet; on {remote}: not checked with the network off "
                            "(publish checks it again right before pushing the tag)")
    else:
        url = remote_url(top, remote, push=True) or remote
        r = subprocess.run(["git", "ls-remote", "--tags", url, f"refs/tags/{tag}"], capture_output=True, text=True, timeout=60)
        if r.returncode:
            item("tag", "FAIL", f"couldn't ask {remote} about {tag}: {r.stderr.strip()[:200]}")
        elif r.stdout.strip():
            item("tag", "FAIL", f"{remote} already has {tag}")
        else:
            item("tag", "PASS", f"{tag} isn't a tag here or on {remote}")
    on = current_branch(top)
    status = [ln for ln in kit.git_status(top).splitlines() if ln.strip()]
    want_branch = b["branch"] if b.get("applied") else on
    if b.get("applied") and on != b["branch"]:
        item("tree", "FAIL", f"you're on {on or 'a detached HEAD'}, not the release branch {b['branch']}")
    elif status:
        item("tree", "FAIL", f"{plural(len(status), 'uncommitted change')} on {want_branch}", [ln[3:] for ln in status])
    else:
        item("tree", "PASS", f"on {want_branch or 'a detached HEAD'}, nothing uncommitted")
    test_cmd = cfg.get("test_cmd")
    if a.no_tests:
        item("tests", "SKIP", f"not run: {a.no_tests}")
    elif not test_cmd:
        item("tests", "FAIL", "no test command: pass --test \"<command>\", or --no-tests \"<why>\" if the project has none")
    else:
        tcmd, why = real_python(kit.as_cmd(test_cmd))
        if why:
            print(f"note: {why}")
        res = kit.run_fresh(cfg, ws, tcmd, rev=sha, timeout=a.timeout, hint="tests")
        r = res["runs"][0] if res["runs"] else None
        if r and not kit.failed(r):
            item("tests", "PASS", f"{test_cmd} passed in a fresh sandbox of {ref} ({r['secs']} s)")
        else:
            item("tests", "FAIL", f"{test_cmd} failed in a fresh sandbox of {ref}", kit.summary(res).splitlines()[-12:])
    build = rj(ws, "build.json")
    if not build:
        item("build", "FAIL", "nothing built yet: run build")
    elif not build["ok"] or not build["artifacts"]:
        item("build", "FAIL", "the last build failed or made no artifacts")
    elif build["sha"] != sha:
        item("build", "FAIL", f"built from {build['sha'][:10]}, but {ref} is now at {sha[:10]}: build again, then smoke again")
    else:
        arts = build["artifacts"]
        bad = [x["name"] for x in arts if not (ws / "artifacts" / x["name"]).is_file()
               or sha256(ws / "artifacts" / x["name"]) != x["sha256"]]
        if bad:
            item("build", "FAIL", "artifacts changed or went missing since the build: " + ", ".join(bad))
        else:
            item("build", "PASS", f"built from {ref} at {sha[:10]}: {plural(len(arts), 'artifact')}, hashes match"
                 + (", reproducible" if (build.get("twice") or {}).get("reproducible") else ""))
    smokes = rj(ws, "smoke.json", []) or []
    current = {x["name"]: x["sha256"] for x in (build or {}).get("artifacts", [])}
    if not smokes:
        item("smoke", "FAIL", "nothing was test-installed: run smoke")
    else:
        bad = []
        for s in smokes:
            if s["verdict"] != "PASS":
                bad.append(f"{s['label']}: FAIL ({'; '.join(s['reasons'])[:160]})")
            elif any(current.get(x["name"]) != x["sha256"] for x in s["artifacts"]):
                bad.append(f"{s['label']}: tested an older build; smoke again")
        good = len(smokes) - len(bad)
        if bad:
            item("smoke", "FAIL", f"{good} of {len(smokes)} smoke installs pass against this build", bad)
        else:
            what = [f"{s['label']}: " + (f"printed {s['version']['expected']}" if s["version"].get("found") else
                                         f"version not checked ({s['version'].get('skipped')})")
                    + ("" if not s["network"] else ", network on") for s in smokes]
            item("smoke", "PASS", f"{len(smokes)} of {len(smokes)} clean installs pass against this build", what)
    hits = []
    base = b.get("base") if b.get("applied") else None
    ok_names = names_allowed(Tree(top, base or sha))
    if base:
        log = kit.git(top, "log", "--format=%x1e%H%x1f%an <%ae>%x1f%cn <%ce>%x1f%B", f"{base}..{sha}")
        recs = [r.split("\x1f") for r in log.split("\x1e")[1:]]
        for sha_, author, committer, body in recs:
            short = sha_[:10]
            hits += attribution_hits(f"{author}\n{committer}", f"commit {short}'s author", ok_names)
            hits += attribution_hits(body, f"commit {short}'s message", ok_names)
            patch = subprocess.run(["git", "-C", str(top), "show", "--format=", "--no-color", "--no-ext-diff", sha_],
                                   capture_output=True).stdout.decode("utf-8", "replace")
            added = "\n".join(ln[1:] for ln in patch.split("\n") if ln.startswith("+") and not ln.startswith("+++"))
            hits += [dict(h, where=f"commit {short}'s changes", line=0) for h in attribution_hits(added, "", ok_names)]
    if (ws / "notes.md").is_file():
        hits += attribution_hits((ws / "notes.md").read_text(encoding="utf-8"), "notes.md", ok_names)
    if cl:
        parsed = parse_changelog(tree.read(cl) or "")
        sec = section_for(parsed, v)
        if sec:
            for h in attribution_hits("\n".join(parsed["lines"][sec["line"]:sec["end"]]), cl, ok_names):
                hits.append(dict(h, line=h["line"] + sec["line"]))
    scanned = (f"{plural(len(recs), 'release commit')}" if base else "no release commits") + \
              (", notes.md" if (ws / "notes.md").is_file() else "") + (f" or {cl}'s section" if cl else "")
    if hits:
        item("attribution", "FAIL", f"AI attribution in the release ({plural(len(hits), 'line')})",
             [f"{h['where']}" + (f" line {h['line']}" if h["line"] else "") + f" ({h['why']}): {h['text']}" for h in hits])
    else:
        item("attribution", "PASS", f"no AI attribution in {scanned}" + ("; Claude is named in the README, so the name "
                                                                          "alone isn't taken as a credit" if ok_names else ""))
    probs, gnotes = guard_findings(ws, cfg)
    if probs:
        item("outward", "FAIL", "something outward happened that this run didn't do", probs)
    else:
        item("outward", "PASS", "no tags made or pushed and nothing fetched or pushed since the run started; "
             + (f"{cfg['branch']} unchanged" if cfg.get("branch") else "no branch moved"))
    findings = (rj(ws, "findings.json", {}) or {}).get("items", [])
    reviewed = (ws / "review.json").exists()
    open_ = [f for f in findings if f["status"] == "open" and f["kind"] in BLOCKING]
    if open_:
        item("review", "FAIL", f"{plural(len(open_), 'finding')} still open: fix each, or keep it with update --status kept",
             [f"{f['id']} {f['kind']}: {f['text'][:140]}" for f in open_])
    elif reviewed:
        item("review", "PASS", "every reviewer finding is fixed or kept as a decision")
    else:
        item("review", "SKIP", "no release reviewer's reply was collected")
    failed = [i for i in items if i["status"] == "FAIL"]
    verdict = "FAIL" if failed else "PASS"
    sj(ws, "check.json", {"at": kit.now(), "ref": ref, "sha": sha, "version": str(v), "tag": tag, "items": items,
                          "verdict": verdict})
    if failed:
        print(f"FAIL: {len(failed)} of {len(items)} gate checks fail: " + ", ".join(i["name"] for i in failed) + ".")
        sys.exit(1)
    skipped_ = [i["name"] for i in items if i["status"] == "SKIP"]
    print(f"PASS: {len(items) - len(skipped_)} of {len(items)} gate checks pass" + (f", {', '.join(skipped_)} not run" if skipped_ else "")
          + ". Next: publish-plan.")


def cmd_guard(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    if a.accept:
        save_guard(ws, top, "accepted by the lead")
        print("Took the project's current state as the new baseline.")
        return
    probs, notes = guard_findings(ws, cfg)
    for n in notes:
        print(f"note: {n}")
    if probs:
        print("FAIL: the project changed in ways this run didn't make:")
        for p in probs:
            print(f"  {p}")
        print("Find out what did it and undo it, then tell the user. guard --accept takes the current state as the baseline.")
        sys.exit(1)
    print("PASS: no tags, pushes or commits to your branch since this run last changed the project.")


# ---------------------------------------------------------------- publish-plan, publish

def cmd_publish_plan(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    shape = rj(ws, "shape.json", {})
    b = bump_state(ws)
    if not b.get("to"):
        fail("no version yet: run bump first")
    v = Ver.parse(b["to"])
    tag = b["tag"]
    ref = release_ref(ws, cfg)
    sha = rev_sha(top, ref)
    check = rj(ws, "check.json")
    gate_ok = bool(check and check["verdict"] == "PASS" and check["sha"] == sha)
    remote = shape.get("remote")
    push_url = remote_url(top, remote, push=True)
    gh = shape.get("github")
    ci = ci_publishers(shape)
    ci_keys = {k for w in ci for k, _ in w["publishes"]}
    ci_on_tag = [w for w in ci if w["tags"]]
    info = shape.get("info") or {}
    build = rj(ws, "build.json") or {}
    arts = [str(ws / "artifacts" / x["name"]) for x in build.get("artifacts", [])]
    notes_file = ws / "notes.md"
    name = info.get("name") or top.name
    sign = (shape.get("signing") or {}).get("tags")
    steps = []

    def step(title, cmds, note="", **extra):
        steps.append({"n": len(steps) + 1, "title": title, "argv": cmds, "show": [show(c) for c in cmds], "note": note,
                      "done": None, **extra})

    rel, orig = (b.get("branch") if b.get("applied") else None), b.get("orig") or cfg.get("branch")
    if rel and orig and rel != orig:
        cmds = [["git", "switch", orig], ["git", "merge", "--ff-only", rel]]
        if remote:
            cmds.append(["git", "push", remote, orig])
        step(f"Bring {rel} into {orig}" + (f" and push {orig}" if remote else ""), cmds,
             (f"If {orig} is protected, open a pull request instead: git push -u {remote} {rel}, then "
              f"gh pr create --base {orig} --head {rel} --title \"Release {v}\", and tag the merged commit."
              if remote and gh else f"The merge is a fast-forward: {orig} ends up at {sha[:10]}, the commit that was checked."))
    step(f"Tag {sha[:10]} as {tag}" + (", signed as this project's tags are" if sign else ""),
         [["git", "tag", "-s" if sign else "-a", tag, "-m", f"Release {v}", sha]])
    if remote:
        on_push = ""
        if ci_on_tag:
            on_push = ("This starts " + ", ".join(w["file"] for w in ci_on_tag) + ", which "
                       + "; ".join(x[1] for w in ci_on_tag for x in w["publishes"]) + ". Watch it with: gh run watch")
        step(f"Push the tag {tag} to {remote}", [["git", "push", remote, f"refs/tags/{tag}"]], on_push, precheck_tag=True)
    pre = v.pre is not None
    if "pypi" not in ci_keys and info.get("ecosystem") == "python":
        dists = [x for x in arts if x.endswith((".whl", ".tar.gz"))]
        if dists:
            step(f"Upload {name} {v} to PyPI", [[sys.executable, "-m", "twine", "upload", *dists]],
                 "These are the files the smoke test installed. Needs twine and your PyPI token.")
    if "npm" not in ci_keys and info.get("ecosystem") == "node" and not info.get("private"):
        tgz = [x for x in arts if x.endswith(".tgz")]
        if tgz:
            step(f"Publish {name} {v} to npm", [["npm", "publish", tgz[0]] + (["--tag", "next"] if pre else [])],
                 "A pre-release goes out under the next tag, so latest doesn't move." if pre else "The tarball the smoke test installed.")
    if "crates" not in ci_keys and info.get("ecosystem") == "rust" and kit.git_ok(top, "rev-parse", "--verify", "-q", "HEAD"):
        step(f"Publish {name} {v} to crates.io", [["cargo", "publish"]],
             f"cargo publish packs from the source, not from the run's artifacts: run it on {tag} with a clean tree.")
    if "rubygems" not in ci_keys and info.get("ecosystem") == "ruby":
        gems = [x for x in arts if x.endswith(".gem")]
        if gems:
            step(f"Push {name} {v} to RubyGems", [["gem", "push", gems[0]]])
    if "github" not in ci_keys and gh and remote:
        notes_args = ["--notes-file", str(notes_file)] if notes_file.is_file() else ["--notes", ""]
        step(f"Create the GitHub release {tag}", [["gh", "release", "create", tag, "--title", f"{name} {v}", *notes_args,
                                                   "--verify-tag", *(["--prerelease"] if pre else []), *arts]],
             "It uploads the artifacts the smoke test installed." if arts else "")
    lines = [f"# Publishing {name} {v}", ""]
    lines.append("Nothing in this file has been run. Every step is outward-facing and waits for your OK.")
    lines.append("")
    if gate_ok:
        lines.append(f"**Gate:** PASS at {sha[:10]} ({check['at']}).")
    else:
        lines.append(f"**Gate:** not passed for {sha[:10] if sha else ref}. Run `release.py check` first; don't publish before it passes.")
    lines.append("")
    if ci:
        for w in ci:
            lines.append(f"**CI:** {w['file']} " + "; ".join(x[1] for x in w["publishes"])
                         + (f" when a tag matching {', '.join(w['tags'])} is pushed." if w["tags"] else " when a release is published.")
                         + (" It reads the changelog." if w["reads_changelog"] else ""))
        if ci_on_tag:
            lines.append("")
            lines.append("So the plan is: push the tag, and CI does the rest.")
        lines.append("")
    if remote:
        lines += [f"Right before the tag goes out, publish checks that {remote} doesn't have it yet: "
                  f"`git ls-remote --tags {push_url or remote} refs/tags/{tag}` prints nothing.", ""]
    else:
        lines += ["There's no remote, so nothing is pushed.", ""]
    for s in steps:
        lines += [f"## Step {s['n']}. {s['title']}", "", "```sh", *s["show"], "```", ""]
        if s["note"]:
            lines += [s["note"], ""]
        lines += [f"Run it through the skill, after the OK: `release.py publish -w <run> --step {s['n']} --yes`", ""]
    (ws / "publish.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    sj(ws, "publish.json", {"at": kit.now(), "version": str(v), "tag": tag, "ref": ref, "sha": sha, "remote": remote,
                            "push_url": push_url, "gate_ok": gate_ok, "ci": [w["file"] for w in ci_on_tag], "steps": steps})

    def rewait(lst):
        lst[:] = [x for x in lst if x.get("source") != "publish-plan"]
        for s in steps:
            lst.append({"text": f"Publish step {s['n']}: {s['title']}", "command": " && ".join(s["show"]),
                        "source": "publish-plan", "step": s["n"], "at": kit.now()})
    mutate(ws, "waiting.json", [], rewait)
    print(f"Wrote {ws / 'publish.md'}: {plural(len(steps), 'step')}. Nothing was run.")
    if not gate_ok:
        print("note: the gate hasn't passed for this commit; run check before anyone publishes.")
    if ci_on_tag:
        print("CI publishes on the tag push (" + ", ".join(w["file"] for w in ci_on_tag) + "): push the tag; CI does the rest.")
    for s in steps:
        print(f"  {s['n']}. {s['title']}")
        for c in s["show"]:
            print(f"       {c}")


def cmd_publish(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    plan = rj(ws, "publish.json") or fail("no publish plan: run publish-plan first")
    s = next((x for x in plan["steps"] if x["n"] == a.step), None) or fail(f"the plan has no step {a.step}")
    if not a.yes:
        fail(f"step {a.step} ({s['title']}) is outward-facing. Ask the user first; run it with --yes only after they "
             "said yes to this step in this session. Nothing was run.")
    check = rj(ws, "check.json")
    if not check or check["verdict"] != "PASS" or check["sha"] != plan["sha"]:
        fail("the gate hasn't passed for the commit this plan publishes; run check (and publish-plan) again. Nothing was run.")
    if rev_sha(top, plan["ref"]) != plan["sha"]:
        fail(f"{plan['ref']} moved since the plan was written; run check and publish-plan again. Nothing was run.")
    if s.get("done"):
        fail(f"step {a.step} already ran at {s['done']}")
    earlier = [x for x in plan["steps"] if x["n"] < a.step and not x.get("done")]
    if earlier:
        fail(f"step {earlier[0]['n']} ({earlier[0]['title']}) isn't done yet; steps run in order. Nothing was run.")
    if s.get("precheck_tag"):
        url = plan.get("push_url") or plan.get("remote")
        r = subprocess.run(["git", "ls-remote", "--tags", url, f"refs/tags/{plan['tag']}"], capture_output=True, text=True,
                           timeout=120)
        if r.returncode:
            fail(f"couldn't ask {plan['remote']} whether it has {plan['tag']}: {r.stderr.strip()[:300]}. Nothing was pushed.")
        if r.stdout.strip():
            fail(f"{plan['remote']} already has {plan['tag']}; nothing was pushed")
    print(f"Step {a.step}: {s['title']}")
    results = []
    for argv, shown in zip(s["argv"], s["show"]):
        exe = shutil.which(argv[0]) or argv[0]
        r = subprocess.run([exe, *argv[1:]], cwd=str(top), capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (r.stdout + r.stderr).strip()
        results.append({"cmd": shown, "code": r.returncode, "tail": tail(out, 20)})
        print(f"  $ {shown}  -> exit {r.returncode}")
        if out:
            print(indent(tail(out, 12), "      "))
        if r.returncode:
            break
    ok = all(x["code"] == 0 for x in results) and len(results) == len(s["argv"])

    def mark(p):
        for x in p["steps"]:
            if x["n"] == a.step:
                x["runs"] = x.get("runs", []) + [{"at": kit.now(), "results": results, "ok": ok}]
                if ok:
                    x["done"] = kit.now()
    mutate(ws, "publish.json", {}, mark)
    save_guard(ws, top, f"publish step {a.step}")
    if not ok:
        print(f"FAIL: step {a.step} stopped at `{results[-1]['cmd']}`. Later commands in it didn't run.")
        sys.exit(1)
    print(f"Done: step {a.step}.")


# ---------------------------------------------------------------- decide, wait, report, clean

def cmd_decide(a) -> None:
    ws, _ = load(a)
    mutate(ws, "decisions.json", [], lambda d: d.append({"text": a.text, "at": kit.now()}))
    print(f"Recorded the decision: {a.text}")


def cmd_wait(a) -> None:
    ws, _ = load(a)
    mutate(ws, "waiting.json", [], lambda d: d.append({"text": a.text, "command": a.command, "source": "lead", "at": kit.now()}))
    print(f"Recorded as waiting for the user's OK: {a.text}")


def cell(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def cmd_report(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    shape = rj(ws, "shape.json", {}) or {}
    info = shape.get("info") or {}
    name = info.get("name") or top.name
    b = bump_state(ws)
    v = target_version(ws, cfg, shape)
    check = rj(ws, "check.json")
    build = rj(ws, "build.json")
    smokes = rj(ws, "smoke.json", []) or []
    changes = rj(ws, "changes.json")
    clog = rj(ws, "changelog.json")
    plan = rj(ws, "publish.json")
    findings = (rj(ws, "findings.json", {}) or {}).get("items", [])
    decisions = rj(ws, "decisions.json", []) or []
    waiting = rj(ws, "waiting.json", []) or []
    done_steps = {s["n"] for s in (plan or {}).get("steps", []) if s.get("done")}
    waiting = [w for w in waiting if w.get("step") not in done_steps]
    sha = rev_sha(top, release_ref(ws, cfg))
    gate_now = bool(check and check["sha"] == sha)
    failed = [i for i in (check or {}).get("items", []) if i["status"] == "FAIL"]
    total = len((check or {}).get("items", []))
    passed_smokes = [s for s in smokes if s["verdict"] == "PASS"]
    label = f"{name} {v}" if v else name
    if build and not build.get("ok"):
        status, headline = "blocked", f"{label} is blocked: the build failed in a fresh sandbox"
    elif not b.get("to"):
        status, headline = "partial", f"{label}: no version chosen yet, so nothing was prepared"
    elif check and check["verdict"] == "PASS" and gate_now:
        ver_ok = [s for s in passed_smokes if s["version"].get("found")]
        status = "done"
        headline = (f"{label} is ready: {total - len([i for i in check['items'] if i['status'] == 'SKIP'])} of {total} gate "
                    f"checks pass and {len(passed_smokes)} of {len(smokes)} clean installs from the built artifacts"
                    + (f" printed {v}" if ver_ok else " ran"))
    elif check and gate_now:
        status = "partial"
        headline = f"{label} is not ready: {len(failed)} of {total} gate checks fail (" + ", ".join(i["name"] for i in failed) + ")"
    else:
        status = "partial"
        headline = f"{label}: the gate hasn't run on the release branch's current commit"
    if done_steps and status == "done":
        headline += f"; {plural(len(done_steps), 'publish step')} run with your OK"
    commits = []
    if b.get("applied") and b.get("base") and sha:
        commits = [ln for ln in kit.git(top, "log", "--format=%h %s", "--reverse", f"{b['base']}..{sha}", check=False).splitlines() if ln]
    L = [f"# Release report: {label}", "", f"**Result:** {headline}."]
    if b.get("to"):
        L.append(f"**Version:** {b.get('from')} -> {b['to']} ({b.get('kind')}) · tag `{b.get('tag')}`")
    if b.get("applied"):
        L.append(f"**Branch:** `{b['branch']}`, {plural(len(commits), 'commit')} on top of `{b.get('orig') or 'the starting commit'}` "
                 f"at `{(b.get('base') or '')[:10]}`" + ("; nothing pushed" if not done_steps else ""))
    L.append(f"**Landing:** {cfg.get('landing')} · **Project:** `{top}`")
    L.append("")
    if check:
        L += ["## Gate", "", f"Run at {check['at']} on `{check['ref']}` at `{check['sha'][:10]}`"
              + ("" if gate_now else " (the branch has moved since: run check again)") + ".", "",
              "| Check | Result | Detail |", "|---|---|---|"]
        for i in check["items"]:
            L.append(f"| {i['name']} | {i['status']} | {cell(i['detail'])} |")
        for i in check["items"]:
            if i["status"] == "FAIL" and i["lines"]:
                L += ["", f"**{i['name']}:**"] + [f"- {cell(x)}" for x in i["lines"][:15]]
        L.append("")
    if smokes:
        L += ["## Clean installs", "", "Each one installed the built artifacts in an empty sandbox with no project source, "
              "its own home and temp folders, and the user's Python and npm settings hidden.", ""]
        for s in smokes:
            first = s["commands"][0] if s["commands"] else None
            L.append(f"- **{s['label']}** ({s['variant']}, network {'on' if s['network'] else 'off'}"
                     + (f", wheelhouse {', '.join(s['wheelhouse'])}" if s["wheelhouse"] else "") + f"): **{s['verdict']}**. "
                     + (f"`{first['cmd']}` printed `{(first.get('tail') or '').strip().splitlines()[0] if (first.get('tail') or '').strip() else ''}`. " if first else "")
                     + ("; ".join(s["reasons"]) if s["reasons"] else "")
                     + f" Artifacts: {', '.join(x['name'] for x in s['artifacts'])}.")
        L.append("")
    if build:
        L += ["## Artifacts", "", f"Built with `{show(build['cmd'])}` from `{build['rev']}` at `{build['sha'][:10]}` in "
              f"{build['secs']} s, network {'on' if build['network'] else 'off'}."
              + (" Built twice: " + ("reproducible." if build["twice"]["reproducible"] else "differs in " + ", ".join(build["twice"]["differ"]) + ".")
                 if build.get("twice") else ""), ""]
        if build["artifacts"]:
            L += ["| File | Bytes | SHA-256 |", "|---|---|---|"]
            L += [f"| {x['name']} | {x['size']:,} | `{x['sha256']}` |" for x in build["artifacts"]]
            L.append("")
    if changes:
        rec = changes["recommend"]
        L += [f"## Changes since {changes['since'] or 'the start'}", "",
              f"{plural(len(changes['commits']), 'commit')}, {plural(len(changes['files']), 'file')} changed. "
              f"The script suggested a {rec['kind']} release" + (f"; {rec['note']}" if rec["note"] else "") + ".", ""]
        for it in surface_items(changes)[:20]:
            L.append(f"- {it['what']}")
        L.append("")
    if clog:
        L += ["## Changelog against the changes", "",
              f"`{clog['file']}`, section {clog['section']}: {plural(len(clog['entries']), 'entry', 'entries')}; "
              f"{len(clog['entries_without_change'])} with no change behind them, {len(clog['commits_without_entry'])} "
              f"commits and {len(clog['surface_without_entry'])} surface changes with no entry, matched by words, file "
              "names and options.", ""]
    review = rj(ws, "review.json")
    if review or findings:
        L += ["## Review findings", ""]
        if review:
            L.append("Release reviewer: " + ", ".join(f"{n} {k}" for k, n in review["counts"].items() if n)
                     + f", {plural(review['missing'], 'missing change')}.")
        inst = rj(ws, "installer.json")
        if inst:
            L.append(f"First-time installer: {inst['outcome']}" + (f", saw {inst['version_seen']}" if inst.get("version_seen") else "") + ".")
        L.append("")
        for f in findings:
            L.append(f"- **{f['id']}** ({f['kind']}, {f['status']}): {cell(f['text'])}" + (f" *{cell(f['note'])}*" if f.get("note") else ""))
        L.append("")
    if plan:
        L += ["## Published", ""] if done_steps else []
        for s in plan["steps"]:
            if s.get("done"):
                L.append(f"- Step {s['n']}, {s['title']}: done {s['done']}")
        if done_steps:
            L.append("")
    if decisions:
        L += ["## Decisions to check", ""] + [f"- {cell(d['text'])}" for d in decisions] + [""]
    if waiting:
        L += ["## Waiting for your OK", "", "Prepared, not done. " + (f"The full plan is in `{ws / 'publish.md'}`." if plan else ""), ""]
        for w in waiting:
            L.append(f"- {cell(w['text'])}" + (f": `{w['command']}`" if w.get("command") else ""))
        L.append("")
    nots = []
    if check:
        nots += [f"{i['name']}: {i['detail']}" for i in check["items"] if i["status"] == "SKIP"]
        tag_item = next((i for i in check["items"] if i["name"] == "tag"), None)
        if tag_item and "not checked" in tag_item["detail"]:
            nots.append("whether the remote already has the tag (network off); publish checks before pushing it")
    plats = {s["platform"] for s in smokes}
    if smokes:
        missing = sorted({"windows", "macos", "linux"} - plats)
        nots.append(f"installs on {', '.join(missing)}: smoke ran on {', '.join(sorted(plats))} only")
    if not (ws / "installer.json").exists():
        nots.append("a first-time installer following the docs: not run")
    if smokes and any(s["network"] for s in smokes):
        nots.append("an install with the network off for " + ", ".join(s["label"] for s in smokes if s["network"]))
    if nots:
        L += ["## Not checked", ""] + [f"- {cell(n)}" for n in nots] + [""]
    L += ["## Run details", "", f"Run folder: `{ws}`", ""]
    (ws / "report.md").write_text("\n".join(L), encoding="utf-8", newline="\n")
    numbers = {
        "version_sites": len([s for s in shape.get("sites", []) if s["group"] != "example"]),
        "commits_since": len(changes["commits"]) if changes else None,
        "changelog_entries": len(clog["entries"]) if clog else None,
        "entries_without_change": len(clog["entries_without_change"]) if clog else None,
        "changes_without_entry": (len(clog["commits_without_entry"]) + len(clog["surface_without_entry"])) if clog else None,
        "artifacts": len(build["artifacts"]) if build else 0,
        "smoke_passed": len(passed_smokes), "smoke_total": len(smokes),
        "gate_failed": len(failed) if check else None, "gate_total": total if check else None,
        "findings_open": len([f for f in findings if f["status"] == "open"]),
        "publish_steps": len((plan or {}).get("steps", [])), "publish_done": len(done_steps),
    }
    summary = {"skill": "killer-release", "status": status, "headline": headline, "report": str(ws / "report.md"),
               "branch": b.get("branch") if b.get("applied") else None, "commits": commits,
               "decisions": [d["text"] for d in decisions],
               "waiting": [w["text"] + (f": {w['command']}" if w.get("command") else "") for w in waiting],
               "numbers": numbers}
    sj(ws, "summary.json", summary)
    print(f"Wrote {ws / 'report.md'} and summary.json.")
    print(f"killer-release: {status} · {headline} · {ws / 'report.md'}")


def cmd_clean(a) -> None:
    ws, cfg = load(a)
    n = kit.clean_sandboxes(ws, cfg)
    print(f"Deleted {plural(n, 'sandbox', 'sandboxes')}.")


# ---------------------------------------------------------------- CLI

def main(argv=None) -> None:
    kit.utf8_stdout()
    p = argparse.ArgumentParser(prog="release.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def cmd(name, func, help_, ws=True):
        sp = sub.add_parser(name, help=help_, description=help_)
        if ws:
            sp.add_argument("--workspace", "-w", required=True, help="the run folder init printed")
        sp.set_defaults(func=func)
        return sp

    s = cmd("init", cmd_init, "Create the run folder and read the project's release shape.", ws=False)
    s.add_argument("--repo", default=".", help="the project folder (default: here)")
    s.add_argument("--package", help="in a monorepo, the package's folder")
    s.add_argument("--target", default="", help="one line on what the project is")
    s.add_argument("--landing", choices=["prepare", "publish", "check"], default="prepare",
                   help="prepare everything (default), publish after an OK for each step, or check only")
    s.add_argument("--notes", choices=["draft", "as-is", "none"], default="draft", help="release notes (default draft)")
    s.add_argument("--test", help="the project's test command, run by check in a fresh sandbox")
    s.add_argument("--branch", help="commit on this branch (killer-goal's) instead of making killer-release/v<version>")
    s.add_argument("--network", action="store_true", help="let the run's sandboxes (not smoke) use the network")
    s.add_argument("--include", nargs="*", help="ignored paths every project sandbox needs, like node_modules")
    s.add_argument("--env", nargs="*", help="NAME=VALUE set in every sandbox")
    s.add_argument("--slug", help="short name for the run folder")
    s.add_argument("--workspace", "-w", help="use this run folder instead of the default")
    s.add_argument("--force", action="store_true", help="start over in an existing run folder")

    s = cmd("versions", cmd_versions, "List every version site and whether they agree (exit 1 if not).")
    s.add_argument("--rev", help="read the sites at this commit instead of the working tree")

    s = cmd("changes", cmd_changes, "What changed since the last release: commits, files, the user-visible surface.")
    s.add_argument("--since", help="compare from this tag or commit (default: the last release)")
    s.add_argument("--to", help="compare up to this ref (default: the release branch, else HEAD)")
    s.add_argument("--help-cmd", action="append", help="a command whose --help is compared, like \"python -m mytool\"")
    s.add_argument("--no-help", action="store_true", help="don't run any --help")
    s.add_argument("--limit", type=int, default=40, help="commits to print (all are saved)")

    s = cmd("bump", cmd_bump, "Compute the next version and show every edit; --apply commits them on a new branch.")
    s.add_argument("kind", help="patch, minor, major, or a version like 1.4.0")
    s.add_argument("--pre", help="make it a pre-release: rc, rc.1, beta.2 (rc alone counts up)")
    s.add_argument("--apply", action="store_true", help="write the edits on a new local branch and commit them")
    s.add_argument("--branch", help="the branch to commit on (default killer-release/v<version>); the current one is used as is")
    s.add_argument("--include-examples", action="store_true", help="bump example pins like FOO_VERSION=v1.0.0 too")

    s = cmd("changelog-check", cmd_changelog_check, "Map the changelog section's entries to the changes, both ways.")
    s.add_argument("--file", help="the changelog (default: the one init found)")
    s.add_argument("--version", help="the section to read (default: the release's version)")

    s = cmd("commit", cmd_commit, "Commit on the release branch; refuses a message that credits an AI.")
    s.add_argument("--message", "-m", required=True)
    s.add_argument("paths", nargs="*", help="files to commit (default: every tracked change)")

    s = cmd("sandbox", cmd_sandbox, "Make a sandbox for an agent.")
    s.add_argument("name", help="a name, like review or installer")
    s.add_argument("--empty", action="store_true", help="no project source: the artifacts only (for the first-time installer)")
    s.add_argument("--rev", help="the commit to copy (default: the release branch)")
    s.add_argument("--net", action="store_true", help="allow the network in it")
    s.add_argument("--env", nargs="*", help="NAME=VALUE set in it")

    s = cmd("collect", cmd_collect, "Read a release reviewer's or first-time installer's reply.")
    s.add_argument("reply", nargs="?", help="a reply saved to a file (or use --sandbox)")
    s.add_argument("--sandbox", help="read reply.json from this sandbox's scratch folder, then delete the sandbox")
    s.add_argument("--as", dest="role", choices=["reviewer", "installer"], required=True)
    s.add_argument("--source", help="a label for who sent it")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")

    s = cmd("update", cmd_update, "Mark a finding fixed, or kept as it is (recorded as a decision).")
    s.add_argument("id")
    s.add_argument("--status", choices=["fixed", "kept", "open"], required=True)
    s.add_argument("--note", default="")

    s = cmd("build", cmd_build, "Build in a fresh sandbox from the release branch and collect the artifacts.")
    s.add_argument("--rev", help="build this commit instead of the release branch")
    s.add_argument("--artifacts", nargs="*", help="globs for the release files, relative to the project, like 'dist/*' install.sh")
    s.add_argument("--twice", action="store_true", help="build again in another sandbox and compare the bytes")
    s.add_argument("--net", action="store_true", help="let the build use the network (say so in the report)")
    s.add_argument("--tag-in-sandbox", action="store_true", help="tag the sandbox's copy first, for versions computed from tags")
    s.add_argument("--timeout", type=float, default=1800)
    s.add_argument("cmd", nargs=argparse.REMAINDER, help="-- then the build command")

    s = cmd("smoke", cmd_smoke, "Install the artifacts in an empty sandbox, network off, and run what a new user runs.")
    s.add_argument("--install", choices=RECIPES, required=True, help="how a user installs it")
    s.add_argument("--artifact", action="append", help="which artifact(s), by name or glob (default: what suits the recipe)")
    s.add_argument("--name", help="pip: the package to install when there are several wheels")
    s.add_argument("--python", help="pip: the Python to make the virtual environment with (default: this one)")
    s.add_argument("--wheelhouse", action="append", help="pip: a folder of dependency wheels to install from offline")
    s.add_argument("--script", help="script: the installer among the artifacts, like install.sh")
    s.add_argument("--script-args", help="script: arguments for it")
    s.add_argument("--bin-dir", action="append", help="a folder to put first on the PATH; {home} is the sandbox's home")
    s.add_argument("--then", action="append", help="another command a new user runs; it must exit 0")
    s.add_argument("--expect", action="append", help="text that must appear in the install's or the commands' output")
    s.add_argument("--version", help="the version to look for (default: the release's)")
    s.add_argument("--no-version-check", help="don't look for the version, and say why")
    s.add_argument("--label", help="a name for this smoke install (default: '<recipe> on <platform>')")
    s.add_argument("--cwd", help="run the commands from this folder inside the sandbox's working folder")
    s.add_argument("--env", nargs="*", help="NAME=VALUE for the install and the commands")
    s.add_argument("--net", action="store_true", help="allow the network (say so in the report)")
    s.add_argument("--timeout", type=float, default=120, help="seconds per command")
    s.add_argument("--install-timeout", type=float, default=900)
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")
    s.add_argument("cmd", nargs=argparse.REMAINDER, help="-- then the first command, which should print the version")

    s = cmd("notes", cmd_notes, "Draft release notes from the changelog section into notes.md.")
    s.add_argument("--file", help="the changelog (default: the one init found)")
    s.add_argument("--version", help="the section to use (default: the release's version)")
    s.add_argument("--as-is", action="store_true", help="the changelog section exactly, nothing added")
    s.add_argument("--force", action="store_true", help="draft again over an existing notes.md")

    s = cmd("check", cmd_check, "The final gate: versions, changelog, tag, tree, tests, build, smoke, attribution.")
    s.add_argument("--test", help="the test command to run in a fresh sandbox (remembered for the run)")
    s.add_argument("--no-tests", help="run the gate without tests, and say why (recorded as a decision)")
    s.add_argument("--net", action="store_true", help="also ask the remote whether the tag exists")
    s.add_argument("--timeout", type=float, default=1800)

    s = cmd("guard", cmd_guard, "Has the real project changed in ways this run didn't make?")
    s.add_argument("--accept", action="store_true", help="take the current state as the new baseline")

    cmd("publish-plan", cmd_publish_plan, "Write the exact publish commands to publish.md. Runs nothing.")

    s = cmd("publish", cmd_publish, "Run one publish step, only after the user's OK for it in this session.")
    s.add_argument("--step", type=int, required=True)
    s.add_argument("--yes", action="store_true", help="the user said yes to this step in this session")

    s = cmd("decide", cmd_decide, "Record a call the user should check.")
    s.add_argument("text")
    s = cmd("wait", cmd_wait, "Record an outward action prepared but not done.")
    s.add_argument("text")
    s.add_argument("--command", required=True, help="the exact command that would do it")
    cmd("report", cmd_report, "Write report.md and summary.json.")
    cmd("clean", cmd_clean, "Delete this run's sandboxes.")

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
