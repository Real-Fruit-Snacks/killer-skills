#!/usr/bin/env python3
"""The machinery behind upgrade.py, killer-upgrade's bookkeeping script. Run upgrade.py, not this.

It's a module rather than part of upgrade.py so that Python caches its compiled bytecode: a script run
directly is compiled afresh every time, which for one this size is most of a quick command's run time.
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import math
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("killer_kit", HERE / "kit.py")
kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit)

try:
    import tomllib
except ModuleNotFoundError:   # Python 3.10
    tomllib = None

SKILL = "upgrade"
SCRIPT = "upgrade.py"
GROUP_ORDER = ("deps", "dev", "major", "runtime", "action", "image", "manual")
OPEN = ("pending", "fail", "install-fail")
STATES = ("pending", "accepted", "fail", "install-fail", "held", "split")
LOCKFILES = {"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb",
             "uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock", "Cargo.lock", "go.sum", "Gemfile.lock",
             "packages.lock.json", "composer.lock"}
MANIFESTS = {"package.json", "pyproject.toml", "Pipfile", "Cargo.toml", "go.mod", "Gemfile",
             "Directory.Packages.props", "composer.json"}
REQ_FILE = re.compile(r"^(?:[\w.-]*requirements[\w.-]*|constraints[\w.-]*)\.txt$", re.I)
PROJ_FILE = re.compile(r"\.(?:cs|fs|vb)proj$", re.I)
DEP_DIRS = ["node_modules/", ".venv/", "venv/", "target/", "obj/", "bin/", ".gradle/", ".pnpm-store/",
            ".bundle/", "vendor/bundle/", "__pypackages__/", ".tox/", ".nox/"]
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec|specs|testdata)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|"
                       r"\.(test|spec)\.[cm]?[jt]sx?$|_spec\.rb$|Tests?\.(cs|fs|vb)$|(^|/)conftest\.py$", re.I)
DEV_GROUPS = {"dev", "test", "tests", "testing", "lint", "docs", "doc", "typing", "types", "check"}
SAVED_TAIL = 400_000

# ---------------------------------------------------------------- small helpers


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def dur(s) -> str:
    s = float(s or 0)
    if s < 10:
        return f"{s:.1f} s"
    s = int(round(s))
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s} s" if s else f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min"


def tail(text: str, n: int = 25) -> str:
    return "\n".join((text or "").rstrip("\n").splitlines()[-n:])


def split_cmd(s: str) -> list[str]:
    """A command typed as one string: a JSON list, or words split the way the shell would."""
    s = (s or "").strip()
    if s.startswith("["):
        try:
            v = json.loads(s)
        except json.JSONDecodeError:
            kit.fail(f"not a JSON list of arguments: {s}")
        if not isinstance(v, list) or not v or not all(isinstance(x, str) for x in v):
            kit.fail(f"a command as JSON is a list of strings, not {s}")
        return v
    if os.name == "nt":   # keep backslashes in Windows paths
        parts = shlex.split(s, posix=False)
        return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    return shlex.split(s)


def show_cmd(cmd) -> str:
    return " ".join(c if c and not re.search(r"[\s\"'`]", c) else json.dumps(c) for c in (cmd or []))


def extract_json(text: str):
    """The first JSON value in a tool's output, skipping any warnings printed before it."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for m in re.finditer(r"^[ \t]*[\[{]", text, re.M):
        try:
            return dec.raw_decode(text, m.end() - 1)[0]
        except json.JSONDecodeError:
            continue
    return None


def json_stream(text: str) -> list:
    """JSON values written one after another (go list -json, cargo outdated per crate)."""
    dec, out, pos = json.JSONDecoder(), [], 0
    starts = re.compile(r"^[ \t]*[\[{]", re.M)
    while True:
        m = starts.search(text or "", pos)
        if not m:
            return out
        try:
            obj, pos = dec.raw_decode(text, m.end() - 1)
            out.append(obj)
        except json.JSONDecodeError:
            pos = m.end()


def read_text(path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def read_json_file(path):
    t = read_text(path)
    if t is None:
        return None
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


def J(*parts) -> str:
    """A path inside the project, written with forward slashes; '.' is the top."""
    out = "/".join(p.strip("/") for p in parts if p and p != ".")
    return out or "."


def norm_name(n: str) -> str:
    return re.sub(r"[-_.]+", "-", str(n)).lower()


# ---------------------------------------------------------------- TOML (tomllib, or just enough of it on 3.10)


def read_toml(text):
    if text is None:
        return None
    if tomllib:
        try:
            return tomllib.loads(text)
        except Exception:   # noqa: BLE001 - a broken manifest is reported as unreadable
            return None
    return mini_toml(text)


def _strip_comment(line: str) -> str:
    out, q = [], None
    for ch in line:
        if q:
            out.append(ch)
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).strip()


def _depth(s: str) -> int:
    d, q = 0, None
    for ch in s:
        if q:
            q = None if ch == q else q
        elif ch in "\"'":
            q = ch
        elif ch in "[{":
            d += 1
        elif ch in "]}":
            d -= 1
    return d


def _toml_key(k: str) -> str:
    return k.strip().strip("\"'")


def _split_dotted(k: str) -> list[str]:
    return [_toml_key(x) for x in re.findall(r'"[^"]*"|\'[^\']*\'|[^.]+', k.strip())]


def _toml_value(s: str):
    s = s.strip()
    if s[:1] in "\"'":
        q = s[0]
        end = s.find(q, 1)
        return s[1:end] if end > 0 else s[1:]
    if s[:1] in "[{":
        items, depth, cur, q = [], 0, [], None
        for ch in s[1:-1]:
            if q:
                cur.append(ch)
                q = None if ch == q else q
                continue
            if ch in "\"'":
                q = ch
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            if ch == "," and depth == 0:
                items.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        if "".join(cur).strip():
            items.append("".join(cur))
        if s[0] == "[":
            return [_toml_value(x) for x in items if x.strip()]
        out = {}
        for it in items:
            k, eq, v = it.partition("=")
            if eq:
                out[_toml_key(k)] = _toml_value(v)
        return out
    if s in ("true", "false"):
        return s == "true"
    return s


def mini_toml(text: str) -> dict:
    """Tables, strings, arrays and inline tables: what dependency sections use. Not a full TOML parser."""
    data: dict = {}
    cur = data
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = _strip_comment(lines[i])
        i += 1
        if not line:
            continue
        m = re.match(r"^\[\[?\s*(.+?)\s*\]\]?$", line)
        if m:
            cur, parts, arr = data, _split_dotted(m.group(1)), line.startswith("[[")
            for j, part in enumerate(parts):
                nxt = cur.get(part)
                if arr and j == len(parts) - 1:
                    lst = nxt if isinstance(nxt, list) else []
                    cur[part] = lst
                    lst.append({})
                    cur = lst[-1]
                elif isinstance(nxt, list) and nxt:
                    cur = nxt[-1]
                elif isinstance(nxt, dict):
                    cur = nxt
                else:
                    cur[part] = {}
                    cur = cur[part]
            continue
        key, eq, val = line.partition("=")
        if not eq:
            continue
        val = val.strip()
        while val[:1] in "[{" and _depth(val) > 0 and i < len(lines):
            val += " " + _strip_comment(lines[i])
            i += 1
        parts = _split_dotted(key)
        t = cur
        for part in parts[:-1]:
            t = t.setdefault(part, {})
        if parts:
            t[parts[-1]] = _toml_value(val)
    return data


# ---------------------------------------------------------------- versions

VER = re.compile(r"^[v=^~<>! ]*(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?(.*)$", re.I)
PRE = re.compile(r"^[-.]?(a|b|c|rc|alpha|beta|pre|preview|dev|canary|next|nightly|snapshot|m)\d*\b", re.I)


def vparse(v):
    m = VER.match(str(v or "").strip())
    if not m:
        return None
    nums = [int(x) if x is not None else None for x in m.group(1, 2, 3, 4)]
    rest = m.group(5) or ""
    pre = bool(PRE.match(rest)) or (rest.startswith("-") and not rest.startswith("-r"))
    return nums, pre, rest


def vkey(v):
    p = vparse(v)
    if not p:
        return (-1, 0, 0, 0, 0, str(v))
    nums, pre, rest = p
    return tuple(x or 0 for x in nums) + (0 if pre else 1, rest)


def newer(a, b) -> bool:
    return bool(a) and bool(b) and vkey(a) > vkey(b)


def vmajor(v) -> int:
    p = vparse(v)
    return (p[0][0] or 0) if p else 0


def classify(cur, new) -> str:
    """patch, minor or major, by semver: a 0.x minor (and a 0.0.x patch) is a major; anything odd is a major."""
    a, b = vparse(cur), vparse(new)
    if not a or not b or b[1]:
        return "major"
    (a1, a2, a3, _), (b1, b2, b3, _) = a[0], b[0]
    a2, a3, b2, b3 = a2 or 0, a3 or 0, b2 or 0, b3 or 0
    if a1 != b1:
        return "major"
    if a1 == 0:
        if a2 != b2 or (a2 == 0 and a3 != b3):
            return "major"
        return "patch"
    return "minor" if a2 != b2 else "patch"


def trunc(v: str, n: int) -> str:
    """v cut (or padded) to n numeric parts, like the old version it replaces: trunc('2.4.1', 2) == '2.4'."""
    p = vparse(v)
    if not p:
        return v
    return ".".join(str(x or 0) for x in p[0][:max(1, min(4, n))])


def satisfies(v: str, clauses) -> bool:
    """Does version v meet every clause, like ['>=2.0', '<3']? Covers what requirement files use."""
    for c in clauses:
        m = re.match(r"^(===|==|!=|~=|>=|<=|>|<)\s*(\S+)$", c.strip())
        if not m:
            return False
        op, w = m.groups()
        if op in ("==", "!=") and w.endswith(".*"):
            pre = w[:-2]
            hit = v == pre or v.startswith(pre + ".")
            ok = hit if op == "==" else not hit
        elif op == "~=":
            prefix = ".".join(w.split(".")[:-1])
            ok = vkey(v) >= vkey(w) and (v == prefix or v.startswith(prefix + "."))
        else:
            a, b = vkey(v), vkey(w)
            ok = {"==": a == b, "===": v == w, "!=": a != b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[op]
        if not ok:
            return False
    return True


def bump_spec(spec: str, to: str) -> str | None:
    """A requirement's version spec moved to `to`, or None when nothing in it stops `to` (it floats)."""
    s = (spec or "").strip()
    if not s:
        return None
    clauses = [c.strip() for c in s.split(",") if c.strip()]
    if len(clauses) == 1:
        m = re.fullmatch(r"(===?)\s*(\S+)", clauses[0])
        if m and not m.group(2).endswith("*"):
            return f"{m.group(1)}{to}"
        m = re.fullmatch(r"~=\s*(\S+)", clauses[0])
        if m:
            return f"~={trunc(to, max(2, len(m.group(1).split('.'))))}"
    if satisfies(to, clauses):
        return None
    return f">={to},<{vmajor(to) + 1}"


# ---------------------------------------------------------------- reading test output

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
DURATION = re.compile(r"\s*\(?\b\d+(?:\.\d+)?\s?(?:ms|s|sec|seconds)\)?\s*$")
PYTEST_STATE = {"PASSED": "pass", "XFAIL": "pass", "XPASS": "pass", "FAILED": "fail", "ERROR": "fail",
                "SKIPPED": "skip"}
UT_STATUS = r"(ok|FAIL|ERROR|skipped.*|expected failure|unexpected success)"


def clean_id(s: str) -> str:
    s = s.strip()
    for _ in range(3):
        t = re.sub(r"\s*\(\d+ tests?[^)]*\)\s*$", "", DURATION.sub("", s))
        if t == s:
            break
        s = t
    return s.strip()


def _ut_state(st: str):
    if st in ("ok", "expected failure"):
        return "pass"
    if st.startswith("skipped"):
        return "skip"
    return "fail" if st in ("FAIL", "ERROR", "unexpected success") else None


def _ut_id(name: str, where: str) -> str:
    return where if where == name or where.endswith("." + name) else f"{where}.{name}"


def parse_tests(out: str) -> dict:
    """Per-test results from a runner's output: pytest, unittest, go test -v, cargo test, TAP, jest,
    vitest, mocha and node's spec reporter. Anything else counts by the command's exit code alone."""
    text = ANSI.sub("", (out or "").replace("\r\n", "\n").replace("\r", "\n"))
    lines = text.split("\n")
    res = {"pass": set(), "fail": set(), "skip": set()}
    fmts: list[str] = []

    def mark(fmt, tid, state):
        tid = (tid or "").strip()
        if tid and state in res:
            res[state].add(tid)
            if fmt not in fmts:
                fmts.append(fmt)
    tap = any(re.match(r"^\s*TAP version \d+", ln) or re.match(r"^\s*1\.\.\d+\s*$", ln) for ln in lines)
    mocha = any(re.match(r"^\s*\d+ passing \(", ln) for ln in lines)
    pending = None
    for raw in lines:
        s = raw.rstrip()
        if pending:
            m = re.search(r"(?:^|\s)\.\.\.\s+" + UT_STATUS + "$", s) or re.match("^" + UT_STATUS + "$", s)
            if m:
                mark("unittest", pending, _ut_state(m.group(1)))
                pending = None
                continue
        m = re.match(r"^(?:\[gw\d+\]\s+\[\s*\d+%\]\s+)?(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+(\S+?::\S.*?)"
                     r"(?:\s+-\s.*)?$", s)
        if m:
            mark("pytest", m.group(2), PYTEST_STATE[m.group(1)])
            continue
        m = re.match(r"^(\S+?::\S.*?)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)(?:\s+\([^)]*\))?"
                     r"(?:\s+\[\s*\d+%\])?$", s)
        if m:
            mark("pytest", m.group(1), PYTEST_STATE[m.group(2)])
            continue
        m = re.match(r"^(FAILED|ERROR)\s+(\S+\.py)(?:\s+-\s.*)?$", s)
        if m:
            mark("pytest", m.group(2), "fail")
            continue
        m = re.match(r"^(FAIL|ERROR|UNEXPECTED SUCCESS): (\w+) \(([\w.]+)\)", s)
        if m:
            mark("unittest", _ut_id(m.group(2), m.group(3)), "fail")
            continue
        m = re.match(r"^(\w+) \(([\w.]+)\)(?:\s+\[[^\]]*\])?(?:\s+\.\.\.\s*(.*))?$", s)
        if m and (m.group(3) is not None or "." in m.group(2)):
            tid, st = _ut_id(m.group(1), m.group(2)), (m.group(3) or "").strip()
            state = _ut_state(st) if st else None
            if state:
                mark("unittest", tid, state)
            else:
                pending = tid
            continue
        m = re.match(r"^\s*--- (PASS|FAIL|SKIP): (\S+)", s)
        if m:
            mark("go", m.group(2), {"PASS": "pass", "FAIL": "fail", "SKIP": "skip"}[m.group(1)])
            continue
        m = re.match(r"^FAIL\s+(\S+)\s+\[(build failed|setup failed)\]", s)
        if m:
            mark("go", f"{m.group(1)} [{m.group(2)}]", "fail")
            continue
        m = re.match(r"^test (.+?) \.\.\. (ok|FAILED|ignored)\b", s)
        if m:
            mark("cargo", m.group(1), {"ok": "pass", "FAILED": "fail", "ignored": "skip"}[m.group(2)])
            continue
        if tap:
            m = re.match(r"^\s*(not ok|ok)\b(?:\s+\d+)?(?:\s+-)?\s*(.*?)\s*$", s)
            if m:
                desc, directive = m.group(2), None
                d = re.search(r"\s*#\s*(SKIP|TODO)\b.*$", desc, re.I)
                if d:
                    directive, desc = d.group(1).upper(), desc[:d.start()]
                if directive != "TODO":
                    mark("tap", clean_id(desc), "skip" if directive == "SKIP" else
                         ("pass" if m.group(1) == "ok" else "fail"))
                continue
        m = re.match(r"^\s*(PASS|FAIL)\s+(\S+\.(?:[cm]?[jt]sx?))(?:\s|$)", s)
        if m:
            mark("jest", m.group(2), "pass" if m.group(1) == "PASS" else "fail")
            continue
        m = re.match(r"^\s*([✓✔√✕✖×✗])\s+(.+)$", s)
        if m:
            mark("spec", clean_id(m.group(2)), "pass" if m.group(1) in "✓✔√" else "fail")
            continue
        if mocha:
            m = re.match(r"^\s{2,}\d+\) (.+?):?$", s)
            if m:
                mark("mocha", clean_id(m.group(1)), "fail")
    res["pass"] -= res["fail"]
    res["skip"] -= res["fail"] | res["pass"]
    return {"passed": sorted(res["pass"]), "failed": sorted(res["fail"]), "skipped": sorted(res["skip"]),
            "format": "+".join(fmts) or None}


def parse_junit(paths) -> dict:
    import xml.etree.ElementTree as ET   # only when the tests write JUnit XML
    res = {"pass": set(), "fail": set(), "skip": set()}
    for p in paths:
        try:
            root = ET.parse(p).getroot()
        except (ET.ParseError, OSError):
            continue
        for tc in root.iter("testcase"):
            name, cls = tc.get("name") or "", tc.get("classname") or ""
            tid = f"{cls}.{name}" if cls else name
            if tc.find("failure") is not None or tc.find("error") is not None:
                res["fail"].add(tid)
            elif tc.find("skipped") is not None:
                res["skip"].add(tid)
            else:
                res["pass"].add(tid)
    res["pass"] -= res["fail"]
    return {"passed": sorted(res["pass"]), "failed": sorted(res["fail"]), "skipped": sorted(res["skip"]),
            "format": "junit" if any(res.values()) else None}


# ---------------------------------------------------------------- reading the ecosystems' own tools


def parse_npm_outdated(text: str, root_name: str | None = None) -> list[dict]:
    """`npm outdated --json --long`: {name: {current, wanted, latest, type, dependent}} (a list per name
    when several workspaces have it)."""
    data = extract_json(text)
    out = []
    for name, v in (data.items() if isinstance(data, dict) else []):
        for e in (v if isinstance(v, list) else [v]):
            if not isinstance(e, dict):
                continue
            dep = e.get("dependent")
            out.append({"name": name, "current": e.get("current"), "wanted": e.get("wanted"),
                        "latest": e.get("latest"), "dev": e.get("type") == "devDependencies",
                        "workspace": dep if dep and root_name and dep != root_name else None})
    return out


def parse_pnpm_outdated(text: str) -> list[dict]:
    data = extract_json(text)
    out = []
    for name, e in (data.items() if isinstance(data, dict) else []):
        if not isinstance(e, dict):
            continue
        deps = [d.get("name") for d in e.get("dependentPackages") or [] if isinstance(d, dict)]
        out.append({"name": name, "current": e.get("current"), "wanted": e.get("wanted"), "latest": e.get("latest"),
                    "dev": e.get("dependencyType") == "devDependencies", "workspace": None, "dependents": deps})
    return out


def parse_yarn_outdated(text: str) -> list[dict]:
    """yarn 1's `outdated --json`: lines of JSON, one of them {"type": "table", "data": {head, body}}."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "table":
            continue
        head = [str(h).lower() for h in obj.get("data", {}).get("head", [])]
        for row in obj.get("data", {}).get("body", []):
            r = dict(zip(head, row))
            out.append({"name": r.get("package"), "current": r.get("current"), "wanted": r.get("wanted"),
                        "latest": r.get("latest"), "dev": r.get("package type") == "devDependencies",
                        "workspace": r.get("workspace")})
    return out


def parse_pip_outdated(text: str) -> list[dict]:
    """`pip list --outdated --format=json` (and uv pip list): [{name, version, latest_version}]."""
    data = extract_json(text)
    return [{"name": e.get("name"), "current": e.get("version"), "latest": e.get("latest_version"), "wanted": None}
            for e in (data if isinstance(data, list) else []) if isinstance(e, dict) and e.get("name")]


def parse_pip_index(text: str) -> list[str]:
    """`pip index versions <name>`: 'Available versions: 2.31.0, 2.30.0, ...'."""
    m = re.search(r"Available versions:\s*(.+)", text or "")
    return [v.strip() for v in m.group(1).split(",") if v.strip()] if m else []


def parse_poetry_outdated(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^([A-Za-z0-9][\w.-]*)\s+(?:\(!\)\s+)?(\d\S*)\s+(\d\S*)(?:\s|$)", line.strip())
        if m:
            out.append({"name": m.group(1), "current": m.group(2), "latest": m.group(3), "wanted": None})
    return out


def parse_cargo_outdated(text: str) -> list[dict]:
    """`cargo outdated --format json`: one {crate_name, dependencies: [...]} per crate."""
    out = []
    for doc in json_stream(text):
        for d in (doc.get("dependencies") or []) if isinstance(doc, dict) else []:
            val = lambda k: None if str(d.get(k) or "") in ("", "---", "Removed") else d.get(k)
            out.append({"name": d.get("name"), "current": val("project"), "wanted": val("compat"),
                        "latest": val("latest"), "dev": d.get("kind") == "Development",
                        "crate": doc.get("crate_name")})
    return out


def parse_cargo_dry_run(text: str) -> list[dict]:
    """`cargo update --dry-run`: 'Updating serde v1.0.100 -> v1.0.190' for each compatible update."""
    out = []
    for m in re.finditer(r"^\s*Updating\s+(\S+)\s+v(\S+)\s+->\s+v(\S+)", text or "", re.M):
        out.append({"name": m.group(1), "current": m.group(2), "wanted": m.group(3), "latest": None})
    return out


def parse_go_list(text: str) -> list[dict]:
    """`go list -m -u -json all`: one module object after another; those with an Update are behind."""
    out = []
    for m in json_stream(text):
        if not isinstance(m, dict) or m.get("Main") or not isinstance(m.get("Update"), dict):
            continue
        new = m["Update"].get("Version")
        out.append({"name": m.get("Path"), "current": m.get("Version"), "wanted": new, "latest": new,
                    "indirect": bool(m.get("Indirect"))})
    return out


def parse_bundle_outdated(text: str) -> list[dict]:
    """`bundle outdated --parseable`: 'rack (newest 3.0.8, installed 2.2.8, requested ~> 2.2)'."""
    out = []
    for m in re.finditer(r"^\s*(\S+) \(newest ([^,)]+), installed ([^,)]+)(?:, requested ([^)]+))?\)",
                         text or "", re.M):
        out.append({"name": m.group(1), "current": m.group(3).strip(), "latest": m.group(2).strip(), "wanted": None,
                    "requested": (m.group(4) or "").strip() or None})
    return out


def parse_dotnet_outdated(text: str) -> list[dict]:
    """`dotnet list package --outdated --format json`."""
    data = extract_json(text)
    out = []
    for proj in (data or {}).get("projects", []) if isinstance(data, dict) else []:
        for fw in proj.get("frameworks") or []:
            for p in fw.get("topLevelPackages") or []:
                if p.get("latestVersion") and p.get("resolvedVersion"):
                    out.append({"name": p.get("id"), "current": p.get("resolvedVersion"),
                                "latest": p.get("latestVersion"), "wanted": None, "project": proj.get("path")})
    return out


def parse_npm_audit(text: str) -> list[dict]:
    """`npm audit --json` (npm 7+: vulnerabilities) or the older shape (advisories), which pnpm also prints."""
    data = extract_json(text)
    out = []
    if not isinstance(data, dict):
        return out
    for name, v in (data.get("vulnerabilities") or {}).items():
        if not isinstance(v, dict):
            continue
        fix = v.get("fixAvailable")
        fixed_in = fix.get("version") if isinstance(fix, dict) and fix.get("name") == name else None
        for via in v.get("via") or []:
            if isinstance(via, dict):
                url = str(via.get("url") or "")
                aid = url.rstrip("/").rsplit("/", 1)[-1] if url else f"npm-{via.get('source')}"
                out.append({"package": name, "id": aid, "severity": via.get("severity") or v.get("severity"),
                            "title": via.get("title"), "fixed_in": fixed_in, "direct": v.get("isDirect")})
    for aid, adv in (data.get("advisories") or {}).items():
        if isinstance(adv, dict):
            m = re.search(r"\d[\w.\-]*", str(adv.get("patched_versions") or ""))
            out.append({"package": adv.get("module_name"), "id": adv.get("github_advisory_id") or f"npm-{aid}",
                        "severity": adv.get("severity"), "title": adv.get("title"),
                        "fixed_in": m.group(0) if m else None, "direct": None})
    return out


def parse_pip_audit(text: str) -> list[dict]:
    data = extract_json(text)
    deps = data.get("dependencies") if isinstance(data, dict) else data
    out = []
    for d in deps if isinstance(deps, list) else []:
        for v in (d.get("vulns") or []) if isinstance(d, dict) else []:
            fixes = sorted(v.get("fix_versions") or [], key=vkey)
            out.append({"package": d.get("name"), "id": v.get("id"), "severity": None,
                        "title": (v.get("aliases") or [None])[0], "fixed_in": fixes[0] if fixes else None,
                        "direct": None})
    return out


def parse_cargo_audit(text: str) -> list[dict]:
    data = extract_json(text)
    out = []
    for v in ((data or {}).get("vulnerabilities") or {}).get("list") or [] if isinstance(data, dict) else []:
        adv, pkg = v.get("advisory") or {}, v.get("package") or {}
        patched = ((v.get("versions") or {}).get("patched") or [])
        m = re.search(r"\d[\w.\-]*", patched[0]) if patched else None
        out.append({"package": pkg.get("name") or adv.get("package"), "id": adv.get("id"), "severity": None,
                    "title": adv.get("title"), "fixed_in": m.group(0) if m else None, "direct": None})
    return out


def parse_custom_outdated(text: str) -> tuple[list[dict], list[dict]]:
    """A custom ecosystem's list: [{name, current, latest, wanted?, dev?, advisories?: [id | {id, severity,
    fixed_in}]}], or {"packages": [...]}."""
    data = extract_json(text)
    if isinstance(data, dict):
        data = data.get("packages")
    pkgs, advs = [], []
    for e in data if isinstance(data, list) else []:
        if not isinstance(e, dict) or not e.get("name") or not e.get("current"):
            continue
        pkgs.append({"name": str(e["name"]), "current": str(e["current"]), "wanted": e.get("wanted"),
                     "latest": e.get("latest"), "dev": bool(e.get("dev"))})
        for adv in kit.as_list(e.get("advisories")):
            a = adv if isinstance(adv, dict) else {"id": str(adv)}
            advs.append({"package": str(e["name"]), "id": a.get("id"), "severity": a.get("severity"),
                         "title": a.get("title"), "fixed_in": a.get("fixed_in"), "direct": True})
    return pkgs, advs


def parse_node_index(text: str) -> str | None:
    data = extract_json(text)
    for e in data if isinstance(data, list) else []:
        if isinstance(e, dict) and e.get("lts"):
            return str(e.get("version", "")).lstrip("v") or None
    return None


def parse_python_eol(text: str) -> str | None:
    data = extract_json(text)
    rows = [e for e in data if isinstance(e, dict) and e.get("latest")] if isinstance(data, list) else []
    rows.sort(key=lambda e: vkey(e["latest"]), reverse=True)
    return str(rows[0]["latest"]) if rows else None


def parse_go_dl(text: str) -> str | None:
    data = extract_json(text)
    for e in data if isinstance(data, list) else []:
        if isinstance(e, dict) and e.get("stable"):
            return str(e.get("version", "")).removeprefix("go") or None
    return None


def parse_rust_channel(text: str) -> str | None:
    m = re.search(r"\[pkg\.rust\][^\[]*?\nversion\s*=\s*\"(\d+\.\d+\.\d+)", text or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------- finding the ecosystems

AUTH_KEY = re.compile(r"_authToken|_auth\b|_password|npmAuthToken|npmAlwaysAuth|//[^\s/]+/:_|"
                      r"(index-url|extra-index-url|repository)\s*=\s*https?://[^\s/@]+:[^\s/@]+@|"
                      r"password\s*=|token\s*=|ClearTextPassword|\$\{?[A-Z_]*TOKEN", re.I)
AUTH_FILES = {".npmrc", ".yarnrc", ".yarnrc.yml", "pip.conf", "pip.ini", ".pypirc", "poetry.toml", "nuget.config",
              "NuGet.Config", "NuGet.config", "settings.xml", "config.toml", "config"}
CI_CMD = re.compile(r"\b(pytest|tox|nox|unittest|jest|vitest|mocha|node --test|cargo (test|build|nextest)|"
                    r"go (test|build|vet)|(npm|pnpm|yarn|bun)( run)? (test|build|ci)|make (test|build|check)|"
                    r"rspec|rake|dotnet (test|build)|mvn|gradle|uv run|poetry run)\b")


def _globs(v) -> list[str]:
    if isinstance(v, dict):
        v = v.get("packages")
    return [str(g) for g in v] if isinstance(v, list) else []


def _under(child: str, parent: str) -> str | None:
    if parent == ".":
        return child
    return child[len(parent) + 1:] if child.startswith(parent + "/") else None


def _pnpm_globs(text: str) -> list[str]:
    out, inside = [], False
    for line in (text or "").splitlines():
        if re.match(r"^packages\s*:", line):
            inside = True
            continue
        if inside:
            m = re.match(r"^\s*-\s*['\"]?([^'\"#]+?)['\"]?\s*(#.*)?$", line)
            if m:
                out.append(m.group(1).strip())
            elif line.strip() and not line.startswith((" ", "\t")):
                break
    return out


def detect(top: Path, is_git: bool) -> dict:
    """What the project uses: ecosystems, runtimes, CI actions, base images, commands, registries."""
    files = kit.all_files(top, is_git)
    names: dict[str, set] = {}
    for f in files:
        d, _, n = f.rpartition("/")
        names.setdefault(d or ".", set()).add(n)
    found = {"ecosystems": [], "listed": [], "runtimes": [], "actions": [], "images": [], "commands": [],
             "notes": [], "auth": []}
    ecos, runtimes, cmds = found["ecosystems"], found["runtimes"], found["commands"]

    def cmd(kind, text, source):
        if not any(c["cmd"] == text and c["kind"] == kind for c in cmds):
            cmds.append({"kind": kind, "cmd": text, "source": source})

    def rt(name, label, file, current, kind, old, new_fmt):
        runtimes.append({"runtime": name, "label": label, "file": file, "current": str(current).strip(),
                         "kind": kind, "line": old, "format": new_fmt})

    # ---- Node.js: npm, pnpm, yarn (and bun, listed)
    pkg_dirs = sorted((d for d, ns in names.items() if "package.json" in ns),
                      key=lambda d: (-1 if d == "." else d.count("/"), d))
    members, loose = set(), []
    for d in pkg_dirs:
        if d in members:
            continue
        ns = names[d]
        pj = read_json_file(top / J(d, "package.json"))
        if not isinstance(pj, dict):
            found["notes"].append(f"{J(d, 'package.json')} isn't valid JSON; left out")
            continue
        globs = _globs(pj.get("workspaces"))
        if "pnpm-workspace.yaml" in ns:
            globs += _pnpm_globs(read_text(top / J(d, "pnpm-workspace.yaml")))
        lock = next((x for x in ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock",
                                 "bun.lockb") if x in ns), None)
        if not lock and d != "." and not globs:
            loose.append(J(d, "package.json"))
            continue
        manager = {"package-lock.json": "npm", "npm-shrinkwrap.json": "npm", "pnpm-lock.yaml": "pnpm",
                   "yarn.lock": "yarn", "bun.lock": "bun", "bun.lockb": "bun"}.get(lock, "npm")
        pm = str(pj.get("packageManager") or "").split("@")[0]
        if pm in ("npm", "pnpm", "yarn", "bun"):
            manager = pm
        berry = manager == "yarn" and (".yarnrc.yml" in ns or "__metadata:" in (read_text(top / J(d, "yarn.lock")) or ""))
        mem = []
        for m in pkg_dirs:
            rel = _under(m, d)
            if m != d and rel and any(fnmatch.fnmatch(rel, g.rstrip("/")) for g in globs):
                mem.append(m)
        members.update(mem)
        eco = {"id": f"{manager}:{d}", "manager": manager, "dir": d, "manifest": J(d, "package.json"),
               "lockfile": J(d, lock) if lock else None, "workspaces": globs, "members": mem,
               "name": pj.get("name"), "berry": berry, "tool": "yarn" if manager == "yarn" else manager,
               "upgradable": manager != "bun"}
        if manager == "bun":
            found["listed"].append({"what": f"bun at {d}", "why": "bun has no outdated command upgrade.py reads; "
                                    "add steps by hand"})
            continue
        ecos.append(eco)
        run = {"npm": "npm run", "pnpm": "pnpm run", "yarn": "yarn"}[manager]
        scripts = pj.get("scripts") if isinstance(pj.get("scripts"), dict) else {}
        if scripts.get("test") and "no test specified" not in str(scripts["test"]):
            cmd("tests", f"{manager} test", f"{J(d, 'package.json')} scripts.test")
        if scripts.get("build"):
            cmd("build", f"{run} build", f"{J(d, 'package.json')} scripts.build")
        hooks = [k for k in ("preinstall", "install", "postinstall", "prepare") if k in scripts]
        install = {"npm": "npm ci" if lock else "npm install",
                   "pnpm": "pnpm install --frozen-lockfile",
                   "yarn": "yarn install --immutable" if berry else "yarn install --frozen-lockfile"}[manager]
        if manager in ("npm", "pnpm") and not hooks:
            install += " --ignore-scripts"
        cmd("install", install + (f"   (in {d})" if d != "." else ""),
            f"{J(d, lock) if lock else 'no lockfile'}" + ("" if hooks or manager == "yarn" else
                                                         "; drop --ignore-scripts if the dependencies need their install scripts"))
        engines = pj.get("engines") if isinstance(pj.get("engines"), dict) else {}
        if engines.get("node"):
            rt("node", "Node.js", J(d, "package.json"), engines["node"], "minimum", None, None)
        if not lock:
            found["notes"].append(f"{J(d, 'package.json')} has no lockfile, so installs aren't reproducible: "
                                  "each sandbox may resolve different versions")
    if loose:
        found["notes"].append(f"{plural(len(loose), 'package.json')} without a lockfile or workspace left out "
                              f"(fixtures or examples?): {', '.join(loose[:5])}" + (" ..." if len(loose) > 5 else ""))

    # ---- Python: pip (requirements, PEP 621), uv, poetry; Pipfile and pdm listed
    req_by_dir: dict[str, list[str]] = {}
    for d, ns in names.items():
        for n in ns:
            if REQ_FILE.match(n):
                req_by_dir.setdefault(d, []).append(J(d, n))
        if d.rsplit("/", 1)[-1] == "requirements":
            parent = d.rpartition("/")[0] or "."
            req_by_dir.setdefault(parent, []).extend(J(d, n) for n in ns if n.endswith(".txt"))
    py_dirs = sorted(set(req_by_dir) | {d for d, ns in names.items() if "pyproject.toml" in ns or "Pipfile" in ns})
    for d in py_dirs:
        ns = names.get(d, set())
        pp = read_toml(read_text(top / J(d, "pyproject.toml"))) if "pyproject.toml" in ns else None
        pp = pp if isinstance(pp, dict) else ({} if "pyproject.toml" in ns else None)
        proj = (pp or {}).get("project") or {}
        poetry = ((pp or {}).get("tool") or {}).get("poetry")
        reqs = sorted(set(req_by_dir.get(d, [])))
        if pp is not None and proj.get("requires-python"):
            rt("python", "Python", J(d, "pyproject.toml"), proj["requires-python"], "minimum", None, None)
        if "Pipfile" in ns:
            found["listed"].append({"what": f"pipenv at {d} (Pipfile)", "why": "pipenv isn't supported; "
                                    "describe it with custom, or add steps by hand"})
        if "pdm.lock" in ns:
            found["listed"].append({"what": f"pdm at {d} (pdm.lock)", "why": "pdm isn't supported; describe it "
                                    "with custom, or add steps by hand"})
            continue
        if "uv.lock" in ns:
            ecos.append({"id": f"uv:{d}", "manager": "uv", "dir": d, "manifest": J(d, "pyproject.toml"),
                         "lockfile": J(d, "uv.lock"), "tool": "uv", "upgradable": True})
            cmd("install", "uv sync --frozen" + (f"   (in {d})" if d != "." else ""), J(d, "uv.lock"))
        elif "poetry.lock" in ns or poetry:
            ecos.append({"id": f"poetry:{d}", "manager": "poetry", "dir": d, "manifest": J(d, "pyproject.toml"),
                         "lockfile": J(d, "poetry.lock") if "poetry.lock" in ns else None, "tool": "poetry",
                         "upgradable": True})
            cmd("install", "poetry install --no-interaction" + (f"   (in {d})" if d != "." else ""),
                "pyproject.toml [tool.poetry]; set --env POETRY_VIRTUALENVS_IN_PROJECT=true at init")
        elif reqs or proj.get("dependencies"):
            manifests = reqs + ([J(d, "pyproject.toml")] if proj.get("dependencies") else [])
            pinned = any(re.search(r"^[A-Za-z0-9][^#\n]*==", read_text(top / r) or "", re.M) for r in reqs)
            ecos.append({"id": f"pip:{d}", "manager": "pip", "dir": d, "manifest": manifests[0],
                         "manifests": manifests, "lockfile": None, "pinned": pinned, "tool": None,
                         "upgradable": True})
            what = " ".join(f"-r {r}" for r in reqs) if reqs else ("-e ." if proj.get("dependencies") else "")
            cmd("install", f"python -m pip install {what}".rstrip(), "with baseline --venv, so it goes into the sandbox")
            if not pinned:
                found["notes"].append(f"{', '.join(manifests)}: nothing is pinned with ==, so installs aren't "
                                      "reproducible and most upgrades happen on their own at install time")
        if pp is not None or reqs:
            text = " ".join((read_text(top / r) or "") for r in reqs) + json.dumps(pp or {})
            where = f"   (in {d})" if d != "." else ""
            if re.search(r"\bpytest\b", text):
                cmd("tests", "python -m pytest -rA" + where, "pytest is a dependency; -rA prints every test's result")
            elif any(n.startswith("test") and n.endswith(".py") for n in names.get(J(d, "tests"), set())):
                cmd("tests", "python -m unittest discover -v -s tests" + where,
                    "test files in tests/, and pytest isn't a dependency")
        if ".python-version" in ns:
            v = (read_text(top / J(d, ".python-version")) or "").strip().splitlines()
            if v:
                rt("python", "Python", J(d, ".python-version"), v[0], "pin", v[0], "same")

    # ---- Cargo
    cargo_dirs = sorted((d for d, ns in names.items() if "Cargo.toml" in ns),
                        key=lambda d: (-1 if d == "." else d.count("/"), d))
    cmembers = set()
    for d in cargo_dirs:
        if d in cmembers:
            continue
        ct = read_toml(read_text(top / J(d, "Cargo.toml"))) or {}
        ws = ct.get("workspace") if isinstance(ct.get("workspace"), dict) else None
        mem = []
        if ws:
            for m in cargo_dirs:
                rel = _under(m, d)
                if m != d and rel and any(fnmatch.fnmatch(rel, g) for g in ws.get("members") or []):
                    mem.append(m)
        cmembers.update(mem)
        lock = "Cargo.lock" in names[d]
        ecos.append({"id": f"cargo:{d}", "manager": "cargo", "dir": d, "manifest": J(d, "Cargo.toml"),
                     "manifests": [J(d, "Cargo.toml")] + [J(m, "Cargo.toml") for m in mem],
                     "lockfile": J(d, "Cargo.lock") if lock else None, "tool": "cargo", "upgradable": True})
        cmd("install", "cargo fetch" + (f"   (in {d})" if d != "." else ""), J(d, "Cargo.toml"))
        cmd("build", "cargo build --locked", J(d, "Cargo.toml"))
        cmd("tests", "cargo test --locked", J(d, "Cargo.toml"))
        for f in ("rust-toolchain.toml", "rust-toolchain"):
            if f in names[d]:
                t = read_text(top / J(d, f)) or ""
                m = re.search(r'^\s*channel\s*=\s*"([^"]+)"', t, re.M) if f.endswith(".toml") else \
                    re.match(r"\s*(\S+)", t)
                if m:
                    line = m.group(0).strip()
                    rt("rust", "Rust", J(d, f), m.group(1), "pin", line, "same")

    # ---- Go
    for d, ns in sorted(names.items()):
        if "go.mod" not in ns:
            continue
        gm = read_text(top / J(d, "go.mod")) or ""
        ecos.append({"id": f"go:{d}", "manager": "go", "dir": d, "manifest": J(d, "go.mod"),
                     "lockfile": J(d, "go.sum") if "go.sum" in ns else None, "tool": "go", "upgradable": True})
        cmd("install", "go mod download" + (f"   (in {d})" if d != "." else ""), J(d, "go.mod"))
        cmd("build", "go build ./...", J(d, "go.mod"))
        cmd("tests", "go test ./...", J(d, "go.mod"))
        m = re.search(r"^go\s+(\S+)", gm, re.M)
        if m:
            rt("go", "Go", J(d, "go.mod"), m.group(1), "minimum", None, None)
        m = re.search(r"^toolchain\s+go(\S+)", gm, re.M)
        if m:
            rt("go", "Go", J(d, "go.mod"), m.group(1), "pin", m.group(0), "toolchain")

    # ---- Ruby
    for d, ns in sorted(names.items()):
        if "Gemfile" in ns:
            ecos.append({"id": f"bundler:{d}", "manager": "bundler", "dir": d, "manifest": J(d, "Gemfile"),
                         "lockfile": J(d, "Gemfile.lock") if "Gemfile.lock" in ns else None, "tool": "bundle",
                         "upgradable": True})
            cmd("install", "bundle install", J(d, "Gemfile"))
            if "Rakefile" in ns:
                cmd("tests", "bundle exec rake test", J(d, "Rakefile"))
            if "spec" in {p.rsplit("/", 1)[-1] for p in names if _under(p, d)}:
                cmd("tests", "bundle exec rspec", J(d, "spec/"))
        if ".ruby-version" in ns:
            v = (read_text(top / J(d, ".ruby-version")) or "").strip().splitlines()
            if v:
                rt("ruby", "Ruby", J(d, ".ruby-version"), v[0], "pin", v[0], "same")

    # ---- .NET
    for f in files:
        if PROJ_FILE.search(f) and "<PackageReference" in (read_text(top / f) or ""):
            d = f.rpartition("/")[0] or "."
            lock = J(d, "packages.lock.json") if "packages.lock.json" in names.get(d, set()) else None
            ecos.append({"id": f"dotnet:{f}", "manager": "dotnet", "dir": d, "manifest": f, "lockfile": lock,
                         "tool": "dotnet", "upgradable": True})
            cmd("install", "dotnet restore", f)
            cmd("build", "dotnet build", f)
            cmd("tests", "dotnet test", f)
        if f.rsplit("/", 1)[-1] == "Directory.Packages.props":
            found["notes"].append(f"{f}: central package versions; dotnet add package updates them there")

    # ---- Maven and Gradle: listed
    for f in files:
        base = f.rsplit("/", 1)[-1]
        if base in ("pom.xml", "build.gradle", "build.gradle.kts"):
            found["listed"].append({"what": f"{'Maven' if base == 'pom.xml' else 'Gradle'} ({f})",
                                    "why": "listed only: upgrade it by hand with add, or describe it with custom"})

    # ---- runtimes in pin files
    for d, ns in names.items():
        for f, name, label in ((".nvmrc", "node", "Node.js"), (".node-version", "node", "Node.js")):
            if f in ns:
                v = (read_text(top / J(d, f)) or "").strip().splitlines()
                if v:
                    rt(name, label, J(d, f), v[0], "pin", v[0], "same")
        if ".tool-versions" in ns:
            for line in (read_text(top / J(d, ".tool-versions")) or "").splitlines():
                m = re.match(r"^\s*(nodejs|node|python|ruby|golang|go|rust)\s+(\S+)", line)
                if m:
                    name = {"nodejs": "node", "golang": "go"}.get(m.group(1), m.group(1))
                    label = {"node": "Node.js", "python": "Python", "ruby": "Ruby", "go": "Go", "rust": "Rust"}[name]
                    rt(name, label, J(d, ".tool-versions"), m.group(2), "pin", line.strip(), "same")

    # ---- CI actions and base images
    for f in files:
        if re.match(r"^\.github/workflows/[^/]+\.ya?ml$", f):
            for i, line in enumerate((read_text(top / f) or "").splitlines(), 1):
                m = re.match(r"^\s*-?\s*uses:\s*['\"]?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)((?:/[^@\s'\"]*)?)@"
                             r"([^\s'\"#]+)", line)
                if m:
                    ref = m.group(4)
                    pin = "sha" if re.fullmatch(r"[0-9a-f]{40}", ref) else \
                          ("tag" if re.match(r"^v?\d", ref) else "branch")
                    found["actions"].append({"file": f, "line": i, "text": line.rstrip(), "owner": m.group(1),
                                             "repo": m.group(2), "path": m.group(3), "ref": ref, "pin": pin})
                c = line.strip().removeprefix("- ").strip()
                c = c[4:].strip() if c.startswith("run:") else c
                if CI_CMD.search(c) and not re.match(r"^[\w.-]+:(\s|$)", c) and not c.startswith("#") and len(c) < 120:
                    cmd("ci", c.strip("'\" |"), f)
        base = f.rsplit("/", 1)[-1]
        if base == "Dockerfile" or base.startswith("Dockerfile.") or base.endswith(".dockerfile"):
            stages = set()
            for i, line in enumerate((read_text(top / f) or "").splitlines(), 1):
                m = re.match(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", line, re.I)
                if m:
                    img = m.group(1)
                    if img.lower() != "scratch" and "$" not in img and img not in stages:
                        found["images"].append({"file": f, "line": i, "text": line.rstrip(), "image": img})
                    if m.group(2):
                        stages.add(m.group(2))
        if base == "Makefile":
            text = read_text(top / f) or ""
            for t in ("test", "check", "build"):
                if re.search(rf"^{t}\s*:", text, re.M):
                    cmd("tests" if t != "build" else "build", f"make {t}", f)

    # ---- private registries and their credentials
    for f in files:
        base = f.rsplit("/", 1)[-1]
        if base not in AUTH_FILES or (base in ("config", "config.toml") and "/.cargo/" not in "/" + f and
                                      not f.startswith(".cargo/") and "/.bundle/" not in "/" + f
                                      and not f.startswith(".bundle/")):
            continue
        for i, line in enumerate((read_text(top / f) or "").splitlines(), 1):
            low = line.lower()
            if AUTH_KEY.search(line):
                found["auth"].append(f"{f} line {i} has credentials (or a token variable) for a registry")
                break
            if re.search(r"(registry|index-url|extra-index-url|source)\s*[=:]", low) and not re.search(
                    r"registry\.npmjs\.org|pypi\.org|rubygems\.org|crates\.io|nuget\.org|yarnpkg\.com", low):
                found["auth"].append(f"{f} line {i} points at a private registry")
                break
    return found


# ---------------------------------------------------------------- the run's files


def load(a) -> tuple[Path, dict]:
    return kit.load_run(a.workspace)


def load_found(ws: Path) -> dict:
    return kit.load_json(ws / "ecosystems.json", None) or kit.fail("no ecosystems.json: run init first")


def need_baseline(ws: Path) -> dict:
    b = kit.load_json(ws / "baseline.json", None)
    if not b:
        kit.fail("no baseline yet: run baseline first")
    if not b.get("ok"):
        kit.fail(f"the baseline isn't usable ({b.get('problem')}); fix it and run baseline again")
    return b


def load_plan(ws: Path, required: bool = True) -> dict | None:
    p = kit.load_json(ws / "plan.json", None)
    if p is None:
        if required:
            kit.fail("no plan yet: run plan first")
        return None
    problems = []
    ids = [s.get("id") for s in p.get("steps", [])]
    if len(set(ids)) != len(ids):
        problems.append("two steps share an id")
    for s in p.get("steps", []):
        if s.get("status") not in STATES:
            problems.append(f"{s.get('id')} has status {s.get('status')!r} (one of {', '.join(STATES)})")
        for u in s.get("upgrades", []):
            if u not in p.get("upgrades", {}):
                problems.append(f"{s.get('id')} lists {u}, which isn't in upgrades")
    for sid in p.get("accepted", []):
        st = next((s for s in p["steps"] if s["id"] == sid), None)
        if not st or st["status"] != "accepted" or not (ws / "steps" / f"{sid}.patch").is_file():
            problems.append(f"accepted lists {sid}, but it isn't an accepted step with a patch in steps/")
    if problems:
        kit.fail("plan.json: " + "; ".join(problems) + ". Fix it by hand, or start again with plan --force.")
    for k, v in (("accepted", []), ("holds", {}), ("left_out", []), ("bisects", {})):
        p.setdefault(k, v)
    return p


def mutate_plan(ws: Path, fn):
    with kit.locked(ws):
        plan = load_plan(ws)
        out = fn(plan)
        kit.save_json(ws / "plan.json", plan)
    return out


def get_step(plan: dict, sid: str) -> dict:
    for s in plan["steps"]:
        if s["id"].lower() == str(sid).lower():
            return s
    kit.fail(f"no step {sid}; run status to see the steps")


def step_ups(plan: dict, st: dict) -> list[dict]:
    return [plan["upgrades"][u] for u in st["upgrades"]]


def up_text(u: dict) -> str:
    if u.get("from") and u.get("to"):
        return f"{u['name']} {u['from']} → {u['to']}"
    return u["name"]


def names_of(ups: list[dict], limit: int = 6) -> str:
    shown = [up_text(u) for u in ups[:limit]]
    return ", ".join(shown) + (f" and {len(ups) - limit} more" if len(ups) > limit else "")


def title_for(ups: list[dict], group: str) -> str:
    if not ups:
        return "Nothing left"
    if len(ups) == 1 or len({u.get("link") for u in ups}) == 1 and ups[0].get("link"):
        if len(ups) == 1:
            u = ups[0]
            where = f" in {u['manifest']}" if group == "runtime" else ""
            return f"Upgrade {u['name']} {u['from']} → {u['to']}{where}" if u.get("from") else f"Upgrade {u['name']}"
        names = [u["name"] for u in ups]
        joined = ", ".join(names[:-1]) + " and " + names[-1]
        froms = {u["from"] for u in ups}
        return f"Upgrade {joined} {ups[0]['from']} → {ups[0]['to']}" if len(froms) == 1 else \
            f"Upgrade {joined} to {ups[0]['to']}"
    kinds = {u["kind"] for u in ups}
    kind = "patch and minor" if kinds >= {"patch", "minor"} or "major" in kinds else next(iter(kinds))
    return f"Upgrade {len(ups)} {kind} {'dev ' if group == 'dev' else ''}dependencies"


def add_decision(ws: Path, text: str) -> None:
    with kit.locked(ws):
        d = kit.load_json(ws / "decisions.json", [])
        if not any(x["text"] == text for x in d):
            d.append({"text": text, "at": kit.now()})
        kit.save_json(ws / "decisions.json", d)


def project_change(cfg: dict) -> str | None:
    """Has the real project moved since init? Sandboxes copy it as it is now."""
    if not cfg.get("git"):
        return None
    top = Path(cfg["repo"])
    head = kit.git(top, "rev-parse", "HEAD", check=False).strip()
    out = []
    if (cfg.get("head") or "") and head != cfg["head"]:
        out.append(f"HEAD moved from {cfg['head'][:10]} to {head[:10] or 'nothing'}")
    if kit.git_status(top) != cfg.get("init_status", ""):
        out.append("the working tree differs from when the run began")
    return "; ".join(out) or None


def warn_if_changed(cfg: dict) -> None:
    ch = project_change(cfg)
    if ch:
        print(f"note: the project changed since init ({ch}). Sandboxes copy it as it is now, so accepted "
              "patches may not apply. If you didn't change it, find out what did.")


# ---------------------------------------------------------------- sandboxes and trials


def run_cache(cfg: dict) -> Path:
    p = Path(cfg["sandboxes_home"]) / cfg["run_id"] / "_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def venv_bin(root: Path) -> Path:
    return root / "venv" / ("Scripts" if os.name == "nt" else "bin")


def box_env(cfg: dict, root: Path, base: dict | None) -> dict:
    """Package caches shared by the run's sandboxes, and global installs kept inside the sandbox."""
    cache, home = run_cache(cfg), root / "home"
    env = {"npm_config_cache": str(cache / "npm"), "npm_config_store_dir": str(cache / "pnpm"),
           "YARN_CACHE_FOLDER": str(cache / "yarn"), "PIP_CACHE_DIR": str(cache / "pip"),
           "UV_CACHE_DIR": str(cache / "uv"), "POETRY_CACHE_DIR": str(cache / "poetry"),
           "npm_config_prefix": str(home / ".npm-global"), "BUNDLE_PATH": str(home / ".bundle"),
           "npm_config_update_notifier": "false", "npm_config_fund": "false", "npm_config_audit": "false",
           "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if base and base.get("venv"):
        env["VIRTUAL_ENV"] = str(root / "venv")
        env["PATH"] = str(venv_bin(root)) + os.pathsep + os.environ.get("PATH", "")
    return env


def make_box(ws: Path, cfg: dict, name: str, base: dict | None, rev: str | None = None) -> Path:
    root = kit.box_path(cfg, name)
    box = kit.make_sandbox(cfg, ws, name, rev=rev, env=box_env(cfg, root, base))
    with (box / "repo" / ".git" / "info" / "exclude").open("a", encoding="utf-8", newline="\n") as f:
        f.write("".join(d + "\n" for d in DEP_DIRS))
    return box


def new_box(ws: Path, cfg: dict, label: str, base: dict | None, rev: str | None = None) -> Path:
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", label)[:24]
    return make_box(ws, cfg, f"{label}-{secrets.token_hex(3)}", base, rev)


def drop_box(ws: Path, cfg: dict, box: Path) -> None:
    kit.drop_sandbox(ws, cfg, box.name)


def rebase_box(box: Path, message: str) -> str:
    """Commit the sandbox as it stands and make that its base, so box_diff shows only what comes next."""
    repo = box / "repo"
    kit.git(repo, *kit.RAW, "add", "-A")
    kit.git(repo, *kit.BASE_IDENTITY, "commit", "-q", "--no-verify", "--allow-empty", "-m", message)
    sha = kit.git(repo, "rev-parse", "HEAD").strip()
    (box / "base.txt").write_text(sha + "\n", encoding="utf-8")
    return sha


def changed_paths(box: Path) -> set[str]:
    """The files changed in the sandbox since its base: one git status, cheaper than a full diff."""
    out = kit.git(box / "repo", *kit.RAW, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    return {e[3:] for e in out.split("\0") if len(e) > 3}


def run_cmd(box: Path, cmd: list[str], timeout: float, cwd: str | None = None, retry: bool = False) -> dict:
    cwd = None if cwd in (None, "", ".") else cwd
    r = kit.run_in(box, cmd, 1, timeout, cwd)["runs"][0]
    if retry and kit.failed(r):   # one more go: registries fail now and then
        r = kit.run_in(box, cmd, 1, timeout, cwd)["runs"][0]
        r["retried"] = True
    r["cmd"] = cmd
    return r


def run_tool(box: Path, cmd: list[str], cwd: str | None = None, timeout: float = 900,
             extra_env: dict | None = None) -> tuple[int | None, str, str]:
    """Run a tool in the sandbox's environment with stdout and stderr apart (JSON comes on stdout)."""
    r = kit.runner()
    env = r.build_env(box)
    env.update(extra_env or {})
    where = box / "repo" / cwd if cwd and cwd != "." else box / "repo"
    try:
        p = subprocess.run(r.resolve(cmd, env), cwd=str(where), env=env, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except OSError as e:
        return 127, "", f"could not start {cmd[0]}: {e}"
    except subprocess.TimeoutExpired:
        return None, "", f"{cmd[0]} timed out after {timeout:.0f} s"
    dec = lambda b: (b or b"").decode("utf-8", "replace").replace("\r\n", "\n")
    return p.returncode, dec(p.stdout), dec(p.stderr)


def check_venv(py: list[str]) -> None:
    """Stop with a plain message when the Python given can't make a virtual environment."""
    msg = ("{who} has no venv module (Python's embeddable package doesn't), so it can't make a virtual "
           "environment for the sandboxes. Pass a full Python with --venv, like --venv \"py -3.12\" or "
           "--venv /usr/bin/python3, or install with the project's own tool (uv sync, poetry install).")
    if [os.path.normcase(x) for x in py] == [os.path.normcase(sys.executable)]:
        if importlib.util.find_spec("venv") is None or importlib.util.find_spec("ensurepip") is None:
            kit.fail(msg.format(who=sys.executable))
        return
    try:
        r = subprocess.run([*py, "-c", "import venv, ensurepip"], capture_output=True, timeout=60)
        ok = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        kit.fail(msg.format(who=show_cmd(py)))


def merge_actions(acts: list[dict]) -> list[dict]:
    """Package-manager commands that take several packages at once run once: npm install a@1 b@2."""
    out: list[dict] = []
    for a in acts:
        a = json.loads(json.dumps(a))
        prev = out[-1] if out else None
        if (a["kind"] == "run" and prev and prev["kind"] == "run" and a.get("merge") and prev.get("merge")
                and prev["prefix"] == a["prefix"] and prev.get("suffix", []) == a.get("suffix", [])
                and prev.get("cwd") == a.get("cwd")):
            prev["args"] += [x for x in a["args"] if x not in prev["args"]]
        else:
            out.append(a)
    return out


def act_cmd(a: dict) -> list[str]:
    return [*a["prefix"], *a.get("args", []), *a.get("suffix", [])]


def do_edit(repo: Path, act: dict) -> tuple[bool, str]:
    p = repo / act["file"]
    if not p.is_file():
        return False, f"{act['file']} isn't there"
    text = p.read_bytes().decode("utf-8", "surrogateescape")
    old, new = act["old"], act["new"]
    if old not in text and "\n" in old and old.replace("\n", "\r\n") in text:
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    if old not in text:
        return False, f"{act['file']} doesn't contain {old!r}"
    p.write_bytes(text.replace(old, new).encode("utf-8", "surrogateescape"))
    return True, ""


def apply_accepted(ws: Path, box: Path, accepted: list[str]) -> None:
    for sid in accepted:
        ok, msg = kit.apply_patch(box / "repo", kit.read_patch(ws / "steps" / f"{sid}.patch"))
        if not ok:
            kit.fail(f"the accepted patch for {sid} doesn't apply to a fresh copy of the project ({msg}). "
                     "Has the project changed since init? Run status.")
    if accepted:
        rebase_box(box, "accepted upgrades")


def apply_upgrades(box: Path, ups: list[dict], timeout: float) -> tuple[bool, str, str]:
    for act in merge_actions([a for u in ups for a in u.get("actions", [])]):
        if act["kind"] == "edit":
            ok, msg = do_edit(box / "repo", act)
            if not ok:
                return False, f"the edit didn't apply: {msg}", ""
        else:
            r = run_cmd(box, act_cmd(act), timeout, act.get("cwd"), retry=True)
            if kit.failed(r):
                how = "timed out" if r["timed_out"] else f"exited {r['code']}"
                return False, f"`{show_cmd(act_cmd(act))}` {how}", r["out"]
    return True, "", ""


def probe_runtime(box: Path, ups: list[dict], timeout: float) -> tuple[bool, str]:
    for u in ups:
        pr = u.get("probe")
        if not pr:
            continue
        r = run_cmd(box, pr["cmd"], min(timeout, 300), pr.get("cwd"))
        first = (r["out"].strip().splitlines() or ["nothing"])[0]
        if kit.failed(r) or pr["want"] not in r["out"]:
            return False, (f"`{show_cmd(pr['cmd'])}` printed {first!r}, not {pr['want']}: the toolchain here "
                           f"doesn't follow {u['manifest']}, so the tests can't run on the new version")
    return True, ""


def make_venv(box: Path, base: dict) -> dict | None:
    if not base.get("venv"):
        return None
    return run_cmd(box, [*base["venv"], "-m", "venv", str(box / "venv")], 900)


def run_install(box: Path, base: dict) -> tuple[list[dict], bool]:
    out = []
    v = make_venv(box, base)
    if v is not None:
        out.append(v)
        if kit.failed(v):
            return out, False
    for c in base["commands"]["install"]:
        r = run_cmd(box, c, base["timeout"], retry=True)
        out.append(r)
        if kit.failed(r):
            return out, False
    return out, True


def run_tests_once(box: Path, base: dict) -> list[dict]:
    res = []
    for c in base["commands"]["tests"]:
        junit = base.get("junit")
        if junit:
            for p in (box / "repo").glob(junit):
                p.unlink(missing_ok=True)
        r = run_cmd(box, c, base["timeout"])
        parsed = parse_junit(sorted((box / "repo").glob(junit))) if junit else parse_tests(r["out"])
        res.append({"cmd": c, "code": r["code"], "timed_out": r["timed_out"], "secs": r["secs"], **parsed,
                    "out": r["out"]})
    return res


def summarize_unit(cmd: list[str], runs: list[dict]) -> dict:
    """What one test command showed over the baseline's runs."""
    P = [set(r["passed"]) for r in runs]
    F = [set(r["failed"]) for r in runs]
    ever_fail = set().union(*F)
    failing = set.intersection(*F) if F else set()
    always = (set.intersection(*P) if P else set()) - ever_fail
    unstable = (set().union(*P) - always - ever_fail) | (ever_fail - failing)
    oks = [not r["timed_out"] and r["code"] == 0 for r in runs]
    cmd_ok = True if all(oks) else (False if not any(oks) else None)
    parsed = any(P) or any(F)
    judge = "tests" if parsed else ("exit" if cmd_ok else "none")
    fmt = next((r["format"] for r in runs if r.get("format")), None)
    return {"cmd": cmd, "always_pass": sorted(always), "ever_fail": sorted(ever_fail), "failing": sorted(failing),
            "flaky": sorted(unstable), "cmd_ok": cmd_ok, "judge": judge, "format": fmt,
            "runs": [{k: r[k] for k in ("code", "timed_out", "secs", "format")} | {"passed": len(r["passed"]),
                     "failed": len(r["failed"])} for r in runs],
            "secs": round(sum(r["secs"] for r in runs) / max(1, len(runs)), 2)}


def regressions(unit: dict, run: dict) -> list[str]:
    """What passed at the baseline and doesn't now, for one test command's run."""
    if unit["judge"] == "none":
        return []
    out = sorted(set(run["failed"]) - set(unit["ever_fail"]))
    crashed = run["timed_out"] or run["code"] != 0
    if unit["judge"] == "tests":
        seen = set(run["passed"]) | set(run["failed"]) | set(run["skipped"])
        if seen or crashed:
            gone = set(unit["always_pass"]) - set(run["passed"]) - set(run["failed"])
            skipped = sorted(gone & set(run["skipped"]))
            unrun = sorted(gone - set(run["skipped"]))
            out += [f"{t} (now skipped)" for t in skipped]
            if unrun and not seen:
                out.append(f"`{show_cmd(unit['cmd'])}` ran none of the {len(unit['always_pass'])} tests that pass "
                           "at the baseline")
            else:
                out += [f"{t} (didn't run)" for t in unrun]
    if not out and unit["cmd_ok"] and crashed:
        how = "timed out" if run["timed_out"] else f"exited {run['code']}"
        out.append(f"`{show_cmd(unit['cmd'])}` {how}; it passed at the baseline")
    return out


def save_log(ws: Path, tid: str, phase: str, text: str) -> str:
    path = ws / "trials" / f"{tid}-{phase}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text[-SAVED_TAIL:], encoding="utf-8")
    return str(path)


def dep_file_matcher(found: dict):
    paths = set()
    for e in found.get("ecosystems", []):
        paths.update(x for x in [e.get("manifest"), e.get("lockfile"), *e.get("manifests", [])] if x)
    def is_dep(f: str) -> bool:
        b = f.rsplit("/", 1)[-1]
        return f in paths or b in LOCKFILES or b in MANIFESTS or bool(REQ_FILE.match(b)) or bool(PROJ_FILE.search(b))
    return is_dep


def split_patch(patch: str) -> dict[str, str]:
    out = {}
    for part in re.split(r"(?m)^(?=diff --git )", patch):
        m = re.match(r"diff --git a/(.+?) b/(.+)$", part.split("\n", 1)[0])
        if m:
            out[m.group(2)] = part
    return out


def new_trial_id(ws: Path) -> str:
    d = ws / "trials"
    with kit.locked(ws):
        d.mkdir(parents=True, exist_ok=True)
        n = 1 + max([int(m.group(1)) for f in d.glob("T*.json") if (m := re.match(r"T(\d+)\.json$", f.name))] or [0])
        kit.save_json(d / f"T{n}.json", {"id": f"T{n}", "running": True})
    return f"T{n}"


def trial(ws: Path, cfg: dict, base: dict, *, step: str | None, ups: list[dict], migrations=(), label: str = "try",
          on_top: list[str] | None = None, rev: str | None = None, keep: bool = False) -> tuple[dict, str]:
    """One proof: a fresh sandbox, the accepted patches, these upgrades (and migration patches), then
    install, build and the tests (up to the baseline's number of runs). Returns the record and the patch."""
    tid = new_trial_id(ws)
    if on_top is None:
        on_top = [] if rev else list(load_plan(ws)["accepted"])
    rec = {"id": tid, "step": step, "upgrades": [u["id"] for u in ups], "label": label, "on_top_of": on_top,
           "rev": rev, "migrations": [Path(m).name for m in migrations], "verdict": None, "phase": None,
           "detail": "", "broken": [], "notes": [], "runs": 0, "kept": None, "started": kit.now()}
    t0 = time.monotonic()
    found = kit.load_json(ws / "ecosystems.json", {})
    box = new_box(ws, cfg, f"{label}-{step or 'branch'}".lower(), base, rev)
    patch = ""

    def done(verdict, phase=None, detail="", broken=(), output=None):
        rec.update(verdict=verdict, phase=phase, detail=detail, broken=list(broken))
        if output:
            rec["log"] = save_log(ws, tid, phase or "out", output)
    try:
        if not rev:
            apply_accepted(ws, box, on_top)
        ok, detail, out = apply_upgrades(box, ups, base["timeout"])
        if ok:
            for m in migrations:
                ok, msg = kit.apply_patch(box / "repo", kit.read_patch(m))
                if not ok:
                    detail = f"the migration patch {Path(m).name} doesn't apply: {msg}"
                    break
        if not ok:
            done("install-fail", "apply", detail, [], out)
            return rec, ""
        applied = changed_paths(box)
        ok, detail = probe_runtime(box, ups, base["timeout"])
        if not ok:
            done("install-fail", "runtime", detail)
            return rec, ""
        installs, ok = run_install(box, base)
        if not ok:
            r = installs[-1]
            how = "timed out" if r["timed_out"] else f"exited {r['code']}"
            done("install-fail", "install", f"`{show_cmd(r['cmd'])}` {how}", [], r["out"])
            return rec, ""
        is_dep = dep_file_matcher(found)
        parts = split_patch(kit.box_diff(box))
        kept = {f: p for f, p in parts.items() if f in applied or is_dep(f)}
        dropped = sorted(set(parts) - set(kept))
        if dropped:
            rec["notes"].append(f"the install also changed {', '.join(dropped[:5])}; not kept")
        patch = "".join(kept[f] for f in sorted(kept))
        for i, c in enumerate(base["commands"]["build"]):
            r = run_cmd(box, c, base["timeout"])
            if kit.failed(r) and base["build"][i]["ok"]:
                how = "timed out" if r["timed_out"] else f"exited {r['code']}"
                done("fail", "build", f"the build `{show_cmd(c)}` {how}; it passed at the baseline",
                     [f"the build: `{show_cmd(c)}`"], r["out"])
                return rec, patch
        keep_ids = None
        for k in range(base["runs"]):
            results = run_tests_once(box, base)
            rec["runs"] = k + 1
            regs = []
            for unit, res in zip(base["tests"], results):
                regs += regressions(unit, res)
            passed_now = set().union(*[set(r["passed"]) for r in results]) if results else set()
            ids = set().union(*[set(u["always_pass"]) for u in base["tests"]]) & passed_now
            keep_ids = ids if keep_ids is None else keep_ids & ids
            if regs:
                text = "\n".join(f"$ {show_cmd(r['cmd'])}\n# exit {r['code']}\n{r['out']}" for r in results)
                done("fail", "tests", f"{plural(len(regs), 'test')} that passed at the baseline "
                     f"{'fails' if len(regs) == 1 else 'fail'} (run {k + 1} of {base['runs']})", regs, text)
                rec["kept"] = len(keep_ids)
                return rec, patch
        rec["kept"] = len(keep_ids or set())
        if not rev:
            save_tested_files(ws, tid, box, sorted(kept))
        done("pass", None, f"everything that passed at the baseline still passes ({plural(base['runs'], 'run')})")
        return rec, patch
    finally:
        rec["secs"] = round(time.monotonic() - t0, 1)
        if keep:
            rec["box"] = str(box)
        else:
            drop_box(ws, cfg, box)
        kit.save_json(ws / "trials" / f"{tid}.json", rec)


def save_tested_files(ws: Path, tid: str, box: Path, files: list[str]) -> None:
    """Keep the files a passing trial changed, exactly as it tested them; land commits these."""
    dst = ws / "trials" / f"{tid}.files"
    kit.rmtree(dst)
    dst.mkdir(parents=True)
    gone = []
    for f in files:
        src = box / "repo" / f
        if src.exists() or src.is_symlink():
            kit.copy_path(src, dst / f)
        else:
            gone.append(f)
    kit.save_json(dst.with_name(f"{tid}.files.json"), {"files": [f for f in files if f not in gone], "deleted": gone})


def drop_tested_files(ws: Path, tid: str) -> None:
    kit.rmtree(ws / "trials" / f"{tid}.files")
    (ws / "trials" / f"{tid}.files.json").unlink(missing_ok=True)


def trial_line(rec: dict) -> str:
    if rec["verdict"] == "pass":
        return "pass"
    if rec["verdict"] == "install-fail":
        return f"install-fail ({rec['detail']})"
    shown = ", ".join(rec["broken"][:3]) + (f" and {len(rec['broken']) - 3} more" if len(rec["broken"]) > 3 else "")
    return f"fail ({shown})"


def print_failure(rec: dict) -> None:
    if rec["verdict"] == "fail" and rec["phase"] == "tests":
        print(f"  {rec['detail']}:")
        for b in rec["broken"][:15]:
            print(f"    {b}")
        if len(rec["broken"]) > 15:
            print(f"    and {len(rec['broken']) - 15} more")
    else:
        print(f"  {rec['detail']}")
    if rec.get("log"):
        text = Path(rec["log"]).read_text(encoding="utf-8", errors="replace")
        print(f"  --- last lines (full output in {rec['log']}) ---")
        for ln in tail(text, 20).splitlines():
            print(f"  {ln}")


def accept(ws: Path, plan: dict, st: dict, rec: dict, patch: str) -> bool:
    """Accept a passing trial's patch for a step, if nothing was accepted since the trial began."""
    st["tries"].append(rec["id"])
    st["last"] = {k: rec[k] for k in ("id", "verdict", "phase", "detail", "broken")}
    if rec["verdict"] != "pass":
        st["status"] = rec["verdict"]
        return False
    if plan["accepted"] != rec["on_top_of"]:
        st["status"] = "pending"
        st["note"] = "it passed, but another step was accepted while it ran; try it again"
        drop_tested_files(ws, rec["id"])
        return False
    kit.write_patch(ws / "steps" / f"{st['id']}.patch", patch)
    kit.rmtree(ws / "steps" / f"{st['id']}.files")
    (ws / "trials" / f"{rec['id']}.files").rename(ws / "steps" / f"{st['id']}.files")
    os.replace(ws / "trials" / f"{rec['id']}.files.json", ws / "steps" / f"{st['id']}.files.json")
    st["status"] = "accepted"
    st["patch_files"] = kit.changed_files(patch)
    st.pop("note", None)
    plan["accepted"].append(st["id"])
    return True


def unaccept(ws: Path, plan: dict, sid: str) -> list[str]:
    """Take a step out of the accepted ones, and every step accepted after it (their patches built on it)."""
    if sid not in plan["accepted"]:
        return []
    i = plan["accepted"].index(sid)
    gone, plan["accepted"] = plan["accepted"][i:], plan["accepted"][:i]
    for g in gone:
        s = get_step(plan, g)
        s["status"] = "pending"
        if g != sid:
            s["note"] = f"{sid} changed, and this was accepted on top of it; try it again"
        (ws / "steps" / f"{g}.patch").unlink(missing_ok=True)
        kit.rmtree(ws / "steps" / f"{g}.files")
        (ws / "steps" / f"{g}.files.json").unlink(missing_ok=True)
    return gone


# ---------------------------------------------------------------- what each ecosystem's tool says, and how to apply


def node_dep(repo: Path, eco: dict, name: str, workspace: str | None) -> dict:
    """Where a package is declared: its range, whether it's a dev dependency, and in which workspace."""
    for d in [eco["dir"], *eco.get("members", [])]:
        pj = read_json_file(repo / J(d, "package.json")) or {}
        if workspace and d != eco["dir"] and pj.get("name") != workspace:
            continue
        if workspace and d == eco["dir"] and pj.get("name") != workspace and eco.get("members"):
            continue
        for sec in ("dependencies", "devDependencies", "optionalDependencies"):
            spec = (pj.get(sec) or {}).get(name)
            if spec is not None:
                return {"spec": str(spec), "dev": sec == "devDependencies",
                        "workspace": pj.get("name") if d != eco["dir"] else None}
    return {"spec": None, "dev": False, "workspace": workspace}


def py_deps(pp: dict) -> dict:
    out = {}
    proj = (pp or {}).get("project") or {}

    def add(req, dev, group):
        m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", str(req))
        if m:
            out.setdefault(norm_name(m.group(1)), {"name": m.group(1), "spec": str(req), "dev": dev, "group": group})
    for r in proj.get("dependencies") or []:
        add(r, False, "main")
    for extra, reqs in (proj.get("optional-dependencies") or {}).items():
        for r in reqs or []:
            add(r, extra.lower() in DEV_GROUPS, f"optional:{extra}")
    for g, reqs in ((pp or {}).get("dependency-groups") or {}).items():
        for r in reqs or []:
            if isinstance(r, str):
                add(r, True, f"group:{g}")
    tool = (pp or {}).get("tool") or {}
    for r in (tool.get("uv") or {}).get("dev-dependencies") or []:
        add(r, True, "dev")
    poetry = tool.get("poetry") or {}
    for n in poetry.get("dependencies") or {}:
        if n.lower() != "python":
            add(n, False, "main")
    for n in poetry.get("dev-dependencies") or {}:
        add(n, True, "poetry:dev")
    for g, body in (poetry.get("group") or {}).items():
        for n in (body or {}).get("dependencies") or {} if isinstance(body, dict) else {}:
            add(n, True, f"poetry:{g}")
    return out


def req_entries(text: str):
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "-", ".", "/")) or "://" in s:
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", s)
        if m:
            yield line, m.group(1)


def req_line_edit(line: str, name: str, to: str) -> str | None:
    m = re.match(r"^(\s*)([A-Za-z0-9][A-Za-z0-9._-]*)(\s*\[[^\]]*\])?(\s*)([^;#]*?)(\s*(?:[;#].*)?)$", line)
    if not m or norm_name(m.group(2)) != norm_name(name):
        return None
    new = bump_spec(m.group(5), to)
    if new is None:
        return None
    return m.group(1) + m.group(2) + (m.group(3) or "") + m.group(4) + new + m.group(6)


def pyproject_edit(text: str, name: str, to: str) -> tuple[str, str] | None:
    for m in re.finditer(r"([\"'])([A-Za-z0-9][A-Za-z0-9._-]*)(\s*\[[^\]\"']*\])?\s*([^\"';]*?)\s*(;[^\"']*)?\1",
                         text or ""):
        if norm_name(m.group(2)) != norm_name(name) or not m.group(4).strip():
            continue
        new = bump_spec(m.group(4), to)
        if new is None:
            return None
        q = m.group(1)
        marker = f" {m.group(5).strip()}" if m.group(5) else ""
        return m.group(0), f"{q}{m.group(2)}{m.group(3) or ''}{new}{marker}{q}"
    return None


def cargo_edits(text: str, name: str, to: str) -> list[tuple[str, str]]:
    out = []
    n = re.escape(name)
    for line in (text or "").splitlines():
        m = re.match(rf'^(\s*{n}\s*=\s*")([\^~=<>]*\s*)([0-9][^"]*)(".*)$', line) or \
            re.match(rf'^(\s*{n}\s*=\s*\{{[^}}]*?\bversion\s*=\s*")([\^~=<>]*\s*)([0-9][^"]*)(".*)$', line)
        if m:
            new = m.group(1) + m.group(2) + trunc(to, len(m.group(3).split("."))) + m.group(4)
            if new != line and (line, new) not in out:
                out.append((line, new))
    return out


def gemfile_edits(text: str, name: str, to: str) -> list[tuple[str, str]]:
    out = []
    for line in (text or "").splitlines():
        m = re.match(rf'''^(\s*gem\s+["']{re.escape(name)}["']\s*,\s*["'])([~><=! ]*)([0-9][^"']*)(["'].*)$''', line)
        if m:
            out.append((line, m.group(1) + m.group(2) + trunc(to, len(m.group(3).split("."))) + m.group(4)))
    return out


def run_act(prefix: list[str], args=(), suffix=(), cwd: str | None = None, merge: bool = False) -> dict:
    return {"kind": "run", "prefix": list(prefix), "args": list(args), "suffix": list(suffix),
            "cwd": None if cwd in (None, ".") else cwd, "merge": merge}


def edit_act(file: str, old: str, new: str) -> dict:
    return {"kind": "edit", "file": file, "old": old, "new": new}


def actions_for(eco: dict, pkg: dict, to: str, kind: str, repo: Path) -> tuple[list[dict] | None, str | None]:
    """How to apply one upgrade in the ecosystem's own terms; (None, why) when it can't be."""
    m, name, d, cur = eco["manager"], pkg["name"], eco["dir"], pkg["current"]
    if m == "custom":
        fill = lambda x: x.replace("{name}", name).replace("{version}", to).replace("{from}", cur)
        return [run_act([fill(x) for x in eco["apply"]], cwd=d)], None
    if m in ("npm", "pnpm", "yarn"):
        info = node_dep(repo, eco, name, pkg.get("workspace"))
        spec, dev, ws = info["spec"] or "", info["dev"] or bool(pkg.get("dev")), info["workspace"]
        exact, tilde = bool(re.match(r"^\d", spec)), spec.startswith("~")
        if m == "npm":
            style = ["--save-exact"] if exact else (["--save-prefix=~"] if tilde else [])
            pre = ["npm", "install", "--package-lock-only", "--no-audit", "--no-fund"] + \
                  (["-w", ws] if ws else []) + (["--save-dev"] if dev else []) + style
            return [run_act(pre, [f"{name}@{to}"], cwd=d, merge=True)], None
        if m == "pnpm":
            pre = ["pnpm", "add"] + (["--filter", ws] if ws else (["-w"] if eco.get("workspaces") else [])) + \
                  (["-D"] if dev else []) + (["--save-exact"] if exact else [])
            return [run_act(pre, [f"{name}@{to}"], cwd=d, merge=True)], None
        if eco.get("berry"):
            prefix = "" if exact else ("~" if tilde else "^")
            return [run_act(["yarn", "up"], [f"{name}@{prefix}{to}"], cwd=d, merge=True)], None
        pre = ["yarn"] + (["workspace", ws] if ws else []) + ["add"] + \
              (["-W"] if eco.get("workspaces") and not ws else []) + (["--dev"] if dev else []) + \
              (["--exact"] if exact else (["--tilde"] if tilde else []))
        return [run_act(pre, [f"{name}@{to}"], cwd=d, merge=True)], None
    if m == "pip":
        acts = []
        for f in eco.get("manifests", [eco["manifest"]]):
            text = read_text(repo / f) or ""
            if f.endswith("pyproject.toml"):
                e = pyproject_edit(text, name, to)
                if e:
                    acts.append(edit_act(f, *e))
                continue
            for line, n in req_entries(text):
                if norm_name(n) == norm_name(name):
                    new = req_line_edit(line, name, to)
                    if new and new != line:
                        acts.append(edit_act(f, line, new))
        if not acts:
            return None, "not pinned: a fresh install already gets the newest version it allows"
        return acts, None
    if m in ("uv", "poetry"):
        info = py_deps(read_toml(read_text(repo / eco["manifest"])) or {}).get(norm_name(name)) or {}
        group = info.get("group", "main")
        if m == "uv":
            if kind != "major":
                return [run_act(["uv", "lock"], ["--upgrade-package", f"{name}=={to}"], cwd=d, merge=True)], None
            flags = [] if group == "main" else (["--dev"] if group == "dev" else
                                                (["--optional", group.split(":", 1)[1]] if group.startswith("optional:")
                                                 else ["--group", group.split(":", 1)[1]]))
            return [run_act(["uv", "add", "--no-sync", *flags], [f"{name}>={to}"], cwd=d, merge=True)], None
        flags = [] if group == "main" else ["--group", group.split(":", 1)[1]]
        return [run_act(["poetry", "add", "--lock", *flags], [f"{name}@^{to}"], cwd=d, merge=True)], None
    if m == "cargo":
        if kind != "major":
            return [run_act(["cargo", "update", "-p", f"{name}@{cur}", "--precise", to], cwd=d)], None
        acts = []
        for f in eco.get("manifests", [eco["manifest"]]):
            for old, new in cargo_edits(read_text(repo / f), name, to):
                acts.append(edit_act(f, old, new))
        if not acts:
            return None, f"couldn't find {name}'s version in Cargo.toml to raise it"
        return acts + [run_act(["cargo", "update", "-p", name], cwd=d)], None
    if m == "go":
        return [run_act(["go", "get"], [f"{name}@{to}"], cwd=d, merge=True)], None
    if m == "bundler":
        acts = []
        if kind == "major":
            acts = [edit_act(eco["manifest"], o, n) for o, n in gemfile_edits(read_text(repo / eco["manifest"]), name, to)]
        return acts + [run_act(["bundle", "update", "--conservative"], [name], cwd=d, merge=True)], None
    if m == "dotnet":
        return [run_act(["dotnet", "add", eco["manifest"], "package", name, "--version", to])], None
    return None, f"{m} upgrades aren't supported"


PYPI_CACHE: dict[str, list[str]] = {}


def pypi_versions(name: str) -> list[str]:
    """Release versions from PyPI's JSON API (the script itself reads it; only with the network on)."""
    if name in PYPI_CACHE:
        return PYPI_CACHE[name]
    import urllib.request   # loaded only when needed: it is slow to import
    out = []
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=15) as r:
            rel = json.loads(r.read().decode("utf-8")).get("releases") or {}
        out = [v for v, files in rel.items() if files and not all(f.get("yanked") for f in files)
               and vparse(v) and not vparse(v)[1]]
    except Exception:   # noqa: BLE001 - offline, private index, or a name PyPI doesn't have
        out = []
    PYPI_CACHE[name] = out
    return out


def newest_in_major(versions: list[str], cur: str) -> str | None:
    same = [v for v in versions if classify(cur, v) != "major" and newer(v, cur)]
    return max(same, key=vkey) if same else None


def outdated_for(box: Path, eco: dict, base: dict, cfg: dict) -> tuple[list[dict] | None, list[dict], str | None]:
    """(packages, advisories, why unchecked) for one ecosystem, from its own tools in the sandbox."""
    m, d = eco["manager"], eco["dir"]
    repo = box / "repo"
    advs: list[dict] = []

    def fail_why(cmd, code, out, err):
        return f"`{show_cmd(cmd)}` gave nothing upgrade.py could read (exit {code}): " + \
               (tail(err or out, 3).replace("\n", " ") or "no output")
    if m == "custom":
        code, out, err = run_tool(box, eco["outdated"], d)
        if extract_json(out) is None:
            return None, [], fail_why(eco["outdated"], code, out, err)
        pkgs, advs = parse_custom_outdated(out)
        data = extract_json(out)
        rows = data.get("packages") if isinstance(data, dict) else data
        if any(isinstance(r, dict) and "advisories" in r for r in rows or []):
            eco["audited_by"] = "its outdated command"
        return pkgs, advs, None
    if m in ("npm", "pnpm", "yarn"):
        if m == "npm":
            cmd = ["npm", "outdated", "--json", "--long"] + (["--workspaces", "--include-workspace-root"]
                                                             if eco.get("workspaces") else [])
            code, out, err = run_tool(box, cmd, d)
            data = extract_json(out)
            if data is None and not out.strip() and code == 0:
                data = {}
            if data is None:
                return None, [], fail_why(cmd, code, out, err)
            pkgs = parse_npm_outdated(out or "{}", eco.get("name"))
            acode, aout, _ = run_tool(box, ["npm", "audit", "--json"], d)
            advs = parse_npm_audit(aout)
            if isinstance(extract_json(aout), dict):
                eco["audited_by"] = "npm audit"
        elif m == "pnpm":
            cmd = ["pnpm", "outdated", "--format", "json"] + (["-r"] if eco.get("workspaces") else [])
            code, out, err = run_tool(box, cmd, d)
            if extract_json(out) is None and code not in (0, 1):
                return None, [], fail_why(cmd, code, out, err)
            pkgs = parse_pnpm_outdated(out or "{}")
            _, aout, _ = run_tool(box, ["pnpm", "audit", "--json"], d)
            advs = parse_npm_audit(aout)
            if isinstance(extract_json(aout), dict):
                eco["audited_by"] = "pnpm audit"
        else:
            if eco.get("berry"):
                return None, [], "yarn 2 and later have no outdated command; add steps by hand"
            cmd = ["yarn", "outdated", "--json"]
            code, out, err = run_tool(box, cmd, d)
            pkgs = parse_yarn_outdated(out)
            if not pkgs and code not in (0, 1):
                return None, [], fail_why(cmd, code, out, err)
        for p in pkgs:
            if not p.get("current"):
                p["skip"] = "not installed in the sandbox"
            p["dev"] = bool(p.get("dev")) or node_dep(repo, eco, p["name"], p.get("workspace"))["dev"]
        return pkgs, advs, None
    if m in ("pip", "uv", "poetry"):
        if m == "pip":
            if not base.get("venv"):
                return None, [], "pip needs baseline --venv, so the sandbox has its own environment to compare"
            cmd = ["python", "-m", "pip", "list", "--outdated", "--format=json"]
        elif m == "uv":
            cmd = ["uv", "pip", "list", "--outdated", "--format", "json"]
        else:
            cmd = ["poetry", "show", "--outdated", "--top-level", "--no-ansi"]
        code, out, err = run_tool(box, cmd, d)
        pkgs = parse_poetry_outdated(out) if m == "poetry" else parse_pip_outdated(out)
        if code not in (0, 1) or (m != "poetry" and extract_json(out) is None):
            return None, [], fail_why(cmd, code, out, err)
        direct: dict[str, dict] = {}
        if m == "pip":
            for f in eco.get("manifests", []):
                if f.endswith("pyproject.toml"):
                    for k, v in py_deps(read_toml(read_text(repo / f)) or {}).items():
                        direct.setdefault(k, v)
                else:
                    dev = bool(re.search(r"dev|test|lint|doc", f.rsplit("/", 1)[-1], re.I))
                    for _, n in req_entries(read_text(repo / f)):
                        direct.setdefault(norm_name(n), {"name": n, "dev": dev})
        else:
            direct = py_deps(read_toml(read_text(repo / eco["manifest"])) or {})
        pkgs = [dict(p, dev=direct[norm_name(p["name"])]["dev"]) for p in pkgs if norm_name(p["name"]) in direct]
        if cfg.get("network"):
            for p in pkgs:
                if classify(p["current"], p["latest"]) == "major":
                    p["wanted"] = newest_in_major(pypi_versions(p["name"]), p["current"])
        if shutil.which("pip-audit"):
            env = {"PIPAPI_PYTHON_LOCATION": str(venv_bin(box) / "python")} if base.get("venv") else {}
            _, aout, _ = run_tool(box, ["pip-audit", "-f", "json", "--progress-spinner", "off"], d, extra_env=env)
            advs = parse_pip_audit(aout)
            if extract_json(aout) is not None:
                eco["audited_by"] = "pip-audit"
        return pkgs, advs, None
    if m == "cargo":
        code, _, _ = run_tool(box, ["cargo", "outdated", "--version"], d, timeout=60)
        if code == 0:
            cmd = ["cargo", "outdated", "--format", "json", "--root-deps-only"]
            code, out, err = run_tool(box, cmd, d)
            pkgs = parse_cargo_outdated(out)
            if not pkgs and extract_json(out) is None:
                return None, [], fail_why(cmd, code, out, err)
        else:
            cmd = ["cargo", "update", "--dry-run"]
            code, out, err = run_tool(box, cmd, d)
            if code != 0:
                return None, [], fail_why(cmd, code, out, err)
            names_ = set()
            for f in eco.get("manifests", [eco["manifest"]]):
                ct = read_toml(read_text(repo / f)) or {}
                for k, v in ct.items():
                    if k.endswith("dependencies") and isinstance(v, dict):
                        names_.update(v)
                for t in (ct.get("target") or {}).values() if isinstance(ct.get("target"), dict) else []:
                    for k, v in (t or {}).items():
                        if k.endswith("dependencies") and isinstance(v, dict):
                            names_.update(v)
            pkgs = [p for p in parse_cargo_dry_run(out + "\n" + err) if p["name"] in names_]
            eco.setdefault("notes", []).append("cargo-outdated isn't installed, so only compatible updates were "
                                               "found; majors weren't looked for")
        code, _, _ = run_tool(box, ["cargo", "audit", "--version"], d, timeout=60)
        if code == 0:
            _, aout, _ = run_tool(box, ["cargo", "audit", "--json"], d)
            advs = parse_cargo_audit(aout)
            if isinstance(extract_json(aout), dict):
                eco["audited_by"] = "cargo audit"
        return pkgs, advs, None
    if m == "go":
        cmd = ["go", "list", "-m", "-u", "-json", "all"]
        code, out, err = run_tool(box, cmd, d)
        if code != 0:
            return None, [], fail_why(cmd, code, out, err)
        pkgs = parse_go_list(out)
        ind = [p for p in pkgs if p["indirect"]]
        for p in ind:
            p["skip"] = "indirect"
        if ind:
            eco.setdefault("notes", []).append(f"{plural(len(ind), 'indirect module')} behind, left alone")
        eco.setdefault("notes", []).append("go list doesn't see a new major that changes the module path (/v2); "
                                           "add those by hand")
        return pkgs, [], None
    if m == "bundler":
        cmd = ["bundle", "outdated", "--parseable", "--only-explicit"]
        code, out, err = run_tool(box, cmd, d)
        pkgs = parse_bundle_outdated(out)
        if not pkgs and code not in (0, 1):
            return None, [], fail_why(cmd, code, out, err)
        return pkgs, [], None
    if m == "dotnet":
        cmd = ["dotnet", "list", eco["manifest"], "package", "--outdated", "--format", "json"]
        code, out, err = run_tool(box, cmd)
        if extract_json(out) is None:
            return None, [], fail_why(cmd, code, out, err)
        pkgs = parse_dotnet_outdated(out)
        _, out2, _ = run_tool(box, cmd + ["--highest-minor"])
        minor = {p["name"]: p["latest"] for p in parse_dotnet_outdated(out2)}
        for p in pkgs:
            p["wanted"] = minor.get(p["name"])
            p["dev"] = bool(TEST_FILE.search(eco["manifest"]))
        return pkgs, [], None
    return None, [], f"{m} isn't supported"


def link_names(a: str, b: str) -> bool:
    """Packages released together: one npm scope, or one name extending the other (react, react-dom)."""
    if a.startswith("@") and b.startswith("@"):
        return a.split("/")[0] == b.split("/")[0]
    x, y = sorted([a, b], key=len)
    return y.startswith(x + "-") or y.startswith(x + ".")


def upgrades_for(eco: dict, pkgs: list[dict], repo: Path, advs: list[dict]) -> tuple[list[dict], list[dict]]:
    ups, skipped = [], []
    by_pkg: dict[str, list[dict]] = {}
    for a in advs:
        by_pkg.setdefault(norm_name(a.get("package") or ""), []).append(a)
    for p in pkgs:
        if p.get("skip"):
            skipped.append({"name": p["name"], "eco": eco["id"], "why": p["skip"]})
            continue
        cur = p["current"]
        cands = [v for v in (p.get("wanted"), p.get("latest")) if v and newer(v, cur)]
        in_major = max((v for v in cands if classify(cur, v) != "major"), key=vkey, default=None)
        major = max((v for v in cands if classify(cur, v) == "major"), key=vkey, default=None)
        for to in [v for v in (in_major, major) if v]:
            kind = classify(cur, to)
            acts, why = actions_for(eco, p, to, kind, repo)
            mine = by_pkg.get(norm_name(p["name"]), [])
            fixes = [a["id"] for a in mine if a.get("fixed_in") and not newer(a["fixed_in"], to)]
            ups.append({"id": None, "eco": eco["id"], "manager": eco["manager"], "manifest": eco["manifest"],
                        "name": p["name"], "from": cur, "to": to, "kind": kind, "dev": bool(p.get("dev")),
                        "group": "major" if kind == "major" else ("dev" if p.get("dev") else "deps"),
                        "actions": acts or [], "note": why, "advisories": [a["id"] for a in mine],
                        "fixes": fixes, "link": None, "workspace": p.get("workspace")})
    if eco["manager"] in ("npm", "pnpm", "yarn"):
        parent = {i: i for i in range(len(ups))}

        def find(i):
            while parent[i] != i:
                i = parent[i]
            return i
        for i in range(len(ups)):
            for j in range(i + 1, len(ups)):
                a, b = ups[i], ups[j]
                if a["to"] == b["to"] and a["group"] == b["group"] and link_names(a["name"], b["name"]):
                    parent[find(j)] = find(i)
        roots: dict[int, list[int]] = {}
        for i in range(len(ups)):
            roots.setdefault(find(i), []).append(i)
        for r, members in roots.items():
            if len(members) > 1:
                for i in members:
                    ups[i]["link"] = f"{eco['id']}|{ups[r]['name']}@{ups[r]['to']}"
    return ups, skipped


def fetch(url: str, timeout: float = 15) -> str | None:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:   # noqa: BLE001 - offline, blocked or moved: the runtime stays unchecked
        return None


RUNTIME_SOURCES = {
    "node": ("https://nodejs.org/dist/index.json", parse_node_index, ["node", "--version"]),
    "python": ("https://endoflife.date/api/python.json", parse_python_eol, ["python", "--version"]),
    "go": ("https://go.dev/dl/?mode=json", parse_go_dl, ["go", "version"]),
    "rust": ("https://static.rust-lang.org/dist/channel-rust-stable.toml", parse_rust_channel, ["rustc", "--version"]),
}


def runtime_upgrades(found: dict, network: bool) -> tuple[list[dict], list[str]]:
    ups, unchecked = [], []
    latest: dict[str, str | None] = {}
    for r in found.get("runtimes", []):
        if r["kind"] == "minimum":
            unchecked.append(f"{r['label']} {r['current']} in {r['file']} is a minimum your users need; raising it "
                             "is your call, so it's left alone (add a step by hand if you want it)")
            continue
        if r["runtime"] not in RUNTIME_SOURCES:
            unchecked.append(f"{r['label']} {r['current']} in {r['file']}: no source for its latest version")
            continue
        cur = r["current"].removeprefix("v")
        if not vparse(cur):
            unchecked.append(f"{r['label']} {r['current']} in {r['file']} names a channel, not a version; left alone")
            continue
        if r["runtime"] not in latest:
            url, parser, _ = RUNTIME_SOURCES[r["runtime"]]
            text = fetch(url) if network else None
            latest[r["runtime"]] = parser(text) if text else None
        new = latest[r["runtime"]]
        if not new:
            unchecked.append(f"{r['label']} {r['current']} in {r['file']}: couldn't read the latest version"
                             + ("" if network else " (the network is off)"))
            continue
        target = trunc(new, len(cur.split(".")))
        if not newer(target, cur):
            continue
        shown = ("v" if r["current"].startswith("v") else "") + target
        old_line = r["line"]
        new_line = old_line.replace(r["current"], shown, 1)
        ups.append({"id": None, "eco": f"runtime:{r['file']}", "manager": "runtime", "manifest": r["file"],
                    "name": r["label"], "from": r["current"], "to": shown, "kind": classify(cur, target),
                    "dev": False, "group": "runtime", "actions": [edit_act(r["file"], old_line, new_line)],
                    "note": None, "advisories": [], "fixes": [], "link": None,
                    "probe": {"cmd": RUNTIME_SOURCES[r["runtime"]][2], "want": target,
                              "cwd": r["file"].rpartition("/")[0] or None}})
    return ups, unchecked


def gh_ready() -> bool:
    if not shutil.which("gh"):
        return False
    try:
        return subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def action_upgrades(found: dict) -> tuple[list[dict], list[str]]:
    acts = [a for a in found.get("actions", [])]
    if not acts:
        return [], []
    if not gh_ready():
        return [], [f"{plural(len(acts), 'CI action pin')}: gh isn't installed or logged in, so their latest "
                    "releases weren't looked up"]
    ups, unchecked, latest = [], [], {}
    groups: dict[tuple, list[dict]] = {}
    for a in acts:
        if a["pin"] != "tag":
            unchecked.append(f"{a['owner']}/{a['repo']}@{a['ref'][:12]} in {a['file']} is pinned to a "
                             f"{'commit' if a['pin'] == 'sha' else 'branch'}; left alone")
            continue
        groups.setdefault((a["owner"], a["repo"], a["path"], a["ref"]), []).append(a)
    for (owner, repo, path, ref), items in groups.items():
        key = f"{owner}/{repo}"
        if key not in latest:
            try:
                r = subprocess.run(["gh", "api", f"repos/{key}/releases/latest", "--jq", ".tag_name"],
                                   capture_output=True, text=True, timeout=60)
                latest[key] = r.stdout.strip() if r.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired):
                latest[key] = None
        tag = latest[key]
        if not tag or not vparse(tag):
            unchecked.append(f"{key}@{ref}: no release found")
            continue
        n = len(ref.removeprefix("v").split("."))
        new = ("v" if ref.startswith("v") else "") + trunc(tag.removeprefix("v"), n)
        if not newer(new, ref):
            continue
        edits = []
        for a in items:
            if not any(e["old"] == a["text"] for e in edits):
                edits.append(edit_act(a["file"], a["text"], a["text"].replace(f"@{ref}", f"@{new}", 1)))
        ups.append({"id": None, "eco": "actions", "manager": "actions", "manifest": items[0]["file"],
                    "name": f"{key}{path}", "from": ref, "to": new, "kind": classify(ref, new), "dev": True,
                    "group": "action", "actions": edits, "note": None, "advisories": [], "fixes": [], "link": None,
                    "unchecked_by_tests": "CI runs it; the tests in the sandbox don't"})
    return ups, unchecked


# ---------------------------------------------------------------- commands: setup


def cmd_init(a) -> None:
    scope = a.scope or "all"
    if scope not in ("all", "security", "minor") and not scope.startswith("package:"):
        kit.fail("--scope is all, security, minor or package:<name>[,<name>]")
    if a.workspace and a.force:
        ws0 = Path(a.workspace).expanduser().resolve()
        for f in ("ecosystems.json", "baseline.json", "outdated.json", "plan.json", "decisions.json", "waiting.json",
                  "land.json", "notes.json", "report.md", "summary.json"):
            (ws0 / f).unlink(missing_ok=True)
        for d in ("steps", "trials", "migrations", "notes"):
            kit.rmtree(ws0 / d)
    settings = {"scope": scope, "majors": a.majors, "also": sorted(set(a.also or [])), "after": a.after}
    ws, cfg, _ = kit.init_run(SKILL, a.repo, slug=a.slug or "upgrade", target=a.target or "",
                              network=not a.no_network, include=a.include or [], env=a.env or [],
                              workspace=a.workspace, force=a.force, extra={"upgrade": settings})
    top = Path(cfg["repo"])
    status = kit.git_status(top) if cfg["git"] else ""
    kit.update_cfg(ws, lambda c: c.update(init_status=status))
    found = detect(top, cfg["git"])
    kit.save_json(ws / "ecosystems.json", found)
    print(f"Upgrading: {scope_text(settings)}.")
    if not cfg["network"]:
        print("note: the network is off, so installs only work from a vendored or cached tree, and outdated "
              "can only ask tools that work offline.")
    if status.strip():
        n = len(status.strip().splitlines())
        print(f"note: {plural(n, 'uncommitted change')}. Every sandbox copies them, but the upgrade branch starts "
              "from the last commit, so commit or stash them first for a clean run.")
    print("Ecosystems:")
    for e in found["ecosystems"]:
        tool = e.get("tool")
        have = "" if not tool else (f" · {tool} found" if shutil.which(tool) else f" · {tool} isn't installed: "
                                    "it can't be checked here")
        extra = f", workspaces {', '.join(e['workspaces'])}" if e.get("workspaces") else ""
        files = " + ".join(e.get("manifests") or [e["manifest"]]) + (f" + {e['lockfile']}" if e.get("lockfile")
                                                                   else " (no lockfile)")
        print(f"  {e['manager']:<8} {files}{extra}{have}")
    if not found["ecosystems"]:
        print("  none upgrade.py knows. Describe yours with: "
              f"{SCRIPT} custom -w {ws} --name <name> --manifest <file> --outdated <cmd> --apply <cmd>")
    for x in found["listed"]:
        print(f"  listed   {x['what']}: {x['why']}")
    if found["runtimes"]:
        print("Runtimes: " + "; ".join(f"{r['label']} {r['current']} ({r['file']}"
                                        + (", a minimum)" if r["kind"] == "minimum" else ")") for r in found["runtimes"]))
    if found["actions"]:
        uniq = sorted({f"{x['owner']}/{x['repo']}{x['path']}@{x['ref']}" for x in found["actions"]})
        print(f"CI actions: {plural(len(found['actions']), 'pin')} in "
              f"{plural(len({x['file'] for x in found['actions']}), 'workflow')}: {', '.join(uniq[:6])}"
              + (" ..." if len(uniq) > 6 else ""))
    if found["images"]:
        print("Base images: " + ", ".join(f"{i['image']} ({i['file']})" for i in found["images"][:6])
              + ". No registry is asked for newer tags; add a step by hand for one you want moved.")
    for x in found["auth"]:
        print(f"Private registry: {x}. Sandboxes drop credentials, so packages from it can't be installed or "
              "checked; they'll fail as install-fail.")
    for n in found["notes"]:
        print(f"note: {n}")
    if settings["also"]:
        print(f"Also asked for: {', '.join(settings['also'])}.")
    if found["commands"]:
        print("Commands found (confirm them, then run baseline):")
        for c in found["commands"]:
            print(f"  {c['kind']:<8} {c['cmd']}   ({c['source']})")
    print(f"Next: {SCRIPT} baseline -w {ws} [--venv] --install -- <command> --build -- <command> --tests -- <command>")


def scope_text(s: dict) -> str:
    what = {"all": "everything that's out of date", "security": "security fixes only",
            "minor": "patch and minor upgrades only"}.get(s["scope"], f"only {s['scope'].split(':', 1)[-1]}")
    majors = {"try": "majors tried and migrated if they break", "list": "majors listed, not tried",
              "hold": "majors tried and held if they break"}[s["majors"]]
    return f"{what}; {majors}" if s["scope"] != "minor" else what


def cmd_custom(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    d = J(a.dir)
    for f in (a.manifest, a.lockfile):
        if f and not (top / J(d, f)).is_file():
            kit.fail(f"no {J(d, f)} in the project")
    apply = split_cmd(a.apply)
    if "{name}" not in " ".join(apply) or "{version}" not in " ".join(apply):
        kit.fail("--apply needs {name} and {version} in it, like '[\"tool\", \"add\", \"{name}@{version}\"]'")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", a.name):
        kit.fail("--name is letters, digits, dots, dashes and underscores")
    eco = {"id": f"{a.name}:{d}", "manager": "custom", "name": a.name, "dir": d, "manifest": J(d, a.manifest),
           "lockfile": J(d, a.lockfile) if a.lockfile else None, "outdated": split_cmd(a.outdated),
           "apply": apply, "tool": None, "upgradable": True}

    def put(found):
        found["ecosystems"] = [e for e in found["ecosystems"] if e["id"] != eco["id"]] + [eco]
    with kit.locked(ws):
        found = load_found(ws)
        put(found)
        kit.save_json(ws / "ecosystems.json", found)
    print(f"Added ecosystem {a.name} at {d}: {eco['manifest']}" + (f" + {eco['lockfile']}" if eco["lockfile"] else "")
          + f". outdated runs `{show_cmd(eco['outdated'])}`; each upgrade runs `{show_cmd(apply)}`.")


def cmd_baseline(a) -> None:
    ws, cfg = load(a)
    secs = getattr(a, "sections", None) or {"install": [], "build": [], "tests": []}
    if not secs["tests"]:
        kit.fail(f"give the test command: {SCRIPT} baseline -w <run> --tests -- <command> "
                 "(and --install -- <command> or --build -- <command> when the project needs them)")
    plan = load_plan(ws, required=False)
    if plan and plan["accepted"] and not a.force:
        kit.fail("steps were accepted against the current baseline; --force records a new one, and they'll "
                 "need trying again")
    if a.runs < 1:
        kit.fail("--runs is 1 or more")
    venv = None
    if a.venv is not None:
        venv = split_cmd(a.venv) if a.venv.strip() else [sys.executable]
        check_venv(venv)
    old = kit.load_json(ws / "baseline.json", None)
    if old and old.get("box"):
        kit.drop_sandbox(ws, cfg, old["box"])
    base = {"commands": secs, "runs": a.runs, "timeout": a.timeout, "venv": venv, "junit": a.junit,
            "at": kit.now(), "ok": False, "problem": None, "install": [], "build": [], "tests": []}
    warn_if_changed(cfg)
    box = new_box(ws, cfg, "baseline", base)
    base["box"] = box.name
    t0 = time.monotonic()
    print(f"Baseline, in a fresh sandbox (network {'allowed' if cfg['network'] else 'off'}):")
    installs, ok = run_install(box, base)
    for i, r in enumerate(installs):
        what = f"venv     {show_cmd(venv)} -m venv, in the sandbox" if venv and i == 0 else f"install  {show_cmd(r['cmd'])}"
        print(f"  {what}: {'FAILED' if kit.failed(r) else 'passed'}, {dur(r['secs'])}")
    base["install"] = [{"cmd": r["cmd"], "ok": not kit.failed(r), "secs": r["secs"]} for r in installs]
    if not ok:
        r = installs[-1]
        base["problem"] = f"the install failed: `{show_cmd(r['cmd'])}`"
        base["log"] = save_log(ws, "baseline", "install", r["out"])
        kit.save_json(ws / "baseline.json", base)
        for ln in tail(r["out"], 20).splitlines():
            print(f"  {ln}")
        print(f"Blocked: {base['problem']}, so nothing can be checked. Fix the command (or the network, or the "
              f"registry's credentials) and run baseline again. Full output: {base['log']}")
        sys.exit(1)
    for c in secs["build"]:
        r = run_cmd(box, c, a.timeout)
        base["build"].append({"cmd": c, "ok": not kit.failed(r), "secs": r["secs"]})
        print(f"  build    {show_cmd(c)}: {'FAILED' if kit.failed(r) else 'passed'}, {dur(r['secs'])}")
        if kit.failed(r):
            base["build"][-1]["log"] = save_log(ws, "baseline", f"build{len(base['build'])}", r["out"])
            print(f"  warning: the build fails before any upgrade, so it can't check upgrades. "
                  f"Output: {base['build'][-1]['log']}")
    runs = [run_tests_once(box, base) for _ in range(a.runs)]
    for i, c in enumerate(secs["tests"]):
        unit = summarize_unit(c, [rs[i] for rs in runs])
        base["tests"].append(unit)
        how = {"tests": f"read as {unit['format']}", "exit": "by its exit code (no per-test results found)",
               "none": "can't check anything"}[unit["judge"]]
        bits = []
        if unit["judge"] == "tests":
            bits.append(f"{len(unit['always_pass'])} pass every run")
            if unit["failing"]:
                bits.append(f"{len(unit['failing'])} fail every run (not counted against upgrades)")
            if unit["flaky"]:
                bits.append(f"{len(unit['flaky'])} flaky (ignored)")
        elif unit["judge"] == "exit":
            bits.append("passed every run")
        else:
            bits.append("failed" + (" in some runs" if unit["cmd_ok"] is None else "")
                        + " and printed no per-test results, so it can't tell a new failure from an old one")
        print(f"  tests    {show_cmd(c)}, {plural(a.runs, 'run')} ({how}): {'; '.join(bits)}; {dur(unit['secs'])} a run")
        if unit["judge"] == "none":
            base["tests"][-1]["log"] = save_log(ws, "baseline", f"tests{i + 1}", runs[-1][i]["out"])
            print(f"  warning: make it print per-test results (pytest -rA, unittest -v, go test -v, a TAP or "
                  f"JUnit reporter) or it checks nothing. Output: {base['tests'][-1]['log']}")
    checks = sum(len(u["always_pass"]) for u in base["tests"])
    exit_units = sum(1 for u in base["tests"] if u["judge"] == "exit")
    builds = sum(1 for b in base["build"] if b["ok"])
    if not checks and not exit_units:
        base["problem"] = "nothing passes at the baseline, so no upgrade can be proven"
    else:
        base["ok"] = True
    t_install = sum(b["secs"] for b in base["install"])
    t_build = sum(b["secs"] for b in base["build"])
    t_tests = sum(u["secs"] for u in base["tests"])
    base["secs"] = {"install": round(t_install, 2), "build": round(t_build, 2), "tests": round(t_tests, 2),
                    "try": round(t_install + t_build + t_tests * a.runs + 3, 1), "total": round(time.monotonic() - t0, 1)}
    kit.save_json(ws / "baseline.json", base)
    if not base["ok"]:
        print(f"Blocked: {base['problem']}.")
        sys.exit(1)
    what = []
    if builds:
        what.append("the build")
    if checks:
        what.append(f"the {plural(checks, 'test')} that passed in all {plural(a.runs, 'run')}")
    if exit_units:
        what.append(f"{plural(exit_units, 'test command')} by exit code")
    print(f"Upgrades are checked against {' and '.join(what)}.")
    print(f"One try takes about {dur(base['secs']['try'])}: install {dur(t_install)}, build {dur(t_build)}, "
          f"tests {a.runs} × {dur(t_tests)}.")
    print(f"Next: {SCRIPT} outdated -w {ws}")


def cmd_outdated(a) -> None:
    ws, cfg = load(a)
    base = need_baseline(ws)
    found = load_found(ws)
    settings = cfg["upgrade"]
    ecos = [e for e in found["ecosystems"] if e.get("upgradable")]
    unchecked, ups, skipped, advisories, audited = [], [], [], [], []
    for x in found.get("listed", []):
        unchecked.append(f"{x['what']}: {x['why']}")
    box = None
    if base.get("box") and (kit.box_path(cfg, base["box"])).is_dir():
        box = kit.box_path(cfg, base["box"])
    try:
        if ecos and box is None:
            box = new_box(ws, cfg, "outdated", base)
            _, ok = run_install(box, base)
            if not ok:
                kit.fail("the install failed in a fresh sandbox, though it passed at the baseline; run baseline again")
        for eco in ecos:
            tool = eco.get("tool")
            if tool and not shutil.which(tool):
                unchecked.append(f"{eco['manager']} at {eco['dir']}: {tool} isn't installed here")
                continue
            pkgs, advs, why = outdated_for(box, eco, base, cfg)
            if pkgs is None:
                unchecked.append(f"{eco['manager']} at {eco['dir']}: {why}")
                continue
            for x in advs:
                x["eco"] = eco["id"]
            advisories += advs
            if eco.get("audited_by"):
                audited.append({"eco": eco["id"], "by": eco["audited_by"]})
            else:
                unchecked.append(f"{eco['manager']} at {eco['dir']}: no audit tool ran, so known vulnerabilities "
                                 "weren't looked for")
            new, sk = upgrades_for(eco, pkgs, box / "repo", advs)
            ups += new
            skipped += sk
            for n in eco.get("notes", []):
                unchecked.append(f"{eco['manager']} at {eco['dir']}: {n}")
    finally:
        if box is not None:
            kit.drop_sandbox(ws, cfg, box.name)
    with kit.locked(ws):
        b = kit.load_json(ws / "baseline.json", {})
        b.pop("box", None)
        kit.save_json(ws / "baseline.json", b)
    if "runtimes" in settings["also"]:
        r_ups, r_un = runtime_upgrades(found, cfg["network"])
        ups += r_ups
        unchecked += r_un
    elif found.get("runtimes"):
        unchecked.append(f"{plural(len(found['runtimes']), 'runtime version')}: not asked for (init --also runtimes)")
    if "actions" in settings["also"]:
        a_ups, a_un = action_upgrades(found)
        ups += a_ups
        unchecked += a_un
    elif found.get("actions"):
        unchecked.append(f"{plural(len(found['actions']), 'CI action pin')}: not asked for (init --also actions)")
    if found.get("images"):
        unchecked.append(f"{plural(len(found['images']), 'base image')} in Dockerfiles: no registry is asked for "
                         "newer tags; add a step by hand to move one")
    for i, u in enumerate(ups, 1):
        u["id"] = f"U{i}"
    data = {"at": kit.now(), "upgrades": ups, "skipped": skipped, "advisories": advisories, "audited": audited,
            "unchecked": unchecked}
    kit.save_json(ws / "outdated.json", data)
    if not ups:
        print("Nothing out of date that upgrade.py can see.")
    else:
        print("Out of date:")
        w = max(len(u["name"]) for u in ups)
        for u in ups:
            flags = [x for x in ("dev" if u["dev"] and u["group"] != "action" else "",
                                 f"fixes {', '.join(u['fixes'])}" if u["fixes"] else "",
                                 f"can't apply: {u['note']}" if u["note"] else "",
                                 "moves with " + u["link"].split("|", 1)[1] if u.get("link") else "") if x]
            print(f"  {u['id']:<4} {u['manager']:<8} {u['name']:<{w}}  {u['from']} → {u['to']}  {u['kind']}"
                  + (f"  ({'; '.join(flags)})" if flags else ""))
        kinds = {k: sum(1 for u in ups if u["kind"] == k) for k in ("patch", "minor", "major")}
        zero = sum(1 for u in ups if u["kind"] == "major" and vmajor(u["from"]) == 0)
        print(f"{plural(len(ups), 'upgrade')}: {kinds['patch']} patch, {kinds['minor']} minor, {kinds['major']} major"
              + (f" ({zero} of them 0.x, where a minor counts as major)" if zero else "") + "; "
              + f"{sum(1 for u in ups if u['dev'] and u['group'] in ('dev', 'major'))} on dev dependencies.")
    if advisories:
        print(f"Security advisories: {len(advisories)}: " + "; ".join(
            f"{x['package']} {x['id']}" + (f" (fixed in {x['fixed_in']})" if x.get("fixed_in") else "")
            for x in advisories[:8]) + (" ..." if len(advisories) > 8 else ""))
    for x in skipped[:10]:
        print(f"  skipped {x['name']} ({x['eco']}): {x['why']}")
    for x in unchecked:
        print(f"Not checked: {x}")
    if settings["scope"] == "security" and not advisories:
        print("Scope is security fixes only, and no audit found an advisory (or no audit tool was available).")
    print(f"Next: {SCRIPT} plan -w {ws}")


def select(ups: dict, settings: dict, holds: dict) -> tuple[list[str], list[dict]]:
    chosen, left = [], []
    held = {k for k, h in holds.items() if not re.fullmatch(r"S\d+", k)}
    held_ids = {u for h in holds.values() for u in h.get("upgrades", [])}
    want = None
    if settings["scope"].startswith("package:"):
        want = {norm_name(x) for x in settings["scope"].split(":", 1)[1].split(",") if x.strip()}
    for uid, u in ups.items():
        why = None
        if norm_name(u["name"]) in {norm_name(h) for h in held} or uid in held_ids:
            why = "held"
        elif u.get("note"):
            why = f"can't be applied: {u['note']}"
        elif want is not None and norm_name(u["name"]) not in want:
            why = "not one of the packages asked for"
        elif settings["scope"] == "security" and not u.get("advisories"):
            why = "not a security fix (scope: security fixes only)"
        elif settings["scope"] == "minor" and u["kind"] == "major":
            why = "a major (scope: patch and minor only)"
        elif u["kind"] == "major" and u["group"] == "major" and settings["majors"] == "list":
            why = "a major, listed only"
        if why:
            left.append({"upgrade": uid, "why": why})
        else:
            chosen.append(uid)
    if settings["scope"] == "security":   # per package, only the smallest upgrade that fixes its advisories
        by_pkg: dict[tuple, list[str]] = {}
        for uid in chosen:
            by_pkg.setdefault((ups[uid]["eco"], norm_name(ups[uid]["name"])), []).append(uid)
        for ids in by_pkg.values():
            fixing = [i for i in ids if ups[i].get("fixes")]
            keep = min(fixing or ids, key=lambda i: vkey(ups[i]["to"]))
            for i in ids:
                if i != keep:
                    chosen.remove(i)
                    left.append({"upgrade": i, "why": "a smaller upgrade fixes the advisory"})
    return chosen, left


def cmd_plan(a) -> None:
    ws, cfg = load(a)
    od = kit.load_json(ws / "outdated.json", None) or kit.fail("no outdated.json: run outdated first")
    old = load_plan(ws, required=False)
    if old and (old["accepted"] or any(s["tries"] for s in old["steps"])) and not a.force:
        kit.fail("steps have been tried already; plan --force starts the plan again and drops what was accepted")
    if old and a.force:
        kit.rmtree(ws / "steps")
    settings = cfg["upgrade"]
    ups = {u["id"]: u for u in od["upgrades"]}
    if old:   # keep upgrades added by hand
        for uid, u in old["upgrades"].items():
            if u.get("manual"):
                ups[uid] = u
    holds = (old or {}).get("holds", {})
    chosen, left = select(ups, settings, holds)
    steps, n = [], 0

    def step(group, uids, **extra):
        nonlocal n
        n += 1
        s = {"id": f"S{n}", "group": group, "title": title_for([ups[u] for u in uids], group), "upgrades": uids,
             "status": "pending", "tries": [], "attempts": 0, "from": None}
        s.update(extra)
        steps.append(s)
    pick = lambda g: [u for u in chosen if ups[u]["group"] == g]
    for g in ("deps", "dev"):
        if pick(g):
            step(g, pick(g))
    majors = sorted(pick("major"), key=lambda u: (ups[u]["name"].lower(), vkey(ups[u]["to"])))
    seen = set()
    for u in majors:
        if u in seen:
            continue
        link = ups[u].get("link")
        group_ids = [v for v in majors if link and ups[v].get("link") == link] or [u]
        seen.update(group_ids)
        step("major", group_ids, hold_on_fail=settings["majors"] == "hold")
    for g in ("runtime", "action", "image", "manual"):
        for u in pick(g):
            step(g, [u], **({"title": ups[u]["title"]} if ups[u].get("title") else {}))
    plan = {"version": 1, "at": kit.now(), "upgrades": ups, "steps": steps, "accepted": [], "holds": holds,
            "left_out": left, "bisects": {}}
    with kit.locked(ws):
        kit.save_json(ws / "plan.json", plan)
    print(f"Plan: {plural(len(steps), 'step')} for {plural(sum(len(s['upgrades']) for s in steps), 'upgrade')}.")
    for s in steps:
        print(f"  {s['id']:<4} {s['group']:<8} {s['title']}" + (f": {names_of(step_ups(plan, s))}"
                                                               if len(s["upgrades"]) > 1 else ""))
    for x in left:
        print(f"  left out: {up_text(ups[x['upgrade']])} ({x['why']})")
    base = kit.load_json(ws / "baseline.json", {}) or {}
    t = (base.get("secs") or {}).get("try")
    if t and steps:
        batch = max((len(s["upgrades"]) for s in steps if s["group"] in ("deps", "dev")), default=0)
        extra = ""
        if batch > 1:
            k = 2 * math.ceil(math.log2(batch)) + 1
            extra = f" If the batch of {batch} fails, bisecting it takes up to about {k} more ({dur(k * t)})."
        print(f"Each try takes about {dur(t)}, so trying the {plural(len(steps), 'step')} takes about "
              f"{dur(t * len(steps))}.{extra}")
    print(f"Edit {ws / 'plan.json'} to change it (or use hold). Next: {SCRIPT} try -w {ws} {steps[0]['id'] if steps else 'S1'}")


def cmd_add(a) -> None:
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    cmd = kit.strip_dashes(a.cmd or [])
    edits = a.edit or []
    if not edits and not cmd:
        kit.fail("give --edit FILE OLD NEW (one or more), or -- <command>")
    acts = []
    for f, old, new in edits:
        f = J(f.replace("\\", "/"))
        text = read_text(top / f)
        if text is None:
            kit.fail(f"no {f} in the project")
        if old not in text:
            kit.fail(f"{f} doesn't contain {old!r}")
        acts.append(edit_act(f, old, new))
    if cmd:
        acts.append(run_act(cmd))

    def change(plan):
        n = 1 + max([int(u[1:]) for u in plan["upgrades"] if re.fullmatch(r"U\d+", u)] or [0])
        sn = 1 + max([int(s["id"][1:]) for s in plan["steps"] if re.fullmatch(r"S\d+", s["id"])] or [0])
        uid, sid = f"U{n}", f"S{sn}"
        kind = classify(a.from_, a.to) if a.from_ and a.to else "manual"
        u = {"id": uid, "eco": "manual", "manager": "manual", "manifest": edits[0][0] if edits else "",
             "name": a.name or a.title, "from": a.from_, "to": a.to, "kind": kind, "dev": False, "group": a.group,
             "actions": acts, "note": None, "advisories": [], "fixes": [], "link": None, "manual": True,
             "title": a.title}
        if a.probe:
            if not a.to:
                kit.fail("--probe needs --to: the version the probe's output must show")
            u["probe"] = {"cmd": split_cmd(a.probe), "want": a.to.removeprefix("v"), "cwd": None}
        if a.group == "action":
            u["unchecked_by_tests"] = "CI runs it; the tests in the sandbox don't"
        plan["upgrades"][uid] = u
        plan["steps"].append({"id": sid, "group": a.group, "title": a.title, "upgrades": [uid], "status": "pending",
                              "tries": [], "attempts": 0, "from": None})
        return sid
    sid = mutate_plan(ws, change)
    print(f"Added {sid}: {a.title} ({plural(len(acts), 'action')}). Next: {SCRIPT} try -w {ws} {sid}")


def cmd_hold(a) -> None:
    ws, cfg = load(a)
    target = a.package

    def change(plan):
        st = next((s for s in plan["steps"] if s["id"].lower() == target.lower()), None)
        if st:
            ids = list(st["upgrades"])
            key = st["id"]
        else:
            ids = [uid for uid, u in plan["upgrades"].items() if norm_name(u["name"]) == norm_name(target)]
            key = target
        if not ids:
            kit.fail(f"no upgrade or step called {target} in the plan")
        plan["holds"][key] = {"reason": a.reason, "upgrades": ids, "by": a.by, "at": kit.now()}
        said = []
        for s in plan["steps"]:
            hit = [u for u in s["upgrades"] if u in ids]
            if not hit or s["status"] == "split":
                continue
            was = s["status"]
            gone = unaccept(ws, plan, s["id"]) if was == "accepted" else []
            if len(hit) == len(s["upgrades"]):
                s["status"], s["reason"] = "held", a.reason
                said.append(f"{s['id']} is held")
            else:
                s["upgrades"] = [u for u in s["upgrades"] if u not in ids]
                s["title"] = title_for(step_ups(plan, s), s["group"])
                s["status"] = "pending"
                said.append(f"{s['id']} goes on without it and needs trying again")
            if len(gone) > 1:
                said.append(f"{', '.join(gone[1:])} {'was' if len(gone) == 2 else 'were'} accepted on top of "
                            f"{s['id']}, so {'it needs' if len(gone) == 2 else 'they need'} trying again too")
        return said, [plan["upgrades"][u] for u in ids]
    said, ups = mutate_plan(ws, change)
    print(f"Held {names_of(ups)}: {a.reason}." + (" " + "; ".join(said) + "." if said else ""))


# ---------------------------------------------------------------- commands: proving


def cmd_try(a) -> None:
    ws, cfg = load(a)
    base = need_baseline(ws)
    plan = load_plan(ws)
    st = get_step(plan, a.step)
    if st["status"] in ("held", "split"):
        kit.fail(f"{st['id']} is {st['status']}" + (f" ({st.get('reason')})" if st.get("reason") else "")
                 + "; there's nothing to try")
    if st["status"] == "accepted":
        if not a.again:
            kit.fail(f"{st['id']} is accepted already; --again tries it again (and every step accepted after it)")
        mutate_plan(ws, lambda p: unaccept(ws, p, st["id"]))
        plan = load_plan(ws)
        st = get_step(plan, a.step)
    if not st["upgrades"]:
        kit.fail(f"{st['id']} has no upgrades left")
    warn_if_changed(cfg)
    ups = step_ups(plan, st)
    migs = [ws / "steps" / st["migration_patch"]] if st.get("migration_patch") else []
    print(f"Trying {st['id']}: {st['title']}" + (f" ({names_of(ups)})" if len(ups) > 1 else "")
          + (f", on top of {', '.join(plan['accepted'])}" if plan["accepted"] else "") + "...")
    rec, patch = trial(ws, cfg, base, step=st["id"], ups=ups, migrations=migs, label="try", keep=a.keep)
    took = mutate_plan(ws, lambda p: accept(ws, p, get_step(p, st["id"]), rec, patch))
    report_try(ws, plan, st, rec, took, ups)
    sys.exit(0 if took else 1)


def report_try(ws: Path, plan: dict, st: dict, rec: dict, took: bool, ups: list[dict]) -> None:
    sid = st["id"]
    if took:
        files = kit.changed_files(kit.read_patch(ws / "steps" / f"{sid}.patch"))
        print(f"{sid} passes: {rec['detail']}. Accepted ({rec['id']}, {dur(rec['secs'])}); it changes "
              f"{', '.join(files) or 'nothing'}.")
        for n in rec["notes"]:
            print(f"  note: {n}")
        if any(u.get("unchecked_by_tests") for u in ups):
            print("  note: CI runs this, not the sandbox's tests, so the tests passing doesn't check it.")
        return
    if rec["verdict"] == "pass":
        print(f"{sid} passed, but another step was accepted while it ran; try it again.")
        return
    word = "fails" if rec["verdict"] == "fail" else "couldn't be installed"
    print(f"{sid} {word} ({rec['id']}, {dur(rec['secs'])}):")
    print_failure(rec)
    units = len({u.get("link") or u["id"] for u in ups})
    if units > 1 and not st.get("migration_patch"):
        print(f"Next: {SCRIPT} bisect -w {ws} {sid}   (finds which of the {len(ups)} breaks it)")
    elif st.get("hold_on_fail"):
        print(f"Majors that break are held in this run: {SCRIPT} hold -w {ws} {sid} --reason \"<what broke>\"")
    else:
        print(f"Next: send a migrator ({SCRIPT} sandbox -w {ws} migrate-{sid} --step {sid}), or hold it.")


def find_min(items: list[int], fails) -> list[int]:
    """The smallest part of a failing list of units that still fails: halves first, then interactions."""
    if len(items) == 1:
        return items
    a, b = items[:len(items) // 2], items[len(items) // 2:]
    if fails(a):
        return find_min(a, fails)
    if fails(b):
        return find_min(b, fails)
    a2 = minimize(a, b, fails)
    return sorted(a2 + minimize(b, a2, fails))


def minimize(items: list[int], ctx: list[int], fails) -> list[int]:
    """The smallest part of items that still fails together with ctx (items + ctx fails, ctx alone passes)."""
    if len(items) == 1:
        return items
    a, b = items[:len(items) // 2], items[len(items) // 2:]
    if fails(sorted(a + ctx)):
        return minimize(a, ctx, fails)
    if fails(sorted(b + ctx)):
        return minimize(b, ctx, fails)
    a2 = minimize(a, sorted(b + ctx), fails)
    return a2 + minimize(b, sorted(a2 + ctx), fails)


class OutOfTrials(Exception):
    pass


def cmd_bisect(a) -> None:
    ws, cfg = load(a)
    base = need_baseline(ws)
    plan = load_plan(ws)
    st = get_step(plan, a.step)
    if st["status"] not in ("fail", "install-fail"):
        kit.fail(f"bisect needs a step whose try failed; {st['id']} is {st['status']}")
    if st.get("migration_patch"):
        kit.fail(f"{st['id']} has a migration; bisect splits batches of plain upgrades")
    ups = step_ups(plan, st)
    units: list[list[dict]] = []
    for u in ups:
        same = next((x for x in units if u.get("link") and x[0].get("link") == u["link"]), None)
        (same.append(u) if same is not None else units.append([u]))
    if len(units) < 2:
        kit.fail(f"{st['id']} is one upgrade (or upgrades that move together), so there's nothing to split. "
                 "Send a migrator, or hold it.")
    warn_if_changed(cfg)
    on_top = list(plan["accepted"])
    known: dict[frozenset, bool] = {frozenset(range(len(units))): True}
    trials: dict[frozenset, tuple[dict, str]] = {}
    log: list[dict] = []
    t0 = time.monotonic()
    print(f"Bisecting {st['id']} ({plural(len(ups), 'upgrade')}"
          + (f" in {len(units)} groups that move together" if len(units) != len(ups) else "")
          + "): each trial is a fresh sandbox with part of the batch.")

    def subset(idx):
        return [u for i in sorted(idx) for u in units[i]]

    def fails(idx) -> bool:
        key = frozenset(idx)
        if key in known:
            return known[key]
        if len(trials) >= a.max_trials:
            raise OutOfTrials
        rec, patch = trial(ws, cfg, base, step=st["id"], ups=subset(idx), label="bisect", on_top=on_top)
        trials[key] = (rec, patch)
        known[key] = rec["verdict"] != "pass"
        log.append({"trial": rec["id"], "upgrades": rec["upgrades"], "verdict": rec["verdict"],
                    "broken": rec["broken"][:5], "detail": rec["detail"]})
        print(f"  trial {len(trials)}: {names_of(subset(idx), 4)} → {trial_line(rec)}")
        return known[key]
    culprits: list[list[int]] = []
    remaining = list(range(len(units)))
    rest_ok, out_of = False, False
    try:
        while remaining:
            m = find_min(remaining, fails)
            if not fails(m):   # every part passes alone: the batch's failure didn't come back (flaky?)
                break
            culprits.append(m)
            remaining = [i for i in remaining if i not in m]
            if not remaining or not fails(remaining):
                rest_ok = bool(remaining)
                break
    except OutOfTrials:
        out_of = True
    secs = round(time.monotonic() - t0, 1)
    if not culprits and not out_of:
        print(f"No part of {st['id']} fails on its own any more: the failure may be flaky. {st['id']} is pending "
              "again; try it once more.")
        mutate_plan(ws, lambda p: get_step(p, st["id"]).update(status="pending"))
        for rec, _ in trials.values():
            drop_tested_files(ws, rec["id"])
        sys.exit(1)

    def culprit_text(c):
        cu = subset(c)
        rec = trials.get(frozenset(c), (None,))[0]
        many = len(cu) > 1
        if rec and rec["verdict"] == "install-fail":
            how = f"can't be installed{' together' if len(c) > 1 else ''} ({rec['detail']})"
        else:
            broken = (rec or {}).get("broken") or []
            what = ", ".join(broken[:3]) + (f" and {len(broken) - 3} more" if len(broken) > 3 else "") if broken \
                else "the tests"
            how = f"{'break' if many else 'breaks'} {what}" + (" together" if len(c) > 1 else "")
        if not many:
            return f"{up_text(cu[0])} {how}"
        if len(c) == 1:
            return f"{names_of(cu)} (released together) {how}"
        alone = "each passes without the other" if len(c) == 2 else "no smaller part of them fails"
        return f"{' and '.join(up_text(u) for u in cu)} {how} ({alone})"
    lines = [culprit_text(c) for c in culprits]
    rest = subset(remaining) if remaining else []
    if rest_ok:
        tail_txt = f"the other {len(rest)} pass together" if len(rest) > 1 else f"{up_text(rest[0])} passes"
    elif out_of:
        tail_txt = f"ran out of trials ({a.max_trials}) with {plural(len(rest), 'upgrade')} not yet proven"
    else:
        tail_txt = "nothing else is left"
    result = "; ".join(lines + [tail_txt]) + "."

    def split(plan):
        s = get_step(plan, st["id"])
        nums = [int(x["id"][1:]) for x in plan["steps"] if re.fullmatch(r"S\d+", x["id"])]
        nxt = max(nums or [0]) + 1
        made = []
        if rest:
            rid = f"S{nxt}"
            nxt += 1
            rs = {"id": rid, "group": s["group"], "title": title_for(rest, s["group"]), "upgrades": [u["id"] for u in rest],
                  "status": "pending", "tries": [], "attempts": 0, "from": s["id"]}
            if rest_ok:
                rec, patch = trials[frozenset(remaining)]
                accept(ws, plan, rs, rec, patch)
            made.append(rs)
        for c in culprits:
            cid = f"S{nxt}"
            nxt += 1
            cu = subset(c)
            rec = trials.get(frozenset(c), (None,))[0]
            cs = {"id": cid, "group": "major" if any(u["kind"] == "major" for u in cu) else s["group"],
                  "title": title_for(cu, s["group"]), "upgrades": [u["id"] for u in cu],
                  "status": rec["verdict"] if rec else "fail", "tries": [rec["id"]] if rec else [], "attempts": 0,
                  "from": s["id"], "hold_on_fail": s.get("hold_on_fail", False)}
            if rec:
                cs["last"] = {k: rec[k] for k in ("id", "verdict", "phase", "detail", "broken")}
            elif len(c) == len(units) and s.get("last"):   # the whole batch: its own try is the evidence
                cs["last"], cs["tries"], cs["status"] = dict(s["last"]), list(s["tries"][-1:]), s["status"]
            made.append(cs)
        s["status"], s["split_into"] = "split", [x["id"] for x in made]
        i = plan["steps"].index(s)
        plan["steps"][i + 1:i + 1] = made
        plan["bisects"][s["id"]] = {"trials": len(trials), "secs": secs, "log": log, "result": result,
                                    "culprits": [[u["id"] for u in subset(c)] for c in culprits]}
        return made
    made = mutate_plan(ws, split)
    for rec, _ in trials.values():   # passing parts that weren't kept as the rest
        drop_tested_files(ws, rec["id"])
    print(f"Result, after {plural(len(trials), 'trial')} ({dur(secs)}): {result}")
    for m in made:
        print(f"  {m['id']}: {m['title']} · {'accepted' if m['status'] == 'accepted' else m['status']}"
              + (" (its trial passed, so it's accepted)" if m["status"] == "accepted" else ""))
    print(f"{st['id']} is split into {', '.join(m['id'] for m in made)}. Next: send a migrator to each failing step, "
          "or hold it" + (f"; then try {made[0]['id']}" if made and made[0]["status"] == "pending" else "") + ".")
    sys.exit(0 if not out_of else 1)


def failures_text(ws: Path, plan: dict, st: dict, base: dict) -> str:
    ups = step_ups(plan, st)
    last = st.get("last") or {}
    lines = ["Upgrade: " + "; ".join(f"{up_text(u)} ({u['manager']}, {u['manifest']})" for u in ups), ""]
    if last.get("verdict") in ("fail", "install-fail"):
        lines.append(f"With it, {last.get('detail')}.")
        if last.get("broken"):
            lines.append("These passed before the upgrade and don't now:")
            lines += [f"  {b}" for b in last["broken"][:40]]
        old = sorted({t for u in base["tests"] for t in u["failing"]})
        flaky = sorted({t for u in base["tests"] for t in u["flaky"]})
        if old:
            lines += ["", "These failed before the upgrade too (not your job):"] + [f"  {t}" for t in old[:20]]
        if flaky:
            lines += ["", "These fail now and then even without it (ignore them):"] + [f"  {t}" for t in flaky[:20]]
    lines += ["", "The project's commands (run them through the run command):"]
    for k in ("install", "build", "tests"):
        for c in base["commands"][k]:
            lines.append(f"  {k}: {show_cmd(c)}")
    rec = kit.load_json(ws / "trials" / f"{last.get('id')}.json", {}) if last.get("id") else {}
    if rec.get("log") and Path(rec["log"]).is_file():
        lines += ["", "Output of the failing run:", Path(rec["log"]).read_text(encoding="utf-8", errors="replace")[-60000:]]
    return "\n".join(lines) + "\n"


def changelog_links(u: dict) -> list[str]:
    n, to, m = u["name"], str(u.get("to") or "").removeprefix("v"), u["manager"]
    return {"npm": [f"https://www.npmjs.com/package/{n}?activeTab=versions"],
            "pnpm": [f"https://www.npmjs.com/package/{n}?activeTab=versions"],
            "yarn": [f"https://www.npmjs.com/package/{n}?activeTab=versions"],
            "pip": [f"https://pypi.org/project/{n}/{to}/"], "uv": [f"https://pypi.org/project/{n}/{to}/"],
            "poetry": [f"https://pypi.org/project/{n}/{to}/"],
            "cargo": [f"https://crates.io/crates/{n}/versions", f"https://docs.rs/{n}/{to}"],
            "go": [f"https://pkg.go.dev/{n}@{u.get('to')}"], "bundler": [f"https://rubygems.org/gems/{n}/versions"],
            "dotnet": [f"https://www.nuget.org/packages/{n}/{to}"],
            "actions": [f"https://github.com/{n.split('/')[0]}/{n.split('/')[1] if '/' in n else n}/releases"],
            }.get(m, [])


def cmd_sandbox(a) -> None:
    ws, cfg = load(a)
    base = need_baseline(ws)
    plan = load_plan(ws)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", a.name):
        kit.fail("a sandbox name is letters, digits, dots, dashes and underscores")
    st = get_step(plan, a.step) if a.step else None
    if st and st["status"] in ("held", "split"):
        kit.fail(f"{st['id']} is {st['status']}")
    if st and st["status"] == "accepted":
        upto = plan["accepted"][:plan["accepted"].index(st["id"]) + 1]
    else:
        upto = list(plan["accepted"])
    box = make_box(ws, cfg, a.name, base)
    try:
        apply_accepted(ws, box, upto)
        if st and st["status"] != "accepted":
            ok, detail, out = apply_upgrades(box, step_ups(plan, st), base["timeout"])
            if not ok:
                kit.fail(f"the upgrade didn't apply in the sandbox: {detail}\n{tail(out, 15)}")
        _, ok = run_install(box, base)
        if not ok:
            kit.fail("the install failed in the sandbox; read the step's last try for why")
        rebase_box(box, "the upgrade, installed")
        if st:
            (box / "scratch" / "failures.txt").write_text(failures_text(ws, plan, st, base), encoding="utf-8")
    except BaseException:
        kit.drop_sandbox(ws, cfg, a.name)
        raise
    print(f"Sandbox {a.name} is ready.")
    print(kit.sandbox_text(box, cfg["network"]))
    if st:
        what = "applied and installed" if st["status"] != "accepted" else "applied (it's accepted) and installed"
        print(f"  state:        the accepted steps{' up to ' + st['id'] if st['status'] == 'accepted' else ''} and "
              f"{st['id']} ({names_of(step_ups(plan, st), 4)}) {what}; the agent's changes are what differs from this")
        print(f"  failures:     {box / 'scratch' / 'failures.txt'}" if (st.get("last") or {}).get("verdict") != "pass"
              else "  failures:     none (the step passed)")
        links = [x for u in step_ups(plan, st) for x in changelog_links(u)]
        if links:
            print(f"  changelogs:   {' '.join(links[:6])}")
    else:
        print("  state:        the accepted steps applied and installed")


def manifest_fingerprint(path: str, text: str | None):
    """The dependency part of a manifest, to tell code and config changes from dependency changes."""
    if text is None:
        return None
    b = path.rsplit("/", 1)[-1]
    if b == "package.json":
        j = extract_json(text) or {}
        if not isinstance(j, dict):
            return text
        keys = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies", "overrides",
                "resolutions", "bundleDependencies", "workspaces")
        return json.dumps({k: j.get(k) for k in keys} | {"pnpm": (j.get("pnpm") or {}).get("overrides")}, sort_keys=True)
    if b in ("pyproject.toml", "Cargo.toml"):
        t = read_toml(text) if tomllib else None
        if t is None:
            return text
        def deps(node, out, path=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    p = f"{path}.{k}" if path else k
                    if re.search(r"dependencies|dependency-groups|^build-system|\.uv\.(sources|override|constraint)|"
                                 r"^patch|^replace|requires$", p):
                        out[p] = v
                    else:
                        deps(v, out, p)
            return out
        return json.dumps(deps(t, {}), sort_keys=True, default=str)
    if b == "Gemfile":
        return "\n".join(ln.strip() for ln in text.splitlines() if re.match(r"\s*(gem|source|gemspec|ruby)\b", ln))
    if PROJ_FILE.search(b) or b == "Directory.Packages.props":
        return "\n".join(sorted(re.findall(r"<Package(?:Reference|Version)\b[^>]*>", text)))
    return "\n".join(ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(("#", "//")))


def dependency_changes(box: Path, files: list[str], found: dict) -> list[str]:
    repo, base = box / "repo", kit.box_base(box)
    is_dep = dep_file_matcher(found)
    lockpaths = {e.get("lockfile") for e in found.get("ecosystems", []) if e.get("lockfile")}
    out = []
    for f in files:
        b = f.rsplit("/", 1)[-1]
        if b in LOCKFILES or f in lockpaths:
            out.append(f"it changes the lockfile {f}")
            continue
        if not is_dep(f):
            continue
        r = subprocess.run(["git", "-C", str(repo), "show", f"{base}:{f}"], capture_output=True)
        before = r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
        after = read_text(repo / f)
        if manifest_fingerprint(f, before) != manifest_fingerprint(f, after):
            out.append(f"it changes the dependencies in {f}")
    return out


def read_reply_soft(path: Path) -> tuple[dict | None, str | None]:
    if not path.is_file():
        return None, "no reply.json"
    try:
        return kit.read_reply(path), None
    except SystemExit:
        return None, "reply.json has no JSON object that parses"


def cmd_migrate(a) -> None:
    ws, cfg = load(a)
    base = need_baseline(ws)
    plan = load_plan(ws)
    st = get_step(plan, a.step)
    if st["status"] not in OPEN:
        kit.fail(f"{st['id']} is {st['status']}; a migration is for a step that fails")
    box = kit.find_sandbox(cfg, a.sandbox)
    found = load_found(ws)
    patch = kit.box_diff(box)
    files = kit.changed_files(patch)
    reply, reply_note = read_reply_soft(box / "scratch" / kit.REPLY)
    n = st.get("attempts", 0) + 1
    problems = [] if patch.strip() else ["the migrator changed nothing"]
    problems += dependency_changes(box, files, found)
    tests_changed = [f for f in files if TEST_FILE.search(f)]
    mdir = ws / "migrations"
    mdir.mkdir(exist_ok=True)
    kit.keep_scratch(box, mdir / f"{st['id']}-{n}")
    pname = f"{st['id']}.migration-{n}.patch"
    kit.write_patch(ws / "steps" / pname, patch)
    if not a.keep:
        kit.drop_sandbox(ws, cfg, a.sandbox)
    info = {"attempt": n, "files": files, "tests_changed": tests_changed, "problems": problems,
            "reply": reply, "reply_note": reply_note, "patch": pname, "at": kit.now()}
    rec = None
    if not problems:
        print(f"Checking {st['id']}'s migration (attempt {n}: {', '.join(files)}) with a fresh try...")
        rec, full = trial(ws, cfg, base, step=st["id"], ups=step_ups(plan, st), migrations=[ws / "steps" / pname],
                          label="migrate")
        info["trial"] = rec["id"]
        info["verdict"] = rec["verdict"]
    else:
        full = ""
        info["verdict"] = "refused"
    kit.save_json(mdir / f"{st['id']}-{n}.json", info)

    def update(plan):
        s = get_step(plan, st["id"])
        s["attempts"] = n
        s.setdefault("migrations", []).append(f"{st['id']}-{n}")
        took = False
        if rec is not None:
            took = accept(ws, plan, s, rec, full)
            if took:
                s["migration_patch"] = pname
        if not took and n >= 2:
            why = "; ".join(problems) if problems else (rec["detail"] if rec else "")
            broken = (rec or {}).get("broken") or []
            reason = (f"two migration attempts failed; the last: {why}"
                      + (f" ({', '.join(broken[:3])})" if broken else ""))
            s["status"], s["reason"] = "held", reason
            plan["holds"][s["id"]] = {"reason": reason, "upgrades": list(s["upgrades"]), "by": "upgrade.py",
                                      "at": kit.now()}
        return took, s["status"]
    took, status = mutate_plan(ws, update)
    if reply_note:
        print(f"note: {reply_note} from the migrator, so there's no summary of what it changed")
    if problems:
        print(f"{st['id']}: migration attempt {n} refused: {'; '.join(problems)}. A migration changes the "
              "project's code to work with the new version; it doesn't pin back, add dependencies or touch the lockfile.")
    elif took:
        print(f"{st['id']} passes with the migration: {rec['detail']}. Accepted ({rec['id']}); the code changed in "
              f"{', '.join(files)}.")
        if tests_changed:
            text = (f"{st['title']}: the migration changed test files ({', '.join(tests_changed)}); check it didn't "
                    "weaken them")
            add_decision(ws, text)
            print(f"  It changed test files: {', '.join(tests_changed)}. Recorded as a decision to check.")
    else:
        print(f"{st['id']}: migration attempt {n} didn't pass ({rec['id']}):")
        print_failure(rec)
    if not took:
        if status == "held":
            print(f"{st['id']} is held: two attempts failed.")
        else:
            print(f"Next: one more attempt: a fresh migrator with the new failures ({SCRIPT} sandbox -w {ws} "
                  f"migrate-{st['id']}-{n + 1} --step {st['id']}), or hold it.")
    sys.exit(0 if took else 1)


def cmd_notes(a) -> None:
    ws, cfg = load(a)
    base = need_baseline(ws)
    plan = load_plan(ws)
    st = get_step(plan, a.step)
    if st["status"] != "accepted":
        kit.fail(f"notes are for an accepted step; {st['id']} is {st['status']}")
    if bool(a.reply) == bool(a.sandbox):
        kit.fail("give the reply file, or --sandbox <name> to read its reply.json")
    box = kit.find_sandbox(cfg, a.sandbox) if a.sandbox else None
    data = kit.read_reply(box / "scratch" / kit.REPLY if box else Path(a.reply))
    changes = [c for c in kit.as_list(data.get("changes")) if isinstance(c, dict) and str(c.get("change") or "").strip()]
    scratch = ws / "notes" / st["id"]
    if box:
        kit.keep_scratch(box, scratch)
        if not a.keep:
            kit.drop_sandbox(ws, cfg, a.sandbox)
    upto = plan["accepted"][:plan["accepted"].index(st["id"]) + 1]
    for c in changes:
        cmd = kit.as_cmd(c.get("command"))
        c["checked"] = None
        if not cmd:
            continue
        b = new_box(ws, cfg, f"notes-{st['id']}", base)
        try:
            apply_accepted(ws, b, upto)
            _, ok = run_install(b, base)
            if ok:
                if scratch.is_dir():
                    shutil.copytree(scratch, b / "scratch", dirs_exist_ok=True)
                r = kit.run_in(b, cmd, 1, min(base["timeout"], 600), log=False)["runs"][0]
                c["checked"] = kit.classify(r)
                c["rerun_out"] = tail(r["out"], 15)
            else:
                c["checked"] = "inconclusive"
        finally:
            drop_box(ws, cfg, b)
    with kit.locked(ws):
        notes = kit.load_json(ws / "notes.json", {})
        notes[st["id"]] = {"read": kit.as_list(data.get("read")), "changes": changes, "at": kit.now()}
        kit.save_json(ws / "notes.json", notes)
    shows = sum(1 for c in changes if c["checked"] == "shows")
    tried = sum(1 for c in changes if c["checked"])
    print(f"{st['id']}: {plural(len(changes), 'behaviour change')} noted; {tried} had a reproduction, rerun in a "
          f"fresh copy: {shows} showed it. The rest are advice, unchecked.")
    for c in changes:
        tag = {"shows": "reproduced", "behaves": "didn't reproduce", "inconclusive": "inconclusive",
               None: "unchecked"}[c["checked"]]
        print(f"  [{tag}] {c['change']}" + (f" ({c.get('affects')})" if c.get("affects") else ""))


def cmd_status(a) -> None:
    ws, cfg = load(a)
    plan = load_plan(ws, required=False)
    ch = project_change(cfg)
    print(f"Project: {cfg['repo']}" + (f": changed since init ({ch})" if ch else ": unchanged since init"))
    base = kit.load_json(ws / "baseline.json", None)
    if not base:
        print(f"Next: {SCRIPT} baseline -w {ws} --install -- <command> --tests -- <command>")
        return
    if not base.get("ok"):
        print(f"Baseline: blocked ({base.get('problem')}). Next: fix it and run baseline again.")
        return
    if not (ws / "outdated.json").is_file():
        print(f"Next: {SCRIPT} outdated -w {ws}")
        return
    if not plan:
        print(f"Next: {SCRIPT} plan -w {ws}")
        return
    order = plan["accepted"] + [s["id"] for s in plan["steps"] if s["id"] not in plan["accepted"]]
    print("Steps (accepted ones in the order they'll land):")
    for sid in order:
        s = get_step(plan, sid)
        extra = s.get("reason") or s.get("note") or (f"into {', '.join(s['split_into'])}" if s.get("split_into") else "") \
            or ((s.get("last") or {}).get("detail") if s["status"] in ("fail", "install-fail") else "")
        print(f"  {s['id']:<4} {s['status']:<12} {s['title']}" + (f": {extra}" if extra else ""))
    land = kit.load_json(ws / "land.json", None)
    nxt = None
    for s in plan["steps"]:
        if s["status"] == "pending":
            nxt = f"{SCRIPT} try -w {ws} {s['id']}"
            break
    if not nxt:
        for s in plan["steps"]:
            if s["status"] in ("fail", "install-fail"):
                units = len({plan["upgrades"][u].get("link") or u for u in s["upgrades"]})
                if units > 1 and not s.get("migration_patch"):
                    nxt = f"{SCRIPT} bisect -w {ws} {s['id']}"
                elif s.get("hold_on_fail"):
                    nxt = f"{SCRIPT} hold -w {ws} {s['id']} --reason \"<what broke>\""
                else:
                    nxt = (f"a migrator for {s['id']} ({SCRIPT} sandbox -w {ws} migrate-{s['id']} --step {s['id']}), "
                           f"attempt {s.get('attempts', 0) + 1} of 2, or hold it")
                break
    if not nxt:
        nxt = f"{SCRIPT} report -w {ws}" if land else (f"{SCRIPT} land -w {ws}" if plan["accepted"] else
                                                         f"{SCRIPT} report -w {ws}")
    if land:
        print(f"Landed on {land['branch']}: {plural(len(land['commits']), 'commit')}; final check "
              f"{land['final']['verdict']}.")
    print(f"Next: {nxt}")


# ---------------------------------------------------------------- commands: landing and reporting


def commit_message(ws: Path, plan: dict, st: dict, base: dict | None = None) -> tuple[str, str]:
    ups = step_ups(plan, st)
    body = []
    if len(ups) > 1:
        body += [f"- {up_text(u)}" + (f" ({u['manifest']})" if u.get("manifest") else "") for u in ups] + [""]
    if any(u.get("unchecked_by_tests") for u in ups):
        body.append("CI runs this, so the local build and tests don't exercise it.")
    else:
        cmds = (base or {}).get("commands") or {}
        what = [w for w, k in (("install", "install"), ("build", "build"), ("tests", "tests")) if cmds.get(k)] or ["tests"]
        said = what[0] if len(what) == 1 else ", ".join(what[:-1]) + " and " + what[-1]
        body.append(f"The {said} pass as {'it' if said in ('install', 'build') else 'they'} did before the upgrade."
                    .replace("The install pass", "The install passes").replace("The build pass", "The build passes"))
    if st.get("migration_patch"):
        files = kit.changed_files(kit.read_patch(ws / "steps" / st["migration_patch"]))
        if files:
            body.append(f"Code changed for the new version: {', '.join(files)}.")
    fixes = sorted({f for u in ups for f in u.get("fixes", [])})
    if fixes:
        body.append(f"Fixes {', '.join(fixes)}.")
    return st["title"], "\n".join(body)


def default_branch(top: Path) -> str:
    base = f"killer-upgrade/{kit.today()}"
    name, n = base, 2
    while kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{name}"):
        name, n = f"{base}-{n}", n + 1
    return name


def audit_now(box: Path, found: dict, base: dict) -> tuple[list[dict], list[str]]:
    advs, checked = [], []
    for eco in found.get("ecosystems", []):
        m, d = eco["manager"], eco["dir"]
        if m == "npm" and shutil.which("npm"):
            advs += [dict(x, eco=eco["id"]) for x in parse_npm_audit(run_tool(box, ["npm", "audit", "--json"], d)[1])]
            checked.append(eco["id"])
        elif m == "pnpm" and shutil.which("pnpm"):
            advs += [dict(x, eco=eco["id"]) for x in parse_npm_audit(run_tool(box, ["pnpm", "audit", "--json"], d)[1])]
            checked.append(eco["id"])
        elif m == "custom":
            code, out, _ = run_tool(box, eco["outdated"], d)
            if extract_json(out) is not None:
                advs += [dict(x, eco=eco["id"]) for x in parse_custom_outdated(out)[1]]
                checked.append(eco["id"])
        elif m in ("pip", "uv", "poetry") and shutil.which("pip-audit"):
            env = {"PIPAPI_PYTHON_LOCATION": str(venv_bin(box) / "python")} if base.get("venv") else {}
            advs += [dict(x, eco=eco["id"]) for x in parse_pip_audit(
                run_tool(box, ["pip-audit", "-f", "json", "--progress-spinner", "off"], d, extra_env=env)[1])]
            checked.append(eco["id"])
        elif m == "cargo" and run_tool(box, ["cargo", "audit", "--version"], d, timeout=60)[0] == 0:
            advs += [dict(x, eco=eco["id"]) for x in parse_cargo_audit(run_tool(box, ["cargo", "audit", "--json"], d)[1])]
            checked.append(eco["id"])
    return advs, checked


def cmd_land(a) -> None:
    ws, cfg = load(a)
    base = need_baseline(ws)
    plan = load_plan(ws)
    if not cfg["git"] or not cfg.get("head"):
        kit.fail("land needs a git repository with at least one commit; the accepted patches are in "
                 f"{ws / 'steps'} to apply by hand")
    if (ws / "land.json").is_file() and not a.again:
        old = kit.load_json(ws / "land.json", {})
        kit.fail(f"already landed on {old.get('branch')}; --again lands once more")
    accepted = list(plan["accepted"])
    if not accepted:
        kit.fail("nothing is accepted, so there's nothing to land", 1)
    top = Path(cfg["repo"])
    current = kit.git(top, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip()
    branch = a.branch or default_branch(top)
    if not kit.git_ok(top, "check-ref-format", "--branch", branch):
        kit.fail(f"{branch!r} isn't a valid branch name")
    exists = kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")
    here = exists and branch == current
    if exists and not kit.git_ok(top, "merge-base", "--is-ancestor", cfg["head"], branch):
        kit.fail(f"{branch} doesn't contain {cfg['head'][:10]}, the commit the run started from")
    patches = {sid: kit.read_patch(ws / "steps" / f"{sid}.patch") for sid in accepted}
    touched = sorted({f for p in patches.values() for f in kit.changed_files(p)})
    # The patches were made from the project's files as they are on disk. Those files must still be what
    # they were when the run began, or the commits would carry someone else's changes.
    if touched and not kit.git_ok(top, "diff", "--quiet", cfg["head"], "--", *touched):
        moved = kit.git(top, "diff", "--name-only", cfg["head"], "--", *touched, check=False).split()
        kit.fail(f"{', '.join(moved[:5]) or 'files the upgrades change'} changed since the run began (uncommitted "
                 "work, or a new commit); commit or stash it, or start a new run")
    for sid in accepted:
        if patches[sid].strip() and not (ws / "steps" / f"{sid}.files.json").is_file():
            kit.fail(f"the files {sid}'s try tested are missing from {ws / 'steps'}; try it again with --again")
    status_before = kit.git_status(top)
    commits, skipped = [], []
    wt, finished = None, False
    try:
        if here:
            where = top
        else:
            if not exists:
                kit.git(top, "branch", branch, cfg["head"])
            wt = Path(cfg["sandboxes_home"]) / cfg["run_id"] / f"land-{secrets.token_hex(3)}"
            wt.parent.mkdir(parents=True, exist_ok=True)
            kit.git(top, "worktree", "add", "-q", str(wt), branch)
            where = wt
        print(f"Landing {plural(len(accepted), 'accepted step')} on {branch}"
              + (" (the current branch)" if here else (" (new, from " + cfg["head"][:10] + ")" if not exists else ""))
              + ":")
        for sid in accepted:
            st = get_step(plan, sid)
            if not patches[sid].strip():
                skipped.append(sid)
                print(f"  {sid}: changes nothing, no commit")
                continue
            # The files exactly as the step's try tested them. Whole files rather than the patch, so git's
            # line-ending settings treat them as the user's own edits, whatever the checkout looks like.
            tested = kit.load_json(ws / "steps" / f"{sid}.files.json", {})
            for f in tested.get("files", []):
                kit.copy_path(ws / "steps" / f"{sid}.files" / f, where / f)
            for f in tested.get("deleted", []):
                if (where / f).exists() or (where / f).is_symlink():
                    (where / f).unlink()
            files = tested.get("files", []) + tested.get("deleted", [])
            subject, body = commit_message(ws, plan, st, base)
            kit.git(where, "add", "-A", "--", *files)
            if here:
                kit.git(where, "commit", "-q", "-m", subject, "-m", body, "--", *files)
            else:
                kit.git(where, "commit", "-q", "-m", subject, "-m", body)
            sha = kit.git(where, "rev-parse", "HEAD").strip()
            commits.append({"step": sid, "sha": sha, "subject": subject, "files": files})
            print(f"  {sha[:10]} {subject}")
        finished = True
    finally:
        if wt is not None:
            kit.git(top, "worktree", "remove", "--force", str(wt), check=False)
            kit.git(top, "worktree", "prune", check=False)
            kit.rmtree(wt)
        if not exists and not commits:   # stopped before the first commit, or nothing to commit: no empty branch
            kit.git(top, "branch", "-D", branch, check=False)
    if finished and not commits:
        kit.fail("every accepted step changes nothing, so there's nothing to land", 1)
    if not here and kit.git_status(top) != status_before:
        print("warning: the project's working tree changed while landing; check git status")
    print(f"Checking {branch} in a fresh sandbox (install, build, {plural(base['runs'], 'test run')})...")
    rec, _ = trial(ws, cfg, base, step=None, ups=[], label="final", rev=branch, keep=True)
    found = load_found(ws)
    advs_left, audited = [], []
    try:
        if rec["verdict"] == "pass":
            advs_left, audited = audit_now(Path(rec["box"]), found, base)
    finally:
        kit.drop_sandbox(ws, cfg, Path(rec["box"]).name)
    land = {"branch": branch, "created": not exists, "on_current": here, "base": cfg["head"], "commits": commits,
            "skipped": skipped, "final": {k: rec[k] for k in ("id", "verdict", "phase", "detail", "broken", "kept", "secs")},
            "audited": audited, "advisories_left": advs_left, "at": kit.now()}
    kit.save_json(ws / "land.json", land)
    if rec["verdict"] == "pass":
        print(f"{branch} passes: {rec['detail']} ({dur(rec['secs'])}).")
    else:
        print(f"{branch} FAILED its final check: don't merge it as it is.")
        print_failure(rec)
    if audited:
        print(f"Audit on the branch: {plural(len(advs_left), 'advisory', 'advisories')} left.")
    print(f"The branch you were on{f' ({current})' if current and not here else ''} is untouched; nothing was pushed. "
          f"Next: {SCRIPT} report -w {ws}")
    sys.exit(0 if rec["verdict"] == "pass" else 1)


def cmd_decide(a) -> None:
    ws, _ = load(a)
    add_decision(ws, a.text)
    print(f"Recorded the decision: {a.text}")


def cmd_wait(a) -> None:
    ws, _ = load(a)
    with kit.locked(ws):
        w = kit.load_json(ws / "waiting.json", [])
        w.append({"text": a.text, "command": a.command, "at": kit.now()})
        kit.save_json(ws / "waiting.json", w)
    print(f"Recorded what's waiting for an OK: {a.text} (`{a.command}`)")


def cell(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def cmd_report(a) -> None:
    ws, cfg = load(a)
    base = kit.load_json(ws / "baseline.json", None)
    od = kit.load_json(ws / "outdated.json", None) or {}
    plan = load_plan(ws, required=False) or {"steps": [], "upgrades": {}, "accepted": [], "holds": {}, "left_out": [],
                                             "bisects": {}}
    land = kit.load_json(ws / "land.json", None)
    notes = kit.load_json(ws / "notes.json", {})
    decisions = kit.load_json(ws / "decisions.json", [])
    waiting = kit.load_json(ws / "waiting.json", [])
    settings = cfg["upgrade"]
    ups = plan["upgrades"] or {u["id"]: u for u in od.get("upgrades", [])}
    steps = plan["steps"]
    accepted = [get_step(plan, s) for s in plan["accepted"]]
    acc_ids = [u for s in accepted for u in s["upgrades"]]
    held_steps = [s for s in steps if s["status"] == "held"]
    held_ids = sorted({u for h in plan["holds"].values() for u in h.get("upgrades", [])} |
                      {u for s in held_steps for u in s["upgrades"]}, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)
    held_ids = [u for u in held_ids if u not in acc_ids]
    open_steps = [s for s in steps if s["status"] in OPEN]
    open_ids = [u for s in open_steps for u in s["upgrades"]]
    landed_ids = [u for c in (land or {}).get("commits", []) for u in get_step(plan, c["step"])["upgrades"]] if land else []
    trial_files = [f for f in (ws / "trials").glob("T*.json")] if (ws / "trials").is_dir() else []
    trials_n = len(trial_files)
    bisect_trials = sum(b["trials"] for b in plan["bisects"].values())
    migr = [kit.load_json(f, {}) for f in sorted((ws / "migrations").glob("*.json"))] if (ws / "migrations").is_dir() else []
    base_pass = sum(len(u["always_pass"]) for u in (base or {}).get("tests", []))
    final = (land or {}).get("final")
    advs = od.get("advisories", [])
    adv_left = (land or {}).get("advisories_left")
    landed_set = set(landed_ids or (acc_ids if not land else []))
    if land and land.get("audited"):
        left_ids = {(x["package"], x["id"]) for x in adv_left or []}
        fixed = [x for x in advs if x.get("eco") in land["audited"] and (x["package"], x["id"]) not in left_ids]
        left = [x for x in advs if (x["package"], x["id"]) in left_ids or x.get("eco") not in land["audited"]]
    else:
        fixed = [x for x in advs if any(x["id"] in ups[u].get("fixes", []) for u in landed_set if u in ups)]
        left = [x for x in advs if x not in fixed]
    # status and headline
    if not base or not base.get("ok"):
        status = "blocked"
        headline = f"blocked: {(base or {}).get('problem') or 'no baseline was recorded'}"
    else:
        total = len(acc_ids) + len(held_ids) + len(open_ids)
        finished = not open_steps
        if land:
            ok_final = final and final["verdict"] == "pass"
            status = "done" if finished and ok_final else "partial"
            headline = (f"{len(landed_ids)} of {total} upgrades landed in {plural(len(land['commits']), 'commit')} on "
                        f"{land['branch']}" + (f"; {len(held_ids)} held" if held_ids else "")
                        + (f"; {len(open_ids)} not done" if open_ids else "")
                        + ("; the branch passes everything the baseline passed" if ok_final else
                           "; the branch FAILED its final check"))
        else:
            status = "done" if finished and settings["after"] != "commit" and steps else "partial"
            headline = (f"{len(acc_ids)} of {total} upgrades proven, not landed" + (f"; {len(held_ids)} held"
                        if held_ids else "") + (f"; {len(open_ids)} not done" if open_ids else ""))
        if not total:
            headline = "nothing to upgrade" if od else "outdated hasn't run"
            status = "done" if od else "partial"
    title = cfg.get("target") or Path(cfg["repo"]).name
    L = [f"# Upgrade report: {title}", "",
         f"**Project:** `{cfg['repo']}`" + (f" at `{(cfg.get('head') or '')[:10]}`" if cfg.get("head") else "")
         + (f" ({cfg['branch']})" if cfg.get("branch") else ""),
         f"**Asked for:** {scope_text(settings)}" + (f"; also {', '.join(settings['also'])}" if settings["also"] else ""),
         f"**Result:** {headline[0].upper() + headline[1:]}.", ""]
    if base and base.get("ok"):
        fl = sum(len(u["flaky"]) for u in base["tests"])
        kn = sum(len(u["failing"]) for u in base["tests"])
        L += [f"**Baseline:** {plural(base_pass, 'test')} passing in all {plural(base['runs'], 'run')}"
              + (f", {kn} failing already (not counted)" if kn else "") + (f", {fl} flaky (ignored)" if fl else "")
              + (f"; the build {'passes' if all(b['ok'] for b in base['build']) else 'FAILS'}" if base["build"] else "")
              + ".", ""]
    L += ["## Upgraded", ""]
    if accepted:
        L += ["| Step | Package | From | To | Kind | Commit |", "|---|---|---|---|---|---|"]
        shas = {c["step"]: c["sha"][:10] for c in (land or {}).get("commits", [])}
        for s in accepted:
            for uid in s["upgrades"]:
                u = ups[uid]
                L.append(f"| {s['id']} | {cell(u['name'])} | {cell(u.get('from'))} | {cell(u.get('to'))} | "
                         f"{cell(u['kind'])}{' (dev)' if u.get('dev') and u['group'] != 'action' else ''} | "
                         f"{shas.get(s['id'], 'not landed')} |")
        L.append("")
    else:
        L += ["None.", ""]
    if held_ids:
        L += ["## Held", ""]
        for uid in held_ids:
            reason = next((h["reason"] for h in plan["holds"].values() if uid in h.get("upgrades", [])), None) or \
                next((s.get("reason") for s in held_steps if uid in s["upgrades"]), "")
            L.append(f"- **{cell(up_text(ups[uid]))}**: {reason}")
        L.append("")
    if plan["bisects"] or any(s["status"] in ("fail", "install-fail") for s in steps):
        L += ["## Failed, and what bisect found", ""]
        for sid, b in plan["bisects"].items():
            st = get_step(plan, sid)
            L += [f"- **{sid}** ({plural(sum(1 for _ in st['upgrades']), 'upgrade')}) failed. Bisect, in "
                  f"{plural(b['trials'], 'trial')} ({dur(b['secs'])}): {b['result']}"]
            for t in b["log"]:
                L.append(f"  - {t['trial']}: {', '.join(up_text(ups[u]) for u in t['upgrades'])} → {t['verdict']}"
                         + (f" ({', '.join(t['broken'][:2])})" if t["broken"] else ""))
        for s in open_steps:
            if s["status"] in ("fail", "install-fail"):
                L.append(f"- **{s['id']}** {s['title']}: {s['status']}, {(s.get('last') or {}).get('detail', '')}")
        L.append("")
    majors = [s for s in steps if s["group"] == "major" and s["status"] != "split"]
    if majors:
        L += ["## Majors", ""]
        for s in majors:
            ms = [m for m in migr if m.get("patch", "").startswith(s["id"] + ".")]
            if s["status"] == "accepted" and s.get("migration_patch"):
                m = next((x for x in ms if x["patch"] == s["migration_patch"]), {})
                summ = (m.get("reply") or {}).get("summary") if isinstance(m.get("reply"), dict) else None
                L.append(f"- **{s['title']}**: passed after a code change (attempt {m.get('attempt')}), in "
                         f"{', '.join(m.get('files', []))}." + (f" The migrator's summary: {summ}" if summ else "")
                         + (f" **Test files changed: {', '.join(m['tests_changed'])}.**" if m.get("tests_changed") else ""))
            elif s["status"] == "accepted":
                L.append(f"- **{s['title']}**: passed with no code changes.")
            else:
                L.append(f"- **{s['title']}**: {s['status']}" + (f" ({s.get('reason')})" if s.get("reason") else "")
                         + (f"; {len(ms)} migration attempt{'s' if len(ms) != 1 else ''}" if ms else "") + ".")
        L.append("")
    if notes:
        L += ["## Behaviour changes to check", "",
              "From reading the release notes of majors that passed. Advice unless marked reproduced.", ""]
        for sid, nb in notes.items():
            for c in nb["changes"]:
                tag = {"shows": "reproduced in a fresh copy", "behaves": "a reproduction was given; it didn't show",
                       "inconclusive": "a reproduction was given; inconclusive", None: "unchecked"}[c.get("checked")]
                L.append(f"- **{get_step(plan, sid)['title']}**: {c['change']}"
                         + (f" ({c['affects']})" if c.get("affects") else "") + f". *{tag}.*")
        L.append("")
    audits = od.get("audited", [])
    if audits and not advs:
        L += ["## Security advisories", "",
              "None found by " + ", ".join(sorted({a["by"] for a in audits})) + ".", ""]
    if advs:
        L += ["## Security advisories", ""]
        if audits:
            L.append("Looked for by " + ", ".join(sorted({a["by"] for a in audits})) + ".")
        how = "the audit rerun on the branch" if land and land.get("audited") else "the fixed versions the audit gave"
        L.append(f"Fixed ({len(fixed)}), by {how}: " + (", ".join(f"{x['package']} {x['id']}" for x in fixed) or "none") + ".")
        L.append(f"Left ({len(left)}): " + (", ".join(f"{x['package']} {x['id']}" + (f" (fixed in {x['fixed_in']})"
                                                                                    if x.get("fixed_in") else "")
                                                       for x in left) or "none") + ".")
        L.append("")
    rt_steps = [s for s in steps if s["group"] in ("runtime", "action", "image")]
    if rt_steps:
        L += ["## Runtimes, CI actions and images", ""]
        for s in rt_steps:
            un = any(ups[u].get("unchecked_by_tests") for u in s["upgrades"])
            L.append(f"- **{s['title']}**: {s['status']}" + (f" ({s.get('reason')})" if s.get("reason") else "")
                     + (". CI runs it; the sandbox's tests don't, so watch the first CI run" if un and s["status"] == "accepted" else "") + ".")
        L.append("")
    if plan["left_out"]:
        L += ["## Listed, not tried", ""]
        L += [f"- {cell(up_text(ups[x['upgrade']]))}: {x['why']}" for x in plan["left_out"] if x["upgrade"] in ups] + [""]
    L += ["## Final check", ""]
    if final:
        L.append(f"`{land['branch']}`, in a fresh sandbox: {final['verdict']}. {final['detail'][0].upper()}"
                 f"{final['detail'][1:]}" + (f"; {final['kept']} of the {base_pass} baseline tests passed in every run."
                                            if final.get("kept") is not None and base_pass else "."))
        if final["broken"]:
            L.append("Broken: " + ", ".join(final["broken"][:10]))
    else:
        L.append("Not landed, so there's no branch to check.")
    L.append("")
    un = list(od.get("unchecked", []))
    for u in (base or {}).get("tests", []):
        if u["judge"] == "none":
            un.append(f"`{show_cmd(u['cmd'])}` fails at the baseline and prints no per-test results, so it checked nothing")
        if u["flaky"]:
            un.append(f"{plural(len(u['flaky']), 'flaky test')} in `{show_cmd(u['cmd'])}` ignored")
    if not cfg["network"]:
        un.append("the network was off in the sandboxes")
    if notes and any(c.get("checked") is None for nb in notes.values() for c in nb["changes"]):
        un.append("behaviour changes without a reproduction are advice, unchecked")
    L += ["## Not checked", ""] + ([f"- {x}" for x in un] or ["Nothing left unchecked."]) + [""]
    if decisions:
        L += ["## Decisions to check", ""] + [f"- {d['text']}" for d in decisions] + [""]
    if waiting:
        L += ["## Waiting for your OK", ""] + [f"- {w['text']}: `{w['command']}`" for w in waiting] + [""]
    L += ["## Run details", "", f"Run folder: `{ws}`. Trials: {trials_n}"
          + (f" ({bisect_trials} in bisects)" if bisect_trials else "") + f". Migration attempts: {len(migr)}.", ""]
    (ws / "report.md").write_text("\n".join(L), encoding="utf-8")
    numbers = {"upgrades_found": len(od.get("upgrades", [])), "upgrades_planned": len(acc_ids) + len(held_ids) + len(open_ids),
               "upgrades_accepted": len(acc_ids), "upgrades_landed": len(landed_ids), "upgrades_held": len(held_ids),
               "upgrades_not_done": len(open_ids), "steps_accepted": len(accepted),
               "commits": len((land or {}).get("commits", [])), "trials": trials_n, "bisect_trials": bisect_trials,
               "migration_attempts": len(migr), "baseline_tests_passing": base_pass,
               "final_tests_passing": (final or {}).get("kept"), "advisories_found": len(advs),
               "advisories_fixed": len(fixed), "advisories_left": len(left)}
    summary = {"skill": "killer-upgrade", "status": status, "headline": headline, "report": str(ws / "report.md"),
               "branch": (land or {}).get("branch"),
               "commits": [f"{c['sha'][:10]} {c['subject']}" for c in (land or {}).get("commits", [])],
               "decisions": [d["text"] for d in decisions],
               "waiting": [f"{w['text']}: {w['command']}" for w in waiting], "numbers": numbers}
    kit.save_json(ws / "summary.json", summary)
    print(f"Wrote {ws / 'report.md'} and summary.json.")
    print(f"killer-upgrade: {status} · {headline} · {ws / 'report.md'}")


def cmd_clean(a) -> None:
    ws, cfg = load(a)
    n = kit.clean_sandboxes(ws, cfg)
    kit.rmtree(Path(cfg["sandboxes_home"]) / cfg["run_id"] / "_cache")
    holder = Path(cfg["sandboxes_home"]) / cfg["run_id"]
    if holder.is_dir():
        for left in holder.iterdir():
            kit.rmtree(left)
        try:
            holder.rmdir()
        except OSError:
            pass
    if cfg.get("git"):
        kit.git(Path(cfg["repo"]), "worktree", "prune", check=False)
    print(f"Deleted {plural(n, 'sandbox', 'sandboxes')} and the run's package cache.")


# ---------------------------------------------------------------- CLI

MARKERS = ("--install", "--build", "--tests")


def split_sections(argv: list[str]) -> tuple[list[str], dict]:
    """baseline's --install -- <cmd> --build -- <cmd> --tests -- <cmd>: each may repeat."""
    secs = {"install": [], "build": [], "tests": []}
    i = next((k for k in range(len(argv) - 1) if argv[k] in MARKERS and argv[k + 1] == "--"), len(argv))
    head, rest = argv[:i], argv[i:]
    for t in head:
        if t in MARKERS:
            kit.fail(f"put -- after {t}, like: {t} -- <command>")
    cur = None
    j = 0
    while j < len(rest):
        t = rest[j]
        if t in MARKERS and j + 1 < len(rest) and rest[j + 1] == "--":
            cur = []
            secs[t[2:]].append(cur)
            j += 2
            continue
        cur.append(t)
        j += 1
    for k, cmds in secs.items():
        if any(not c for c in cmds):
            kit.fail(f"--{k} -- needs a command after it")
    return head, secs


def main(argv=None, doc: str | None = None) -> None:
    kit.utf8_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    sections = None
    if argv[:1] == ["baseline"] and "-h" not in argv[:4] and "--help" not in argv[:4]:
        argv, sections = split_sections(argv)
    p = argparse.ArgumentParser(prog=SCRIPT, description=doc or __doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name, func, help_, ws=True):
        sp = sub.add_parser(name, help=help_, description=help_)
        if ws:
            sp.add_argument("--workspace", "-w", required=True, help="the run folder")
        sp.set_defaults(func=func)
        return sp

    s = cmd("init", cmd_init, "Find the ecosystems, runtimes, actions and images, and make the run folder.", ws=False)
    s.add_argument("--repo", default=".", help="the project folder (default: here)")
    s.add_argument("--target", default="", help="one line on what the project is")
    s.add_argument("--slug", help="short name for the run folder")
    s.add_argument("--scope", default="all", help="all (default), security, minor, or package:<name>[,<name>]")
    s.add_argument("--majors", choices=["try", "list", "hold"], default="try",
                   help="try majors and migrate the code if they break (default), list them, or hold any that break")
    s.add_argument("--also", nargs="*", choices=["runtimes", "actions"], help="upgrade runtime versions, CI actions")
    s.add_argument("--after", choices=["commit", "ask", "report"], default="commit",
                   help="what happens once upgrades are proven (default commit: land them on a new branch)")
    s.add_argument("--no-network", action="store_true", help="cut the sandboxes off the network (installs need it)")
    s.add_argument("--include", nargs="*", help="ignored paths every sandbox needs")
    s.add_argument("--env", nargs="*", help="NAME=VALUE set in every sandbox")
    s.add_argument("--workspace", "-w", help="use this run folder instead of the default")
    s.add_argument("--force", action="store_true", help="start over in an existing run folder")

    s = cmd("custom", cmd_custom, "Describe an ecosystem upgrade.py doesn't know.")
    s.add_argument("--name", required=True, help="a short name for it")
    s.add_argument("--dir", default=".", help="its folder in the project (default the top)")
    s.add_argument("--manifest", required=True, help="the file that lists the dependencies, in that folder")
    s.add_argument("--lockfile", help="the lockfile, if there is one")
    s.add_argument("--outdated", required=True,
                   help="command that prints JSON [{name, current, latest, wanted?, dev?, advisories?}]")
    s.add_argument("--apply", required=True, help="command that applies one upgrade, with {name} and {version}")

    s = cmd("baseline", cmd_baseline, "Install, build and test in a fresh sandbox, and record what passes. "
            "Commands go last: --install -- <cmd> --build -- <cmd> --tests -- <cmd>, each one repeatable.")
    s.add_argument("--venv", nargs="?", const="", default=None,
                   help="make a virtual environment in each sandbox, with this Python (default the one running this)")
    s.add_argument("--runs", type=int, default=3, help="test runs, to catch flaky tests (default 3)")
    s.add_argument("--timeout", type=float, default=1800, help="seconds per command (default 1800)")
    s.add_argument("--junit", help="JUnit XML files the tests write (a glob in the project), read for per-test results")
    s.add_argument("--force", action="store_true", help="record a new baseline even though steps were accepted")

    cmd("outdated", cmd_outdated, "Ask each ecosystem's tool what's out of date, and classify the upgrades.")

    s = cmd("plan", cmd_plan, "Group the upgrades into steps in plan.json.")
    s.add_argument("--force", action="store_true", help="start the plan again, dropping what was accepted")

    s = cmd("add", cmd_add, "Add a step by hand: edits, and or a command after --.")
    s.add_argument("--title", required=True, help="the step's title, which becomes its commit subject")
    s.add_argument("--group", choices=["deps", "dev", "major", "runtime", "action", "image", "manual"], default="manual")
    s.add_argument("--edit", nargs=3, action="append", metavar=("FILE", "OLD", "NEW"), help="replace OLD with NEW in FILE")
    s.add_argument("--name", help="the package or runtime (default the title)")
    s.add_argument("--from", dest="from_", help="its version now")
    s.add_argument("--to", help="its new version")
    s.add_argument("--probe", help="for a runtime: a command whose output must show --to, like 'node --version'")
    s.add_argument("cmd", nargs=argparse.REMAINDER, help="-- then a command that applies it")

    s = cmd("hold", cmd_hold, "Keep a package (or a step, by its id) at its version.")
    s.add_argument("package", help="the package name, or a step id like S6")
    s.add_argument("--reason", required=True)
    s.add_argument("--by", default="lead", help=argparse.SUPPRESS)

    s = cmd("try", cmd_try, "Apply a step on top of the accepted ones in a fresh sandbox, then install, build, test.")
    s.add_argument("step")
    s.add_argument("--again", action="store_true", help="try an accepted step again")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")

    s = cmd("bisect", cmd_bisect, "Find which upgrades in a failing batch break it, and split the batch.")
    s.add_argument("step")
    s.add_argument("--max-trials", type=int, default=24, help="stop after this many trials (default 24)")

    s = cmd("sandbox", cmd_sandbox, "Make a sandbox for an agent, with a step applied and installed.")
    s.add_argument("name", help="a name for it, like migrate-S3")
    s.add_argument("--step", help="the step to apply on top of the accepted ones")

    s = cmd("migrate", cmd_migrate, "Check a migrator's code changes for a step with a fresh try.")
    s.add_argument("step")
    s.add_argument("--sandbox", required=True, help="the migrator's sandbox")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")

    s = cmd("notes", cmd_notes, "Record a changelog reader's reply for an accepted step.")
    s.add_argument("step")
    s.add_argument("reply", nargs="?", help="a reply saved to a file (or use --sandbox)")
    s.add_argument("--sandbox", help="read reply.json from this sandbox, then delete it")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")

    cmd("status", cmd_status, "Show every step, whether the project changed, and what to do next.")

    s = cmd("land", cmd_land, "Commit each accepted step on a new local branch, then check the branch.")
    s.add_argument("--branch", help="commit here instead of killer-upgrade/<date> (when it's the current branch, "
                                    "commit on it)")
    s.add_argument("--again", action="store_true", help="land once more, after an earlier land")

    s = cmd("decide", cmd_decide, "Record a call the user should check.")
    s.add_argument("text")
    s = cmd("wait", cmd_wait, "Record an outward action prepared but not done.")
    s.add_argument("text")
    s.add_argument("--command", required=True, help="the exact command that would do it")

    cmd("report", cmd_report, "Write report.md and summary.json.")
    cmd("clean", cmd_clean, "Delete the run's sandboxes and package cache.")

    a = p.parse_args(argv)
    if sections is not None:
        a.sections = sections
    a.func(a)

