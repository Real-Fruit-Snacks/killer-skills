#!/usr/bin/env python3
"""Bookkeeping for a killer-tests run: mutation testing, and the checks that make a new test count.

Commands:
  init        create the run folder; record the scope and the test command (after --)
  baseline    run the suite 3 times in a fresh copy (it must pass every time) and time it
  generate    make the mutants: one small fault on one line each, never inside a string or comment
  run         run the suite against each mutant in a pool of sandboxes (--resume continues)
  score       the mutation score, per file, and how many survivors are left
  show        mutants as diffs (--patch for a patch git apply takes)
  slices      group the survivors into slices, one per test writer, and into batches
  sandbox     a copy of the project for one agent, with the accepted tests in it and a
              slice's mutants (as diffs and a helper that puts them in) in its scratch folder
  collect     take a writer's tests from its sandbox and check them (check-test)
  check-test  the core check: the new tests pass 3 times on the real code, fail twice on
              each mutant they kill, and that mutant passes without them
  accept      add a checked patch to the accepted list; later checks build on it
  drop        take an accepted patch out; the ones accepted after it need checking again
  equivalent  record an equivalence judge's verdict on a survivor
  review      record the test reviewer's findings
  combined    the accepted tests as one diff
  land        commit the accepted tests on a new local branch, then run the suite there
  rescore     run the same sample against the final tests, for a clean before and after
  decide      record a call the user should check
  wait        record an outward action prepared but not done
  status      compare the real project's git status with the one at the start (exit 1 if changed)
  report      write report.md and summary.json
  clean       delete this run's sandboxes

Agents write their reply to reply.json in their sandbox's scratch folder; collect,
equivalent and review read it from there with --sandbox, then delete the sandbox.
Runs live in ~/.claude/killer-tests/<project>/<date>-<slug>/ and sandboxes in the temp
folder; KILLER_TESTS_HOME and KILLER_TESTS_SANDBOXES move them.
Exit codes: 0 fine, 1 a check failed (the verdict), 2 usage or setup error.
Standard library only. Run `mutants.py <command> --help` for options.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import importlib.util
import io
import json
import os
import queue
import random
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import threading
import time
import tokenize
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("killer_kit", HERE / "kit.py")
kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit)

SKILL = "tests"
OPERATORS = ("comparison", "boolean", "arithmetic", "constant", "return", "statement", "condition")
VIABLE = ("killed", "survived", "timeout")
MASK_S, MASK_C = "\x01", "\x02"      # string and comment characters, once masked
NO_BYTECODE = {"PYTHONDONTWRITEBYTECODE": "1"}   # a stale .pyc would hide a mutant

fail = kit.fail


# ---------------------------------------------------------------- small helpers

def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def num(mid: str) -> int:
    m = re.match(r"[A-Za-z]+(\d+)$", str(mid))
    return int(m.group(1)) if m else 10 ** 9


def norm_ids(values) -> list[str]:
    out = []
    for v in kit.as_list(values):
        for tok in re.findall(r"[MmPpSs]\d+", str(v)):
            tok = tok.upper()
            if tok not in out:
                out.append(tok)
    return out


def default_jobs() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def fresh_state() -> dict:
    return {"patches": {}, "patch_next": 1, "accepted": [], "kills": {}, "claims": {}, "judges": {},
            "slices": {}, "slice_next": 1, "boxes": {}, "reviews": [], "dropped": [], "flaky": [],
            "unstable": {}, "decisions": [], "waiting": [], "land": None}


def load_state(ws: Path) -> dict:
    st = kit.load_json(ws / "state.json", None) or {}
    base = fresh_state()
    base.update(st)
    return base


def change_state(ws: Path, fn):
    with kit.locked(ws):
        st = load_state(ws)
        out = fn(st)
        kit.save_json(ws / "state.json", st)
    return out


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":   # os.kill(pid, 0) would end the process on Windows
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@contextlib.contextmanager
def busy(ws: Path, what: str):
    """Only one long job (run, check-test, rescore) at a time in a run folder."""
    lock = ws / ".busy"
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {what}".encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                pid, _, other = lock.read_text(encoding="utf-8").partition(" ")
                alive = pid_alive(int(pid))
            except (OSError, ValueError):
                alive = False
            if alive:
                fail(f"another mutants.py ({other or 'job'}, pid {pid}) is working in this run; wait for it to finish")
            lock.unlink(missing_ok=True)
    else:
        fail(f"couldn't take {lock}; delete it if nothing is running")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def split_lines(text: str) -> list[str]:
    """Lines with their endings, split on \\n only (as Python's tokenizer counts them)."""
    parts = text.split("\n")
    return [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def bare(line: str) -> str:
    return line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", "surrogateescape")


def has_surrogates(s: str) -> bool:
    return any("\udc80" <= ch <= "\udcff" for ch in s)


def globmatch(rel: str, pattern: str) -> bool:
    p = pattern.replace("\\", "/").rstrip("/")
    name = rel.rsplit("/", 1)[-1]
    return (fnmatch.fnmatchcase(rel, p) or rel.startswith(p + "/") or fnmatch.fnmatchcase(name, p)
            or fnmatch.fnmatchcase(rel.lower(), p.lower()))


# ---------------------------------------------------------------- what counts as source, tests, or neither

LANGS = {".py": "python", ".pyw": "python",
         ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js",
         ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".cts": "ts",
         ".go": "go", ".rs": "rust", ".java": "java", ".cs": "csharp",
         ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
         ".kt": "kotlin", ".swift": "swift", ".scala": "scala", ".dart": "dart",
         ".sh": "shell", ".bash": "shell", ".ps1": "powershell", ".psm1": "powershell"}
CLIKE = {"js", "ts", "go", "rust", "java", "csharp", "c", "cpp", "kotlin", "swift", "scala", "dart"}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testing", "testdata", "test_data", "fixtures",
             "__mocks__", "__snapshots__", "e2e", "cypress"}
TEST_NAMES = ["test_*.py", "*_test.py", "tests.py", "conftest.py", "*_test.go", "*.test.*", "*.spec.*", "*_spec.*",
              "*test.java", "*tests.java", "*test.kt", "*tests.kt", "*test.cs", "*tests.cs", "*tests.swift",
              "*test.swift", "test_*.sh", "*_test.sh", "*.bats", "*.tests.ps1"]
VENDOR_DIRS = {"vendor", "vendored", "_vendor", "third_party", "third-party", "thirdparty", "node_modules",
               "bower_components", ".venv", "venv", "site-packages", "dist", "build", "target", "coverage"}
DOC_DIRS = {"docs", "doc", "examples", "example", "samples", "sample", "benchmarks", "benchmark", "bench"}
MIGRATION_DIRS = {"migrations", "migration", "alembic"}
GENERATED_NAMES = ["*_pb2.py", "*_pb2_grpc.py", "*.pb.go", "*.pb.cc", "*.pb.h", "*.g.dart", "*.generated.*",
                   "*.min.js", "*.bundle.js", "*-min.js"]
CONSTANT_NAMES = ["constants.*", "constant.*", "consts.*", "const.*", "_version.py", "version.py", "__version__.py"]
CONFIG_NAMES = ["setup.py", "noxfile.py", "fabfile.py", "manage.py", "settings.py", "wsgi.py", "asgi.py",
                "*.config.js", "*.config.cjs", "*.config.mjs", "*.config.ts", ".eslintrc.*", "eslint.config.*",
                "gulpfile.*", "gruntfile.*", "webpack.*.js", "build.rs"]
GENERATED_MARK = re.compile(r"(?i)code generated|do not edit|@generated|auto-?generated|"
                            r"this file (?:is|was) (?:automatically )?generated")
JUNK = [".coverage", ".coverage.*", "coverage.xml", "htmlcov/*", "coverage/*", ".nyc_output/*", "*.log",
        ".hypothesis/*", "junit*.xml", "test-results/*", "node_modules/.cache/*", "*.orig", "*.rej"]


def _named(name: str, pats) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, p.lower()) for p in pats)


def is_test(rel: str, cfg: dict) -> bool:
    if any(globmatch(rel, g) for g in cfg.get("tests") or []):
        return True
    for seg in rel.split("/")[:-1]:
        s = seg.lower()
        if s in TEST_DIRS or re.search(r"\.(unit|integration)?tests?$", s):
            return True
    return _named(rel.rsplit("/", 1)[-1], TEST_NAMES)


def classify(rel: str, cfg: dict, head: str = "", source=None) -> tuple[str, str]:
    """('source', language) | ('test', why) | ('skip', why)."""
    low = rel.lower()
    name = low.rsplit("/", 1)[-1]
    segs = {s.lower() for s in rel.split("/")[:-1]}
    if is_test(rel, cfg):
        return "test", "test files"
    if low.endswith(".d.ts"):
        return "skip", "type declarations"
    lang = LANGS.get(os.path.splitext(low)[1])
    if lang is None:
        return "skip", "not a language it mutates (docs, config, data)"
    globs = source if source is not None else (cfg.get("source") or [])
    explicit = any(globmatch(rel, g) for g in globs)
    if globs and not explicit:
        return "skip", "outside --source"
    if segs & VENDOR_DIRS or _named(name, ["*.min.js", "*.bundle.js", "*-min.js"]):
        return "skip", "vendored or built"
    if not explicit:
        if segs & DOC_DIRS:
            return "skip", "docs, examples or benchmarks"
        if segs & MIGRATION_DIRS:
            return "skip", "migrations"
        if _named(name, GENERATED_NAMES) or GENERATED_MARK.search(head):
            return "skip", "generated"
        if _named(name, CONSTANT_NAMES):
            return "skip", "constants files"
        if _named(name, CONFIG_NAMES):
            return "skip", "build and config scripts"
    return "source", lang


def changed_lines(top: Path, base: str, rel: str) -> set[int] | None:
    """Lines added or changed in rel since base (None: the whole file is new)."""
    if not kit.git_ok(top, "cat-file", "-e", f"{base}:{rel}"):
        return None
    out = kit.git(top, "diff", "-U0", "--no-color", "--no-ext-diff", base, "--", rel, check=False)
    lines: set[int] = set()
    for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", out, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        lines.update(range(start, start + count))
    return lines


# ---------------------------------------------------------------- mutants in Python: ast for meaning, tokenize for positions

PY_CMP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "=="}
PY_ARITH = {"+": "-", "-": "+", "*": "/", "/": "*"}
PY_AUG = {"+=": "-=", "-=": "+="}
PY_LOGGING = re.compile(r"^(print|pprint|pprint\.pprint|warnings\.warn|(?:.+\.)?(?:_?log|_?logger|logging|LOG|LOGGER)"
                        r"\.(?:debug|info|warning|warn|error|exception|critical|log|trace))$")
ENV_PROBE = re.compile(r"^(sys\.(version_info|platform|implementation|byteorder|maxsize)|os\.name|__name__|platform\.)")
MAIN_GUARD = re.compile(r"__name__ == ['\"]__main__['\"]|['\"]__main__['\"] == __name__|(typing\.|t\.)?TYPE_CHECKING")


def _ccol(lines: list[str], row: int, bcol: int) -> int:
    """ast counts columns in UTF-8 bytes; tokenize and str slicing count characters."""
    s = lines[row - 1]
    if s.isascii():
        return bcol
    return len(s.encode("utf-8", "surrogateescape")[:bcol].decode("utf-8", "surrogateescape"))


def _call_name(func) -> str:
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return ""
    parts.append(func.id)
    return ".".join(reversed(parts))


def _src(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:   # noqa: BLE001 - only used to recognise a few idioms
        return ""


def _is_text(node) -> bool:
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)))


def _py_funcs(tree, nlines: int) -> list[str]:
    names = [""] * (nlines + 2)

    def visit(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                nm = prefix + ch.name
                for r in range(ch.lineno, min(nlines, ch.end_lineno or ch.lineno) + 1):
                    names[r] = nm
                visit(ch, nm + ".")
            else:
                visit(ch, prefix)
    visit(tree, "")
    return names


def py_compiles(text: str, rel: str) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            compile(text, rel, "exec", dont_inherit=True)
        return True
    except (SyntaxError, ValueError, OverflowError, RecursionError, MemoryError):
        return False


def py_mutants(text: str, rel: str = "<file>") -> tuple[list[dict], str | None, int, list[str]]:
    """(mutants, problem, how many were dropped because they didn't compile, function name per line)."""
    if re.search(r"\r(?!\n)", text):
        return [], "uses old Mac line endings", 0, []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [], "doesn't parse as Python 3", 0, []
    lines = split_lines(text)
    toks: dict[int, list] = {}
    try:
        for t in tokenize.generate_tokens(io.StringIO(text).readline):
            if t.type in (tokenize.OP, tokenize.NAME, tokenize.NUMBER):
                toks.setdefault(t.start[0], []).append(t)
    except (tokenize.TokenError, SyntaxError):
        return [], "doesn't tokenize", 0, []
    parents = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parents[ch] = node
    skip_lines: set[int] = set()
    fspans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):   # f-strings: leave everything inside them alone
            fspans.append(((node.lineno, _ccol(lines, node.lineno, node.col_offset)),
                           (node.end_lineno, _ccol(lines, node.end_lineno, node.end_col_offset))))
        if isinstance(node, ast.If) and MAIN_GUARD.fullmatch(_src(node.test)):
            skip_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    def pos(node, end=False):
        row = node.end_lineno if end else node.lineno
        return row, _ccol(lines, row, node.end_col_offset if end else node.col_offset)

    found = []

    def add(row, c0, c1, repl, op, detail):
        if row in skip_lines or any(a <= (row, c0) < b for a, b in fspans):
            return
        found.append((row, c0, c1, repl, op, detail))

    def tok_between(a, b, strings):
        for row in range(a[0], b[0] + 1):
            for t in toks.get(row, ()):
                if t.start >= a and t.end <= b and t.string in strings:
                    return t
        return None

    def tok_at(p, strings):
        return next((t for t in toks.get(p[0], ()) if t.start == p and t.string in strings), None)

    def swap(t, table, op):
        if t:
            add(t.start[0], t.start[1], t.end[1], table[t.string], op, f"{t.string} → {table[t.string]}")

    def module_constant(node) -> bool:
        p = parents.get(node)
        while isinstance(p, (ast.UnaryOp, ast.Tuple, ast.List)):
            p = parents.get(p)
        if not isinstance(p, (ast.Assign, ast.AnnAssign)) or not isinstance(parents.get(p), ast.Module):
            return False
        targets = p.targets if isinstance(p, ast.Assign) else [p.target]
        return all(isinstance(t, ast.Name) and re.fullmatch(r"_?[A-Z][A-Z0-9_]*", t.id) for t in targets)

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            if any(ENV_PROBE.match(_src(x)) for x in [node.left, *node.comparators]):
                continue   # sys.version_info >= (3, 8) and friends: equivalent on this machine
            prev = node.left
            for nxt in node.comparators:
                swap(tok_between(pos(prev, True), pos(nxt), PY_CMP), PY_CMP, "comparison")
                prev = nxt
        elif isinstance(node, ast.BoolOp):
            for a, b in zip(node.values, node.values[1:]):
                swap(tok_between(pos(a, True), pos(b), {"and", "or"}), {"and": "or", "or": "and"}, "boolean")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            p, q = pos(node), pos(node.operand)
            if tok_at(p, {"not"}) and q[0] == p[0]:
                add(p[0], p[1], q[1], "", "boolean", "not x → x")
        elif isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult, ast.Div):
            if not (_is_text(node.left) or _is_text(node.right)):   # "a" + b would just raise
                swap(tok_between(pos(node.left, True), pos(node.right), PY_ARITH), PY_ARITH, "arithmetic")
        elif isinstance(node, ast.AugAssign) and type(node.op) in (ast.Add, ast.Sub):
            swap(tok_between(pos(node.target, True), pos(node.value), PY_AUG), PY_AUG, "arithmetic")
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            swap(tok_at(pos(node), {"True", "False"}), {"True": "False", "False": "True"}, "boolean")
        elif isinstance(node, ast.Constant) and type(node.value) is int and not module_constant(node):
            p = pos(node)
            t = next((t for t in toks.get(p[0], ()) if t.start == p and t.type == tokenize.NUMBER), None)
            if t and re.fullmatch(r"(0|[1-9][0-9_]*)", t.string):   # decimal only; hex and octal are masks
                n = int(t.string.replace("_", ""))
                new = "1" if n == 0 else "0" if n == 1 else str(n + 1)
                add(p[0], t.start[1], t.end[1], new, "constant", f"{t.string} → {new}")
        elif isinstance(node, ast.Return) and node.value is not None and node.lineno == node.end_lineno:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                a, b = pos(node.value), pos(node.value, True)
                add(a[0], a[1], b[1], "None", "return", "return x → return None")
        elif isinstance(node, ast.Expr) and node.lineno == node.end_lineno:
            call = node.value.value if isinstance(node.value, ast.Await) else node.value
            if isinstance(call, ast.Call) and not PY_LOGGING.match(_call_name(call.func)):
                a, b = pos(node), pos(node, True)
                line = bare(lines[a[0] - 1])
                rest = line[b[1]:].strip()
                if not line[:a[1]].strip() and (not rest or rest.startswith("#")):
                    add(a[0], a[1], b[1], "pass", "statement", "call removed")
        elif isinstance(node, (ast.If, ast.While)):
            test = node.test
            if test.lineno == test.end_lineno and not (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)):
                a, b = pos(test), pos(test, True)
                seg = bare(lines[a[0] - 1])[a[1]:b[1]]
                kw = "while" if isinstance(node, ast.While) else "if"
                add(a[0], a[1], b[1], f"not ({seg})", "condition", f"{kw} x → {kw} not (x)")

    out, dropped, seen = [], 0, set()
    for row, c0, c1, repl, op, detail in sorted(found):
        line = bare(lines[row - 1])
        new = line[:c0] + repl + line[c1:]
        if new == line or (row, new) in seen or has_surrogates(line):
            continue
        seen.add((row, new))
        ending = lines[row - 1][len(line):]
        if not py_compiles("".join(lines[:row - 1]) + new + ending + "".join(lines[row:]), rel):
            dropped += 1
            continue
        out.append({"line": row, "col": c0, "op": op, "detail": detail, "original": line, "mutated": new})
    return out, None, dropped, _py_funcs(tree, len(lines))


# ---------------------------------------------------------------- other languages: mask strings and comments, then match

def _blank(chars: list, a: int, b: int, ch: str) -> None:
    for k in range(a, min(b, len(chars))):
        if chars[k] not in "\r\n":
            chars[k] = ch


def _qend(s: str, i: int, q: str, esc: str | None = "\\", multiline: bool = False) -> int:
    """Where a literal opened by s[i] == q ends (exclusive); an unclosed one ends at the line's end."""
    n, j = len(s), i + 1
    while j < n:
        c = s[j]
        if esc and c == esc:
            j += 2
            continue
        if c == q:
            return j + 1
        if c == "\n" and not multiline:
            return j
        j += 1
    return n


def _block_end(s: str, i: int, nested: bool) -> int:
    if not nested:
        k = s.find("*/", i + 2)
        return len(s) if k < 0 else k + 2
    depth, j = 1, i + 2
    while j < len(s):
        if s.startswith("/*", j):
            depth, j = depth + 1, j + 2
        elif s.startswith("*/", j):
            depth, j = depth - 1, j + 2
            if depth == 0:
                return j
        else:
            j += 1
    return len(s)


def _template_end(s: str, i: int) -> int:
    n, j = len(s), i + 1
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
        elif c == "`":
            return j + 1
        elif s.startswith("${", j):
            j = _js_code_end(s, j + 2)
        else:
            j += 1
    return n


def _js_code_end(s: str, j: int) -> int:
    """The end of the code inside a template's ${ }, after its closing brace."""
    depth, n = 1, len(s)
    while j < n:
        c = s[j]
        if c in "\"'":
            j = _qend(s, j, c)
            continue
        if c == "`":
            j = _template_end(s, j)
            continue
        if s.startswith("//", j):
            k = s.find("\n", j)
            j = n if k < 0 else k
            continue
        if s.startswith("/*", j):
            j = _block_end(s, j, False)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def _regex_end(s: str, i: int) -> int | None:
    n, j, in_class = len(s), i + 1, False
    while j < n:
        c = s[j]
        if c == "\n":
            return None
        if c == "\\":
            j += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < n and s[j].isalpha():
                j += 1
            return j
        j += 1
    return None


JS_BEFORE_REGEX = set("(,=:[!&|?{};+-*%<>~^}")
JS_KEYWORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw", "yield",
               "await", "instanceof"}
RUST_RAW = re.compile(r'b?r(#*)"')
RUST_CHAR = re.compile(r"'(?:\\(?:u\{[0-9a-fA-F]+\}|x[0-9a-fA-F]{2}|.)|[^\\'\n])'")
CPP_RAW = re.compile(r'(?:u8|[uUL])?R"([^()\\\s]{0,16})\(')
CS_VERBATIM = re.compile(r'(?:\$@|@\$|@)"')


def mask_clike(s: str, lang: str) -> str:
    chars, n, i = list(s), len(s), 0
    nested = lang in ("rust", "kotlin", "swift", "scala")
    js = lang in ("js", "ts")
    last = ""   # the last code token: a word, or one character
    if s.startswith("#!"):
        k = s.find("\n")
        i = n if k < 0 else k
        _blank(chars, 0, i, MASK_C)
    while i < n:
        c = s[i]
        if c == "/" and s.startswith("//", i):
            k = s.find("\n", i)
            k = n if k < 0 else k
            _blank(chars, i, k, MASK_C)
            i = k
            continue
        if c == "/" and s.startswith("/*", i):
            k = _block_end(s, i, nested)
            _blank(chars, i, k, MASK_C)
            i = k
            continue
        k = None
        if js and c == "/" and (last == "" or last in JS_BEFORE_REGEX or last in JS_KEYWORDS):
            k = _regex_end(s, i)
        elif c == "`" and js:
            k = _template_end(s, i)
        elif c == "`" and lang == "go":
            e = s.find("`", i + 1)
            k = n if e < 0 else e + 1
        elif s.startswith('"""', i) and lang in ("java", "kotlin", "swift", "scala", "csharp", "dart"):
            e = s.find('"""', i + 3)
            k = n if e < 0 else e + 3
        elif lang == "rust" and c in "br" and (m := RUST_RAW.match(s, i)):
            e = s.find('"' + m.group(1), m.end())
            k = n if e < 0 else e + 1 + len(m.group(1))
        elif lang == "rust" and c == "b" and s[i + 1:i + 2] == '"':
            k = _qend(s, i + 1, '"', multiline=True)
        elif lang == "rust" and c == "b" and s[i + 1:i + 2] == "'" and (m := RUST_CHAR.match(s, i + 1)):
            k = m.end()
        elif lang == "cpp" and c in "uULR" and (m := CPP_RAW.match(s, i)):
            e = s.find(")" + m.group(1) + '"', m.end())
            k = n if e < 0 else e + len(m.group(1)) + 2
        elif lang == "csharp" and c in "@$" and (m := CS_VERBATIM.match(s, i)):
            j = m.end()
            while j < n:
                if s[j] == '"':
                    if s[j + 1:j + 2] == '"':
                        j += 2
                        continue
                    break
                j += 1
            k = min(n, j + 1)
        elif c == '"':
            k = _qend(s, i, '"', multiline=lang == "rust")
        elif c == "'":
            if lang == "rust":
                m = RUST_CHAR.match(s, i)
                if not m:          # a lifetime or a loop label
                    last, i = "'", i + 1
                    continue
                k = m.end()
            else:
                k = _qend(s, i, "'")
        if k:
            _blank(chars, i, k, MASK_S)
            i, last = k, MASK_S
            continue
        if c.isspace():
            i += 1
            continue
        if c.isalnum() or c in "_$":
            k = i
            while k < n and (s[k].isalnum() or s[k] in "_$"):
                k += 1
            last, i = s[i:k], k
            continue
        last, i = c, i + 1
    return "".join(chars)


HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)\\?([A-Za-z_][A-Za-z0-9_]*)\2")


def _sh_dq_end(s: str, i: int) -> int:
    n, j = len(s), i + 1
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
        elif c == '"':
            return j + 1
        elif s.startswith("$(", j):
            j = _sh_paren_end(s, j + 2)
        elif c == "`":
            k = s.find("`", j + 1)
            j = n if k < 0 else k + 1
        else:
            j += 1
    return n


def _sh_paren_end(s: str, j: int) -> int:
    depth, n = 1, len(s)
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "'":
            k = s.find("'", j + 1)
            j = n if k < 0 else k + 1
            continue
        if c == '"':
            j = _sh_dq_end(s, j)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def mask_shell(s: str) -> str:
    chars, n, i, heredocs = list(s), len(s), 0, []
    while i < n:
        c = s[i]
        if c == "\n":
            i += 1
            for word, tabs in heredocs:
                start = i
                while i < n:
                    k = s.find("\n", i)
                    k = n if k < 0 else k
                    line = s[i:k].rstrip("\r")
                    i = k + 1
                    if (line.lstrip("\t") if tabs else line) == word:
                        break
                _blank(chars, start, i, MASK_S)
            heredocs = []
            continue
        if c == "\\":
            i += 2
            continue
        if c == "#" and (i == 0 or s[i - 1] in " \t\n;|&()"):
            k = s.find("\n", i)
            k = n if k < 0 else k
            _blank(chars, i, k, MASK_C)
            i = k
            continue
        if c == "'":
            if i > 0 and s[i - 1] == "$":
                k = _qend(s, i, "'", multiline=True)
            else:
                e = s.find("'", i + 1)
                k = n if e < 0 else e + 1
            _blank(chars, i, k, MASK_S)
            i = k
            continue
        if c == '"':
            k = _sh_dq_end(s, i)
            _blank(chars, i, k, MASK_S)
            i = k
            continue
        if s.startswith("<<", i) and not s.startswith("<<<", i) and (m := HEREDOC.match(s, i)):
            heredocs.append((m.group(3), m.group(1) == "-"))
            i = m.end()
            continue
        i += 1
    return "".join(chars)


def mask_powershell(s: str) -> str:
    chars, n, i = list(s), len(s), 0
    while i < n:
        c = s[i]
        if s.startswith("<#", i):
            e = s.find("#>", i + 2)
            k = n if e < 0 else e + 2
            _blank(chars, i, k, MASK_C)
            i = k
            continue
        if c == "#" and (i == 0 or s[i - 1] in " \t\n;|({}"):
            k = s.find("\n", i)
            k = n if k < 0 else k
            _blank(chars, i, k, MASK_C)
            i = k
            continue
        if c == "@" and s[i + 1:i + 2] in ("\"", "'") and s[i + 2:].lstrip(" \t").startswith(("\n", "\r\n")):
            m = re.compile(r"\r?\n" + re.escape(s[i + 1]) + "@").search(s, i + 2)
            k = n if not m else m.end()
            _blank(chars, i, k, MASK_S)
            i = k
            continue
        if c in "'\"":
            j = i + 1
            while j < n:
                if c == '"' and s[j] == "`":
                    j += 2
                    continue
                if s[j] == c:
                    if s[j + 1:j + 2] == c:
                        j += 2
                        continue
                    break
                j += 1
            k = min(n, j + 1)
            _blank(chars, i, k, MASK_S)
            i = k
            continue
        if c == "`":
            i += 2
            continue
        i += 1
    return "".join(chars)


def mask_text(text: str, lang: str) -> str:
    if lang == "shell":
        return mask_shell(text)
    if lang == "powershell":
        return mask_powershell(text)
    return mask_clike(text, lang)


def _int_mutation(tok: str) -> str | None:
    if len(tok) > 1 and tok.startswith("0"):
        return None   # octal in some languages
    n = int(tok)
    return "1" if n == 0 else "0" if n == 1 else str(n + 1)


CL_CMP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==", "===": "!==", "!==": "==="}
CL_ARITH = {"+": "-", "-": "+", "*": "/", "/": "*", "+=": "-=", "-=": "+="}
SPACED = r"(?<=\s)({})(?=\s)"
CLIKE_RULES = [
    ("comparison", re.compile(SPACED.format(r"===|!==|==|!=|<=|>=|<|>")), CL_CMP),
    ("boolean", re.compile(SPACED.format(r"&&|\|\|")), {"&&": "||", "||": "&&"}),
    ("boolean", re.compile(r"(?<![\w$.])(true|false)(?![\w$])"), {"true": "false", "false": "true"}),
    ("boolean", re.compile(r"(?<![\w$)\]!?.])(!)(?=[\w$(!])"), {"!": ""}),
    ("arithmetic", re.compile(SPACED.format(r"\+=|-=|\+|-|\*|/")), CL_ARITH),
    ("constant", re.compile(r"(?<![\w$.])(\d+)(?![\w.])"), _int_mutation),
]
SH_TEST = {"eq": "ne", "ne": "eq", "lt": "le", "le": "lt", "gt": "ge", "ge": "gt"}
SHELL_RULES = [
    ("comparison", re.compile(r"(?<=\s)(-(?:eq|ne|lt|le|gt|ge))(?=\s)"), lambda t: "-" + SH_TEST[t[1:]]),
    ("comparison", re.compile(SPACED.format(r"==|!=|=")), {"==": "!=", "=": "!=", "!=": "="}),
    ("boolean", re.compile(SPACED.format(r"&&|\|\|")), {"&&": "||", "||": "&&"}),
    ("boolean", re.compile(r"(?<![\w$./-])(true|false)(?![\w./=-])"), {"true": "false", "false": "true"}),
    ("constant", re.compile(r"(?<![\w$-])(?:exit|return)\s+(\d+)(?![\w.])"), _int_mutation),
]
PS_RULES = [
    ("comparison", re.compile(r"(?<=\s)(-[ci]?(?:eq|ne|lt|le|gt|ge))(?=\s)", re.I),
     lambda t: t[:-2] + SH_TEST[t[-2:].lower()]),
    ("boolean", re.compile(r"(?<=\s)(-(?:and|or))(?=\s)", re.I), lambda t: "-or" if t.lower() == "-and" else "-and"),
    ("boolean", re.compile(r"(?<=\$)(true|false)\b", re.I), lambda t: "false" if t.lower() == "true" else "true"),
    ("boolean", re.compile(r"(?<=[\s(])(-not\s+|!)(?=[$(\w-])", re.I), lambda t: ""),
    ("arithmetic", re.compile(SPACED.format(r"\+=|-=|\+|-|\*|/")), CL_ARITH),
    ("constant", re.compile(r"(?<![\w$-])(?:exit|return)\s+(\d+)(?![\w.])", re.I), _int_mutation),
]
SKIP_LINE = re.compile(r"(import|package|using|use|export\s+\*|#\s*include|require)\b")
CL_KEYWORDS = {"return", "if", "while", "for", "switch", "catch", "function", "new", "throw", "typeof", "delete",
               "yield", "else", "do", "case", "import", "export", "const", "let", "var", "def", "fn", "func", "go",
               "defer", "sizeof", "assert"}
CL_LOGGING = re.compile(r"^(console\.\w+|fmt\.(?:Print|Fprint)\w*|log\.\w+|logger\.\w+|this\.logger\.\w+|"
                        r"System\.(?:out|err)\.print\w*|Console\.Write\w*|printf|fprintf|puts|print|println|debug)$")
CALL_HEAD = re.compile(r"(?:await\s+)?([A-Za-z_$][\w$]*(?:\s*(?:\.|\?\.|::|->)\s*[A-Za-z_$][\w$]*)*)\s*\(")
NOT_PAREN_COND = {"go", "rust", "swift"}
CONT_END = set("+-*/%=&|^<>!?:,([.~\\")
FUNC_HEAD = re.compile(r"(?:function\*?|func(?:\s*\([^)]*\))?|fn|def|sub)\s+([A-Za-z_$][\w$]*)"
                       r"|^\s*(?:(?:public|private|protected|static|final|async|override|virtual|internal|export|"
                       r"const|let|var)\s+)*(?:[\w<>\[\],.?]+\s+)?([A-Za-z_$][\w$]*)\s*"
                       r"(?:=\s*(?:async\s*)?\([^)]*\)\s*=>|\([^;]*\)\s*\{\s*$)")


def _close_paren(s: str, i: int) -> int | None:
    """The index of the bracket closing s[i], on the same line, or None."""
    pairs, stack = {"(": ")", "[": "]", "{": "}"}, []
    for j in range(i, len(s)):
        c = s[j]
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")]}":
            if not stack or stack.pop() != c:
                return None
            if not stack:
                return j
    return None


def _balanced(s: str) -> bool:
    depth = 0
    for c in s:
        depth += c in "([{"
        depth -= c in ")]}"
        if depth < 0:
            return False
    return depth == 0


def _code_end(mline: str) -> int:
    """Where a masked line's code ends: before trailing comments and spaces."""
    return len(mline.rstrip(" \t" + MASK_C))


def regex_mutants(text: str, lang: str) -> tuple[list[dict], list[str]]:
    masked = mask_text(text, lang)
    lines, mlines = split_lines(text), split_lines(masked)
    rules = SHELL_RULES if lang == "shell" else PS_RULES if lang == "powershell" else CLIKE_RULES
    codes = [bare(m)[:_code_end(bare(m))] for m in mlines]
    funcs, current = [""] * (len(lines) + 2), ""
    found = []
    semicolons = lang in ("js", "ts", "java", "csharp", "c", "cpp", "rust", "dart")

    def neighbour(i: int, step: int) -> str:
        j = i + step
        while 0 <= j < len(codes):
            if codes[j].strip():
                return codes[j].strip()
            j += step
        return ""

    for idx, (line, mline) in enumerate(zip(lines, mlines)):
        row = idx + 1
        line, mline = bare(line), bare(mline)
        if lang in CLIKE and (fm := FUNC_HEAD.search(mline)) and not re.match(r"\s*(if|for|while|switch|catch)\b", mline):
            current = fm.group(1) or fm.group(2) or current
        funcs[row] = current
        code = codes[idx]
        stripped = code.lstrip()
        if not stripped.strip(MASK_S + " \t") or has_surrogates(line):
            continue
        if lang == "rust" and stripped.startswith(("#[", "#![")):
            if "cfg(test)" in line:
                break   # an inline test module: the rest of the file is tests
            continue
        if lang in CLIKE and (stripped.startswith(("#", "@")) or SKIP_LINE.match(stripped)):
            continue
        if lang == "csharp" and stripped.startswith("[") and stripped.endswith("]"):
            continue
        for op, rx, table in rules:
            if lang == "shell" and op == "arithmetic":
                continue
            for m in rx.finditer(code):
                tok = m.group(1)
                new = table(tok) if callable(table) else table.get(tok)
                if new is None:
                    continue
                detail = f"{tok.strip()} → {new}" if new else f"{tok.strip()}x → x"
                found.append((row, m.start(1), m.end(1), new, op, detail))
        if lang == "shell":
            if "((" in code:
                for m in re.compile(SPACED.format(r"\+|-|\*|/")).finditer(code):
                    found.append((row, m.start(1), m.end(1), CL_ARITH[m.group(1)], "arithmetic",
                                  f"{m.group(1)} → {CL_ARITH[m.group(1)]}"))
            m = re.match(r"(\s*(?:if|elif|while)\s+)(?!!)", code)
            if m and re.search(r";\s*(then|do)\b", code):
                found.append((row, m.end(), m.end(), "! ", "condition", "if x → if ! x"))
            continue
        if lang == "powershell":
            for m in re.finditer(r"(?<![\w$-])(?:if|elseif|while)\s*\(", code, re.I):
                close = _close_paren(code, m.end() - 1)
                if close:
                    inner = line[m.end():close]
                    found.append((row, m.end(), close, f"-not ({inner})", "condition", "if (x) → if (-not (x))"))
            continue
        # conditions
        if lang in NOT_PAREN_COND:
            m = re.search(r"(?<![\w$.])(if|while)\s+(?!let\b|var\b|case\b)", code)
            if m and code.rstrip().endswith("{"):
                start, end = m.end(), code.rstrip().rfind("{")
                cond = code[start:end]
                if lang == "go" and ";" in cond:
                    start += cond.rfind(";") + 1
                    while start < end and code[start] == " ":
                        start += 1
                cond = code[start:end].rstrip()
                if cond and "{" not in cond and _balanced(cond):
                    found.append((row, start, start + len(cond), f"!({line[start:start + len(cond)]})",
                                  "condition", "if x → if !(x)"))
        else:
            for m in re.finditer(r"(?<![\w$.])(if|while)\s*\(", code):
                close = _close_paren(code, m.end() - 1)
                if close:
                    found.append((row, m.end(), close, f"!({line[m.end():close]})", "condition",
                                  "if (x) → if (!(x))"))
        prev_ok = not neighbour(idx, -1) or neighbour(idx, -1)[-1] not in CONT_END
        nxt = neighbour(idx, 1)
        next_ok = not nxt or nxt[0] not in ".?+-*/%&|^=<>,:"
        # return x -> return undefined (JavaScript and TypeScript only; elsewhere the type decides)
        if lang in ("js", "ts") and prev_ok and next_ok:
            m = re.match(r"(\s*return\s+)(.+?)(\s*;?)$", code)
            if m and _balanced(m.group(2)) and m.group(2).strip() not in ("undefined", "null") \
                    and m.group(2).rstrip()[-1] not in CONT_END - set(")]"):
                found.append((row, m.start(2), m.end(2), "undefined", "return", "return x → return undefined"))
        # a bare call statement -> nothing
        if lang in ("js", "ts", "java", "csharp", "c", "cpp", "rust", "go") and prev_ok and next_ok:
            m = CALL_HEAD.match(stripped)
            head = re.sub(r"\s+", "", m.group(1)) if m else ""
            if m and re.split(r"\?\.|\.|::|->", head)[0] not in CL_KEYWORDS and head not in ("this", "super") \
                    and not CL_LOGGING.match(head):
                lead = len(code) - len(stripped)
                close = _close_paren(code, lead + m.end() - 1)
                tail = code[close + 1:].strip() if close else None
                if close and (tail == ";" if semicolons and lang not in ("js", "ts") else tail in ("", ";")):
                    found.append((row, lead, len(code), "" if lang == "go" else ";", "statement", "call removed"))

    out, seen = [], set()
    for row, c0, c1, repl, op, detail in sorted(found):
        line = bare(lines[row - 1])
        new = line[:c0] + repl + line[c1:]
        if new != line and (row, new) not in seen:
            seen.add((row, new))
            out.append({"line": row, "col": c0, "op": op, "detail": detail, "original": line, "mutated": new})
    return out, funcs


def make_mutants(text: str, lang: str, rel: str) -> tuple[list[dict], str | None, int, list[str]]:
    if lang == "python":
        return py_mutants(text, rel)
    ms, funcs = regex_mutants(text, lang)
    return ms, None, 0, funcs


# ---------------------------------------------------------------- the run's files

def load_mutants(ws: Path) -> dict:
    db = kit.load_json(ws / "mutants.json", None)
    if db is None:
        fail(f"no mutants yet in {ws}; run generate first")
    db["by_id"] = {m["id"]: m for m in db["mutants"]}
    return db


def mutant(db: dict, mid: str) -> dict:
    m = db["by_id"].get(mid.upper())
    if m is None:
        fail(f"no mutant {mid}")
    return m


def read_results(path: Path) -> dict:
    """The results file is appended to as each mutant finishes; a torn last line is ignored."""
    out = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and r.get("id"):
                out[r["id"]] = r
    return out


def append_result(path: Path, rec: dict) -> None:
    with path.open("a+b") as f:
        if f.seek(0, os.SEEK_END):
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":   # an interrupted run left half a line: start a new one
                f.write(b"\n")
        f.write((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
        f.flush()


def need_baseline(ws: Path) -> dict:
    bl = kit.load_json(ws / "baseline.json", None)
    if bl is None:
        fail("run baseline first: mutation results mean nothing until the suite passes every time")
    if not bl.get("ok"):
        fail(f"the suite's baseline was {bl.get('verdict', 'not clean')}: mutation results mean nothing on a red or "
             "flaky suite. Fix it, or give a test command that leaves the failing tests out, then run baseline again")
    return bl


def sample_ids(db: dict) -> list[str]:
    return list(db["sample"])


# ---------------------------------------------------------------- init and generate

def cmd_init(a) -> None:
    cmd = kit.strip_dashes(a.cmd or [])
    if not cmd:
        fail("give the test command after --, like: init --repo . -- python -m pytest -q")
    if a.jobs is not None and a.jobs < 1:
        fail("--jobs is 1 or more")
    ws, cfg, files = kit.init_run(
        SKILL, a.repo, scope=a.scope, slug=a.slug, target=a.target, network=a.network, include=a.include or [],
        exclude=a.exclude or [], env=a.env or [], workspace=a.workspace, force=a.force,
        extra={"test_cmd": cmd, "cwd": a.cwd, "jobs": a.jobs or default_jobs(), "source": a.source or [],
               "tests": a.tests or [], "branch_name": a.branch, "after": a.after, "goal": a.goal})
    if a.force:
        for f in ("state.json", "mutants.json", "results.jsonl", "baseline.json", "rescore.json", "rescore.jsonl",
                  "slices.json", "report.md", "summary.json"):
            (ws / f).unlink(missing_ok=True)
        kit.rmtree(ws / "patches")
    st = fresh_state()
    st["git_status"] = kit.git_status(Path(cfg["repo"])) if cfg["git"] else None
    kit.save_json(ws / "state.json", st)
    sources = sum(1 for f in files if classify(f, cfg)[0] == "source")
    tests = sum(1 for f in files if classify(f, cfg)[0] == "test")
    print(f"Test command: {' '.join(cmd)}" + (f"   (from {a.cwd})" if a.cwd else ""))
    print(f"Source files it can mutate: {sources}; test files: {tests}")
    if not tests:
        print("warning: no test files found in scope. If the tests live somewhere unusual, start over with --tests <globs>.")
    print(f"Next: mutants.py baseline -w {ws}")


def cmd_generate(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    if (ws / "results.jsonl").exists() and not a.force:
        fail("this run already has results; --force makes new mutants and throws the results away")
    top = Path(cfg["repo"])
    source = a.source if a.source else None
    scope = cfg["scope"]
    base = scope.get("base") if scope["kind"] in ("branch", "changes") else None
    mutants, skipped, files, dropped = [], {}, {}, 0
    for rel in kit.scope_files(ws):
        if kit.excluded(rel, a.exclude):
            skipped[rel] = "excluded by you"
            continue
        p = top / rel
        if not p.is_file():
            continue
        text = read_text(p)
        kind, why = classify(rel, cfg, text[:2000], source)
        if kind != "source":
            skipped[rel] = why
            continue
        ms, problem, gone, funcs = make_mutants(text, why, rel)
        if problem:
            skipped[rel] = problem
            continue
        only = changed_lines(top, base, rel) if base else None
        if only is not None:
            ms = [m for m in ms if m["line"] in only]
        dropped += gone
        for m in ms:
            m.update(file=rel, lang=why, func=funcs[m["line"]] if m["line"] < len(funcs) else "")
        files[rel] = {"lang": why, "mutants": len(ms)}
        mutants += ms
    mutants.sort(key=lambda m: (m["file"], m["line"], m["col"], OPERATORS.index(m["op"]), m["mutated"]))
    for i, m in enumerate(mutants, 1):
        m["id"] = f"M{i}"
    n = None if a.all else a.max
    picked = pick(mutants, n, a.seed)
    db = {"made": kit.now(), "head": cfg.get("head"), "seed": a.seed, "max": n, "dropped": dropped,
          "files": files, "skipped": skipped, "mutants": mutants, "sample": picked}
    with kit.locked(ws):
        kit.save_json(ws / "mutants.json", db)
        (ws / "results.jsonl").unlink(missing_ok=True)
    by_op = {op: sum(1 for m in mutants if m["op"] == op) for op in OPERATORS}
    print(f"Made {plural(len(mutants), 'mutant')} in {plural(len([f for f in files if files[f]['mutants']]), 'file')}: "
          + ", ".join(f"{op} {c}" for op, c in by_op.items() if c) + ".")
    if base:
        print(f"Only lines changed since {scope.get('ref')} were mutated.")
    if dropped:
        print(f"{plural(dropped, 'more')} didn't compile and were dropped.")
    if n is not None and n < len(mutants):
        print(f"Sampled {len(picked)}, spread across files and operators in proportion (seed {a.seed}); --all runs every one.")
    else:
        print(f"All {len(picked)} will run.")
    reasons: dict[str, list[str]] = {}
    for rel, why in skipped.items():
        reasons.setdefault(why, []).append(rel)
    for why, rels in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
        shown = ", ".join(rels[:4]) + (f" and {len(rels) - 4} more" if len(rels) > 4 else "")
        print(f"  not mutated, {why}: {shown}")
    bl = kit.load_json(ws / "baseline.json", None)
    if bl and bl.get("ok"):
        print(estimate(len(picked), bl))
    if not mutants:
        print("warning: no mutants. Check the scope, --source and --exclude.")


def estimate(count: int, bl: dict, jobs: int | None = None) -> str:
    jobs = jobs or bl.get("jobs") or 1
    secs = count * bl["median"] * 1.15 / jobs
    span = f"{secs / 60:.0f} minutes" if secs >= 90 else f"{secs:.0f} seconds"
    if secs >= 5400:
        span = f"{secs / 3600:.1f} hours"
    line = f"Estimate: {count} mutants x {bl['median']:.1f} s / {jobs} sandboxes, about {span}."
    if secs > 7200:
        line += " That's over 2 hours: consider --max 100 or a narrower --scope."
    return line


def pick(mutants: list[dict], n: int | None, seed: int) -> list[str]:
    """A seeded sample, stratified by file and operator: each group gets its share, and at least one."""
    if n is None or n >= len(mutants):
        return [m["id"] for m in mutants]
    groups: dict[tuple, list[dict]] = {}
    for m in mutants:
        groups.setdefault((m["file"], m["op"]), []).append(m)
    keys = sorted(groups)
    for k in keys:
        random.Random(f"{seed}:{k[0]}:{k[1]}").shuffle(groups[k])
    quota = {k: n * len(groups[k]) / len(mutants) for k in keys}
    take = {k: int(quota[k]) for k in keys}
    if n >= len(keys):
        for k in keys:
            take[k] = max(1, take[k])
    order = sorted(keys, key=lambda k: (-(quota[k] - int(quota[k])), k))
    while sum(take.values()) < n:
        grew = False
        for k in order:
            if sum(take.values()) < n and take[k] < len(groups[k]):
                take[k] += 1
                grew = True
        if not grew:
            break
    while sum(take.values()) > n:
        k = max(keys, key=lambda k: (take[k], k))
        take[k] -= 1
    chosen = [m["id"] for k in keys for m in groups[k][:take[k]]]
    return sorted(chosen, key=num)


# ---------------------------------------------------------------- mutants as diffs, and the writer's helper

def mutate_bytes(data: bytes, m: dict, undo: bool = False) -> bytes | None:
    """The file with mutant m put in (or taken out); None if the line isn't what it should be."""
    lines = split_lines(data.decode("utf-8", "surrogateescape"))
    i = m["line"] - 1
    if i >= len(lines):
        return None
    body = bare(lines[i])
    want, put = (m["mutated"], m["original"]) if undo else (m["original"], m["mutated"])
    if body != want:
        return None
    lines[i] = put + lines[i][len(body):]
    return "".join(lines).encode("utf-8", "surrogateescape")


def diff_text(data: bytes, m: dict, ctx: int = 3, exact: bool = False) -> str | None:
    """Mutant m as a unified diff against the file's bytes; exact=True keeps line endings (for git apply)."""
    lines = split_lines(data.decode("utf-8", "surrogateescape"))
    i = m["line"] - 1
    if i >= len(lines) or bare(lines[i]) != m["original"]:
        return None
    a, b = max(0, i - ctx), min(len(lines), i + ctx + 1)
    out = [f"--- a/{m['file']}\n", f"+++ b/{m['file']}\n", f"@@ -{a + 1},{b - a} +{a + 1},{b - a} @@\n"]

    def emit(prefix: str, s: str) -> None:
        if not exact:
            out.append(prefix + bare(s) + "\n")
        elif s.endswith("\n"):
            out.append(prefix + s)
        else:
            out.append(prefix + s + "\n\\ No newline at end of file\n")
    for k in range(a, b):
        if k == i:
            emit("-", lines[k])
            emit("+", m["mutated"] + lines[k][len(bare(lines[k])):])
        else:
            emit(" ", lines[k])
    text = "".join(out)
    return f"diff --git a/{m['file']} b/{m['file']}\n" + text if exact else text


def where(m: dict) -> str:
    return f"{m['file']} line {m['line']}" + (f", in {m['func']}()" if m.get("func") else "")


HELPER = '''"""Put one of the faults in mutants.md into the project copy, or take it out again.

  python ../scratch/mutants/mutant.py M17          put M17 in
  python ../scratch/mutants/mutant.py M17 --undo   take it out again
  python ../scratch/mutants/mutant.py --list       which are in now

It changes one line of one file. Take every fault out before you finish: the project's
own code must end up exactly as it started.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent / "repo"


def lines_of(data):
    parts = data.decode("utf-8", "surrogateescape").split("\\n")
    return [p + "\\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def bare(line):
    return line[:-2] if line.endswith("\\r\\n") else line[:-1] if line.endswith("\\n") else line


def main():
    faults = {m["id"]: m for m in json.loads((HERE / "mutants.json").read_text(encoding="utf-8"))}
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args[0] == "--list":
        for mid, m in faults.items():
            lines = lines_of((REPO / m["file"]).read_bytes())
            now = bare(lines[m["line"] - 1]) if m["line"] <= len(lines) else ""
            print(mid, "IN" if now == m["mutated"] else "out" if now == m["original"] else "line changed")
        return 0
    m = faults.get(args[0].upper())
    if m is None:
        print("no fault called " + args[0] + "; they are " + ", ".join(faults))
        return 2
    undo = "--undo" in args
    path = REPO / m["file"]
    lines = lines_of(path.read_bytes())
    i = m["line"] - 1
    want, put = (m["mutated"], m["original"]) if undo else (m["original"], m["mutated"])
    if i >= len(lines) or bare(lines[i]) != want:
        state = "isn't in" if undo else "is already in, or the line has changed"
        print(m["id"] + " " + state + ": " + m["file"] + " line " + str(m["line"]))
        return 1
    lines[i] = put + lines[i][len(bare(lines[i])):]
    path.write_bytes("".join(lines).encode("utf-8", "surrogateescape"))
    print(("took out " if undo else "put in ") + m["id"] + ": " + m["file"] + " line " + str(m["line"]))
    return 0


sys.exit(main())
'''


def write_handout(box: Path, db: dict, mids: list[str]) -> None:
    """mutants.md (the faults as diffs), mutants.json and mutant.py in the sandbox's scratch folder."""
    folder = box / "scratch" / "mutants"
    folder.mkdir(parents=True, exist_ok=True)
    parts = ["# Faults to catch", "",
             "Each fault is one small change to one line of the project's code. The tests pass with every one of",
             "them in place, so nothing checks that line's behaviour yet. `-` is the line as it is, `+` the fault.", ""]
    subset = []
    for mid in mids:
        m = mutant(db, mid)
        p = box / "repo" / m["file"]
        d = diff_text(p.read_bytes(), m) if p.is_file() else None
        parts += [f"## {m['id']}: {where(m)}", "", "```diff", (d or f"-{m['original']}\n+{m['mutated']}\n").rstrip("\n"),
                  "```", ""]
        subset.append({k: m[k] for k in ("id", "file", "line", "original", "mutated")})
    (folder / "mutants.md").write_text("\n".join(parts), encoding="utf-8")
    kit.save_json(folder / "mutants.json", subset)
    (folder / "mutant.py").write_text(HELPER, encoding="utf-8")


def cmd_show(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    db = load_mutants(ws)
    results = read_results(ws / "results.jsonl")
    st = load_state(ws)
    top = Path(cfg["repo"])
    for mid in norm_ids(a.ids):
        m = mutant(db, mid)
        p = top / m["file"]
        d = diff_text(p.read_bytes(), m, a.context, exact=a.patch) if p.is_file() else None
        if a.patch:
            if d is None:
                fail(f"{mid}: {m['file']} line {m['line']} has changed since the mutants were made")
            sys.stdout.buffer.write(d.encode("utf-8", "surrogateescape"))
            sys.stdout.flush()
            continue
        status = (results.get(mid) or {}).get("status", "not run")
        if mid in st["kills"]:
            status += f", killed by {st['kills'][mid]}"
        print(f"{mid} · {where(m)} · {m['op']} ({m['detail']}) · {status}")
        print(d.rstrip("\n") if d else f"-{m['original']}\n+{m['mutated']}\n(the file has changed since)")
        print()


# ---------------------------------------------------------------- sandboxes that run mutants

class PatchError(Exception):
    def __init__(self, pid: str, msg: str):
        super().__init__(f"{pid} doesn't apply: {msg}")
        self.pid, self.msg = pid, msg


def rebase_box(box: Path) -> None:
    """Commit what's in the copy now as its base, so diffs and resets start from here."""
    repo = box / "repo"
    kit.git(repo, *kit.RAW, "add", "-A")
    kit.git(repo, *kit.BASE_IDENTITY, "commit", "-q", "--no-verify", "--allow-empty", "-m", "accepted tests")
    (box / "base.txt").write_text(kit.git(repo, "rev-parse", "HEAD").strip() + "\n", encoding="utf-8")


def make_box(cfg: dict, ws: Path, name: str, patches=(), rev: str | None = None, network: bool | None = None) -> Path:
    """A sandbox with no compiled-Python caches and the given accepted patches in its base."""
    box = kit.make_sandbox(cfg, ws, name, rev=rev, network=network, env=NO_BYTECODE)
    try:
        changed = False
        for dirpath, dirnames, _ in os.walk(box / "repo"):
            for d in [d for d in dirnames if d == "__pycache__"]:
                kit.rmtree(Path(dirpath) / d)
                changed = True
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
        for pid in patches:
            ok, msg = kit.apply_patch(box / "repo", kit.read_patch(ws / "patches" / f"{pid}.patch"))
            if not ok:
                raise PatchError(pid, msg)
            changed = True
        if changed:
            rebase_box(box)
    except BaseException:
        kit.drop_sandbox(ws, cfg, name)
        raise
    return box


class Box:
    def __init__(self, path: Path):
        self.path, self.repo, self.stamp = path, path / "repo", 0.0

    def write(self, rel: str, data: bytes) -> None:
        """Write a file and move its time forward, so no build tool's cache mistakes it for the last one."""
        p = self.repo / rel
        p.write_bytes(data)
        t = max(time.time(), self.stamp + 1.0)
        self.stamp = t
        os.utime(p, (t, t))

    def tidy(self) -> None:
        """Undo anything a test run left in the copy or the temp folder, so every run starts the same."""
        if kit.git(self.repo, *kit.RAW, "status", "--porcelain", "--untracked-files=all", check=False).strip():
            kit.git(self.repo, *kit.RAW, "reset", "-q", "--hard", check=False)
            kit.git(self.repo, "clean", "-fdq", check=False)
        tmp = self.path / "tmp"
        for child in tmp.iterdir() if tmp.is_dir() else []:
            with contextlib.suppress(OSError):
                kit.rmtree(child)

    def runs(self, cmd: list[str], n: int, timeout: float, cwd: str | None) -> dict:
        """Run cmd n times, tidying in between, as each mutant's run will be."""
        runs = []
        for k in range(n):
            if k:
                self.tidy()
            runs += kit.run_in(self.path, cmd, 1, timeout, cwd, log=False)["runs"]
        self.tidy()
        return {"cmd": cmd, "cwd": str(self.repo / cwd if cwd else self.repo), "runs": runs}

    def unapply(self, patch: str) -> tuple[bool, str]:
        """Take a patch out of the copy again; the next tidy puts it back."""
        r = subprocess.run(["git", "-C", str(self.repo), *kit.RAW, "apply", "-R", "--binary", "--whitespace=nowarn", "-"],
                           input=patch.encode("utf-8", "surrogateescape"), capture_output=True)
        return r.returncode == 0, (r.stderr or r.stdout).decode("utf-8", "replace").strip()


_CC = r"^\S+:\d+:\d+: (?:fatal )?error:"
BUILD_ERRORS = {
    "js": re.compile(r"SyntaxError"),
    "ts": re.compile(r"error TS\d+|\bTS\d{4}:|SyntaxError"),
    "go": re.compile(r"\[build failed\]|\[setup failed\]"),
    "rust": re.compile(r"error\[E\d{4}\]|error: could not compile"),
    "java": re.compile(r"COMPILATION ERROR|Compilation failed|\.java:\d+: error:"),
    "kotlin": re.compile(r"Compilation error|^e: ", re.M),
    "scala": re.compile(r"Compilation failed|\[error\] .*\.scala:\d+"),
    "csharp": re.compile(r"error CS\d{4}"),
    "c": re.compile(_CC, re.M), "cpp": re.compile(_CC, re.M), "swift": re.compile(_CC, re.M),
    "dart": re.compile(r"Error: .*\.dart:\d+|Compilation failed"),
    "shell": re.compile(r"syntax error"),
    "powershell": re.compile(r"ParserError|ParseException"),
}


def node_checkable(cfg: dict, mutants: list[dict]) -> frozenset:
    """JavaScript files `node --check` accepts as they are; their mutants get the same syntax check."""
    node = shutil.which("node")
    files = sorted({m["file"] for m in mutants if m["lang"] == "js" and m["file"].lower().endswith((".js", ".mjs", ".cjs"))})
    ok = set()
    for f in files if node else []:
        try:
            r = subprocess.run([node, "--check", str(Path(cfg["repo"]) / f)], capture_output=True, timeout=60)
            if r.returncode == 0:
                ok.add(f)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return frozenset(ok)


def run_mutant(box: Box, cfg: dict, m: dict, timeout: float, node_ok=frozenset()) -> dict:
    """Put mutant m in, run the suite, put the file back: killed, survived, timeout or error (not viable)."""
    p = box.repo / m["file"]
    try:
        original = p.read_bytes()
    except OSError:
        return {"status": "error", "why": "the file isn't in the sandbox"}
    data = mutate_bytes(original, m)
    if data is None:
        return {"status": "error", "why": "the line has changed since the mutants were made"}
    if m["lang"] == "python" and not py_compiles(data.decode("utf-8", "surrogateescape"), m["file"]):
        return {"status": "error", "why": "doesn't compile"}
    box.write(m["file"], data)
    try:
        if m["file"] in node_ok:
            r = kit.run_in(box.path, ["node", "--check", m["file"]], 1, 60, None, log=False)["runs"][0]
            if kit.failed(r):
                return {"status": "error", "why": "doesn't parse (node --check)"}
        r = kit.run_in(box.path, cfg["test_cmd"], 1, timeout, cfg.get("cwd"), log=False)["runs"][0]
    finally:
        box.write(m["file"], original)
        box.tidy()
    if r["timed_out"]:
        return {"status": "timeout", "secs": r["secs"]}
    if r["code"] == 0:
        return {"status": "survived", "secs": r["secs"]}
    rx = BUILD_ERRORS.get(m["lang"])
    hit = rx.search(r["out"]) if rx and m["file"] not in node_ok else None
    if hit:
        line = r["out"][r["out"].rfind("\n", 0, hit.start()) + 1:].split("\n", 1)[0].strip()
        return {"status": "error", "why": f"the build failed: {line[:160]}", "secs": r["secs"]}
    return {"status": "killed", "secs": r["secs"], "code": r["code"]}


class Worker:
    """One thread's sandboxes, made when first needed and deleted at the end."""

    def __init__(self, cfg, ws, hint, k, specs, preset=None):
        self.cfg, self.ws, self.hint, self.k, self.specs = cfg, ws, hint, k, specs
        self.boxes, self.names = dict(preset or {}), []   # preset boxes belong to the caller

    def box(self, kind: str = "main") -> Box:
        if kind not in self.boxes:
            patches, rev = self.specs[kind]
            name = f"{self.hint}-{kind}-{self.k}-{secrets.token_hex(2)}"
            self.names.append(name)
            self.boxes[kind] = Box(make_box(self.cfg, self.ws, name, patches, rev))
        return self.boxes[kind]

    def close(self) -> None:
        for name in self.names:
            kit.drop_sandbox(self.ws, self.cfg, name)


def pool(cfg: dict, ws: Path, items: list, jobs: int, work, on_done, hint: str, specs: dict, preset=None) -> None:
    """Run work(worker, item) for every item on `jobs` threads; on_done(item, result) runs under a lock.

    preset gives the first thread sandboxes the caller made (and deletes) itself."""
    kit.runner()   # load run.py once, here: kit.runner() isn't safe to call first from two threads at once
    q: queue.Queue = queue.Queue()
    for it in items:
        q.put(it)
    lock, stop, errors = threading.Lock(), threading.Event(), []

    def run(k):
        wk = Worker(cfg, ws, hint, k, specs, preset if k == 0 else None)
        try:
            while not stop.is_set():
                try:
                    it = q.get_nowait()
                except queue.Empty:
                    break
                res = work(wk, it)
                with lock:
                    on_done(it, res)
        except BaseException as e:   # noqa: BLE001 - re-raised in the main thread
            errors.append(e)
            stop.set()
        finally:
            wk.close()
    threads = [threading.Thread(target=run, args=(k,), daemon=True) for k in range(max(1, min(jobs, len(items))))]
    for t in threads:
        t.start()
    try:
        for t in threads:
            while t.is_alive():
                t.join(0.2)
    except KeyboardInterrupt:
        stop.set()
        print("Stopping once the mutants running now finish; run again with --resume to carry on.", flush=True)
        for t in threads:
            t.join()
        raise
    if errors:
        raise errors[0]


FAIL_NAMES = [re.compile(r"^(?:FAIL|ERROR): (\S+ \([^)]*\))", re.M),
              re.compile(r"^FAILED (?!\()(\S+)", re.M),
              re.compile(r"^--- FAIL: (\S+)", re.M),
              re.compile(r"^not ok \d+ - (.+)$", re.M),
              re.compile(r"^\s*(?:✕|×|✗)\s+(.+?)(?:\s+\(\d+ ?ms\))?$", re.M)]


def failing_names(out: str) -> list[str]:
    names = []
    for rx in FAIL_NAMES:
        for m in rx.finditer(out or ""):
            if m.group(1).strip() not in names:
                names.append(m.group(1).strip())
    return names[:10]


def real_status(cfg: dict) -> str | None:
    return kit.git_status(Path(cfg["repo"])) if cfg.get("git") else None


def warn_if_touched(cfg: dict, before: str | None) -> None:
    after = real_status(cfg)
    if before is not None and after != before:
        print("warning: the real project's git status changed while the tests ran in sandboxes. Something wrote "
              "into the project itself; look at `git status` there and undo what isn't yours.")


# ---------------------------------------------------------------- baseline, run, score

def parallel_check(cfg: dict, ws: Path, box: Box, cmd: list[str], cwd: str | None, jobs: int, timeout: float) -> dict:
    """Run the suite in `jobs` copies at once (box is one of them): fixed ports and shared files show up here."""
    outs: list[dict] = []
    barrier = threading.Barrier(jobs)

    def work(wk, _i):
        try:
            b = wk.box()
        except BaseException:
            barrier.abort()
            raise
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait(timeout=600)   # start them together, once every copy exists
        return kit.run_in(b.path, cmd, 1, timeout, cwd, log=False)["runs"][0]
    try:
        pool(cfg, ws, list(range(jobs)), jobs, work, lambda _i, r: outs.append(r), "parallel",
             {"main": ((), None)}, preset={"main": box})
    finally:
        barrier.abort()
    failed = [r for r in outs if kit.failed(r)]
    return {"jobs": jobs, "failed": len(failed), "median": statistics.median(r["secs"] for r in outs)}


def cmd_baseline(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    cmd, cwd = cfg["test_cmd"], cfg.get("cwd")
    jobs = a.jobs or cfg.get("jobs") or 1
    before = real_status(cfg)
    name = f"baseline-{secrets.token_hex(2)}"
    with busy(ws, "baseline"):
        box = Box(make_box(cfg, ws, name))
        try:
            res = box.runs(cmd, a.repeat, a.timeout, cwd)
            par = parallel_check(cfg, ws, box, cmd, cwd, jobs, a.timeout) \
                if jobs > 1 and not any(kit.failed(r) for r in res["runs"]) else None
        finally:
            kit.drop_sandbox(ws, cfg, name)
        runs = res["runs"]
        bad = [r for r in runs if kit.failed(r)]
        rec = {"ok": not bad, "cmd": cmd, "at": kit.now(), "head": cfg.get("head"),
               "runs": [{"code": r["code"], "secs": r["secs"], "timed_out": r["timed_out"]} for r in runs]}
        if bad:
            rec["verdict"] = "failing" if len(bad) == len(runs) else "flaky"
            names = failing_names(bad[0]["out"])
            kit.save_json(ws / "baseline.json", rec)
            if rec["verdict"] == "flaky":
                change_state(ws, lambda st: st["flaky"].append(
                    {"where": "the baseline, on unchanged code", "runs": f"{len(bad)} of {len(runs)} runs failed",
                     "names": names}))
            print(kit.summary(res))
            print()
            named = f" ({', '.join(names)})" if names else ""
            if rec["verdict"] == "flaky":
                print(f"FLAKY: the suite failed {len(bad)} of {len(runs)} runs on unchanged code{named}.")
            else:
                print(f"FAILING: the suite failed every run on unchanged code{named}.")
            print("Mutation results mean nothing on a red or flaky suite. Fix those tests, or start over with a test "
                  "command that leaves them out (init --force ... -- <command>), then run baseline again.")
            warn_if_touched(cfg, before)
            sys.exit(1)
        median = statistics.median(r["secs"] for r in runs)
        used = jobs if par and not par["failed"] else 1
        worst = max(median, par["median"] if par and not par["failed"] else 0)
        timeout = round(max(10.0, 3 * worst + 5), 1)
        rec.update(verdict="clean", median=round(median, 2), timeout=timeout, jobs=used, parallel=par)
        kit.save_json(ws / "baseline.json", rec)
    print(f"Baseline: the suite passed {len(runs)} of {len(runs)} runs in a fresh copy; median {median:.1f} s.")
    if par and par["failed"]:
        print(f"When {jobs} copies ran at once, {par['failed']} failed (fixed ports or shared files, most likely). "
              "Mutants will run one at a time.")
    elif par:
        print(f"{jobs} copies at once all passed (median {par['median']:.1f} s), so mutants run {jobs} at a time.")
    print(f"Each mutant gets {timeout:.0f} s before it counts as a timeout (3 x the median + 5 s, at least 10 s).")
    if (ws / "mutants.json").is_file():
        print(estimate(len(load_mutants(ws)["sample"]), rec))
    warn_if_touched(cfg, before)


def tally(ids, status_of) -> dict:
    c = {"killed": 0, "survived": 0, "timeout": 0, "error": 0, "equivalent": 0, "not run": 0}
    for mid in ids:
        c[status_of(mid)] += 1
    return c


def ratio(c: dict) -> float | None:
    k = c["killed"] + c["timeout"]
    return k / (k + c["survived"]) if k + c["survived"] else None


def confirmed_equivalent(st: dict) -> set:
    return {mid for mid, j in st["judges"].items() if j.get("verdict") == "equivalent"}


def before_status(results: dict, st: dict):
    eq = confirmed_equivalent(st)

    def status(mid):
        s = (results.get(mid) or {}).get("status", "not run")
        return "equivalent" if s == "survived" and mid in eq else s
    return status


def after_status(results: dict, st: dict, rescored: dict | None = None):
    eq, before = confirmed_equivalent(st), before_status(results, st)

    def status(mid):
        s = before(mid)
        if s in ("error", "equivalent", "not run"):
            return s
        if rescored is not None:
            r = (rescored.get(mid) or {}).get("status", "not run")
            return "equivalent" if r == "survived" and mid in eq else r
        return "killed" if s == "survived" and mid in st["kills"] else s
    return status


def survivors(db: dict, results: dict, st: dict) -> list[str]:
    after = after_status(results, st)
    return [mid for mid in sample_ids(db) if after(mid) == "survived"]


def rescore_results(ws: Path, db: dict) -> dict | None:
    meta = kit.load_json(ws / "rescore.json", None)
    if not meta:
        return None
    res = read_results(ws / "rescore.jsonl")
    return res if all(mid in res for mid in meta.get("ids", [])) else None


def score_lines(ws: Path, db: dict, st: dict) -> list[str]:
    results = read_results(ws / "results.jsonl")
    ids = sample_ids(db)
    before = tally(ids, before_status(results, st))
    out = []
    viable = before["killed"] + before["timeout"] + before["survived"]
    out.append(f"Mutation score: {pct(ratio(before))} ({before['killed'] + before['timeout']} of {viable} viable "
               f"mutants killed" + (f"; {before['timeout']} by timing out" if before["timeout"] else "") + ").")
    extras = []
    if before["error"]:
        extras.append(f"{before['error']} not viable (the change broke the build) and left out")
    if before["equivalent"]:
        extras.append(f"{before['equivalent']} a judge called equivalent, left out")
    if before["not run"]:
        extras.append(f"{before['not run']} not run yet")
    if extras:
        out.append("  " + "; ".join(extras) + ".")
    rescored = rescore_results(ws, db)
    if st["accepted"] or rescored:
        after = tally(ids, after_status(results, st, rescored))
        label = "After the new tests (rescored)" if rescored else "With the accepted tests (from their checks)"
        out.append(f"{label}: {pct(ratio(after))} ({after['killed'] + after['timeout']} of "
                   f"{after['killed'] + after['timeout'] + after['survived']} killed).")
    files = sorted({mutant(db, mid)["file"] for mid in ids})
    if files:
        out.append("Per file:")
        width = max(len(f) for f in files)
        for f in files:
            fid = [mid for mid in ids if mutant(db, mid)["file"] == f]
            b = tally(fid, before_status(results, st))
            line = f"  {f:<{width}}  {plural(len(fid), 'mutant'):>12}  {pct(ratio(b)):>4}"
            if st["accepted"] or rescored:
                line += f" -> {pct(ratio(tally(fid, after_status(results, st, rescored))))}"
            out.append(line)
            if b["killed"] + b["timeout"] == 0 and b["survived"] >= 5:
                out.append(f"  warning: none of {f}'s mutants were killed. Check that the tests import the copy in "
                           "the sandbox, not an installed one.")
    left = survivors(db, results, st)
    out.append(f"Survivors left: {len(left)}.")
    goal = str(kit.load_run(ws)[1].get("goal") or "")
    if goal.startswith("score:"):
        want = float(goal.split(":", 1)[1].rstrip("%")) / 100
        now = ratio(tally(ids, after_status(results, st, rescored)))
        out.append(f"Target {want:.0%}: " + ("reached." if now is not None and now >= want else "not reached yet."))
    return out


def cmd_run(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    bl = need_baseline(ws)
    db = load_mutants(ws)
    path = ws / "results.jsonl"
    with busy(ws, "run"):
        done = read_results(path)
        if a.fresh:
            path.unlink(missing_ok=True)
            done = {}
        elif done and not a.resume:
            fail(f"this run already has results for {plural(len(done), 'mutant')}: --resume runs the rest, "
                 "--fresh starts over")
        cfg = kit.load_run(ws)[1]
        for name in list(cfg.get("sandboxes", {})):
            if name.startswith(("pool-", "check-", "rescore-", "parallel-", "baseline-", "combined-")):
                kit.drop_sandbox(ws, cfg, name)   # left by an interrupted run, maybe with a mutant in it
        todo = [mutant(db, mid) for mid in sample_ids(db) if mid not in done]
        jobs = a.jobs or bl["jobs"]
        timeout = a.timeout or bl["timeout"]
        print(f"Running {plural(len(todo), 'mutant')}" + (f" ({len(done)} done already)" if done else "")
              + f" in {plural(jobs, 'sandbox', 'sandboxes')}, {timeout:.0f} s each at most. " + estimate(len(todo), bl, jobs))
        if not todo:
            print("\n".join(score_lines(ws, db, load_state(ws))))
            return
        node_ok = node_checkable(cfg, todo)
        before = real_status(cfg)
        counts = {"killed": 0, "survived": 0, "timeout": 0, "error": 0}
        step = max(1, len(todo) // 10)

        def on_done(m, r):
            append_result(path, {"id": m["id"], **r})
            counts[r["status"]] += 1
            n = sum(counts.values())
            if n % step == 0 or n == len(todo):
                print(f"  {n} of {len(todo)}: {counts['killed']} killed, {counts['survived']} survived, "
                      f"{counts['timeout']} timed out, {counts['error']} not viable", flush=True)
        pool(cfg, ws, todo, jobs, lambda wk, m: run_mutant(wk.box(), cfg, m, timeout, node_ok), on_done, "pool",
             {"main": ((), None)})
    print("\n".join(score_lines(ws, db, load_state(ws))))
    print(f"Next: mutants.py slices -w {ws}")
    warn_if_touched(cfg, before)


def cmd_score(a) -> None:
    ws, _ = kit.load_run(a.workspace)
    db = load_mutants(ws)
    st = load_state(ws)
    print("\n".join(score_lines(ws, db, st)))
    results = read_results(ws / "results.jsonl")
    left = survivors(db, results, st)
    if a.survivors and left:
        print("Survivors:")
        for mid in left:
            m = mutant(db, mid)
            tag = f"  claimed equivalent: {st['claims'][mid]['reason']}" if mid in st["claims"] else ""
            print(f"  {mid:<6} {m['file']}:{m['line']}  {m['detail']}{tag}")


# ---------------------------------------------------------------- slices of survivors, and the agents' sandboxes

def cmd_slices(a) -> None:
    ws, _ = kit.load_run(a.workspace)
    db, st = load_mutants(ws), load_state(ws)
    left = survivors(db, read_results(ws / "results.jsonl"), st)
    taken = {mid: sid for sid, s in st["slices"].items() for mid in s["mutants"]}
    if a.again:
        pool_ids = [mid for mid in left if mid not in st["claims"]
                    and (mid not in taken or st["slices"][taken[mid]].get("patch"))]
    else:
        pool_ids = [mid for mid in left if mid not in taken]
    by_file: dict[str, list[dict]] = {}
    for mid in pool_ids:
        m = mutant(db, mid)
        by_file.setdefault(m["file"], []).append(m)
    size = max(5, min(15, a.size))
    chunks: list[list[dict]] = []
    for f in sorted(by_file):
        ms = sorted(by_file[f], key=lambda m: (m["line"], num(m["id"])))
        cur: list[dict] = []
        for i, m in enumerate(ms):
            rest_of_func = sum(1 for x in ms[i:] if x.get("func") == m.get("func"))
            new_func = cur and m.get("func") != cur[-1].get("func")
            if cur and (len(cur) >= size or (new_func and len(cur) >= 5 and len(cur) + rest_of_func > size)):
                chunks.append(cur)
                cur = []
            cur.append(m)
        if cur:
            chunks.append(cur)
    merged: list[list[dict]] = []
    for c in chunks:   # small slices from different files share a writer
        if merged and len(c) < 5 and len(merged[-1]) < 5 and len(merged[-1]) + len(c) <= size:
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)
    if a.limit:
        merged = merged[:a.limit]
    if not merged:
        print("No survivors left to slice." + ("" if a.again else " (--again includes ones a writer already tried.)"))
        return
    batches: list[set] = []
    made = []

    def save(st):
        for c in merged:
            files = sorted({m["file"] for m in c})
            b = next((i for i, used in enumerate(batches) if not used & set(files)), None)
            if b is None:
                batches.append(set())
                b = len(batches) - 1
            batches[b].update(files)
            sid = f"S{st['slice_next']}"
            st["slice_next"] += 1
            st["slices"][sid] = {"mutants": [m["id"] for m in c], "files": files, "batch": b + 1,
                                 "funcs": sorted({m["func"] for m in c if m.get("func")}), "made": kit.now(),
                                 "patch": None}
            made.append(sid)
        return st["slices"]
    slices = change_state(ws, save)
    for sid in made:
        s = slices[sid]
        funcs = f" in {', '.join(s['funcs'][:4])}" if s["funcs"] else ""
        print(f"{sid} (batch {s['batch']}): {', '.join(s['files'])}, {plural(len(s['mutants']), 'survivor')}{funcs}: "
              + " ".join(s["mutants"]))
    left_out = len(pool_ids) - sum(len(c) for c in merged)
    if left_out:
        print(f"{plural(left_out, 'survivor')} left for later slices (--limit).")
    if len(batches) > 1:
        print("Send each batch's writers together. Make the next batch's sandboxes only after this batch's tests are "
              "accepted: slices in different batches test the same files, and their sandboxes need those tests in.")


def combined_diff(cfg: dict, ws: Path, accepted: list[str]) -> str:
    if not accepted:
        return ""
    name = f"combined-{secrets.token_hex(2)}"
    box = kit.make_sandbox(cfg, ws, name)
    try:
        for pid in accepted:
            ok, msg = kit.apply_patch(box / "repo", kit.read_patch(ws / "patches" / f"{pid}.patch"))
            if not ok:
                fail(f"{pid} doesn't apply on top of the ones before it: {msg}")
        return kit.box_diff(box)
    finally:
        kit.drop_sandbox(ws, cfg, name)


def cmd_sandbox(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    st = load_state(ws)
    mids = []
    sid = a.slice.upper() if a.slice else None
    if sid:
        if sid not in st["slices"]:
            fail(f"no slice {a.slice}; run slices first")
        mids = list(st["slices"][sid]["mutants"])
    mids += [m for m in norm_ids(a.mutants) if m not in mids]
    db = load_mutants(ws) if mids else None
    for mid in mids:
        mutant(db, mid)
    accepted = [] if a.bare else list(st["accepted"])
    try:
        box = make_box(cfg, ws, a.name, accepted, network=True if a.net else None)
    except PatchError as e:
        fail(f"the accepted tests don't apply to a fresh copy: {e}")
    if mids:
        write_handout(box, db, mids)
    if a.patch:
        pf = ws / "patches" / f"{a.patch.upper()}.patch"
        if not pf.is_file():
            forget_box(ws, cfg, a.name)
            fail(f"no patch {a.patch} with changes in this run")
        ok, msg = kit.apply_patch(box / "repo", kit.read_patch(pf))
        if not ok:
            forget_box(ws, cfg, a.name)
            fail(f"{a.patch} doesn't apply on top of the accepted tests: {msg}")
    if a.combined:
        diff = combined_diff(cfg, ws, st["accepted"])
        kit.write_patch(box / "scratch" / "tests.diff", diff)

    def save(st):
        st["boxes"][a.name] = {"slice": sid, "mutants": mids, "accepted": accepted, "made": kit.now()}
        if sid:
            st["slices"][sid]["sent"] = a.name
    change_state(ws, save)
    print(f"Sandbox {a.name} is ready.")
    print(kit.sandbox_text(box, bool(cfg["network"] or a.net)))
    if accepted:
        print(f"  the accepted tests are in the copy already: {', '.join(accepted)}")
    if mids:
        print(f"  faults to catch: {box / 'scratch' / 'mutants' / 'mutants.md'} ({len(mids)}: {' '.join(mids)})")
        print(f"  put one in:      python ../scratch/mutants/mutant.py <id>   (and --undo), from the project copy")
    if a.combined:
        print(f"  the new tests as one diff: {box / 'scratch' / 'tests.diff'}")
    if a.patch:
        print(f"  {a.patch.upper()}'s changes are in the copy, uncommitted; collect takes them with yours")


TEST_DEF = re.compile(r"^\+\s*(?:async\s+def\s+test|def\s+test|func\s+Test|(?:it|test)(?:\.each\S*)?\s*\(|#\[(?:tokio::)?test\]|"
                      r"@Test\b|\[(?:Fact|Theory|Test|TestMethod|TestCase)\b|@test\b)", re.M)


def count_tests(patch: str) -> int:
    return len(TEST_DEF.findall(patch))


def agent_path(path) -> str:
    s = str(path or "").strip().replace("\\", "/")
    m = re.search(r"/repo/(.+)$", s)
    s = m.group(1) if m else s
    s = re.sub(r"(:\d+)+$", "", s)
    return s[2:] if s.startswith("./") else s


def deleted_files(patch: str) -> list[str]:
    out = []
    for block in re.split(r"(?m)^(?=diff --git )", patch):
        m = re.match(r"diff --git a/(.+?) b/(.+)\n", block)
        if m and re.search(r"(?m)^deleted file mode", block.split("\n@@", 1)[0]):
            out.append(m.group(2))
    return out


def tidy_writer_box(box: Path, cfg: dict, db: dict, mids: list[str]) -> list[str]:
    """Put back a fault the writer left in, and drop files a test run left behind."""
    notes, repo, base = [], box / "repo", kit.box_base(box)
    for f in kit.changed_files(kit.box_diff(box)):
        if is_test(f, cfg):
            continue
        p = repo / f
        if any(globmatch(f, j) for j in JUNK):
            if kit.git_ok(repo, "cat-file", "-e", f"{base}:{f}"):
                kit.git(repo, *kit.RAW, "checkout", "-q", base, "--", f)
            else:
                kit.rmtree(p)
            notes.append(f"removed {f}, left behind by a test run")
            continue
        orig = subprocess.run(["git", "-C", str(repo), *kit.RAW, "show", f"{base}:{f}"], capture_output=True)
        if orig.returncode or not p.is_file():
            continue
        now = p.read_bytes()
        for mid in mids:
            m = db["by_id"].get(mid)
            if m and m["file"] == f and mutate_bytes(orig.stdout, m) == now:
                p.write_bytes(orig.stdout)
                notes.append(f"{mid} was still in {f}; the line was put back")
                break
    return notes


def reply_path(cfg: dict, a) -> tuple[Path, str | None]:
    if bool(getattr(a, "reply", None)) == bool(a.sandbox):
        fail("give --sandbox <name> to read its reply.json, or --from <file>")
    if a.sandbox:
        return kit.find_sandbox(cfg, a.sandbox) / "scratch" / kit.REPLY, a.sandbox
    return Path(a.reply), None


def forget_box(ws: Path, cfg: dict, name: str) -> None:
    kit.drop_sandbox(ws, cfg, name)
    change_state(ws, lambda st: st["boxes"].pop(name, None))


def cmd_collect(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    box = kit.find_sandbox(cfg, a.sandbox)
    st = load_state(ws)
    info = st["boxes"].get(a.sandbox, {})
    reply = box / "scratch" / kit.REPLY
    data = kit.read_reply(reply) if reply.is_file() or not a.targets else {}
    tests = [t for t in kit.as_list(data.get("tests")) if isinstance(t, dict)]
    for t in tests:
        t["file"] = agent_path(t.get("file"))
    named = [x for t in tests for x in kit.as_list(t.get("targets"))] + kit.as_list(data.get("targets"))
    targets = norm_ids(a.targets) or [m for m in norm_ids(named) if m.startswith("M")] or list(info.get("mutants") or [])
    claims = []
    for e in kit.as_list(data.get("equivalent")):
        ids = norm_ids(e.get("mutant") or e.get("id")) if isinstance(e, dict) else norm_ids(e)
        if ids:
            reason = str(e.get("reason") or "").strip() if isinstance(e, dict) else ""
            claims.append({"mutant": ids[0], "reason": reason or "no reason given"})
    db = load_mutants(ws)
    notes = tidy_writer_box(box, cfg, db, list(dict.fromkeys(list(info.get("mutants") or []) + targets)))
    patch = kit.box_diff(box)

    def save(st):
        pid = f"P{st['patch_next']}"
        st["patch_next"] += 1
        st["patches"][pid] = {"id": pid, "sandbox": a.sandbox, "slice": info.get("slice"), "tests": tests,
                              "targets": targets, "files": kit.changed_files(patch),
                              "notes": notes + ([str(data["notes"])] if data.get("notes") else []),
                              "status": "collected" if patch.strip() else "empty", "collected": kit.now(),
                              "count": count_tests(patch) or len(tests)}
        for c in claims:
            st["claims"][c["mutant"]] = {"reason": c["reason"], "by": pid}
        if info.get("slice") in st["slices"]:
            st["slices"][info["slice"]]["patch"] = pid
        return pid
    pid = change_state(ws, save)
    if patch.strip():
        kit.write_patch(ws / "patches" / f"{pid}.patch", patch)
    if not a.keep:
        forget_box(ws, cfg, a.sandbox)
    files = kit.changed_files(patch)
    print(f"{pid} from {a.sandbox}: {plural(count_tests(patch) or len(tests), 'test')} in "
          f"{', '.join(files) or 'no files'}; targets {', '.join(targets) or 'none'}"
          + (f"; {len(claims)} claimed equivalent ({', '.join(c['mutant'] for c in claims)})" if claims else "") + ".")
    for n in notes:
        print(f"note: {n}")
    if not patch.strip():
        print("No test changes in the sandbox." + (" The equivalence claims are recorded." if claims else ""))
        sys.exit(0 if claims else 1)
    if a.no_check:
        print(f"Not checked yet: mutants.py check-test -w {ws} {pid}")
        return
    verdict = check_patch(ws, cfg, pid, jobs=a.jobs)
    if verdict == "PASS" and a.accept:
        if not accept_patch(ws, pid):
            sys.exit(1)
    sys.exit(0 if verdict == "PASS" else 1)


# ---------------------------------------------------------------- the core check

def check_patch(ws: Path, cfg: dict, pid: str, targets=None, jobs: int | None = None) -> str:
    """PASS only when the new tests pass 3 of 3 runs on the real code and kill a targeted survivor.

    A kill counts when the suite fails twice with the mutant in and the tests added, and the same
    mutant still passes with only the tests accepted before. Flaky passes, flaky kills and mutants
    that fail without the new tests don't count."""
    st = load_state(ws)
    p = st["patches"].get(pid.upper())
    if p is None:
        fail(f"no patch {pid}; collect makes them")
    pid = p["id"]
    if pid in st["accepted"]:
        fail(f"{pid} is accepted already")
    bl, db = need_baseline(ws), load_mutants(ws)
    pfile = ws / "patches" / f"{pid}.patch"
    patch = kit.read_patch(pfile) if pfile.is_file() else ""
    targets = norm_ids(targets) or list(p.get("targets") or [])
    accepted = list(st["accepted"])
    files = kit.changed_files(patch)

    def finish(verdict, reason, **extra):
        rec = {"verdict": verdict, "reason": reason, "accepted": accepted, "targets": targets, "at": kit.now(), **extra}

        def save(st):
            st["patches"][pid]["check"] = rec
            st["patches"][pid]["status"] = "passed" if verdict == "PASS" else "failed"
            st["flaky"] += extra.get("flaky_runs", [])
            st["unstable"].update(extra.get("unstable", {}))
        change_state(ws, save)
        print(f"{verdict}: {reason}")
        return verdict

    print(f"Checking {pid}: {', '.join(files) or 'no files'}; targets {', '.join(targets) or 'none named'}"
          + (f"; on top of the accepted {', '.join(accepted)}" if accepted else "") + ".")
    if not patch.strip():
        return finish("FAIL", "the patch is empty.")
    others = [f for f in files if not is_test(f, cfg)]
    if others:
        return finish("FAIL", f"it changes files that aren't tests: {', '.join(others)}. New tests may touch only "
                              "test files, so nothing was run.")
    gone = deleted_files(patch)
    if gone:
        return finish("FAIL", f"it deletes test files: {', '.join(gone)}. Nothing was run.")
    results = read_results(ws / "results.jsonl")
    surv = survivors(db, results, st)
    skipped = {}
    for t in targets:
        if t not in db["by_id"]:
            skipped[t] = "no such mutant"
        elif t in st["kills"]:
            skipped[t] = f"already killed by {st['kills'][t]}"
        elif t not in surv:
            skipped[t] = f"not a survivor ({before_status(results, st)(t)})"
    live = [t for t in targets if t not in skipped]
    for t, why in skipped.items():
        print(f"  {t}: {why}, so it can't count")
    before = real_status(cfg)
    with busy(ws, f"check-test {pid}"):
        name = f"check-first-{secrets.token_hex(2)}"
        try:
            first_box = Box(make_box(cfg, ws, name, accepted + [pid]))
        except PatchError as e:
            return finish("FAIL", f"it doesn't apply on top of the accepted tests ({', '.join(accepted) or 'none'}): "
                                  f"{e.msg}. Make a new sandbox (it has them in) and write the tests there.")
        try:
            real = first_box.runs(cfg["test_cmd"], 3, max(60.0, 10 * bl["median"]), cfg.get("cwd"))
            bad = [r for r in real["runs"] if kit.failed(r)]
            if bad:
                print(kit.summary(real))
                names = failing_names(bad[0]["out"])
                named = f" ({', '.join(names)})" if names else ""
                if len(bad) < len(real["runs"]):
                    return finish("FAIL", f"the new tests are flaky: the suite failed {len(bad)} of {len(real['runs'])} "
                                          f"runs on the real code{named}. A test has to pass every time.",
                                  flaky_runs=[{"where": f"{pid}'s tests, on the real code",
                                               "runs": f"{len(bad)} of {len(real['runs'])} runs failed",
                                               "names": names}])
                return finish("FAIL", f"the suite fails on the real code with the new tests in{named}. They have to "
                                      "pass on the code as it is.")
            med = statistics.median(r["secs"] for r in real["runs"])
            mt = round(max(bl["timeout"], 3 * med + 5), 1)
            print(f"  the real code: passed 3 of 3 runs (median {med:.1f} s)")
            order = live + sorted([s for s in surv if s not in live], key=num)
            node_ok = node_checkable(cfg, [mutant(db, x) for x in order])
            out: dict[str, dict] = {}

            def work(wk, mid):
                m, box = mutant(db, mid), wk.box("with")
                first = run_mutant(box, cfg, m, mt, node_ok)
                if first["status"] not in ("killed", "timeout"):
                    return {"kill": False, "why": first["status"]}
                second = run_mutant(box, cfg, m, mt, node_ok)
                if second["status"] not in ("killed", "timeout"):
                    return {"kill": False, "why": "flaky", "detail": f"the suite failed once, then {second['status']}"}
                # The control: the same mutant with the new tests taken out must still pass, so the
                # kill is theirs. run_mutant's tidy puts the new tests back afterwards.
                ok, msg = box.unapply(patch)
                if not ok:
                    box.tidy()
                    return {"kill": False, "why": "unstable", "detail": f"the new tests couldn't be taken out: {msg}"}
                control = run_mutant(box, cfg, m, mt, node_ok)
                box.tidy()
                if control["status"] != "survived":
                    return {"kill": False, "why": "unstable",
                            "detail": f"without the new tests it {control['status']} too, so it isn't a steady survivor"}
                return {"kill": True, "timeout": "timeout" in (first["status"], second["status"])}
            # Another sandbox takes a few seconds to make; it only pays off with a few mutants each.
            workers = min(jobs or bl["jobs"], max(1, len(order) // 3))
            pool(cfg, ws, order, workers, work, lambda mid, r: out.__setitem__(mid, r), "check",
                 {"with": (accepted + [pid], None)}, preset={"with": first_box})
        finally:
            kit.drop_sandbox(ws, cfg, name)
    warn_if_touched(cfg, before)
    killed = [mid for mid in order if out[mid]["kill"]]
    hit = [t for t in live if t in killed]
    bonus = [k for k in killed if k not in live]
    missed = [t for t in live if t not in killed]
    flaky = [mid for mid in order if out[mid].get("why") == "flaky"]
    unstable = {mid: out[mid]["detail"] for mid in order if out[mid].get("why") == "unstable"}
    for mid in flaky:
        print(f"  {mid}: {out[mid]['detail']}; not counted")
    for mid, why in unstable.items():
        print(f"  {mid}: {why}; not counted")
    extra = {"killed": killed, "targets_killed": hit, "bonus": bonus, "missed": missed, "flaky_kills": flaky,
             "unstable": unstable, "median": round(med, 2), "skipped": skipped}
    if flaky:
        extra["flaky_runs"] = [{"where": f"{pid}'s tests, with {', '.join(flaky)} in",
                                "runs": "failed once and passed once on the same mutant", "names": []}]
    tail = (f"; it also kills {', '.join(bonus)}" if bonus else "") + \
           (f". Not killed: {', '.join(missed)}" if missed else "")
    if hit:
        return finish("PASS", f"{pid}'s tests pass 3 of 3 runs on the real code and kill {len(hit)} of its "
                              f"{len(live)} targets ({', '.join(hit)}){tail}.", **extra)
    if not live:
        return finish("FAIL", "none of its targets is a survivor, so it proves nothing new" + tail + ".", **extra)
    return finish("FAIL", f"the tests pass on the real code but kill none of their targets ({', '.join(live)})"
                          + (f"; they kill {', '.join(bonus)}" if bonus else "")
                          + ". Each target needs a test that fails when that line is changed.", **extra)


def cmd_check_test(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    pid = a.patch_id
    if a.patch:
        patch = kit.read_patch(Path(a.patch))

        def save(st):
            nid = f"P{st['patch_next']}"
            st["patch_next"] += 1
            st["patches"][nid] = {"id": nid, "sandbox": None, "slice": None, "tests": [], "targets": norm_ids(a.mutants),
                                  "files": kit.changed_files(patch), "notes": [f"from {a.patch}"], "status": "collected",
                                  "collected": kit.now(), "count": count_tests(patch)}
            return nid
        pid = change_state(ws, save)
        kit.write_patch(ws / "patches" / f"{pid}.patch", patch)
        print(f"Recorded {a.patch} as {pid}.")
    if not pid:
        fail("give a patch id from collect (like P2), or --patch <file> with --mutants")
    verdict = check_patch(ws, cfg, pid, a.mutants, a.jobs)
    if verdict == "PASS" and a.accept and not accept_patch(ws, pid.upper() if not a.patch else pid):
        sys.exit(1)
    sys.exit(0 if verdict == "PASS" else 1)


def accept_patch(ws: Path, pid: str) -> bool:
    def do(st):
        p = st["patches"].get(pid)
        if not p:
            return f"no patch {pid}"
        if pid in st["accepted"]:
            return f"{pid} is accepted already"
        c = p.get("check") or {}
        if c.get("verdict") != "PASS":
            return f"{pid} hasn't passed check-test" + (f" (its last check: {c['reason']})" if c else "")
        if c.get("accepted") != st["accepted"]:
            return (f"{pid} was checked on top of {', '.join(c['accepted']) or 'no accepted tests'}, but the accepted "
                    f"list is now {', '.join(st['accepted']) or 'empty'}. Run check-test {pid} again, so it's checked "
                    "with them, then accept it.")
        st["accepted"].append(pid)
        p["status"] = "accepted"
        for mid in c["killed"]:
            st["kills"].setdefault(mid, pid)
        return None
    err = change_state(ws, do)
    if err:
        print(f"FAIL: {err}")
        return False
    st = load_state(ws)
    db = load_mutants(ws)
    left = survivors(db, read_results(ws / "results.jsonl"), st)
    mine = [m for m, p in st["kills"].items() if p == pid]
    print(f"Accepted {pid}: it kills {plural(len(mine), 'survivor')} ({', '.join(sorted(mine, key=num))}). "
          f"Accepted so far: {', '.join(st['accepted'])}. Survivors left: {len(left)}.")
    return True


def cmd_accept(a) -> None:
    ws, _ = kit.load_run(a.workspace)
    ok = True
    for pid in norm_ids(a.ids):
        ok = accept_patch(ws, pid) and ok
    sys.exit(0 if ok else 1)


def cmd_drop(a) -> None:
    ws, _ = kit.load_run(a.workspace)
    pid = norm_ids([a.id])[0] if norm_ids([a.id]) else a.id

    def do(st):
        if pid not in st["accepted"]:
            return None
        i = st["accepted"].index(pid)
        later = st["accepted"][i + 1:]
        st["accepted"] = st["accepted"][:i]
        for q in [pid] + later:
            st["patches"][q]["status"] = "dropped" if q == pid else "needs recheck"
            if st["patches"][q].get("check"):
                st["patches"][q]["check"]["verdict"] = "STALE"
        st["kills"] = {m: p for m, p in st["kills"].items() if p not in [pid] + later}
        st["dropped"].append({"patch": pid, "reason": a.reason or "", "at": kit.now()})
        return later
    later = change_state(ws, do)
    if later is None:
        fail(f"{pid} isn't accepted")
    print(f"Dropped {pid}" + (f" ({a.reason})" if a.reason else "") + ".")
    if later:
        print(f"{', '.join(later)} were checked on top of it, so they're out too until they pass again: run check-test "
              f"and accept on each, in order ({', '.join(later)}).")


def cmd_equivalent(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    db = load_mutants(ws)
    mid = mutant(db, a.id)["id"]
    path, box = reply_path(cfg, a)
    data = kit.read_reply(path)
    v = str(data.get("verdict") or "").strip().lower()
    if v not in ("equivalent", "different"):
        fail(f"a judge's verdict is equivalent or different, not {data.get('verdict')!r}")
    rec = {"verdict": v, "reason": str(data.get("reason") or ""), "input": str(data.get("input") or ""), "at": kit.now()}
    change_state(ws, lambda st: st["judges"].__setitem__(mid, rec))
    if box:
        forget_box(ws, cfg, box)
    print(f"{mid}: the judge says {v}" + (f": {rec['reason']}" if rec["reason"] else "")
          + (f" (an input that tells them apart: {rec['input']})" if v == "different" and rec["input"] else "") + ".")
    if v == "equivalent":
        print("It's left out of the score from now on. This is the judge's reasoning; no script can prove equivalence.")


def cmd_review(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    path, box = reply_path(cfg, a)
    data = kit.read_reply(path)
    issues = [i for i in kit.as_list(data.get("issues")) if isinstance(i, dict)]
    for i in issues:
        i["file"] = agent_path(i.get("file"))
    rec = {"verdict": str(data.get("verdict") or ("problems" if issues else "ok")), "issues": issues,
           "accepted": load_state(ws)["accepted"], "at": kit.now()}
    change_state(ws, lambda st: st["reviews"].append(rec))
    if box:
        forget_box(ws, cfg, box)
    print(f"Review: {rec['verdict']}, {plural(len(issues), 'issue')}.")
    for n, i in enumerate(issues, 1):
        print(f"  {n}. {i.get('file') or '?'}{'::' + str(i['test']) if i.get('test') else ''} "
              f"[{i.get('kind') or 'other'}]: {i.get('problem') or ''}" + (f" Fix: {i['fix']}" if i.get("fix") else ""))


def cmd_combined(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    diff = combined_diff(cfg, ws, load_state(ws)["accepted"])
    if a.out:
        kit.write_patch(Path(a.out), diff)
        print(f"Wrote {a.out} ({plural(len(kit.changed_files(diff)), 'file')}).")
    else:
        sys.stdout.buffer.write(diff.encode("utf-8", "surrogateescape"))


# ---------------------------------------------------------------- landing the tests, and the clean after

def next_branch(top: Path) -> str:
    base = f"killer-tests/{kit.today()}"
    name, n = base, 2
    while kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{name}"):
        name, n = f"{base}-{n}", n + 1
    return name


def dirty_paths(top: Path) -> set[str]:
    out = set()
    for line in kit.git_status(top).splitlines():
        path = line[3:].strip().strip('"')
        out.update(p.strip().strip('"') for p in path.split(" -> "))
    return out


def commit_message(st: dict, db: dict, pids: list[str], files: list[str]) -> tuple[str, str]:
    kills = [mid for mid, p in st["kills"].items() if p in pids]
    names = sorted({mutant(db, mid)["file"].rsplit("/", 1)[-1] for mid in kills})
    if names:
        subject = "Add tests for " + ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    else:
        subject = f"Add tests in {files[0]}"
    lines = []
    for pid in pids:
        for t in st["patches"][pid].get("tests") or []:
            tf = t.get("file") or ""
            if len(files) == 1 and tf and not (files[0].endswith(tf) or tf.endswith(files[0])):
                continue
            name, checks = str(t.get("name") or "").strip(), str(t.get("checks") or "").strip()
            if name or checks:
                lines.append(f"- {name}: {checks}" if name and checks else f"- {name or checks}")
    body = "\n".join(dict.fromkeys(lines[:30]))
    body += ("\n\n" if body else "") + "Each test passes on the current code and fails when the code it covers changes."
    return subject, body


def apply_all(folder: Path, patches: list[tuple[str, str]]) -> None:
    applied = []
    for pid, patch in patches:
        ok, msg = kit.apply_patch(folder, patch)
        if not ok:
            for q in reversed(applied):
                subprocess.run(["git", "-C", str(folder), *kit.RAW, "apply", "-R", "--binary", "-"],
                               input=q.encode("utf-8", "surrogateescape"), capture_output=True)
            fail(f"{pid} doesn't apply in {folder}: {msg}. Nothing was changed. If you edited those test files since "
                 "the run started, commit or put those edits aside, then land again.")
        applied.append(patch)


def cmd_land(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    st, bl, db = load_state(ws), need_baseline(ws), load_mutants(ws)
    accepted = st["accepted"]
    if not accepted:
        fail("nothing accepted to land")
    top = Path(cfg["repo"])
    patches = [(pid, kit.read_patch(ws / "patches" / f"{pid}.patch")) for pid in accepted]
    touched = sorted({f for _, p in patches for f in kit.changed_files(p)})
    if any(not is_test(f, cfg) for f in touched):
        fail("refusing: an accepted patch changes a file that isn't a test")
    timeout = max(60.0, 10 * bl["median"])
    cmd, cwd = cfg["test_cmd"], cfg.get("cwd")
    if cfg.get("after") == "report" and not (a.uncommitted or a.branch):
        fail("this run is set to report only (init --after report), so nothing lands. Pass --uncommitted or "
             "--branch <name> if the user now wants the tests")
    if a.uncommitted or (cfg.get("after") == "uncommitted" and not a.branch):
        apply_all(top, patches)
        res = kit.run_fresh(cfg, ws, cmd, cwd=cwd, timeout=timeout, hint="land")
        ok = bool(res["runs"]) and not kit.failed(res["runs"][0])
        change_state(ws, lambda st: st.__setitem__("land", {"mode": "uncommitted", "files": touched, "ok": ok,
                                                            "branch": None, "commits": [], "at": kit.now()}))
        print(f"Put the new tests in your project, uncommitted: {', '.join(touched)}.")
        print(f"The full suite in a fresh copy of the project as it is now: {'passed' if ok else 'FAILED'}.")
        if not ok:
            print(kit.summary(res))
        sys.exit(0 if ok else 1)
    if not cfg["git"] or not kit.has_head(top):
        fail("landing on a branch needs a git repository with a commit; land --uncommitted puts the tests in the folder")
    current = kit.git(top, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip()
    branch = a.branch or cfg.get("branch_name") or next_branch(top)
    exists = kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")
    groups = ([(kit.changed_files(p), [pid]) for pid, p in patches] if a.per == "patch"
              else [([f], [pid for pid, p in patches if f in kit.changed_files(p)]) for f in touched])
    commits = []

    def commit_all(folder: Path, in_place: bool) -> None:
        if a.per != "patch":
            apply_all(folder, patches)
        for files, pids in groups:
            if a.per == "patch":
                apply_all(folder, [(pid, p) for pid, p in patches if pid in pids])
            subject, body = commit_message(st, db, pids, files)
            kit.git(folder, "add", "--", *files)
            kit.git(folder, "commit", "-q", "-m", subject, "-m", body, *(["--", *files] if in_place else []))
            commits.append(f"{kit.git(folder, 'rev-parse', '--short', 'HEAD').strip()} {subject}")
    if branch == current:
        mine = dirty_paths(top) & set(touched)
        if mine:
            fail(f"these test files have uncommitted changes of yours: {', '.join(sorted(mine))}. Commit them first, "
                 "so the new tests' commits hold only the new tests")
        commit_all(top, True)
    else:
        wt = Path(cfg["sandboxes_home"]) / cfg["run_id"] / f"land-{secrets.token_hex(2)}"
        wt.parent.mkdir(parents=True, exist_ok=True)
        kit.git(top, "worktree", "add", "-q", *([] if exists else ["-b", branch]), str(wt), branch if exists else "HEAD")
        try:
            commit_all(wt, False)
        finally:
            kit.git(top, "worktree", "remove", "--force", str(wt), check=False)
            kit.git(top, "worktree", "prune", check=False)
            kit.rmtree(wt)
            if not exists and not commits:
                kit.git(top, "branch", "-D", branch, check=False)
    res = kit.run_fresh(cfg, ws, cmd, rev=branch, cwd=cwd, timeout=timeout, hint="land")
    ok = bool(res["runs"]) and not kit.failed(res["runs"][0])
    change_state(ws, lambda st: st.__setitem__("land", {"mode": "branch", "branch": branch, "commits": commits,
                                                        "ok": ok, "per": a.per, "at": kit.now()}))
    print(f"Landed on {branch}: {plural(len(commits), 'commit')}, one per {'patch' if a.per == 'patch' else 'test file'}"
          + (" (your current branch)" if branch == current else f"; you're still on {current or 'a detached HEAD'}") + ".")
    for c in commits:
        print(f"  {c}")
    print(f"The full suite in a fresh copy of {branch}: {'passed' if ok else 'FAILED'}.")
    if not ok:
        print(kit.summary(res))
        if dirty_paths(top):
            print("Your project has uncommitted changes the branch doesn't have; if the tests need them, commit them "
                  "and land again on a new branch.")
        sys.exit(1)
    print(f"Next: mutants.py rescore -w {ws}")


def cmd_rescore(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    bl, db, st = need_baseline(ws), load_mutants(ws), load_state(ws)
    results = read_results(ws / "results.jsonl")
    land = st.get("land") or {}
    rev = a.rev or (land.get("branch") if land.get("mode") == "branch" else None)
    patches = [] if rev or land.get("mode") == "uncommitted" else list(st["accepted"])
    ids = [mid for mid in sample_ids(db) if (results.get(mid) or {}).get("status") in VIABLE]
    path, old = ws / "rescore.jsonl", kit.load_json(ws / "rescore.json", None)
    before = real_status(cfg)
    with busy(ws, "rescore"):
        if a.resume and old and (old.get("rev"), old.get("patches")) != (rev, patches):
            fail("the last rescore ran against other tests; run it again without --resume")
        done = read_results(path) if a.resume else {}
        if not a.resume:
            path.unlink(missing_ok=True)
        name = f"rescore-first-{secrets.token_hex(2)}"
        first_box = Box(make_box(cfg, ws, name, patches, rev))
        try:
            real = first_box.runs(cfg["test_cmd"], 1, max(60.0, 10 * bl["median"]), cfg.get("cwd"))
            if kit.failed(real["runs"][0]):
                print(kit.summary(real))
                print("FAIL: the suite fails on the final code, so there's nothing to rescore. Find out which test broke.")
                sys.exit(1)
            timeout = round(max(bl["timeout"], 3 * real["runs"][0]["secs"] + 5), 1)
            kit.save_json(ws / "rescore.json", {"rev": rev, "patches": patches, "ids": ids, "timeout": timeout,
                                                "at": kit.now()})
            todo = [mutant(db, mid) for mid in ids if mid not in done]
            target = f"branch {rev}" if rev else ("your project with the new tests in" if not patches
                                                  else f"the accepted tests ({', '.join(patches)})")
            print(f"Rescoring {plural(len(todo), 'mutant')} against {target}. " + estimate(len(todo), bl))
            node_ok = node_checkable(cfg, todo)
            pool(cfg, ws, todo, a.jobs or bl["jobs"], lambda wk, m: run_mutant(wk.box(), cfg, m, timeout, node_ok),
                 lambda m, r: append_result(path, {"id": m["id"], **r}), "rescore", {"main": (patches, rev)},
                 preset={"main": first_box})
        finally:
            kit.drop_sandbox(ws, cfg, name)
    print("\n".join(score_lines(ws, db, st)))
    res = read_results(path)
    lost = [mid for mid in ids if results[mid]["status"] in ("killed", "timeout")
            and (res.get(mid) or {}).get("status") == "survived"]
    if lost:
        print(f"warning: {plural(len(lost), 'mutant')} the old tests killed now survive ({', '.join(lost)}): a new "
              "patch weakened an existing test. Find it with show, and drop the patch.")
    warn_if_touched(cfg, before)


def cmd_decide(a) -> None:
    ws, _ = kit.load_run(a.workspace)
    n = change_state(ws, lambda st: (st["decisions"].append(a.text), len(st["decisions"]))[1])
    print(f"Decision {n} recorded: {a.text}")


def cmd_wait(a) -> None:
    ws, _ = kit.load_run(a.workspace)
    n = change_state(ws, lambda st: (st["waiting"].append({"text": a.text, "command": a.command}), len(st["waiting"]))[1])
    print(f"Waiting item {n} recorded: {a.text}" + (f" ({a.command})" if a.command else ""))


def cmd_status(a) -> None:
    """The real project's git status against the one recorded at init (or the last --mark)."""
    ws, cfg = kit.load_run(a.workspace)
    if not cfg.get("git"):
        print("The project isn't a git repository, so there's no git status to compare.")
        return
    now = kit.git_status(Path(cfg["repo"]))
    ref = load_state(ws).get("git_status")
    if a.mark or ref is None:
        change_state(ws, lambda st: st.__setitem__("git_status", now))
        print("Recorded the project's git status as the reference." if ref is None or now != ref
              else "The project's git status is unchanged.")
        return
    if now == ref:
        print("The project's git status is the same as the reference: nothing changed it.")
        return
    old, new = set(ref.splitlines()), set(now.splitlines())
    print("CHANGED: the project's git status differs from the reference.")
    for line in sorted(new - old):
        print(f"  now:  {line}")
    for line in sorted(old - new):
        print(f"  gone: {line}")
    print("If you didn't make these changes, an agent or a test did: undo them, and say so in the report. "
          "If you did, run status --mark.")
    sys.exit(1)


def cmd_clean(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    n = kit.clean_sandboxes(ws, cfg)
    change_state(ws, lambda st: st["boxes"].clear())
    print(f"Deleted {plural(n, 'sandbox', 'sandboxes')}.")


# ---------------------------------------------------------------- report

def cell(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def code(s: str) -> str:
    s = s.strip()
    return f"`` {s} ``" if "`" in s else f"`{s}`"


def cmd_report(a) -> None:
    ws, cfg = kit.load_run(a.workspace)
    st = load_state(ws)
    bl = kit.load_json(ws / "baseline.json", None)
    db = load_mutants(ws) if (ws / "mutants.json").is_file() else None
    results = read_results(ws / "results.jsonl")
    rescored = rescore_results(ws, db) if db else None
    ids = sample_ids(db) if db else []
    bf, af = before_status(results, st), after_status(results, st, rescored)
    b, aft = tally(ids, bf), tally(ids, af)
    sb, sa = ratio(b), ratio(aft)
    accepted = st["accepted"]
    land = st.get("land") or {}
    tests_added = sum(st["patches"][p].get("count") or 0 for p in accepted)
    new_kills = [mid for mid in ids if bf(mid) == "survived" and af(mid) in ("killed", "timeout")]
    left = [mid for mid in ids if af(mid) == "survived"]
    measure = (cfg.get("goal") or "close") == "measure"
    if not bl or not bl.get("ok"):
        status = "blocked"
        headline = ("The suite isn't clean (" + (bl or {}).get("verdict", "no baseline yet") + "), so nothing was "
                    "mutated: mutation results mean nothing on a red or flaky suite")
    elif not db or not ids:
        status, headline = "partial", "The baseline passed, but no mutants were made or run yet"
    else:
        status = "done"
        if b["not run"] or (accepted and not rescored) or (land and not land.get("ok", True)):
            status = "partial"
        if accepted:
            headline = (f"Mutation score {pct(sb)} -> {pct(sa)} on {len(ids)} sampled mutants: "
                        f"{plural(tests_added, 'test')} added, killing {plural(len(new_kills), 'survivor')}"
                        + ("" if rescored else " (from the checks; not rescored yet)"))
        else:
            headline = f"Mutation score {pct(sb)} on {len(ids)} sampled mutants, {plural(len(left), 'survivor')} left"
            if not measure and b["not run"] == 0 and left:
                status = "partial"
    L = [f"# Test report: {cfg.get('target') or Path(cfg['repo']).name}", "",
         f"**Project:** `{cfg['repo']}`" + (f" at `{(cfg.get('head') or '')[:10]}`" if cfg.get("head") else ""),
         f"**Scope:** {kit.describe_scope(cfg['scope'])}",
         f"**Test command:** {code(' '.join(cfg['test_cmd']))}" + (f" from `{cfg['cwd']}`" if cfg.get("cwd") else "")]
    if bl:
        runs = bl.get("runs") or []
        passed = sum(1 for r in runs if r["code"] == 0 and not r["timed_out"])
        L.append(f"**Baseline:** {passed} of {len(runs)} runs passed in a fresh copy"
                 + (f", median {bl['median']:.1f} s" if bl.get("ok") else f" ({bl.get('verdict')})") + ".")
    L += ["", f"**Result:** {headline}.", ""]
    if db and ids:
        files = sorted({mutant(db, m)["file"] for m in ids})
        L += ["## Score", "", "| File | Mutants | Before | After |", "|---|---|---|---|"]
        for f in files:
            fid = [m for m in ids if mutant(db, m)["file"] == f]
            L.append(f"| `{cell(f)}` | {len(fid)} | {pct(ratio(tally(fid, bf)))} | "
                     f"{pct(ratio(tally(fid, af))) if accepted else '-'} |")
        L.append(f"| **All** | {len(ids)} | **{pct(sb)}** | **{pct(sa) if accepted else '-'}** |")
        L.append("")
        L.append(f"The score is the share of viable mutants the tests kill. Made {len(db['mutants'])} mutants; "
                 + (f"ran a sample of {len(ids)}, spread across files and operators (seed {db['seed']})."
                    if len(ids) < len(db["mutants"]) else f"ran all {len(ids)}.")
                 + (f" {plural(b['timeout'], 'mutant')} timed out, which counts as killed." if b["timeout"] else "")
                 + (f" {plural(b['error'], 'mutant')} broke the build, so they're left out." if b["error"] else "")
                 + (f" {plural(b['equivalent'], 'mutant')} a judge called equivalent are left out."
                    if b["equivalent"] else "")
                 + (f" {plural(b['not run'], 'mutant')} didn't run." if b["not run"] else ""))
        if accepted:
            L.append("After: " + ("the same sample rerun against the final tests." if rescored else
                                  "from each patch's own check; run rescore for a clean number."))
        L.append("")
    if accepted:
        L += ["## Tests added", ""]
        for pid in accepted:
            p = st["patches"][pid]
            c = p.get("check") or {}
            L += [f"### {pid}: {', '.join(f'`{f}`' for f in p['files'])} ({plural(p.get('count') or 0, 'test')})", ""]
            for t in p.get("tests") or []:
                name, checks = cell(t.get("name")), cell(t.get("checks"))
                if name or checks:
                    L.append(f"- `{name}`: {checks}" if name else f"- {checks}")
            mine = sorted([m for m, q in st["kills"].items() if q == pid], key=num)
            if mine:
                L.append("")
                L.append("Kills " + ", ".join(f"{m} ({mutant(db, m)['file']}:{mutant(db, m)['line']})" for m in mine) + ".")
            L += ["", f"Checked: passed 3 of 3 runs on the real code; each mutant it kills failed twice with these tests "
                      f"and passed without them" + (f" (median {c['median']} s)" if c.get("median") is not None else "")
                      + ".", ""]
    if db and left:
        L += ["## Survivors left", "", "Each line is a change to the code that every test still passes.", ""]
        targeted = {t for p in st["patches"].values() for t in (p.get("targets") or [])}
        by_file: dict[str, list[str]] = {}
        for mid in left:
            by_file.setdefault(mutant(db, mid)["file"], []).append(mid)
        for f in sorted(by_file):
            L += [f"### `{f}`", ""]
            for mid in by_file[f]:
                m = mutant(db, mid)
                if mid in st["claims"]:
                    tag = f"Claimed equivalent: {st['claims'][mid]['reason']}"
                    j = st["judges"].get(mid)
                    if j and j["verdict"] == "different":
                        tag += "; a judge disagreed" + (f" (input: {j['input']})" if j.get("input") else "")
                elif mid in targeted:
                    tag = "Test written but didn't kill it"
                else:
                    tag = "No test written"
                odd = st["unstable"].get(mid)
                L.append(f"- **{mid}** line {m['line']}: {code(m['original'])} → {code(m['mutated'])}. {tag}."
                         + (f" Unstable: {odd}." if odd else ""))
            L.append("")
    eq = [mid for mid in ids if bf(mid) == "equivalent"]
    if eq:
        L += ["## Left out as equivalent", "", "A judge found no input that tells these apart from the real code. "
              "That's its reasoning; the script can't check it.", ""]
        L += [f"- **{mid}** `{mutant(db, mid)['file']}`:{mutant(db, mid)['line']}: {st['judges'][mid]['reason']}"
              for mid in eq] + [""]
    if st["flaky"]:
        L += ["## Flaky tests found", ""]
        L += [f"- {x['where']}: {x['runs']}" + (f" ({', '.join(x['names'])})" if x.get("names") else "")
              for x in st["flaky"]] + [""]
    if st["reviews"] or st["dropped"]:
        L += ["## Test review", ""]
        for r in st["reviews"]:
            L.append(f"- The reviewer read {', '.join(r['accepted']) or 'the tests'}: {r['verdict']}, "
                     f"{plural(len(r['issues']), 'issue')}.")
            L += [f"  - `{cell(i.get('file'))}`{('::' + cell(i['test'])) if i.get('test') else ''} "
                  f"({cell(i.get('kind') or 'other')}): {cell(i.get('problem'))}" for i in r["issues"]]
        L += [f"- Dropped {d['patch']}" + (f" ({d['reason']})" if d.get("reason") else "")
              + ("; checked again and accepted later" if d["patch"] in accepted else "") for d in st["dropped"]]
        L.append("")
    if db:
        reasons: dict[str, list[str]] = {}
        for rel, why in db["skipped"].items():
            reasons.setdefault(why, []).append(rel)
        L += ["## What wasn't mutated", ""]
        for why, rels in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
            L.append(f"- {why[0].upper() + why[1:]} ({len(rels)}): " + ", ".join(f"`{r}`" for r in rels[:12])
                     + (f" and {len(rels) - 12} more" if len(rels) > 12 else ""))
        L += ["- Inside strings and comments, nothing is ever mutated. Python's `if __name__ == \"__main__\":` and "
              "`TYPE_CHECKING` blocks, logging and print calls, and module-level UPPER_CASE constants are left alone.", ""]
    if st["decisions"]:
        L += ["## Decisions to check", ""] + [f"- {d}" for d in st["decisions"]] + [""]
    if st["waiting"]:
        L += ["## Waiting for your OK", ""] + [f"- {w['text']}" + (f": `{w['command']}`" if w.get("command") else "")
                                               for w in st["waiting"]] + [""]
    L += ["## Run details", "", f"- Run folder: `{ws}`"]
    if bl and bl.get("ok"):
        L.append(f"- {plural(bl['jobs'], 'sandbox', 'sandboxes')} at a time; {bl['timeout']:.0f} s per mutant before "
                 "it counts as a timeout")
    if land.get("mode") == "branch":
        L.append(f"- The new tests are on the local branch `{land['branch']}` ({plural(len(land['commits']), 'commit')}"
                 f"), nothing pushed. The full suite there: {'passed' if land.get('ok') else 'FAILED'}.")
        L += [f"  - `{c}`" for c in land["commits"]]
    elif land.get("mode") == "uncommitted":
        L.append(f"- The new tests are in your project, uncommitted: {', '.join(land['files'])}.")
    L.append("")
    (ws / "report.md").write_text("\n".join(L), encoding="utf-8")
    numbers = {"mutants_made": len(db["mutants"]) if db else 0, "mutants_sampled": len(ids),
               "mutants_run": len(ids) - b["not run"], "score_before": round(100 * sb, 1) if sb is not None else None,
               "score_after": round(100 * sa, 1) if sa is not None and accepted else None,
               "killed_before": b["killed"] + b["timeout"], "survived_before": b["survived"], "timeouts": b["timeout"],
               "not_viable": b["error"], "tests_added": tests_added, "patches_accepted": len(accepted),
               "survivors_killed": len(new_kills), "survivors_left": len(left),
               "claimed_equivalent": sum(1 for m in left if m in st["claims"]), "confirmed_equivalent": b["equivalent"],
               "flaky_found": len(st["flaky"]), "rescored": bool(rescored)}
    summary = {"skill": "killer-tests", "status": status, "headline": headline, "report": str(ws / "report.md"),
               "branch": land.get("branch"), "commits": land.get("commits") or [], "decisions": st["decisions"],
               "waiting": [w["text"] + (f": {w['command']}" if w.get("command") else "") for w in st["waiting"]],
               "numbers": numbers}
    kit.save_json(ws / "summary.json", summary)
    print(f"Wrote {ws / 'report.md'} and summary.json.")
    print(f"killer-tests: {status} · {headline} · {ws / 'report.md'}")


# ---------------------------------------------------------------- CLI

def main(argv=None) -> None:
    kit.utf8_stdout()
    p = argparse.ArgumentParser(prog="mutants.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def cmd(name, func, help_, ws=True):
        sp = sub.add_parser(name, help=help_, description=help_)
        if ws:
            sp.add_argument("--workspace", "-w", required=True, help="the run folder")
        sp.set_defaults(func=func)
        return sp

    s = cmd("init", cmd_init, "Create the run folder and record the scope and the test command (after --).", ws=False)
    s.add_argument("--repo", default=".", help="the project folder (default: here)")
    s.add_argument("--scope", default="repo", help="repo, path:<folder or file>, changes, or branch[:<base>]")
    s.add_argument("--target", default="", help="one line on what the project is")
    s.add_argument("--slug", help="short name for the run folder")
    s.add_argument("--network", action="store_true", help="let sandboxes use the network")
    s.add_argument("--include", nargs="*", help="ignored paths every sandbox needs, like node_modules or .venv")
    s.add_argument("--exclude", nargs="*", help="leave these out of scope, like vendor/ '*.gen.ts'")
    s.add_argument("--env", nargs="*", help="NAME=VALUE set in every sandbox")
    s.add_argument("--source", nargs="*", help="only mutate files matching these globs")
    s.add_argument("--tests", nargs="*", help="globs for test files the usual patterns miss")
    s.add_argument("--cwd", help="folder inside the project to run the tests from")
    s.add_argument("--jobs", type=int, help=f"sandboxes at a time (default: CPUs - 1, at most 8; here {default_jobs()})")
    s.add_argument("--branch", help="land on this branch instead of a new killer-tests/<date>")
    s.add_argument("--after", choices=["branch", "uncommitted", "report"], default="branch",
                   help="where the new tests go (default: a new local branch)")
    s.add_argument("--goal", default="close", help="close (the default), score:<percent>, or measure")
    s.add_argument("--workspace", "-w", help="use this run folder instead of the default")
    s.add_argument("--force", action="store_true", help="start over in an existing run folder")
    s.add_argument("cmd", nargs=argparse.REMAINDER, help="-- then the test command")

    s = cmd("baseline", cmd_baseline, "Run the suite in a fresh copy, 3 times; it must pass every time.")
    s.add_argument("--repeat", type=int, default=3)
    s.add_argument("--timeout", type=float, default=1800, help="seconds per run (default 1800)")
    s.add_argument("--jobs", type=int, help="check this many copies at once (default: the run's)")

    s = cmd("generate", cmd_generate, "Make the mutants and pick the sample.")
    s.add_argument("--max", type=int, default=300, help="sample size (default 300)")
    s.add_argument("--all", action="store_true", help="run every mutant")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--source", nargs="*", help="only these globs (overrides init's)")
    s.add_argument("--exclude", nargs="*", help="leave these globs out too")
    s.add_argument("--force", action="store_true", help="make new mutants even though results exist")

    s = cmd("run", cmd_run, "Run the suite against every sampled mutant.")
    s.add_argument("--resume", action="store_true", help="run only the mutants without a result yet")
    s.add_argument("--fresh", action="store_true", help="throw away the results and start over")
    s.add_argument("--jobs", type=int, help="sandboxes at a time (default: what baseline found safe)")
    s.add_argument("--timeout", type=float, help="seconds per mutant (default: from the baseline)")

    s = cmd("score", cmd_score, "Show the mutation score.")
    s.add_argument("--survivors", action="store_true", help="list every survivor")

    s = cmd("show", cmd_show, "Show mutants as diffs.")
    s.add_argument("ids", nargs="+")
    s.add_argument("--patch", action="store_true", help="print a patch git apply takes, line endings and all")
    s.add_argument("--context", type=int, default=3)

    s = cmd("slices", cmd_slices, "Group the survivors into slices for test writers.")
    s.add_argument("--size", type=int, default=12, help="survivors per slice, 5 to 15 (default 12)")
    s.add_argument("--limit", type=int, help="make at most this many slices")
    s.add_argument("--again", action="store_true", help="include survivors a writer already tried")

    s = cmd("sandbox", cmd_sandbox, "Make a copy of the project for one agent, with the accepted tests in it.")
    s.add_argument("name", help="a name, like write-S1, judge-M9 or review")
    s.add_argument("--slice", help="put this slice's mutants in its scratch folder")
    s.add_argument("--mutants", nargs="*", help="or these mutants")
    s.add_argument("--combined", action="store_true", help="write the accepted tests as one diff to scratch/tests.diff")
    s.add_argument("--bare", action="store_true", help="leave the accepted tests out")
    s.add_argument("--patch", help="put this patch's changes in the copy (uncommitted), to fix a test it adds")
    s.add_argument("--net", action="store_true", help="allow the network in this sandbox")

    s = cmd("collect", cmd_collect, "Take a writer's tests from its sandbox and check them.")
    s.add_argument("--sandbox", required=True)
    s.add_argument("--targets", nargs="*", help="the mutants the tests aim at (default: the reply's, or the slice's)")
    s.add_argument("--accept", action="store_true", help="accept it straight away if it passes")
    s.add_argument("--no-check", action="store_true", help="record it without running check-test")
    s.add_argument("--keep", action="store_true", help="keep the sandbox")
    s.add_argument("--jobs", type=int)

    s = cmd("check-test", cmd_check_test, "The core check on a patch of new tests.")
    s.add_argument("patch_id", nargs="?", help="a patch from collect, like P2")
    s.add_argument("--patch", help="or a patch file (from git diff) to record and check")
    s.add_argument("--mutants", nargs="*", help="the mutants it targets (default: the patch's)")
    s.add_argument("--accept", action="store_true", help="accept it straight away if it passes")
    s.add_argument("--jobs", type=int)

    s = cmd("accept", cmd_accept, "Add checked patches to the accepted list, in order.")
    s.add_argument("ids", nargs="+")

    s = cmd("drop", cmd_drop, "Take an accepted patch out again.")
    s.add_argument("id")
    s.add_argument("--reason", help="why, for the report")

    for name, func, what in (("equivalent", cmd_equivalent, "Record an equivalence judge's verdict on a mutant."),
                             ("review", cmd_review, "Record the test reviewer's findings.")):
        s = cmd(name, func, what)
        if name == "equivalent":
            s.add_argument("id")
        s.add_argument("--sandbox", help="read reply.json from this sandbox, then delete it")
        s.add_argument("--from", dest="reply", help="a reply saved to a file")

    s = cmd("combined", cmd_combined, "Print the accepted tests as one diff.")
    s.add_argument("--out", help="write it to this file instead")

    s = cmd("land", cmd_land, "Commit the accepted tests on a local branch, then run the suite there.")
    s.add_argument("--branch", help="this branch instead of killer-tests/<date>; if it's the current one, commit there")
    s.add_argument("--per", choices=["file", "patch"], default="file", help="one commit per test file (default) or per patch")
    s.add_argument("--uncommitted", action="store_true", help="put the tests in the project without committing")

    s = cmd("rescore", cmd_rescore, "Run the same sample against the final tests.")
    s.add_argument("--rev", help="a branch or commit to rescore (default: the landed branch)")
    s.add_argument("--resume", action="store_true")
    s.add_argument("--jobs", type=int)

    s = cmd("decide", cmd_decide, "Record a call the user should check.")
    s.add_argument("text")
    s = cmd("wait", cmd_wait, "Record an outward action prepared but not done.")
    s.add_argument("text")
    s.add_argument("--command", help="the exact command that would do it")

    s = cmd("status", cmd_status, "Compare the real project's git status with the reference (exit 1 if it changed).")
    s.add_argument("--mark", action="store_true", help="make the current status the reference (after your own change)")

    cmd("report", cmd_report, "Write report.md and summary.json.")
    cmd("clean", cmd_clean, "Delete this run's sandboxes.")

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
