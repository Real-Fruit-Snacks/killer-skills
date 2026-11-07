#!/usr/bin/env python3
"""Proof and bookkeeping for a killer-refactor run: change the structure, prove the behaviour didn't.

Commands:
  init             create the run folder: the scope, the refactor, the base commit, the test command
  baseline         run the tests 3 times in a fresh copy; tests that flip are recorded as flaky
  golden add       record a command's output (stdout, stderr, exit code, files), run in two fresh copies
  golden list      the recorded runs
  golden run       rerun the recorded runs on the original code, or with the accepted steps
  surface record   record the public interface: Python names and signatures, or a command's listing
  surface compare  compare the interface with the accepted steps (and a candidate) against the plan
  plan             add, replace, drop or show the steps, each with the interface changes it may make
  sandbox          make an isolated copy for one agent (a refactorer's has the accepted steps in it)
  pin survey       plant faults function by function: which code do the tests barely check?
  pin collect      take a pin writer's new test files from its sandbox
  pin check        a pin counts when it passes 3 of 3 on the original and fails on a planted fault
  candidate add    save a refactorer's step from its sandbox (or a patch file)
  candidate show   print a step's plan and diff, for a challenger's brief
  verify           apply the accepted steps and this one in a fresh copy: tests, recorded runs,
                   interface and pins must all hold
  probe            run a challenger's probes on the original code and on the changed code; a
                   difference rejects the step
  metrics          lines, long functions, nesting, duplicated blocks and complexity, before and after
  land             commit each accepted step on a new local branch, then check the branch again
  status           the run's progress, and whether the real project's working tree changed
  decide           record a call the user should check
  wait             record an outward action prepared but not done
  report           write report.md and summary.json
  clean            delete this run's sandboxes

A step counts only when verify passes in a fresh copy and, at standard or thorough depth, a
challenger's probes found no input where the original and the changed code differ. Runs
live in ~/.claude/killer-refactor/<project>/<date>-<slug>/; sandboxes in the temp folder.
KILLER_REFACTOR_HOME and KILLER_REFACTOR_SANDBOXES move them.
Exit codes: 0 fine, 1 a check failed (the verdict), 2 usage or setup error.
Standard library only. Run `refactor.py <command> --help` for options.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import tokenize
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, file: str):
    sys.dont_write_bytecode = True   # no __pycache__ left in the installed skill's folder
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kit = _load("killer_kit", "kit.py")
faults = _load("killer_refactor_faults", "faults.py")

SKILL = "refactor"
STATE = "refactor.json"
RUNS = 3                                        # baseline and pin runs: each must pass every time
MIN_PROBES = {"quick": 0, "standard": 3, "thorough": 6}
PIN_FAULTS = {"quick": 4, "standard": 6, "thorough": 10}
SURVEY_FAULTS = {"quick": 2, "standard": 3, "thorough": 5}
INTERFACES = {"same": "everything public stays the same",
              "planned": "only the renames and moves in the plan",
              "free": "free to change (internal code); differences are reported, not refused"}
LONG = 50                                       # a function over this many lines counts as long
WINDOW, WINDOW_CHARS = 6, 60                    # duplicated blocks: 6-line windows with 60+ characters
TEXT_MAX, HASH_MAX = 200_000, 2_000_000
NO_BYTECODE = {"PYTHONDONTWRITEBYTECODE": "1"}  # a stale .pyc would hide a planted fault
RULE_OPS = {"rename": 2, "move": 2, "add": 1, "remove": 1, "change": 1}
TEST_PATTERNS = ["tests/", "test/", "spec/", "specs/", "__tests__/", "testdata/", "fixtures/", "__snapshots__/",
                 "test_*", "*_test.*", "*.test.*", "*.spec.*", "conftest.py", "tests.py", "test.py"]
DEP_PATTERNS = ["package.json", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock",
                "requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "Pipfile.lock",
                "poetry.lock", "uv.lock", "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "Gemfile", "Gemfile.lock",
                "*.csproj", "pom.xml", "build.gradle*", ".github/", "pytest.ini", "tox.ini", "noxfile.py",
                "jest.config.*", "vitest.config.*", "karma.conf.*", ".mocharc*", "phpunit.xml*", "Makefile",
                "CMakeLists.txt", ".gitlab-ci.yml", ".travis.yml", "azure-pipelines.yml"]
CODE_EXT = {".py", ".pyw", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts", ".go", ".rs", ".java",
            ".cs", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".kt", ".swift", ".scala", ".dart", ".php", ".rb",
            ".sh", ".bash", ".ps1", ".psm1", ".lua", ".pl"}
ATTRIBUTION = re.compile(r"(?i)co-authored-by|generated (?:with|by) (?:claude|ai\b|an ai|chatgpt|copilot|a model)"
                         r"|\bclaude\b|\banthropic\b|\bai (?:agent|assistant)\b|\brefactor(?:ing|er) agent\b")
FAIL_NAMES = [re.compile(r"^(?:FAIL|ERROR): (\S+ \([^)]*\))", re.M),
              re.compile(r"^FAILED (?!\()(\S+)", re.M),
              re.compile(r"^--- FAIL: (\S+)", re.M),
              re.compile(r"^not ok \d+ - (.+)$", re.M),
              re.compile(r"^\s*(?:✕|×|✗)\s+(.+?)(?:\s+\(\d+ ?ms\))?$", re.M)]
DEFAULT_MASKS = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"), "<time>"),
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<time>"),
    (re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?[ \t]?(?:ms|msec|seconds|secs|sec|s|µs|μs|us|ns)(?!\w)"), "<duration>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<id>"),
    (re.compile(r"(?<= at )0x[0-9a-fA-F]+(?=>)"), "<address>"),   # only an object's repr: <X object at 0x...>
]
REJECTED = "rejected"


# ---------------------------------------------------------------- small helpers

def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def short(rev: str | None) -> str:
    return rev[:10] if rev else "the folder"


def and_list(items) -> str:
    items = [str(x) for x in items if x]
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def tail(text: str, n: int = 25) -> str:
    return "\n".join((text or "").rstrip().splitlines()[-n:])


def check_name(name: str, what: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name or ""):
        kit.fail(f"a {what} name is letters, digits, dots, dashes and underscores")


def parse_cmd(s: str) -> list[str]:
    """A command given as one string: a JSON list, or split shell-style (use forward slashes)."""
    s = (s or "").strip()
    if s.startswith("["):
        try:
            v = json.loads(s)
        except json.JSONDecodeError as e:
            kit.fail(f"{s!r} isn't a JSON list: {e}")
        if not isinstance(v, list) or not v or not all(isinstance(x, str) for x in v):
            kit.fail(f"{s!r}: a command is a list of strings")
        return v
    v = shlex.split(s)
    if not v:
        kit.fail("an empty command")
    return v


def show_cmd(cmd) -> str:
    return " ".join(shlex.quote(x) if re.search(r"[\s\"']", x) else x for x in cmd or [])


def matches(path: str, patterns) -> bool:
    path = path.replace("\\", "/")
    parts = path.split("/")
    for p in patterns or []:
        p = p.replace("\\", "/")
        if p.endswith("/"):
            d = p.rstrip("/")
            if "/" in d:
                if path.startswith(d + "/"):
                    return True
            elif any(fnmatch.fnmatch(x, d) for x in parts[:-1]):
                return True
        elif "/" in p:
            if fnmatch.fnmatch(path, p):
                return True
        elif fnmatch.fnmatch(parts[-1], p):
            return True
    return False


def is_test(path: str) -> bool:
    return matches(path, TEST_PATTERNS)


def is_code(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_EXT and not is_test(path)


def attribution_hits(text: str) -> list[str]:
    return sorted({m.group(0) for m in ATTRIBUTION.finditer(text or "")})


def added_lines(patch: str) -> str:
    return "\n".join(ln[1:] for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++"))


def line_ending_churn(patch: str) -> list[str]:
    """Files whose diff is mostly the same lines with different line endings."""
    out = []
    for chunk in re.split(r"(?m)^diff --git ", patch)[1:]:
        name = chunk.split("\n", 1)[0].rsplit(" b/", 1)[-1].strip()
        lines = chunk.split("\n")
        minus = [ln[1:] for ln in lines if ln.startswith("-") and not ln.startswith("---")]
        plus = set(ln[1:] for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
        flips = sum(1 for ln in minus if (ln[:-1] if ln.endswith("\r") else ln + "\r") in plus)
        if flips >= 10 and flips * 2 >= len(minus):
            out.append(name)
    return out


def failing_names(out: str) -> list[str]:
    names = []
    for rx in FAIL_NAMES:
        for m in rx.finditer(out or ""):
            if m.group(1).strip() not in names:
                names.append(m.group(1).strip())
    return names


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", "surrogateescape")


def line_diff(a: str, b: str) -> str:
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else None
        y = lb[i] if i < len(lb) else None
        if x != y:
            show = lambda s: "(no line)" if s is None else repr(s[:160])
            return f"line {i + 1} was {show(x)}, now {show(y)}"
    return "has the same lines, but its line endings or its last newline differ"


# ---------------------------------------------------------------- run state

def new_state() -> dict:
    return {"tests": None, "baseline": None, "golden": {}, "surface": None, "plan": [], "candidates": [],
            "chain": [], "pins": [], "survey": None, "boxes": {}, "agents": {}, "metrics": None,
            "decisions": [], "waiting": [], "landed": [], "next": {"S": 1, "C": 1, "N": 1}}


def load_state(ws: Path) -> dict:
    st = new_state()
    st.update(kit.load_json(ws / STATE, None) or {})
    return st


def mutate(ws: Path, fn):
    """Read refactor.json, change it with fn, write it back, under the run's lock."""
    with kit.locked(ws):
        st = load_state(ws)
        out = fn(st)
        kit.save_json(ws / STATE, st)
    return out


def load(a) -> tuple[Path, dict, dict]:
    ws, cfg = kit.load_run(a.workspace)
    return ws, cfg, load_state(ws)


def decide_now(ws: Path, text: str) -> None:
    mutate(ws, lambda s: s["decisions"].append({"text": text, "at": kit.now()}))


def get_cand(st: dict, cid: str) -> dict:
    for c in st["candidates"]:
        if c["id"].lower() == str(cid).lower():
            return c
    kit.fail(f"no candidate {cid}")


def get_step(st: dict, sid: str) -> dict:
    for s in st["plan"]:
        if s["id"].lower() == str(sid).lower():
            return s
    kit.fail(f"no step {sid} in the plan (plan -w <run> shows it)")


def get_pin(st: dict, pid: str) -> dict:
    for p in st["pins"]:
        if p["id"].lower() == str(pid).lower():
            return p
    kit.fail(f"no pin {pid}")


def project_status(cfg: dict) -> str | None:
    return kit.git_status(Path(cfg["repo"])) if cfg["git"] else None


def warn_if_project_changed(cfg: dict) -> None:
    if cfg["git"] and project_status(cfg) != cfg.get("status_at_init"):
        print("warning: the real project's working tree has changed since init. Run refactor.py status to see how; "
              "if nobody meant to change it, undo it.")


# ---------------------------------------------------------------- the plan's interface rules, and renames carried through

def parse_rule(s: str) -> list[str]:
    parts = (s or "").split()
    if not parts or parts[0] not in RULE_OPS or len(parts) - 1 != RULE_OPS[parts[0]]:
        kit.fail(f"{s!r}: an allowed change is 'rename <old> <new>', 'move <old> <new>', 'add <pattern>', "
                 "'remove <pattern>' or 'change <pattern>', with dotted names like pkg.module.function")
    return parts


def rules_of(st: dict, ids) -> list[list[str]]:
    """The interface changes the steps of these candidates allow, in order."""
    out = []
    for cid in ids:
        step = get_step(st, get_cand(st, cid)["step"])
        out += [r.split() for r in step["allow"]]
    return out


def rename_pairs(rules) -> list[tuple[str, str]]:
    return [(r[1], r[2]) for r in rules if r[0] in ("rename", "move")]


def rename_words(text: str, old: str, new: str) -> str:
    """Plain text: the dotted name, then its last part, as whole words (for listings and output)."""
    text = re.sub(rf"(?<![\w.]){re.escape(old)}(?!\w)", lambda m: new, text)
    o, n = old.rsplit(".", 1)[-1], new.rsplit(".", 1)[-1]
    if o != n:
        text = re.sub(rf"(?<!\w){re.escape(o)}(?!\w)", lambda m: n, text)
    return text


def py_rename(text: str, old: str, new: str, classes) -> str:
    """One rename carried through Python code by its tokenizer: names only, never strings or comments.

    `pkg.mod.old` becomes `pkg.mod.new`; a bare `old` (imported, or called) becomes `new`; `x.old` only when
    `old` is a method or attribute of a class, or `x` is its module's name. `from pkg.mod import old` is
    rewritten, and for a move to another module so is the module, when it's the only name imported.
    Keyword arguments named `old` are left alone."""
    op, nw = old.split("."), new.split(".")
    o_short, n_short, o_parent, n_parent = op[-1], nw[-1], op[:-1], nw[:-1]
    member = ".".join(o_parent) in classes
    bom = text.startswith("﻿")
    body = text[1:] if bom else text
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(body, newline="").readline))
    except (tokenize.TokenError, SyntaxError):
        return text
    starts = [0]
    for ln in io.StringIO(body, newline="").readlines():
        starts.append(starts[-1] + len(ln))
    sig = [t for t in toks if t.type in (tokenize.NAME, tokenize.OP)]
    edits = set()

    def put(a, b, s):
        edits.add((starts[a[0] - 1] + a[1], starts[b[0] - 1] + b[1], s))

    def chain_at(i):
        j = i
        while j + 2 < len(sig) and sig[j + 1].string == "." and sig[j + 2].type == tokenize.NAME:
            j += 2
        return sig[i:j + 1:2], j

    i = depth = 0
    while i < len(sig):
        t = sig[i]
        if t.type == tokenize.OP:
            depth += (t.string in ("(", "[", "{")) - (t.string in (")", "]", "}"))
            i += 1
            continue
        prev = sig[i - 1] if i else None
        after_dot = prev is not None and prev.string == "."
        if t.string == "from" and not after_dot:
            k = i + 1
            while k < len(sig) and sig[k].string in (".", "..."):
                k += 1
            mod, j = chain_at(k) if k < len(sig) and sig[k].type == tokenize.NAME else ([], k - 1)
            k = j + 1
            if k < len(sig) and sig[k].string == "import":
                k += 1
                if k < len(sig) and sig[k].string == "(":
                    k += 1
                names = []
                while k < len(sig) and sig[k].type == tokenize.NAME:
                    names.append(sig[k])
                    k += 1
                    if k < len(sig) and sig[k].string == "as":
                        k += 2
                    if k < len(sig) and sig[k].string == ",":
                        k += 1
                        continue
                    break
                if k < len(sig) and sig[k].string == ")":
                    k += 1
                modnames = [m.string for m in mod]
                if mod and modnames[:len(op)] == op:
                    put(mod[0].start, mod[len(op) - 1].end, new)
                elif o_parent and modnames == o_parent:
                    hits = [nm for nm in names if nm.string == o_short]
                    if hits and n_parent == o_parent:
                        for nm in hits:
                            put(nm.start, nm.end, n_short)
                    elif hits and len(names) == 1:
                        put(mod[0].start, mod[-1].end, ".".join(n_parent))
                        put(hits[0].start, hits[0].end, n_short)
                i = k
                continue
        chain, j = chain_at(i)
        names = [c.string for c in chain]
        nxt = sig[j + 1] if j + 1 < len(sig) else None
        if not after_dot and names[:len(op)] == op:
            put(chain[0].start, chain[len(op) - 1].end, new)
        elif o_short != n_short:
            for k, c in enumerate(chain):
                if c.string != o_short:
                    continue
                if k == 0 and not after_dot:
                    kwarg = len(chain) == 1 and depth > 0 and nxt is not None and nxt.string == "="
                    if not kwarg and len(op) > 1 and not member:   # a method is reached through an object
                        put(c.start, c.end, n_short)
                elif member or (k > 0 and o_parent and names[k - 1] == o_parent[-1]):
                    put(c.start, c.end, n_short)
        i = j + 1
    out = body
    for a, b, s in sorted(edits, reverse=True):
        out = out[:a] + s + out[b:]
    return ("﻿" if bom else "") + out


def clike_rename(text: str, old: str, new: str, classes) -> str:
    """One rename carried through C-like code, outside its strings and comments, by the same rules."""
    masked = faults.mask_clike(text)
    op = old.split(".")
    o_short, n_short, o_parent = op[-1], new.split(".")[-1], op[:-1]
    member = ".".join(o_parent) in classes
    edits = [(m.start(), m.end(), new) for m in re.finditer(rf"(?<![\w$.]){re.escape(old)}(?![\w$])", masked)]
    if o_short != n_short and len(op) > 1:
        for m in re.finditer(rf"(?<![\w$]){re.escape(o_short)}(?![\w$])", masked):
            if any(a <= m.start() < b for a, b, _ in edits):
                continue
            before = masked[:m.start()]
            if before.endswith("."):
                pm = re.search(r"([\w$]+)\.$", before)
                if not (member or (pm and o_parent and pm.group(1) == o_parent[-1])):
                    continue
            elif member:   # a method is reached through an object
                continue
            edits.append((m.start(), m.end(), n_short))
    out = text
    for a, b, s in sorted(edits, reverse=True):
        out = out[:a] + s + out[b:]
    return out


class Renames:
    """The plan's renames and moves, carried through the code that uses them: tests, pins, drivers, probes.

    Only code changes: Python names found by its tokenizer, and C-like code outside its strings and comments.
    Strings, comments, data files and output are left as they are, so a rename can't explain away a change
    to a string key, a message or an input file."""

    def __init__(self, pairs=(), classes=()):
        self.pairs = list(pairs)
        cls = set(classes)
        for o, n in self.pairs:   # a renamed class keeps its methods' membership
            for k in list(cls):
                if k == o or k.startswith(o + "."):
                    cls.add(n + k[len(o):])
        self.classes = cls

    def __bool__(self) -> bool:
        return bool(self.pairs)

    def seq(self, reverse: bool = False):
        return [(n, o) for o, n in reversed(self.pairs)] if reverse else self.pairs

    def code(self, text: str, path: str, reverse: bool = False) -> str:
        lang = faults.lang_of(path)
        for old, new in self.seq(reverse):
            if lang == "python":
                text = py_rename(text, old, new, self.classes)
            elif lang == "clike":
                text = clike_rename(text, old, new, self.classes)
        return text

    def data(self, data: bytes, path: str, reverse: bool = False) -> bytes:
        if not self.pairs or faults.lang_of(path) is None:
            return data
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return data
        out = self.code(text, path, reverse)
        return data if out == text else out.encode("utf-8")

    def args(self, cmd, reverse: bool = False) -> list[str]:
        """A command's arguments: only whole dotted names (like pkg.mod.func) are renamed."""
        out = []
        for x in cmd or []:
            for old, new in self.seq(reverse):
                if "." in old:
                    x = re.sub(rf"(?<![\w.]){re.escape(old)}(?![\w])", lambda m, n=new: n, x)
            out.append(x)
        return out

    def output(self, text: str) -> str:
        for old, new in self.pairs:
            text = rename_words(text, old, new)
        return text


NO_RENAMES = Renames()


def classes_of(st: dict) -> set[str]:
    s = st.get("surface") or {}
    if s.get("kind") != "python":
        return set()
    return {entry_key(ln, "python") for ln in s.get("lines") or [] if ln.startswith("class ")}


def renames_for(st: dict, ids) -> Renames:
    return Renames(rename_pairs(rules_of(st, ids)), classes_of(st))


def copy_renamed(src: Path, dst: Path, ren: Renames | None = None, reverse: bool = False) -> list[Path]:
    """Copy a folder's files, with renames carried through the code ones; returns what was written."""
    out = []
    if not src.is_dir():
        return out
    for p in sorted(src.rglob("*")):
        if p.is_file():
            t = dst / p.relative_to(src)
            t.parent.mkdir(parents=True, exist_ok=True)
            data = p.read_bytes()
            t.write_bytes(ren.data(data, p.name, reverse) if ren else data)
            out.append(t)
    return out


# ---------------------------------------------------------------- sandboxes

class PatchError(Exception):
    pass


def commit_state(root: Path) -> str:
    """Commit the sandbox's copy as it stands and make that its base, so box_diff shows only what comes next."""
    return kit.rebase_box(root)


def apply_chain(ws: Path, root: Path, st: dict, ids) -> None:
    done = []
    for cid in ids:
        c = get_cand(st, cid)
        ok, msg = kit.apply_patch(root / "repo", kit.read_patch(ws / c["patch"]))
        if not ok:
            raise PatchError(f"{c['id']} doesn't apply on top of {and_list(done) or 'the original'}: {tail(msg, 4)}")
        done.append(c["id"])


def fresh_box(ws: Path, cfg: dict, st: dict, hint: str, chain=(), rev: str | None = None, commit: bool = True,
              name: str | None = None) -> Path:
    name = name or f"{re.sub(r'[^A-Za-z0-9._-]+', '-', hint)[:24]}-{secrets.token_hex(3)}"
    root = kit.make_sandbox(cfg, ws, name, rev=rev or cfg["base_rev"], env=NO_BYTECODE)
    try:
        apply_chain(ws, root, st, chain)
        if commit and chain:
            commit_state(root)
    except BaseException:
        kit.drop_sandbox(ws, cfg, name)
        raise
    return root


@contextlib.contextmanager
def temp_box(ws: Path, cfg: dict, st: dict, hint: str, chain=(), rev: str | None = None, commit: bool = True):
    root = fresh_box(ws, cfg, st, hint, chain, rev, commit)
    try:
        yield root
    finally:
        kit.drop_sandbox(ws, cfg, root.name)


def execute(root: Path, cmd: list[str], cwd: str | None = None, timeout: float = 300.0,
            stdin: bytes | None = None) -> dict:
    """Run cmd in the sandbox's copy with its environment, stdout and stderr kept apart."""
    run = kit.runner()
    env = run.build_env(root)
    where = root / "repo" / cwd if cwd else root / "repo"
    extra = {"start_new_session": True} if os.name != "nt" else \
        {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    t0 = time.monotonic()
    try:
        p = subprocess.Popen(run.resolve(cmd, env), cwd=str(where), env=env,
                             stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, **extra)
    except OSError as e:
        return {"code": 127, "out": "", "err": f"could not start {cmd[0]}: {e}", "timed_out": False, "secs": 0.0}
    try:
        out, err = p.communicate(input=stdin, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        run.kill_tree(p)
        try:
            out, err = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
        timed_out = True
    dec = lambda x: (x or b"").decode("utf-8", errors="backslashreplace")   # other bytes stay distinct
    return {"code": None if timed_out else p.returncode, "out": dec(out), "err": dec(err), "timed_out": timed_out,
            "secs": round(time.monotonic() - t0, 2)}


# ---------------------------------------------------------------- recorded runs: run, capture, normalise, compare

def _win_path(p: str, fn: str) -> str:
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = getattr(ctypes.windll.kernel32, fn)(p, buf, 32768)
        return buf.value if 0 < n < 32768 else ""
    except Exception:   # noqa: BLE001 - a spelling we can't get is a spelling we don't mask
        return ""


def spellings(root: Path) -> list[str]:
    """Every way a program might print the sandbox's folder: as given, resolved, 8.3 short or long."""
    forms = {str(root), str(root.resolve()), os.path.realpath(str(root)), os.path.abspath(str(root))}
    if os.name == "nt":
        for f in list(forms):
            forms |= {_win_path(f, "GetShortPathNameW"), _win_path(f, "GetLongPathNameW")}
    out = set()
    for f in forms:
        if f:
            out |= {f, f.replace("\\", "/"), f.replace("\\", "\\\\")}
    return sorted(out, key=len, reverse=True)


def normalise(text: str, root: Path | None, masks=(), exact: bool = False, forms=None) -> str:
    """Output as the checks compare it: the copy's folder in every spelling, then the masks. Line endings,
    spaces and blank lines are kept: both sides run on the same machine, and a step that changes them
    changes the output."""
    text = text or ""
    if root is not None:
        flags = re.I if os.name == "nt" else 0
        for f in forms if forms is not None else spellings(root):
            text = re.sub(re.escape(f), "<sandbox>", text, flags=flags)
    for rx in masks or ():
        text = re.sub(rx, "<masked>", text)
    if not exact:
        for rx, rep in DEFAULT_MASKS:
            text = rx.sub(rep, text)
    return text


SIDES = ("repo", "scratch", "home", "tmp")
UNCOMPARED = ("tmp/", "home/.cache/", "home/AppData/Local/", "home/Library/Caches/", "home/.npm/",
              "home/.npm-global/", "home/.cargo-install/")   # put back after each run, but not compared


def snapshot(root: Path, skip=()) -> tuple[dict[str, str], set[str]]:
    """Every file in the copy, the scratch folder and the sandbox's home and temp folders, by content (by size
    and time when it's huge), and every folder."""
    out, dirs = {}, set()
    for side in SIDES:
        top = root / side
        for dirpath, dirnames, filenames in os.walk(top):
            d = Path(dirpath)
            rel_d = d.relative_to(root).as_posix()
            dirnames[:] = sorted(x for x in dirnames if x not in (".git", "__pycache__") and f"{rel_d}/{x}" not in skip)
            dirs.add(rel_d)
            for f in filenames:
                if f.endswith((".pyc", ".pyo")):
                    continue
                p = d / f
                try:
                    st = p.stat()
                    key = hashlib.sha1(p.read_bytes()).hexdigest() if st.st_size <= HASH_MAX \
                        else f"{st.st_size}:{st.st_mtime_ns}"
                except OSError:
                    continue
                out[f"{rel_d}/{f}"] = key
    return out, dirs


def file_content(path: Path, norm) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return "<unreadable>"
    if len(data) <= TEXT_MAX and b"\0" not in data[:8192]:
        return "text:" + norm(data.decode("utf-8", errors="backslashreplace"))
    return "sha256:" + hashlib.sha256(data).hexdigest()


def restore(root: Path, before: dict, after: dict, dirs_before: set, given, skip=()) -> None:
    """Put the copy back as it was before a recorded run, so the next run starts as a fresh copy would: files it
    created go (in the copy, scratch, home and temp folders), changed tracked files come back, and folders
    it made are removed."""
    for rel in after:
        if rel not in before:
            (root / rel).unlink(missing_ok=True)
    if any(rel.startswith("repo/") and before.get(rel) != after.get(rel) for rel in set(before) | set(after)):
        kit.git(root / "repo", *kit.RAW, "reset", "-q", "--hard", check=False)
    for p in given:
        Path(p).unlink(missing_ok=True)
    made = []
    for side in SIDES:
        for dirpath, dirnames, _files in os.walk(root / side):
            rel_d = Path(dirpath).relative_to(root).as_posix()
            dirnames[:] = [x for x in dirnames if x not in (".git", "__pycache__") and f"{rel_d}/{x}" not in skip]
            if rel_d not in dirs_before:
                made.append(Path(dirpath))
    for d in sorted(made, key=lambda x: -len(x.parts)):
        with contextlib.suppress(OSError):
            if not any(d.iterdir()):
                d.rmdir()


def run_recorded(ws: Path, cfg: dict, root: Path, g: dict, ren: Renames = NO_RENAMES, forms=None,
                 folder: Path | None = None) -> dict:
    """One run of a recorded command in a prepared copy, captured and normalised, then undone."""
    folder = folder or ws / "golden" / g["name"]
    given = copy_renamed(folder / "files", root / "scratch", ren)
    stdin = (folder / "stdin").read_bytes() if g.get("stdin") else None
    skip = {f"repo/{i.strip('/')}" for i in cfg.get("include") or []}
    before, dirs_before = snapshot(root, skip)
    r = execute(root, ren.args(g["cmd"]), g.get("cwd"), g["timeout"], stdin)
    after, _ = snapshot(root, skip)
    forms = forms if forms is not None else spellings(root)
    norm = lambda t: normalise(t, root, g.get("masks"), g.get("exact"), forms)
    files = {}
    for rel in sorted(set(before) | set(after)):
        if before.get(rel) == after.get(rel) or rel.startswith(UNCOMPARED):
            continue
        files[norm(rel)] = "<deleted>" if rel not in after else file_content(root / rel, norm)
    def stream(text: str) -> str:   # long output is kept as a hash, so the run's books stay small
        t = norm(text)
        return t if len(t) <= TEXT_MAX else f"sha256:{hashlib.sha256(t.encode('utf-8', 'surrogateescape')).hexdigest()}" \
                                            f" ({len(t)} characters)"
    rec = {"exit": "timeout" if r["timed_out"] else r["code"], "stdout": stream(r["out"]), "stderr": stream(r["err"]),
           "files": files}
    restore(root, before, after, dirs_before, given, skip)
    return rec


def renamed_record(rec: dict, ren: Renames) -> dict:
    """A run's record with the planned renames applied to its text, to tell a rename-only difference apart."""
    return {"exit": rec["exit"], "stdout": ren.output(rec["stdout"]), "stderr": ren.output(rec["stderr"]),
            "files": {k: (ren.output(v) if v.startswith("text:") else v) for k, v in rec["files"].items()}}


def first_diff(want: dict, got: dict) -> str | None:
    if want["exit"] != got["exit"]:
        return f"the exit code was {want['exit']}, now {got['exit']}"
    for stream in ("stdout", "stderr"):
        if want[stream] != got[stream]:
            if want[stream].startswith("sha256:") or got[stream].startswith("sha256:"):
                size = lambda t: t.split(" ", 1)[1].strip("()") if t.startswith("sha256:") else f"{len(t)} characters"
                return f"{stream} differs, too long to show line by line ({size(want[stream])} before, " \
                       f"{size(got[stream])} now)"
            return f"{stream} {line_diff(want[stream], got[stream])}"
    wf, gf = want["files"], got["files"]
    for name in sorted(set(wf) | set(gf)):
        if name not in gf:
            return f"it no longer {'deletes' if wf[name] == '<deleted>' else 'writes'} {name}"
        if name not in wf:
            return f"it now {'deletes' if gf[name] == '<deleted>' else 'writes'} {name}"
        if wf[name] != gf[name]:
            if wf[name].startswith("text:") and gf[name].startswith("text:"):
                return f"{name}: {line_diff(wf[name][5:], gf[name][5:])}"
            return f"{name} has different contents"
    return None


def describe_record(rec: dict) -> str:
    out = len([x for x in rec["stdout"].split("\n") if x])
    err = len([x for x in rec["stderr"].split("\n") if x])
    bits = [f"exit {rec['exit']}", plural(out, "line") + " of output"]
    if err:
        bits.append(plural(err, "line") + " on stderr")
    if rec["files"]:
        bits.append(plural(len(rec["files"]), "file") + f" ({', '.join(sorted(rec['files'])[:4])})")
    return ", ".join(bits)


# ---------------------------------------------------------------- the interface

PROBE = r'''
import ast, importlib, inspect, json, os, pkgutil, re, sys

cfg = json.load(open(sys.argv[1], encoding="utf-8"))
repo = os.path.realpath(os.getcwd())
SKIP_DIRS = set(cfg["skip_dirs"])
OTHER_DIRS = {"test", "tests", "testing", "spec", "specs", "__tests__", "testdata", "fixtures",
              "docs", "doc", "examples", "example", "samples", "sample", "benchmarks", "benchmark", "bench"}
NOT_MODULES = {"setup.py", "conftest.py", "noxfile.py", "fabfile.py", "__main__.py", "manage.py"}
SKIP_DUNDERS = {"__init__", "__new__", "__dict__", "__weakref__", "__module__", "__doc__", "__qualname__",
                "__annotations__", "__slots__", "__firstlineno__", "__static_attributes__", "__match_args__",
                "__dataclass_fields__", "__dataclass_params__", "__hash__", "__orig_bases__", "__parameters__",
                "__abstractmethods__", "__annotate__", "__annotate_func__", "__annotations_cache__"}
lines = set()


def module_of(path):
    folder, name = os.path.split(path)
    parts = [] if name == "__init__.py" else [name[:-3]]
    while os.path.isfile(os.path.join(folder, "__init__.py")):
        folder, pkg = os.path.split(folder)
        parts.insert(0, pkg)
    return folder, ".".join(parts)


found = {}
for top in cfg["folders"]:
    top = os.path.normpath(os.path.join(repo, top))
    walk = [(os.path.dirname(top), [], [os.path.basename(top)])] if os.path.isfile(top) else os.walk(top)
    for dirpath, dirnames, filenames in walk:
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                             and d.lower() not in OTHER_DIRS)
        for f in sorted(filenames):
            low = f.lower()
            if not f.endswith(".py") or f in NOT_MODULES or low.startswith("test_") or low.endswith("_test.py"):
                continue
            root, name = module_of(os.path.join(dirpath, f))
            if name and all(p.isidentifier() for p in name.split(".")):
                found.setdefault(name, (root, os.path.join(dirpath, f)))
roots = []
for root, _ in found.values():
    if root and root not in roots:
        roots.append(root)
sys.path[:0] = [r for r in roots if r not in sys.path] + [repo]
for name in cfg["modules"]:
    found.setdefault(name, (None, None))
    try:
        mod = importlib.import_module(name)
        for info in pkgutil.walk_packages(getattr(mod, "__path__", []) or [], name + "."):
            found.setdefault(info.name, (None, None))
    except BaseException:
        pass
tops = {n.split(".")[0] for n in found}


def public(name):
    return not any(p.startswith("_") for p in name.split("."))


def in_project(mod_name):
    m = sys.modules.get(mod_name or "")
    f = getattr(m, "__file__", None)
    if not f:
        return False
    return os.path.normcase(os.path.realpath(f)).startswith(os.path.normcase(repo) + os.sep)


def clean(text):
    return re.sub(r" at 0x[0-9A-Fa-f]+", "", text)


def sig(obj):
    try:
        s = inspect.signature(obj)
    except (TypeError, ValueError):
        return "(...)"
    try:
        s = s.replace(parameters=[p.replace(annotation=inspect.Parameter.empty) for p in s.parameters.values()],
                      return_annotation=inspect.Signature.empty)
    except (TypeError, ValueError):
        pass
    return clean(str(s))


def static_sig(a):
    parts = []
    pos = a.posonlyargs + a.args
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    for i, (arg, d) in enumerate(zip(pos, defaults)):
        parts.append(arg.arg + ("" if d is None else "=" + ast.unparse(d)))
        if a.posonlyargs and i == len(a.posonlyargs) - 1:
            parts.append("/")
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")
    for arg, d in zip(a.kwonlyargs, a.kw_defaults):
        parts.append(arg.arg + ("" if d is None else "=" + ast.unparse(d)))
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def tree_of(path):
    try:
        with open(path, "rb") as fh:
            return ast.parse(fh.read())
    except (SyntaxError, ValueError, OSError, TypeError):
        return None


def runs_code(tree):
    ok = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign,
          ast.AnnAssign, ast.AugAssign, ast.If, ast.Try, ast.Pass)
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node, ok):
            return True
    return False


def defined(tree):
    names = set()
    body = list(tree.body)
    while body:
        node = body.pop(0)
        if isinstance(node, (ast.If, ast.Try)):
            body += node.body + getattr(node, "orelse", []) + getattr(node, "finalbody", [])
            for h in getattr(node, "handlers", []):
                body += h.body
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for x in ast.walk(t):
                    if isinstance(x, ast.Name):
                        names.add(x.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)   # a decorator may have turned it into some other callable
        elif isinstance(node, ast.ImportFrom) and (node.level or (node.module or "").split(".")[0] in tops):
            names |= {a.asname or a.name for a in node.names}
    return names


def static(name, tree):
    lines.add(f"script {name}")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            kind = "async-function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            lines.add(f"{kind} {name}.{node.name}{static_sig(node.args)}")
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            lines.add(f"class {name}.{node.name}")
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and not m.name.startswith("_"):
                    lines.add(f"method {name}.{node.name}.{m.name}{static_sig(m.args)}")


def members(qual, cls, depth=0):
    for attr in sorted(dir(cls)):
        dunder = attr.startswith("__") and attr.endswith("__")
        if (attr.startswith("_") and not dunder) or attr in SKIP_DUNDERS:
            continue
        owner = next((k for k in cls.__mro__ if attr in vars(k)), None)
        if owner is None or owner is object or not in_project(getattr(owner, "__module__", None)):
            continue
        raw = vars(owner)[attr]
        key = f"{qual}.{attr}"
        if isinstance(raw, staticmethod):
            lines.add(f"staticmethod {key}{sig(raw.__func__)}")
        elif isinstance(raw, classmethod):
            lines.add(f"classmethod {key}{sig(raw.__func__)}")
        elif isinstance(raw, property):
            lines.add(f"property {key}")
        elif inspect.isclass(raw):
            if not dunder and depth < 2:
                lines.add(f"class {key}{sig(raw)}")
                members(key, raw, depth + 1)
        elif inspect.iscoroutinefunction(raw):
            lines.add(f"async-method {key}{sig(raw)}")
        elif callable(raw):
            lines.add(f"method {key}{sig(raw)}")
        elif dunder:
            continue
        elif hasattr(type(raw), "__get__"):
            lines.add(f"property {key}")
        else:
            lines.add(f"attribute {key}: {type(raw).__name__}")


def listing(name, mod, tree):
    lines.add(f"module {name}")
    names = getattr(mod, "__all__", None)
    explicit = isinstance(names, (list, tuple))
    names = [n for n in names if isinstance(n, str)] if explicit else [n for n in vars(mod) if not n.startswith("_")]
    here = defined(tree) if tree is not None else None
    for n in sorted(set(names)):
        key = f"{name}.{n}"
        try:
            obj = getattr(mod, n)
        except AttributeError:
            lines.add(f"missing {key}")
            continue
        if inspect.ismodule(obj):
            continue
        if inspect.isclass(obj):
            if explicit or in_project(obj.__module__):
                lines.add(f"class {key}{sig(obj)}")
                members(key, obj)
        elif inspect.isroutine(obj):
            if explicit or in_project(getattr(obj, "__module__", None)):
                kind = "async-function" if inspect.iscoroutinefunction(obj) else "function"
                lines.add(f"{kind} {key}{sig(obj)}")
        elif callable(obj):
            if explicit or here is None or n in here:
                lines.add(f"callable {key}{sig(obj)}")
        elif explicit or here is None or n in here:
            lines.add(f"constant {key}: {type(obj).__name__}")


for name in sorted(found):
    if not public(name):
        continue
    root, path = found[name]
    tree = tree_of(path) if path else None
    if tree is not None and runs_code(tree):
        static(name, tree)
        continue
    try:
        mod = importlib.import_module(name)
    except BaseException as e:
        lines.add(f"import-error {name}: {type(e).__name__}")
        continue
    if tree is None and getattr(mod, "__file__", None):
        tree = tree_of(mod.__file__)
    listing(name, mod, tree)
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    json.dump({"lines": sorted(lines), "modules": sorted(n for n in found if public(n))}, fh)
'''


def surface_folders(cfg: dict, ws: Path) -> list[str]:
    """Where the probe looks for modules: the scope's folder (a file's own folder), or the changed files' folders."""
    scope = cfg["scope"]
    if scope["kind"] == "path":
        p = scope.get("path") or "."
        top = Path(cfg["repo"]) / p
        return [p if top.is_dir() else (Path(p).parent.as_posix() or ".")]
    if scope["kind"] == "repo":
        return ["."]
    folders = sorted({Path(f).parent.as_posix() for f in kit.scope_files(ws) if f.endswith(".py")})
    keep = []
    for f in folders:
        if not any(f == k or f.startswith(k + "/") or k == "." for k in keep):
            keep.append(f)
    return keep or ["."]


def take_surface(root: Path, s: dict, ws: Path) -> tuple[list[str] | None, str | None]:
    """The interface as the probe (or the lead's command) prints it in this copy: (lines, problem)."""
    if s["kind"] == "command":
        given = copy_renamed(ws / "surface" / "files", root / "scratch")
        try:
            r = execute(root, s["command"], s.get("cwd"), s.get("timeout") or 300)
        finally:
            for p in given:
                p.unlink(missing_ok=True)
        if r["timed_out"] or r["code"] != 0:
            return None, f"the interface command failed (exit {r['code']}): {tail(r['out'] + r['err'], 6)}"
        text = normalise(r["out"], root, s.get("masks"), True)
        return sorted({ln.strip() for ln in text.split("\n") if ln.strip()}), None
    scratch = root / "scratch"
    probe, conf, out = scratch / "_surface_probe.py", scratch / "_surface_cfg.json", scratch / "_surface.json"
    probe.write_text(PROBE, encoding="utf-8")
    conf.write_text(json.dumps({"folders": s["folders"], "modules": s["modules"],
                                "skip_dirs": sorted(kit.SKIP_DIRS)}), encoding="utf-8")
    try:
        r = execute(root, [*s["python"], "-B", str(probe), str(conf), str(out)], None, s.get("timeout") or 300)
        if r["timed_out"] or r["code"] != 0 or not out.is_file():
            return None, f"the interface probe failed (exit {r['code']}): {tail(r['out'] + r['err'], 8)}"
        data = json.loads(out.read_text(encoding="utf-8"))
        return [normalise(ln, root, (), True) for ln in data["lines"]], None
    finally:
        for p in (probe, conf, out):
            p.unlink(missing_ok=True)


def entry_key(line: str, kind: str) -> str:
    if kind != "python":
        return line
    _, _, rest = line.partition(" ")
    m = re.match(r"[^\s(:]+", rest)
    return m.group(0) if m else rest


def compare_surface(kind: str, base: list[str], new: list[str], rules, mode: str) -> dict:
    """What changed in the interface, and what of it the plan's rules don't explain."""
    expected = {entry_key(ln, kind): ln for ln in base}
    adds, removes, changes, renamed, collisions = [], [], [], [], []
    for r in rules:
        if r[0] in ("rename", "move"):
            old, new_name = r[1], r[2]
            if kind == "python":
                moving = {k for k in expected if k == old or k.startswith(old + ".")}
                collisions += sorted(new_name + k[len(old):] for k in moving
                                     if new_name + k[len(old):] in expected and new_name + k[len(old):] not in moving)
            nxt = {}
            for key, line in expected.items():
                if kind == "python" and (key == old or key.startswith(old + ".")):
                    nk = new_name + key[len(old):]
                    word, _, rest = line.partition(" ")
                    nline = f"{word} {nk}{rest[len(key):]}"
                elif kind != "python" and re.search(rf"(?<![\w.]){re.escape(old)}(?!\w)", line):
                    nline = rename_words(line, old, new_name)
                    nk = nline
                else:
                    nk, nline = key, line
                nxt[nk] = nline
            expected = nxt
            renamed.append(f"{old} to {new_name}")
        elif r[0] == "add":
            adds.append(r[1])
        elif r[0] == "remove":
            removes.append(r[1])
        elif r[0] == "change":
            changes.append(r[1])
    got = {entry_key(ln, kind): ln for ln in new}
    hit = lambda k, pats: any(fnmatch.fnmatchcase(k, p) for p in pats)
    missing = [expected[k] for k in sorted(expected) if k not in got and not hit(k, removes)]
    added = [got[k] for k in sorted(got) if k not in expected and not hit(k, adds)]
    changed = [(expected[k], got[k]) for k in sorted(expected) if k in got and expected[k] != got[k]
               and not hit(k, changes)]
    ok = mode == "free" or not (missing or added or changed or collisions)
    return {"ok": ok, "missing": missing, "added": added, "changed": changed, "renamed": renamed,
            "collisions": collisions,
            "allowed_new": sum(1 for k in got if k not in expected and hit(k, adds)),
            "allowed_removed": sum(1 for k in expected if k not in got and hit(k, removes)),
            "allowed_changed": sum(1 for k in expected if k in got and expected[k] != got[k] and hit(k, changes)),
            "differences": len(set(base) ^ set(new)), "mode": mode}


def surface_line(cmp: dict) -> str:
    if cmp.get("collisions") and cmp["mode"] != "free":
        return (f"the plan renames onto {plural(len(cmp['collisions']), 'name')} that already exist "
                f"({', '.join(cmp['collisions'][:4])}), so the rename can't be told apart from replacing them")
    bad = cmp["missing"] or cmp["added"] or cmp["changed"]
    if not bad:
        if not cmp["differences"]:
            return "unchanged"
        bits = [f"renamed {and_list(cmp['renamed'])}" if cmp["renamed"] else ""]
        for key, word in (("allowed_new", "new"), ("allowed_removed", "removed"), ("allowed_changed", "changed")):
            if cmp.get(key):
                bits.append(f"{plural(cmp[key], 'entry', 'entries')} {word}, as the plan allows")
        return "changed only as planned: " + "; ".join(b for b in bits if b)
    parts = [f"removed `{x}`" for x in cmp["missing"][:4]] + [f"added `{x}`" for x in cmp["added"][:4]] + \
            [f"`{x}` is now `{y}`" for x, y in cmp["changed"][:4]]
    n = len(cmp["missing"]) + len(cmp["added"]) + len(cmp["changed"])
    if cmp["mode"] == "free":
        return f"{plural(n, 'difference')}, allowed (the interface is free to change): " + "; ".join(parts)
    return f"{plural(n, 'difference')} the plan doesn't allow: " + "; ".join(parts) + ("; ..." if n > len(parts) else "")


# ---------------------------------------------------------------- tests, pins and the whole check

def run_tests(root: Path, st: dict, timeout: float | None = None) -> tuple[bool, str, str | None]:
    t = st["tests"]
    res = kit.run_in(root, t["cmd"], 1, timeout or t["timeout"], t.get("cwd"), log=False)
    r = res["runs"][0]
    if not kit.failed(r):
        return True, "passed", None
    names = failing_names(r["out"])
    flaky = set((st.get("baseline") or {}).get("flaky") or [])
    if names and not r["timed_out"] and set(names) <= flaky:
        return True, f"passed, apart from {plural(len(names), 'flaky test')} left out ({', '.join(names)})", None
    return False, "FAILED" + (f" ({', '.join(names[:8])})" if names else ""), tail(kit.summary(res), 30)


def pinned(st: dict) -> list[dict]:
    return [p for p in st["pins"] if p["status"] == "pinned"]


def write_pins(ws: Path, root: Path, pins, ren: Renames = NO_RENAMES) -> None:
    for p in pins:
        copy_renamed(ws / "pins" / p["id"] / "files", root / "repo", ren)


def check_all(ws: Path, cfg: dict, st: dict, root: Path, ids, pins_in_tree: bool = False) -> list[dict]:
    """Every recorded run, the interface, every pin and the tests, in a prepared copy."""
    if kit.git(root / "repo", *kit.RAW, "status", "--porcelain", check=False).strip():
        commit_state(root)   # a recorded run's clean-up resets the copy to its last commit
    rules = rules_of(st, ids)
    ren = renames_for(st, ids)
    forms = spellings(root)
    out = []
    for g in st["golden"].values():
        rec = run_recorded(ws, cfg, root, g, ren, forms)
        d = first_diff(g["expected"], rec)
        line = f"recorded run {g['name']}: " + ("same" if d is None else f"DIFFERENT: {d}")
        if d and ren and not first_diff(renamed_record(g["expected"], ren), rec):
            line += (". It differs only by the planned renames (a name in a message or a repr, say); if that's all "
                     "it is, record the run again with that name masked")
        out.append({"check": "recorded run", "name": g["name"], "ok": d is None, "line": line})
    s = st["surface"]
    if s and s["kind"] != "none":
        lines, err = take_surface(root, s, ws)
        if err:
            out.append({"check": "interface", "ok": False, "line": f"interface: {err}"})
        else:
            cmp = compare_surface(s["kind"], s["lines"], lines, rules, cfg["interface"])
            out.append({"check": "interface", "ok": cmp["ok"], "line": f"interface: {surface_line(cmp)}",
                        "compare": {k: v for k, v in cmp.items() if k != "ok"}})
    pins = pinned(st)
    if pins and not pins_in_tree:
        write_pins(ws, root, pins, ren)
    for p in pins:
        res = kit.run_in(root, ren.args(p["command"]), 1, p["timeout"], None, log=False)
        ok = not kit.failed(res["runs"][0])
        out.append({"check": "pins", "name": p["id"], "ok": ok,
                    "line": f"pin {p['id']} ({', '.join(p['files'])}): " + ("passes" if ok else "FAILED"),
                    "detail": None if ok else tail(kit.summary(res), 20)})
    ok, line, detail = run_tests(root, st)
    out.append({"check": "tests", "ok": ok, "line": f"tests: {line}", "detail": detail})
    return out


def restale(s: dict) -> None:
    """After the accepted steps change, a step verified on top of other steps is stale: redo it."""
    for x in s["candidates"]:
        if x["status"] == "verified" and x["on"] != s["chain"]:
            x["status"] = "stale"


def verdict_of(results: list[dict]) -> str:
    for r in results:
        if not r["ok"]:
            return f"{REJECTED}: {r['check']}"
    return "verified"


def ensure_ready(st: dict) -> None:
    if not st.get("tests"):
        kit.fail("no test command: init --test '<command>' (or baseline -- <command>)")
    b = st.get("baseline")
    if not b:
        kit.fail("run baseline first: a check means nothing until the tests pass on the original code")
    if not b["ok"]:
        kit.fail(f"the baseline was {b['verdict']}: fix the test command (baseline -- <command>) and run it again")
    if not st["golden"]:
        kit.fail("no recorded runs yet: golden add -w <run> <name> -- <command that exercises the code in scope>")
    if not st.get("surface"):
        kit.fail("no interface recorded yet: surface record -w <run> (or --command '<cmd>', or --none --why '...')")


# ---------------------------------------------------------------- commands: setup

def cmd_init(a) -> None:
    test_cmd = a.argv_cmd or (parse_cmd(a.test) if a.test else None)
    path = Path(a.repo).expanduser().resolve()
    if not path.is_dir():
        kit.fail(f"no folder {path}")
    top, is_git = kit.find_top(path)
    base = None
    if is_git and kit.has_head(top):
        base = kit.git(top, "rev-parse", "--verify", (a.rev or "HEAD") + "^{commit}").strip()
    elif a.rev:
        kit.fail("--rev needs a git repository with commits")
    protect = list(dict.fromkeys(DEP_PATTERNS + list(a.protect or [])))
    extra = {"base_rev": base, "interface": a.interface, "depth": a.depth, "pins": a.pins, "after": a.after,
             "protect": protect, "branch_name": a.branch, "status_at_init": kit.git_status(top) if is_git else None}
    ws, cfg, files = kit.init_run(SKILL, str(path), scope=a.scope, slug=a.slug or "refactor", target=a.target,
                                  network=a.network, include=a.include or [], exclude=a.exclude or [],
                                  env=a.env or [], workspace=a.workspace, force=a.force, extra=extra)
    if a.force:   # starting over: nothing from the old run may count
        for d in ("golden", "candidates", "pins", "probes"):
            kit.rmtree(ws / d)
        for f in ("report.md", "summary.json"):
            (ws / f).unlink(missing_ok=True)
    st = new_state()
    if test_cmd:
        st["tests"] = {"cmd": test_cmd, "cwd": a.cwd, "timeout": a.test_timeout}
    kit.save_json(ws / STATE, st)
    if base:
        print(f"Base: {short(base)}" + (f" ({a.rev})" if a.rev else (f" ({cfg['branch']})" if cfg["branch"] else "")))
        dirty = [ln for ln in (cfg["status_at_init"] or "").splitlines() if ln.strip()]
        if dirty:
            print(f"note: {plural(len(dirty), 'uncommitted change')} and untracked files are left out: every copy is "
                  f"made from {short(base)}. Commit them first if the refactor is about them.")
    else:
        print("Base: the folder as it is (not a git repository with commits, so land can't commit anything)")
    code = [f for f in files if is_code(f)]
    print(f"Code in scope: {plural(len(code), 'file')}" + (f" ({', '.join(code[:5])}{', ...' if len(code) > 5 else ''})"
                                                          if code else ""))
    print(f"Interface: {INTERFACES[a.interface]}. Depth: {a.depth}. Pins: {'kept in the project' if a.pins == 'keep' else 'this run only'}. "
          f"After: {a.after}.")
    print("Tests: " + (show_cmd(test_cmd) if test_cmd else "not set yet (baseline -- <command>)"))
    print(f"Next: refactor.py baseline -w {ws}")


def cmd_baseline(a) -> None:
    ws, cfg, st = load(a)
    if a.argv_cmd:
        t = {"cmd": a.argv_cmd, "cwd": a.cwd, "timeout": a.timeout or 600}
        mutate(ws, lambda s: s.__setitem__("tests", t))
        st = load_state(ws)
    if not st.get("tests"):
        kit.fail("no test command: baseline -w <run> -- <command>")
    t = st["tests"]
    print(f"Running {show_cmd(t['cmd'])} {RUNS} times in a fresh copy of {short(cfg['base_rev'])} ...", flush=True)
    with temp_box(ws, cfg, st, "baseline") as root:
        res = kit.run_in(root, t["cmd"], RUNS, t["timeout"], t.get("cwd"), log=False)
    runs = res["runs"]
    bad = [r for r in runs if kit.failed(r)]
    per_run = [set(failing_names(r["out"])) for r in bad]
    unnamed = any(kit.failed(r) and not names for r, names in zip(bad, per_run))
    always = set.intersection(*per_run) if len(bad) == len(runs) and per_run else set()
    flaky = sorted(set().union(*per_run) - always) if per_run else []
    secs = sorted(r["secs"] for r in runs)[len(runs) // 2]
    if not bad:
        verdict = "clean"
    elif unnamed or any(r["timed_out"] for r in bad):
        verdict = "unclear"
    elif always:
        verdict = "failing"
    else:
        verdict = "flaky"
    rec = {"ok": verdict in ("clean", "flaky"), "verdict": verdict, "runs": [
        {"code": r["code"], "secs": r["secs"], "timed_out": r["timed_out"]} for r in runs],
        "flaky": flaky, "failing": sorted(always), "secs": secs, "at": kit.now(), "rev": cfg["base_rev"]}
    mutate(ws, lambda s: s.__setitem__("baseline", rec))
    passed = len(runs) - len(bad)
    if verdict == "clean":
        print(f"Baseline: the tests passed {passed} of {len(runs)} runs on the original code (median {secs:.1f} s).")
        return
    print(kit.summary(res))
    if verdict == "flaky":
        decide_now(ws, f"These tests flipped between runs on the original code, so they're left out of every "
                       f"verdict: {', '.join(flaky)}")
        print(f"\nBaseline: {passed} of {len(runs)} runs passed; these tests flipped on the original code and are "
              f"left out of every verdict from now on: {', '.join(flaky)}. Recorded under decisions.")
        return
    if verdict == "failing":
        print(f"\nBaseline: FAILING: these tests fail every time on the original code: {', '.join(sorted(always))}. "
              "A check can't tell a broken step from code that was already broken. Leave them out of the test "
              "command (baseline -- <command>), record why with decide, and run baseline again.")
    else:
        print("\nBaseline: UNCLEAR: a run failed without naming which tests failed (or it timed out). Fix the test "
              "command so it passes on the original code, then run baseline again.")
    sys.exit(1)


def cmd_golden_add(a) -> None:
    ws, cfg, st = load(a)
    check_name(a.name, "recorded run")
    cmd = a.argv_cmd
    if not cmd:
        kit.fail("give the command after --, like: golden add -w <run> report -- python tool.py report data.csv")
    if a.name in st["golden"] and not a.replace:
        kit.fail(f"there's already a recorded run called {a.name}; pass --replace to change it")
    for rx in a.mask or []:
        try:
            re.compile(rx)
        except re.error as e:
            kit.fail(f"--mask {rx!r}: {e}")
    gives = [Path(f).expanduser() for f in a.give or []]
    for f in gives + ([Path(a.stdin)] if a.stdin else []):
        if not f.is_file():
            kit.fail(f"{f}: no such file")
    folder, staging = ws / "golden" / a.name, ws / "golden" / f".{a.name}-{secrets.token_hex(2)}"
    (staging / "files").mkdir(parents=True)
    for f in gives:
        shutil.copy2(f, staging / "files" / f.name)
    if a.stdin:
        shutil.copy2(a.stdin, staging / "stdin")
    g = {"name": a.name, "cmd": cmd, "cwd": a.cwd, "timeout": a.timeout, "masks": list(a.mask or []),
         "exact": a.exact, "files": [f.name for f in gives], "stdin": bool(a.stdin), "kind": "lead",
         "added": kit.now()}
    print(f"Running {show_cmd(cmd)} in two fresh copies of {short(cfg['base_rev'])} ...", flush=True)
    try:   # two copies, in folders of different names and lengths: output that depends on where it runs shows
        with temp_box(ws, cfg, st, "g1") as root:
            one = run_recorded(ws, cfg, root, g, NO_RENAMES, None, staging)
        with temp_box(ws, cfg, st, f"golden-second-copy-{a.name}") as root:
            two = run_recorded(ws, cfg, root, g, NO_RENAMES, None, staging)
        if one["exit"] == "timeout":
            kit.fail(f"it timed out after {a.timeout:g} s on the original code; give it a smaller input or --timeout")
        if one["exit"] == 127 and one["stderr"].startswith("could not start"):
            kit.fail(f"{one['stderr']}. A command that can't start records nothing: check the program's name or path.")
        if cant_run(one):
            print(f"warning: it fails before reaching the code, it seems ({tail(one['stderr'], 1).strip()[:120]}). "
                  "A driver script needs the project on sys.path: sys.path.insert(0, os.getcwd()).")
        d = first_diff(one, two)
        if d:
            kit.fail(f"its output differs between two runs of the same code, in two fresh copies ({d}). A recorded "
                     "run has to give the same output every time, or a difference later means nothing. Mask the part "
                     "that varies with --mask '<regex>' (times, durations, the copy's folder and ids are masked "
                     "already), or make the command deterministic. If it prints something that depends on where the "
                     "copy is (a padded path, a path inside a large file), print less.")
    except BaseException:
        kit.rmtree(staging)
        raise
    kit.rmtree(folder)
    staging.rename(folder)
    g["expected"] = one
    mutate(ws, lambda s: s["golden"].__setitem__(a.name, g))
    print(f"Recorded run {a.name}: {describe_record(one)}; the same in both copies"
          + (f", masking {plural(len(g['masks']), 'pattern')}" if g["masks"] else "") + ".")
    if one["exit"] not in (0, "timeout"):
        print(f"note: it exits {one['exit']} on the original code; that's what every check will expect.")
    if not one["stdout"] and not one["stderr"] and not one["files"]:
        print("note: it prints nothing and writes nothing, so only its exit code is checked. Have it print what it "
              "computes.")


def cmd_golden_list(a) -> None:
    ws, cfg, st = load(a)
    if not st["golden"]:
        print("No recorded runs yet.")
        return
    for g in st["golden"].values():
        bits = [describe_record(g["expected"])]
        if g["masks"]:
            bits.append("masks " + ", ".join(f"`{m}`" for m in g["masks"]))
        if g.get("exact"):
            bits.append("no default masks")
        if g.get("kind") == "probe":
            bits.append(f"from a probe that caught {g.get('from')}")
        print(f"  {g['name']:<18} {show_cmd(g['cmd'])}  ({'; '.join(bits)})")


def cmd_golden_run(a) -> None:
    ws, cfg, st = load(a)
    names = a.names or list(st["golden"])
    for n in names:
        if n not in st["golden"]:
            kit.fail(f"no recorded run {n}")
    chain = list(st["chain"]) if a.chain or a.candidate else []
    if a.candidate:
        c = get_cand(st, a.candidate)
        if c["id"] not in chain:
            chain.append(c["id"])
    ren = renames_for(st, chain)
    where = f"with {and_list(chain)}" if chain else f"on the original code ({short(cfg['base_rev'])})"
    print(f"Rerunning {plural(len(names), 'recorded run')} {where}, in a fresh copy ...", flush=True)
    bad = 0
    try:
        with temp_box(ws, cfg, st, "golden-run", chain) as root:
            forms = spellings(root)
            for n in names:
                g = st["golden"][n]
                d = first_diff(g["expected"], run_recorded(ws, cfg, root, g, ren, forms))
                bad += d is not None
                print(f"  {n}: " + ("same" if d is None else f"DIFFERENT: {d}"))
    except PatchError as e:
        kit.fail(str(e))
    sys.exit(1 if bad else 0)


def cmd_surface_record(a) -> None:
    ws, cfg, st = load(a)
    if a.none:
        s = {"kind": "none", "why": a.why or "", "at": kit.now()}
        mutate(ws, lambda x: x.__setitem__("surface", s))
        decide_now(ws, "No interface is checked" + (f": {a.why}" if a.why else ""))
        print("No interface will be checked; recorded under decisions.")
        return
    if a.give and not a.command:
        kit.fail("--give goes with --command: the files a listing command needs")
    if a.command:
        s = {"kind": "command", "command": parse_cmd(a.command), "cwd": a.cwd, "timeout": a.timeout,
             "masks": list(a.mask or []), "give": [Path(f).name for f in a.give or []]}
        kit.rmtree(ws / "surface")
        (ws / "surface" / "files").mkdir(parents=True)
        for f in a.give or []:
            if not Path(f).is_file():
                kit.fail(f"--give {f}: no such file")
            shutil.copy2(f, ws / "surface" / "files" / Path(f).name)
    else:
        s = {"kind": "python", "python": parse_cmd(a.python) if a.python else [sys.executable],
             "folders": a.folder or surface_folders(cfg, ws), "modules": list(a.module or []), "timeout": a.timeout}
    what = show_cmd(s["command"]) if s["kind"] == "command" else \
        f"a Python probe over {and_list(s['folders'] + s['modules'])}"
    print(f"Recording the interface with {what}, twice, in a fresh copy ...", flush=True)
    with temp_box(ws, cfg, st, "surface") as root:
        one, err = take_surface(root, s, ws)
        if err:
            kit.fail(err)
        two, err = take_surface(root, s, ws)
    if err or one != two:
        kit.fail("the interface listing differs between two runs of the same code: " + (err or
                 f"{len(set(one) ^ set(two))} lines differ") + ". Make the listing deterministic first.")
    if not one:
        kit.fail("the listing is empty: name the modules with --module, the folder with --folder, or give a "
                 "command that prints the interface with --command")
    s["lines"], s["at"] = one, kit.now()
    mutate(ws, lambda x: x.__setitem__("surface", s))
    kinds: dict[str, int] = {}
    for ln in one:
        k = ln.split(" ", 1)[0] if s["kind"] == "python" else "line"
        kinds[k] = kinds.get(k, 0) + 1
    print(f"Interface: {plural(len(one), 'entry', 'entries')} (" + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
          + "). The same both times.")
    for ln in one[:a.show]:
        print(f"  {ln}")
    if len(one) > a.show:
        print(f"  ... and {len(one) - a.show} more (in {ws / STATE})")
    errors = [ln for ln in one if ln.startswith("import-error ")]
    if errors:
        print(f"note: {plural(len(errors), 'module')} didn't import on the original code ({', '.join(errors[:3])}); "
              "check the probe's Python (--python) and the sandbox's includes.")


def cmd_surface_compare(a) -> None:
    ws, cfg, st = load(a)
    s = st.get("surface")
    if not s or s["kind"] == "none":
        kit.fail("no interface recorded")
    chain = list(st["chain"])
    if a.candidate:
        c = get_cand(st, a.candidate)
        if c["id"] not in chain:
            chain.append(c["id"])
    try:
        with temp_box(ws, cfg, st, "surface-compare", chain) as root:
            lines, err = take_surface(root, s, ws)
    except PatchError as e:
        kit.fail(str(e))
    if err:
        kit.fail(err)
    cmp = compare_surface(s["kind"], s["lines"], lines, rules_of(st, chain), cfg["interface"])
    print(f"Interface with {and_list(chain) or 'nothing applied'}: {surface_line(cmp)}")
    sys.exit(0 if cmp["ok"] else 1)


def cmd_plan(a) -> None:
    ws, cfg, st = load(a)
    rules = [" ".join(parse_rule(r)) for r in a.allow or []]
    if rules and not (a.add or a.replace):
        kit.fail("--allow goes with --add or --replace")
    if cfg["interface"] == "same":
        bad = [r for r in rules if r.split()[0] != "add"]
        if bad:
            kit.fail(f"the interface was set to stay the same, so a step may only add new names, not {bad[0]!r}. "
                     "If the user asked for that rename or move, start the run with --interface planned.")
    s = st.get("surface") or {}
    if s.get("kind") == "python":
        keys = {entry_key(ln, "python") for ln in s["lines"]}
        for r in rules:
            p = r.split()
            if p[0] in ("rename", "move", "remove", "change") and not any(
                    fnmatch.fnmatchcase(k, p[1]) or k.startswith(p[1] + ".") for k in keys):
                print(f"warning: {p[1]} isn't in the recorded interface; check the name (surface record lists it)")
            if p[0] in ("rename", "move") and p[2] in keys:
                print(f"warning: {p[2]} is in the interface already; renaming {p[1]} onto it will be refused")
    if a.add:
        def add(x):
            sid = f"S{x['next']['S']}"
            x["next"]["S"] += 1
            x["plan"].append({"id": sid, "title": a.add.strip(), "allow": rules, "status": "planned",
                              "candidates": [], "added": kit.now()})
            return sid
        sid = mutate(ws, add)
        print(f"{sid}: {a.add.strip()}" + (f" (may: {'; '.join(rules)})" if rules else " (no interface changes)"))
    elif a.replace:
        def rep(x):
            step = get_step(x, a.replace)
            if any(get_cand(x, cid)["status"] == "accepted" for cid in step["candidates"]):
                kit.fail(f"{step['id']} has an accepted step; it can't be changed now")
            if a.title:
                step["title"] = a.title.strip()
            step["allow"] = rules
            return step
        step = mutate(ws, rep)
        print(f"{step['id']}: {step['title']}" + (f" (may: {'; '.join(rules)})" if rules else " (no interface changes)")
              + ". Verify its candidates again.")
    elif a.drop:
        def drop(x):
            step = get_step(x, a.drop)
            if any(get_cand(x, cid)["status"] == "accepted" for cid in step["candidates"]):
                kit.fail(f"{step['id']} has an accepted step; it can't be dropped now")
            step["status"] = "dropped"
            return step
        step = mutate(ws, drop)
        print(f"{step['id']}: dropped.")
    st = load_state(ws)
    if not st["plan"]:
        print("No steps yet: plan -w <run> --add '<one step>' [--allow 'rename <old> <new>'] ...")
        return
    print("\nThe plan:")
    for step in st["plan"]:
        print(f"  {step['id']}. {step['title']} [{step_status(st, step)}]"
              + (f"  may: {'; '.join(step['allow'])}" if step["allow"] else ""))


def step_status(st: dict, step: dict) -> str:
    if step.get("status") == "dropped":
        return "dropped"
    stats = [get_cand(st, cid)["status"] for cid in step["candidates"]]
    if "accepted" in stats:
        return "accepted"
    if "verified" in stats:
        return "verified, waiting for a challenger"
    if stats and all(s.startswith(REJECTED) or s == "stale" for s in stats):
        return "rejected, not redone yet"
    return "candidate waiting for verify" if stats else "planned"


# ---------------------------------------------------------------- commands: agents' copies

ROLES = {"ref": "refactorer", "refactor": "refactorer", "pin": "pin writer", "chal": "challenger",
         "challenge": "challenger"}


def cmd_sandbox(a) -> None:
    ws, cfg, st = load(a)
    check_name(a.name, "sandbox")
    role = a.role or ROLES.get(a.name.split("-", 1)[0].lower(), "lead")
    role = {"refactorer": "refactorer", "pin": "pin writer", "pin writer": "pin writer",
            "challenger": "challenger", "lead": "lead"}.get(role)
    if not role:
        kit.fail("--role is refactorer, pin, challenger or lead")
    chain, cid = list(st["chain"]), None
    if role == "challenger" and not a.candidate:
        kit.fail("a challenger's copy needs --candidate <id>: the step it challenges, applied")
    if a.candidate:
        c = get_cand(st, a.candidate)
        cid = c["id"]
        if c["status"] not in ("verified", "accepted"):
            kit.fail(f"{cid} is '{c['status']}': only a verified step gets a challenger")
        chain = chain[:chain.index(cid)] if cid in chain else chain
    if role == "pin writer":
        chain = []
    try:
        root = fresh_box(ws, cfg, st, a.name, chain, commit=True, name=a.name)
    except PatchError as e:
        kit.fail(str(e))
    if cid:
        ok, msg = kit.apply_patch(root / "repo", kit.read_patch(ws / get_cand(st, cid)["patch"]))
        if not ok:
            kit.drop_sandbox(ws, cfg, a.name)
            kit.fail(f"{cid} doesn't apply: {msg}")
    for f in a.give or []:
        src = Path(f).expanduser()
        if not src.exists():
            kit.drop_sandbox(ws, cfg, a.name)
            kit.fail(f"--give {f}: no such file")
        kit.copy_path(src, root / "scratch" / src.name)

    def rec(s):
        s["boxes"][a.name] = {"role": role, "chain": chain, "candidate": cid, "at": kit.now()}
        if role != "lead":
            s["agents"][role] = s["agents"].get(role, 0) + 1
    mutate(ws, rec)
    what = {"refactorer": f"the original code with {and_list(chain)} applied and committed" if chain
            else "the original code", "pin writer": "the original code, with no steps applied",
            "challenger": f"{cid} applied, uncommitted, on top of " + (and_list(chain) or "the original code"),
            "lead": (f"{and_list(chain)} applied" if chain else "the original code")
            + (f", and {cid} uncommitted" if cid else "")}[role]
    print(f"Sandbox {a.name} for a {role} is ready: {what}.")
    print(kit.sandbox_text(root, cfg["network"]))
    if a.give:
        print(f"  given:        {', '.join(Path(f).name for f in a.give)} (in the scratch folder)")


def reply_source(a, cfg) -> tuple[Path, Path | None]:
    if bool(getattr(a, "reply", None)) == bool(a.sandbox):
        kit.fail("give the reply file, or --sandbox <name> to read its reply.json")
    if a.sandbox:
        box = kit.find_sandbox(cfg, a.sandbox)
        return box / "scratch" / kit.REPLY, box
    return Path(a.reply), None


def drop_box(ws: Path, cfg: dict, name: str) -> None:
    kit.drop_sandbox(ws, cfg, name)
    mutate(ws, lambda s: s["boxes"].pop(name, None))


def test_changes_line(old: dict, root: Path, ren: Renames) -> tuple[bool, str]:
    """Did the step change test files only by its planned renames, exactly?"""
    bad = []
    for f, before in old.items():
        now = root / "repo" / f
        if before is None or not now.is_file() or not ren:
            bad.append(f)
        elif ren.code(before.decode("utf-8", "surrogateescape"), f) != read_text(now):
            bad.append(f)
    if bad:
        return False, (f"test files: {', '.join(bad)} changed beyond the renames the plan allows. Tests prove the "
                       "behaviour, so a step may change them only by its planned renames, exactly")
    return True, f"test files: {', '.join(old)} changed only by the planned renames"


def cmd_candidate_add(a) -> None:
    ws, cfg, st = load(a)
    step = get_step(st, a.step)
    if step.get("status") == "dropped":
        kit.fail(f"{step['id']} was dropped from the plan")
    if any(get_cand(st, x)["status"] == "accepted" for x in step["candidates"]):
        kit.fail(f"{step['id']} already has an accepted step")
    if bool(a.sandbox) == bool(a.patch):
        kit.fail("give --sandbox <name> (a refactorer's copy) or --patch <file>")
    reply, box, chain = {}, None, list(st["chain"])
    if a.sandbox:
        box = kit.find_sandbox(cfg, a.sandbox)
        info = st["boxes"].get(a.sandbox) or {}
        if info.get("candidate"):
            kit.fail(f"that copy has {info['candidate']} applied uncommitted, so its diff isn't one step")
        if info.get("role") == "pin writer":
            kit.fail("that's a pin writer's copy: collect it with pin collect")
        if info.get("chain", chain) != chain:
            kit.fail(f"that copy was made on top of {and_list(info.get('chain')) or 'the original code'}, but the "
                     f"accepted steps are now {and_list(chain) or 'none'}. Make a fresh copy and redo the step there.")
        patch = kit.box_diff(box)
        rp = box / "scratch" / kit.REPLY
        if rp.is_file():
            try:
                reply = kit.read_reply(rp)
            except SystemExit:
                if not a.title:
                    raise
                print("warning: its reply.json doesn't parse; using --title")
        source = f"refactorer:{a.sandbox}"
    else:
        if not Path(a.patch).is_file():
            kit.fail(f"no patch file {a.patch}")
        patch = kit.read_patch(Path(a.patch))
        source = f"patch:{Path(a.patch).name}"
    if reply.get("done") is False and not a.force:
        kit.fail(f"the refactorer says it made no safe change: {reply.get('reason') or 'no reason given'}. Record that "
                 "with decide, or pass --force to keep what it left")
    if not patch.strip():
        kit.fail("no changes in that copy, so there's nothing to check")
    files = kit.changed_files(patch)
    tests_changed = [f for f in files if is_test(f)]
    hits = [f for f in files if matches(f, cfg["protect"]) and not is_test(f)]
    if hits and not a.allow_protected:
        kit.fail(f"the step changes files a refactor leaves alone: {', '.join(hits)}. Dependency, build and CI files "
                 "aren't part of a refactor. If the change really needs them, pass --allow-protected (it's recorded "
                 "under decisions).")
    mention = attribution_hits(added_lines(patch))
    if mention and not a.allow_mention:
        kit.fail(f"the step's added lines mention {', '.join(mention)}. Nothing written into the project may say who "
                 "or what made it: take it out. If the project itself is about these, pass --allow-mention.")
    title = (a.title or reply.get("title") or step["title"]).strip().splitlines()[0]
    summary = (a.summary or reply.get("summary") or "").strip()

    def add(s):
        cid = f"C{s['next']['C']}"
        s["next"]["C"] += 1
        rel = f"candidates/{cid}.patch"
        kit.write_patch(ws / rel, patch)
        c = {"id": cid, "step": step["id"], "title": title, "summary": summary, "patch": rel, "files": files,
             "on": chain, "source": source, "status": "new", "verify": None, "probes": [], "reply": reply,
             "tests_changed": tests_changed, "tests_allowed": bool(a.allow_protected and tests_changed),
             "allowed_protected": hits + (tests_changed if a.allow_protected else []), "allow_mention": bool(mention),
             "notes": [], "added": kit.now()}
        s["candidates"].append(c)
        get_step(s, step["id"])["candidates"].append(cid)
        if c["allowed_protected"]:
            s["decisions"].append({"text": f"{cid} ({step['id']}) was allowed to change protected files: "
                                           f"{', '.join(c['allowed_protected'])}", "at": kit.now()})
        return c
    c = mutate(ws, add)
    if box and not a.keep:
        drop_box(ws, cfg, a.sandbox)
    print(f"{c['id']} for {step['id']}: {title} ({plural(len(files), 'file')}: {', '.join(files[:6])}"
          + (f", and {len(files) - 6} more" if len(files) > 6 else "") + ").")
    if tests_changed and not a.allow_protected:
        print(f"It changes test files ({', '.join(tests_changed)}): verify checks they changed only by the step's "
              "planned renames.")
    churn = line_ending_churn(patch)
    if churn:
        print(f"warning: it rewrites the line endings of {', '.join(churn)}, so its commit would touch every line. "
              "Ask the refactorer to keep line endings as they were.")
    print(f"Next: refactor.py verify -w {ws} {c['id']}")


def cmd_candidate_show(a) -> None:
    ws, cfg, st = load(a)
    c = get_cand(st, a.id)
    step = get_step(st, c["step"])
    print(f"{c['id']}: {step['id']}. {step['title']}")
    print("Interface changes the plan allows: " + ("; ".join(step["allow"]) if step["allow"] else "none"))
    if a.with_summary and c.get("summary"):
        print(f"The refactorer's account: {c['summary']}")
    print("\n```diff")
    print(kit.read_patch(ws / c["patch"]).rstrip("\n"))
    print("```")


# ---------------------------------------------------------------- commands: verify and probe

def cmd_verify(a) -> None:
    ws, cfg, st = load(a)
    ensure_ready(st)
    c = get_cand(st, a.id)
    if c["status"] == "accepted":
        kit.fail(f"{c['id']} is accepted already")
    if c["status"] == f"{REJECTED}: behaviour change":
        kit.fail(f"a probe showed {c['id']} changes behaviour; that stands. Redo the step (a fresh sandbox).")
    chain = list(st["chain"])
    if c["on"] != chain:
        kit.fail(f"{c['id']} was made on top of {and_list(c['on']) or 'the original code'}, but the accepted steps are "
                 f"now {and_list(chain) or 'none'}. Redo the step in a fresh copy.")
    step = get_step(st, c["step"])
    if any(get_cand(st, x)["status"] == "accepted" for x in step["candidates"]):
        kit.fail(f"{step['id']} already has an accepted step")
    warn_if_project_changed(cfg)
    print(f"Verifying {c['id']} ({step['id']}. {step['title']}) on top of {and_list(chain) or 'the original code'}, "
          "in a fresh copy ...", flush=True)
    try:
        root = fresh_box(ws, cfg, st, f"verify-{c['id']}", chain)
    except PatchError as e:
        kit.fail(f"the accepted steps don't apply any more: {e}")
    try:
        tests_changed = [f for f in c["files"] if is_test(f)]
        old = {f: (root / "repo" / f).read_bytes() if (root / "repo" / f).is_file() else None for f in tests_changed}
        ok, msg = kit.apply_patch(root / "repo", kit.read_patch(ws / c["patch"]))
        if not ok:
            results = [{"check": "doesn't apply", "ok": False,
                        "line": f"patch: it doesn't apply on top of {and_list(chain) or 'the original code'}: "
                                f"{tail(msg, 4)}"}]
        else:
            commit_state(root)
            results = []
            if tests_changed and not c.get("tests_allowed"):
                fine, line = test_changes_line(old, root, Renames(rename_pairs([r.split() for r in step["allow"]]),
                                                                  classes_of(st)))
                results.append({"check": "test files", "ok": fine, "line": line})
            results += check_all(ws, cfg, st, root, chain + [c["id"]])
    finally:
        kit.drop_sandbox(ws, cfg, root.name)
    for r in results:
        print(f"  {r['line']}")
        if not r["ok"] and r.get("detail"):
            print("    " + r["detail"].replace("\n", "\n    "))
    verdict = verdict_of(results)
    depth = cfg["depth"]
    status = "accepted" if verdict == "verified" and MIN_PROBES[depth] == 0 else verdict
    rec = {"verdict": verdict, "results": [{k: v for k, v in r.items() if k != "detail"} for r in results],
           "on": chain, "at": kit.now()}

    def save(s):
        x = get_cand(s, c["id"])
        x["verify"] = rec
        x["status"] = status
        if s["chain"] != chain and verdict == "verified":   # another step was accepted while this one ran
            x["status"] = "stale"
        elif status == "accepted":
            s["chain"].append(x["id"])
            restale(s)
        return x
    c = mutate(ws, save)
    warn_if_project_changed(cfg)
    if c["status"] == "stale":
        print(f"{c['id']}: the accepted steps changed while it was being checked, so it's stale: redo it on top of them.")
        sys.exit(1)
    if status == "accepted":
        print(f"{c['id']}: accepted: every check held (quick depth: no challenger).")
    elif verdict == "verified":
        print(f"{c['id']}: verified: every check held. Next: a challenger, in a copy with it applied: "
              f"refactor.py sandbox -w {ws} chal-{c['id']} --candidate {c['id']}")
    else:
        what = next(r for r in results if not r["ok"])
        print(f"{c['id']}: {verdict}" + (f" ({what['name']})" if what.get("name") else "")
              + ". The lines above say which check failed and how; the step isn't kept.")
    sys.exit(0 if verdict == "verified" else 1)


CANT_RUN = re.compile(r"ModuleNotFoundError|ImportError|can't open file|No such file or directory|"
                      r"is not recognized as an internal or external command|command not found|could not start")


def cant_run(rec: dict) -> bool:
    """A probe that failed before it reached the code: a missing module or file, a command that isn't there."""
    return rec["exit"] not in (0, "timeout") and bool(CANT_RUN.search(rec["stderr"]))


def judge_probe(old: dict, new: dict, ren: Renames, again) -> tuple[str, str | None]:
    """A probe's outcome from its runs: same, different, unstable, renamed or broken, and the difference.

    A difference counts when the original code gives the same output on a second run (again() makes one more
    run of each side). The changed code doesn't have to: output that stops repeating is a change too."""
    if cant_run(old):
        return "broken", None
    d = first_diff(old, new)
    if not d:
        return "same", None
    if ren and not first_diff(renamed_record(old, ren), new):
        return "renamed", d
    old2, new2 = again()
    if first_diff(old, old2):
        return "unstable", first_diff(old, old2)
    if first_diff(old, new2) is None:
        return "different", f"on its first run, {d}; on a second run it matched: the changed code no longer gives " \
                            "the same output every time"
    return "different", d


def run_probe_side(ws: Path, cfg: dict, st: dict, g: dict, chain, label: str, folder: Path) -> dict:
    """A probe's run in a brand-new copy: the original code (chain empty) or with the steps applied."""
    with temp_box(ws, cfg, st, label, chain) as root:
        return run_recorded(ws, cfg, root, g, renames_for(st, chain), None, folder)


def cmd_probe(a) -> None:
    ws, cfg, st = load(a)
    ensure_ready(st)
    c = get_cand(st, a.id)
    if c["status"] not in ("verified", "accepted"):
        kit.fail(f"{c['id']} is '{c['status']}': probes are for a step that passed verify")
    path, box = reply_source(a, cfg)
    data = kit.read_reply(path)
    raw = [p for p in kit.as_list(data.get("probes")) if isinstance(p, dict) and kit.as_cmd(p.get("command"))]
    if not raw:
        kit.fail("the reply has no probes with a command. A challenge with no probes proves nothing: send a fresh "
                 "challenger with the same brief.")
    chain = list(st["chain"])
    if c["id"] not in chain and c["on"] != chain:
        mutate(ws, lambda s: get_cand(s, c["id"]).__setitem__("status", "stale"))
        kit.fail(f"{c['id']} was verified on top of {and_list(c['on']) or 'the original code'}, but the accepted steps "
                 f"are now {and_list(chain) or 'none'}, so it's stale: redo it on top of them", code=1)
    new_chain = chain[:chain.index(c["id"]) + 1] if c["id"] in chain else chain + [c["id"]]
    ren = renames_for(st, new_chain)
    batch = len(c["probes"]) + 1
    folder = ws / "probes" / c["id"] / str(batch)
    kit.rmtree(folder)
    if box:
        kit.keep_scratch(box, folder)
    else:
        folder.mkdir(parents=True)
    canon = ws / "probes" / c["id"] / f"{batch}-original"   # the probes with the original names
    kit.rmtree(canon)
    copy_renamed(folder, canon / "files", ren, reverse=True)   # they were written against the new names
    results = []
    print(f"Running {plural(len(raw), 'probe')} for {c['id']}, each in two fresh copies: the original code, and "
          f"{and_list(new_chain)} applied ...", flush=True)
    for i, p in enumerate(raw, 1):
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(p.get("name") or f"probe-{i}"))[:40] or f"probe-{i}"
        g = {"name": name, "cmd": ren.args(kit.as_cmd(p["command"]), True), "cwd": None,
             "timeout": a.timeout, "masks": [], "exact": True, "files": [], "stdin": False}
        side = lambda chain_, tag: run_probe_side(ws, cfg, st, g, chain_, f"probe-{i}-{tag}", canon)
        blame = None
        try:
            old = side([], "old")
            outcome, d = judge_probe(old, side(new_chain, "new"), ren,
                                     lambda: (side([], "old2"), side(new_chain, "new2")))
            if outcome == "different":   # which step brought it in: the first that shows it
                for k in range(1, len(new_chain)):
                    if first_diff(old, side(new_chain[:k], f"at{k}")):
                        blame = new_chain[k - 1]
                        break
                blame = blame or c["id"]
        except PatchError as e:
            kit.fail(str(e))
        r = {"name": name, "what": str(p.get("what") or ""), "command": kit.as_cmd(p["command"]), "outcome": outcome,
             "difference": d if outcome not in ("same", "broken") else None, "blame": blame, "golden": None,
             "expected": old if outcome == "different" else None, "cmd_original": g["cmd"]}
        results.append(r)
        print(f"  {name}: " + {
            "same": "same output on both",
            "broken": "it can't run on the original code (" + tail(old["stderr"], 1).strip()[:120] + "), so it "
                      "doesn't count. Probes use names that exist before and after the step, and put the project "
                      "on sys.path",
            "renamed": f"it differs only by the planned renames ({d}), so it doesn't count either way",
            "unstable": f"its output varies between runs of the original code, so it doesn't count ({d})",
            "different": f"DIFFERENT: {d}"}[outcome])
    diffs = [r for r in results if r["outcome"] == "different"]
    need = MIN_PROBES[cfg["depth"]]

    def save(s):
        x = get_cand(s, c["id"])
        for n, r in enumerate(results, 1):   # a probe that caught a difference pins that behaviour from now on
            if r["outcome"] != "different":
                continue
            gname = f"probe-{c['id']}-{batch}-{n}"
            dst = ws / "golden" / gname
            kit.rmtree(dst)
            shutil.copytree(canon / "files", dst / "files")
            s["golden"][gname] = {"name": gname, "cmd": r["cmd_original"], "cwd": None, "timeout": a.timeout,
                                  "masks": [], "exact": False, "files": sorted(
                                      q.relative_to(dst / "files").as_posix() for q in (dst / "files").rglob("*")
                                      if q.is_file()), "stdin": False, "kind": "probe", "from": r["blame"],
                                  "expected": r["expected"], "added": kit.now()}
            r["golden"] = gname
        x["probes"].append({"batch": batch, "results": results, "scratch": f"probes/{c['id']}/{batch}",
                            "at": kit.now()})
        if diffs:
            culprit = diffs[0]["blame"]
            y = get_cand(s, culprit)
            y["status"] = f"{REJECTED}: behaviour change"
            y.setdefault("notes", []).append(f"probe {diffs[0]['name']} (challenging {c['id']}) showed a difference: "
                                             f"{diffs[0]['difference']}")
            if culprit in s["chain"]:
                cut = s["chain"].index(culprit)
                for later in s["chain"][cut + 1:]:
                    get_cand(s, later)["status"] = "stale"
                s["chain"] = s["chain"][:cut]
            if c["id"] != culprit:
                x["status"] = "stale"
            restale(s)
            return culprit
        total = sum(1 for b in x["probes"] for r in b["results"] if r["outcome"] == "same")
        if x["status"] == "verified" and total >= need:
            if s["chain"] != chain:   # the accepted steps changed while the probes ran
                x["status"] = "stale"
            else:
                x["status"] = "accepted"
                s["chain"].append(x["id"])
                restale(s)
        return total
    out = mutate(ws, save)
    if box and not a.keep:
        drop_box(ws, cfg, a.sandbox)
    if diffs:
        culprit = out
        print(f"{culprit}: rejected: behaviour change. Probe {diffs[0]['name']} gives a different result on the changed "
              "code; it's now a recorded run, so a redo of the step has to keep that behaviour.")
        if culprit != c["id"]:
            print(f"The difference comes from {culprit}, accepted earlier, not from {c['id']}: {culprit} and every "
                  f"step after it are out of the accepted list, and those after it are stale (redo them).")
        print("If the old behaviour is a bug this fixes, it still isn't a refactor: record it with decide for the "
              "user, and keep the fix for its own change.")
        sys.exit(1)
    c = get_cand(load_state(ws), c["id"])
    if c["status"] == "stale":
        print(f"{c['id']}: the accepted steps changed while the probes ran, so it's stale: redo it on top of them.")
        sys.exit(1)
    if c["status"] == "accepted":
        print(f"{c['id']}: accepted. {plural(out, 'probe')} found no difference between the original code and the "
              "changed code.")
    else:
        print(f"{c['id']}: {plural(out, 'probe')} found no difference so far; {need} must, at {cfg['depth']} depth. "
              "Send another challenger for the rest.")


# ---------------------------------------------------------------- commands: pins

def pin_targets(root: Path, specs) -> tuple[dict[str, set[int]], list[str]]:
    """'file:function', 'file:first-last' or 'file' -> {file: line numbers}, and the problems."""
    out: dict[str, set[int]] = {}
    problems = []
    for spec in specs:
        f, _, what = str(spec).partition(":")
        if re.fullmatch(r"[A-Za-z]", f) and what:   # a Windows drive letter
            kit.fail(f"{spec}: give the file relative to the project")
        p = root / "repo" / f
        if not p.is_file():
            problems.append(f"{f} doesn't exist in the original code")
            continue
        text = read_text(p)
        n = len(faults.split_lines(text))
        if not what:
            lines = set(range(1, n + 1))
        elif re.fullmatch(r"\d+(-\d+)?", what):
            a_, _, b_ = what.partition("-")
            lines = set(range(int(a_), int(b_ or a_) + 1))
        else:
            fs = [x for x in faults.functions(text, faults.lang_of(f))
                  if x["name"] == what or x["name"].rsplit(".", 1)[-1] == what]
            if not fs:
                problems.append(f"no function {what} in {f}")
                continue
            lines = set().union(*(set(range(x["start"], x["end"] + 1)) for x in fs))
        out.setdefault(f.replace("\\", "/"), set()).update(lines)
    return out, problems


def pin_command(st: dict, files: list[str], given) -> tuple[list[str] | None, str | None]:
    """The command that runs only the pin's files: given (and naming them), or made from the test command."""
    if given:
        joined = " ".join(given).replace("\\", "/")
        missing = [f for f in files if f not in joined and f[:-3].replace("/", ".") not in joined
                   and Path(f).stem not in joined]
        if missing:
            return None, (f"the pin's command doesn't name {', '.join(missing)}, so it might run the whole suite and "
                          "count the project's own tests as the pin's. Give a command that runs only the pin's files.")
        return list(given), None
    t = (st.get("tests") or {}).get("cmd") or []
    py = t[0] if t and re.search(r"(?i)(^|[/\\])(py|python[\d.]*)(\.exe)?$", t[0]) else sys.executable
    if any("pytest" in x for x in t):
        return [py, "-m", "pytest", "-q", *files], None
    if any("unittest" in x for x in t):
        return [py, "-m", "unittest", *files], None
    return None, "the pin has no command and the test command isn't unittest or pytest: give the pin's command"


def cmd_pin_survey(a) -> None:
    ws, cfg, st = load(a)
    ensure_ready_tests(st)
    files = [f for f in kit.scope_files(ws) if is_code(f) and faults.lang_of(f)]
    if a.files:
        files = [f for f in files if any(fnmatch.fnmatch(f, g) or f == g for g in a.files)]
    if not files:
        kit.fail("no Python or C-like code in scope to plant faults in")
    per = a.per_function or SURVEY_FAULTS[cfg["depth"]]
    b = st["baseline"]
    timeout = max(10.0, 3 * (b.get("secs") or 1) + 5)
    rows = []
    with temp_box(ws, cfg, st, "survey") as root:
        plan = []   # (file, its text, the function, the faults to try), worked out before anything runs
        for f in files:
            text = read_text(root / "repo" / f)
            lang = faults.lang_of(f)
            units = faults.functions(text, lang) or [{"name": "(whole file)", "start": 1,
                                                      "end": len(faults.split_lines(text))}]
            for u in units:
                inner = {ln for v in units if v is not u and u["start"] < v["start"] and v["end"] <= u["end"]
                         for ln in range(v["start"], v["end"] + 1)}
                lines = set(range(u["start"], u["end"] + 1)) - inner
                plan.append((f, text, u, faults.sample(faults.find(text, lang, lines), per)))
        runs = sum(len(x[3]) for x in plan)
        print(f"Planting up to {per} faults in each of {plural(len(plan), 'function')} in {plural(len(files), 'file')} "
              f"and running the tests with each: at most {plural(runs, 'test run')}, about "
              f"{max(1, round(runs * (b.get('secs') or 1) / 60))} min at most (it stops at a function's first caught "
              "fault). In a fresh copy of the original code ...", flush=True)
        for f, text, u, chosen in plan:
            path = root / "repo" / f
            tried = caught = 0
            for fault in chosen:
                tried += 1
                path.write_bytes(faults.plant(text, fault).encode("utf-8", "surrogateescape"))
                try:
                    ok, _, _ = run_tests(root, st, timeout)
                finally:
                    path.write_bytes(text.encode("utf-8", "surrogateescape"))
                if not ok:
                    caught += 1
                    break
            rows.append({"file": f, "function": u["name"], "lines": u["end"] - u["start"] + 1,
                         "tried": tried, "caught": caught, "faults": [x["detail"] + f" (line {x['line']})"
                                                                      for x in chosen[:tried]]})
    weak = [r for r in rows if r["tried"] and not r["caught"]]
    mutate(ws, lambda s: s.__setitem__("survey", {"rows": rows, "per": per, "at": kit.now()}))
    for r in rows:
        state = ("nothing to plant" if not r["tried"] else "caught" if r["caught"]
                 else f"BARELY TESTED: all {r['tried']} faults passed the tests ({'; '.join(r['faults'])})")
        print(f"  {r['file']}:{r['function']} ({r['lines']} lines): {state}")
    print(f"\n{plural(len(weak), 'function')} where the tests caught none of the planted faults"
          + (f": {', '.join(r['file'] + ':' + r['function'] for r in weak)}. Pin these before changing them."
             if weak else "."))


def ensure_ready_tests(st: dict) -> None:
    if not st.get("tests"):
        kit.fail("no test command: baseline -w <run> -- <command>")
    if not (st.get("baseline") or {}).get("ok"):
        kit.fail("run baseline first, and get it passing")


def cmd_pin_collect(a) -> None:
    ws, cfg, st = load(a)
    path, box = reply_source(a, cfg)
    if not box:
        kit.fail("pin collect reads a pin writer's sandbox: --sandbox <name>")
    info = st["boxes"].get(a.sandbox) or {}
    if info.get("role") not in (None, "pin writer"):
        kit.fail(f"{a.sandbox} is a {info['role']}'s copy, not a pin writer's")
    reply = kit.read_reply(path) if path.is_file() else {}
    patch = kit.box_diff(box)
    files = kit.changed_files(patch)
    if not files:
        kit.fail("no new files in that copy, so there's no pin to collect")
    base = kit.box_base(box)
    old = [f for f in files if kit.git_ok(box / "repo", "cat-file", "-e", f"{base}:{f}")]
    if old:
        kit.fail(f"it changes files that already exist: {', '.join(old)}. A pin is a new test file; it leaves the "
                 "project's code and tests as they are.")
    not_tests = [f for f in files if not is_test(f)]
    if not_tests:
        kit.fail(f"these aren't test files: {', '.join(not_tests)}. A pin's files go where the project keeps its "
                 "tests, named the way its tests are (test_*.py, *.test.js, ...).")
    mention = attribution_hits(added_lines(patch))
    if mention:
        kit.fail(f"the pin mentions {', '.join(mention)}. Nothing written into the project may say who or what made it.")

    def add(s):
        pid = f"N{s['next']['N']}"
        s["next"]["N"] += 1
        dst = ws / "pins" / pid / "files"
        for f in files:
            (dst / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(box / "repo" / f, dst / f)
        kit.write_patch(ws / "pins" / pid / "pin.patch", patch)
        p = {"id": pid, "files": files, "command": kit.as_cmd(reply.get("command")) or None,
             "targets": [str(x) for x in kit.as_list(reply.get("targets"))], "what": str(reply.get("what") or ""),
             "source": f"pin writer:{a.sandbox}", "status": "new", "check": None, "timeout": a.timeout,
             "added": kit.now()}
        s["pins"].append(p)
        return p
    p = mutate(ws, add)
    if not a.keep:
        drop_box(ws, cfg, a.sandbox)
    print(f"{p['id']}: {', '.join(files)}" + (f", pinning {', '.join(p['targets'])}" if p["targets"] else "") + ".")
    print(f"Next: refactor.py pin check -w {ws} {p['id']}")


def cmd_pin_check(a) -> None:
    ws, cfg, st = load(a)
    ensure_ready_tests(st)
    p = get_pin(st, a.id)
    targets = list(a.target or p["targets"])
    if not targets:
        kit.fail("which code does it pin? Give --target <file>:<function> (or the pin writer's reply lists targets)")
    cmd, problem = pin_command(st, p["files"], parse_cmd(a.command) if a.command else p.get("command"))
    if problem:
        kit.fail(problem)
    n = a.faults or PIN_FAULTS[cfg["depth"]]
    print(f"Checking {p['id']} ({', '.join(p['files'])}) with {show_cmd(cmd)}: {RUNS} runs on the original code, "
          f"then up to {n} faults planted in {', '.join(targets)} ...", flush=True)
    with temp_box(ws, cfg, st, f"pin-{p['id']}") as root:
        where, problems = pin_targets(root, targets)
        if problems:
            kit.fail("; ".join(problems))
        write_pins(ws, root, [p])
        res = kit.run_in(root, cmd, RUNS, p["timeout"], None, log=False)
        runs = res["runs"]
        passed = sum(1 for r in runs if not kit.failed(r))
        tried, caught = [], []
        if passed == len(runs):
            per_file = {f: faults.sample(faults.find(read_text(root / "repo" / f), faults.lang_of(f), lines), n)
                        for f, lines in where.items()}
            chosen = []
            while len(chosen) < n and any(per_file.values()):
                for f in list(per_file):
                    if per_file[f] and len(chosen) < n:
                        chosen.append((f, per_file[f].pop(0)))
            timeout = max(10.0, 3 * max(r["secs"] for r in runs) + 5)
            for f, fault in chosen:
                path = root / "repo" / f
                text = read_text(path)
                path.write_bytes(faults.plant(text, fault).encode("utf-8", "surrogateescape"))
                try:
                    r = kit.run_in(root, cmd, 1, timeout, None, log=False)["runs"][0]
                finally:
                    path.write_bytes(text.encode("utf-8", "surrogateescape"))
                label = f"{f}:{fault['line']} {fault['detail']}"
                tried.append(label)
                if kit.failed(r):
                    caught.append(label)
    if passed < len(runs):
        status = "refused"
        reason = (f"it fails on the original code ({len(runs) - passed} of {len(runs)} runs). A pin records what the "
                  "code does now, so it has to pass every time on it.")
        print(kit.summary(res))
    elif not tried:
        status, reason = "refused", (f"no faults could be planted in {', '.join(targets)}, so nothing shows it pins "
                                     "them. Point it at code with comparisons, arithmetic or constants.")
    elif not caught:
        status, reason = "refused", (f"it still passes with every one of the {len(tried)} faults planted in "
                                     f"{', '.join(targets)}, so it doesn't pin what that code does.")
    else:
        status, reason = "pinned", (f"it passes {passed} of {len(runs)} runs on the original code and fails on "
                                    f"{len(caught)} of the {len(tried)} faults planted in {', '.join(targets)}.")
    chk = {"passed": passed, "runs": len(runs), "tried": tried, "caught": caught, "reason": reason, "command": cmd,
           "targets": targets, "at": kit.now()}

    def save(s):
        x = get_pin(s, p["id"])
        x.update(status=status, check=chk, command=cmd, targets=targets)
    mutate(ws, save)
    for t in tried:
        print(f"  {t}: {'caught' if t in caught else 'passed (not caught)'}")
    print(f"{p['id']}: {status}: {reason}")
    sys.exit(0 if status == "pinned" else 1)


# ---------------------------------------------------------------- metrics

def _py_nest(body, d: int, lines: list[str]) -> int:
    m = d if body else 0
    for s in body:
        m = max(m, d)
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(s, ast.ClassDef):
            m = max(m, _py_nest(s.body, d, lines))
        elif isinstance(s, ast.If):
            m = max(m, _py_nest(s.body, d + 1, lines))
            o = s.orelse
            elif_ = len(o) == 1 and isinstance(o[0], ast.If) and lines[o[0].lineno - 1].lstrip().startswith("elif")
            m = max(m, _py_nest(o, d if elif_ else d + 1, lines))
        elif isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
            m = max(m, _py_nest(s.body, d + 1, lines), _py_nest(s.orelse, d + 1, lines))
        elif isinstance(s, (ast.With, ast.AsyncWith)):
            m = max(m, _py_nest(s.body, d + 1, lines))
        elif isinstance(s, ast.Try) or type(s).__name__ == "TryStar":
            parts = [s.body, s.orelse, s.finalbody] + [h.body for h in s.handlers]
            m = max([m] + [_py_nest(b, d + 1, lines) for b in parts])
        elif type(s).__name__ == "Match":
            m = max([m] + [_py_nest(case.body, d + 2, lines) for case in s.cases])
    return m


def _py_complexity(fn) -> int:
    n, stack = 1, list(ast.iter_child_nodes(fn))
    while stack:
        x = stack.pop()
        if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(x, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler)):
            n += 1
        elif isinstance(x, ast.BoolOp):
            n += len(x.values) - 1
        elif isinstance(x, ast.comprehension):
            n += 1 + len(x.ifs)
        elif type(x).__name__ == "match_case":
            n += 1
        stack.extend(ast.iter_child_nodes(x))
    return n


def file_metrics(text: str, path: str) -> dict:
    lines = text.replace("\r\n", "\n").split("\n")
    m = {"lines": sum(1 for ln in lines if ln.strip()), "functions": [], "nesting": None, "complexity": None}
    lang = faults.lang_of(path)
    tree = None
    if lang == "python":
        with contextlib.suppress(SyntaxError, ValueError):
            tree = ast.parse(text)
    if tree is not None:
        fns = []

        def walk(node, prefix):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = min([ch.lineno] + [x.lineno for x in ch.decorator_list])
                    fns.append({"name": prefix + ch.name, "lines": (ch.end_lineno or ch.lineno) - start + 1,
                                "complexity": _py_complexity(ch), "nesting": _py_nest(ch.body, 0, lines)})
                    walk(ch, prefix + ch.name + ".")
                elif isinstance(ch, ast.ClassDef):
                    walk(ch, prefix + ch.name + ".")
                else:
                    walk(ch, prefix)
        walk(tree, "")
        m["functions"] = fns
        m["nesting"] = max([_py_nest(tree.body, 0, lines)] + [f["nesting"] for f in fns])
        m["complexity"] = True
    elif lang == "clike":
        m["functions"] = [{"name": f["name"], "lines": f["end"] - f["start"] + 1, "complexity": None, "nesting": None}
                          for f in faults.functions(text, "clike")]
        depth = deepest = 0
        for ch in faults.mask_clike(text):
            depth += (ch == "{") - (ch == "}")
            deepest = max(deepest, depth)
        m["nesting"] = max(0, deepest - 1)
    return m


def dup_lines(text: str) -> list[str]:
    out = []
    for ln in text.replace("\r\n", "\n").split("\n"):
        s = " ".join(ln.split())
        if not s or s.startswith(("#", "//", "/*", "*", "--")) or re.match(r"(import|from \S+ import|#include|using|"
                                                                            r"require\(|package) ", s):
            continue
        out.append(s)
    return out


def duplicates(texts: dict[str, str]) -> dict:
    """Blocks of 6 or more normalised lines (60+ characters) that appear more than once, across the files."""
    per = {f: dup_lines(t) for f, t in texts.items()}
    counts: dict[tuple, int] = {}
    for ls in per.values():
        for i in range(len(ls) - WINDOW + 1):
            w = tuple(ls[i:i + WINDOW])
            if sum(map(len, w)) >= WINDOW_CHARS:
                counts[w] = counts.get(w, 0) + 1
    blocks = lines = 0
    for ls in per.values():
        marked = [False] * len(ls)
        for i in range(len(ls) - WINDOW + 1):
            if counts.get(tuple(ls[i:i + WINDOW]), 0) > 1:
                for k in range(i, i + WINDOW):
                    marked[k] = True
        lines += sum(marked)
        blocks += sum(1 for i, x in enumerate(marked) if x and (i == 0 or not marked[i - 1]))
    return {"blocks": blocks, "lines": lines}


def totals(texts: dict[str, str]) -> dict:
    per = {f: file_metrics(t, f) for f, t in texts.items()}
    fns = [dict(f, file=p) for p, m in per.items() for f in m["functions"]]
    cx = [f for f in fns if f["complexity"] is not None]
    nest = [m["nesting"] for m in per.values() if m["nesting"] is not None]
    worst = max(cx, key=lambda f: f["complexity"]) if cx else None
    longest = max(fns, key=lambda f: f["lines"]) if fns else None
    dup = duplicates(texts)
    return {"files": len(texts), "lines": sum(m["lines"] for m in per.values()), "functions": len(fns),
            "long_functions": sum(1 for f in fns if f["lines"] > LONG),
            "longest": f"{longest['name']} ({longest['lines']} lines)" if longest else None,
            "max_nesting": max(nest) if nest else None,
            "duplicated_blocks": dup["blocks"], "duplicated_lines": dup["lines"],
            "complexity_total": sum(f["complexity"] for f in cx) if cx else None,
            "complexity_max": worst["complexity"] if worst else None,
            "most_complex": f"{worst['name']} ({worst['complexity']})" if worst else None,
            "per_file": {p: {"lines": m["lines"], "functions": len(m["functions"]), "nesting": m["nesting"]}
                         for p, m in per.items()}}


METRIC_ROWS = [("lines", "lines (not blank)"), ("functions", "functions"),
               ("long_functions", f"functions over {LONG} lines"), ("max_nesting", "deepest nesting"),
               ("duplicated_blocks", "duplicated blocks"), ("duplicated_lines", "lines in duplicated blocks"),
               ("complexity_total", "complexity, total"), ("complexity_max", "complexity, highest")]


def cmd_metrics(a) -> None:
    ws, cfg, st = load(a)
    chain = list(st["chain"])
    scope = [f for f in kit.scope_files(ws) if is_code(f)]
    if not scope:
        kit.fail("no code files in scope")
    with temp_box(ws, cfg, st, "metrics", commit=False) as root:
        repo = root / "repo"
        before = {f: read_text(repo / f) for f in scope if (repo / f).is_file()}
        try:
            apply_chain(ws, root, st, chain)
        except PatchError as e:
            kit.fail(str(e))
        added = [f for cid in chain for f in get_cand(st, cid)["files"] if is_code(f) and f not in before]
        after = {f: read_text(repo / f) for f in dict.fromkeys(list(before) + added) if (repo / f).is_file()}
    b, af = totals(before), totals(after)
    rec = {"before": b, "after": af, "chain": chain, "at": kit.now()}
    mutate(ws, lambda s: s.__setitem__("metrics", rec))
    print(f"Metrics for the code in scope: {plural(b['files'], 'file')} before, {plural(af['files'], 'file')} after "
          + (f"{and_list(chain)}" if chain else "(no steps accepted yet, so after is the same)") + ".")
    print(f"  {'':<28} {'before':>8} {'after':>8}")
    for key, label in METRIC_ROWS:
        x, y = b[key], af[key]
        if x is None and y is None:
            continue
        print(f"  {label:<28} {'' if x is None else x:>8} {'' if y is None else y:>8}")
    if b["most_complex"] or af["most_complex"]:
        print(f"  most complex function: {b['most_complex']} before, {af['most_complex']} after")
    print(f"Complexity is counted for Python only: 1 + each if, elif, loop, except, conditional expression, "
          f"and/or, comprehension and case. Duplicated blocks are runs of {WINDOW}+ lines that appear more than once "
          "once whitespace, blank lines, comments and imports are ignored.")


def metric_changes(m: dict | None) -> list[str]:
    if not m:
        return []
    out = []
    for key, label in METRIC_ROWS:
        x, y = m["before"].get(key), m["after"].get(key)
        if x is not None and y is not None and x != y:
            out.append(f"{label} {x} → {y}")
    return out


# ---------------------------------------------------------------- land

def next_branch(top: Path) -> str:
    base = f"killer-refactor/{kit.today()}"
    name, n = base, 2
    while kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{name}"):
        name, n = f"{base}-{n}", n + 1
    return name


def checks_text(c: dict) -> str:
    v = c.get("verify") or {}
    res = v.get("results") or []
    golden = [r for r in res if r["check"] == "recorded run"]
    face = next((r for r in res if r["check"] == "interface"), None)
    pins = [r for r in res if r["check"] == "pins"]
    probes = sum(1 for b in c.get("probes") or [] for r in b["results"] if r["outcome"] == "same")
    bits = ["the tests pass"]
    if golden:
        bits.append(f"{plural(len(golden), 'recorded run')} give the same output")
    if face:
        cmp = face.get("compare") or {}
        if not cmp.get("differences"):
            bits.append("the public interface is unchanged")
        elif cmp.get("renamed"):
            bits.append(f"the public interface changed only as planned ({'; '.join('renamed ' + x for x in cmp['renamed'])})")
        else:
            bits.append("the public interface changed only as planned")
    if pins:
        bits.append(f"{plural(len(pins), 'pinning test')} pass")
    if probes:
        bits.append(f"{plural(probes, 'probe')} comparing it with the original code found no difference")
    return and_list(bits)


def commit_message(c: dict) -> str:
    lines = [c["title"].rstrip(".")]
    if c.get("summary"):
        lines += ["", c["summary"]]
    lines += ["", f"Checked in a fresh copy: {checks_text(c)}."]
    return "\n".join(lines) + "\n"


def pins_message(pins: list[dict]) -> str:
    by_file: dict[str, list[str]] = {}
    for t in (t for p in pins for t in p["targets"]):
        f, _, what = t.partition(":")
        what = f"lines {what}" if re.fullmatch(r"\d+(-\d+)?", what) else what
        by_file.setdefault(f, [])
        if what and what not in by_file[f]:
            by_file[f].append(what)
    parts = [f"{and_list(names)} in {f}" if names else f for f, names in by_file.items()]
    return (f"Add tests that pin the current behaviour of {and_list(parts)}\n\nEach one passes on the code as it is "
            "and fails when a small fault is planted in the code it covers, so a later change that alters this "
            "behaviour shows up in the tests.\n")


def undo_land(top: Path, branch: str, base: str, tip: str, existed: bool, on_current: bool) -> str:
    if not existed:
        ok = kit.git_ok(top, "branch", "-D", branch)
        return f"deleted {branch}" if ok else f"couldn't delete {branch}: do it with git branch -D {branch}"
    if on_current:
        ok = kit.git_ok(top, "reset", "-q", "--keep", base)
    else:
        ok = kit.git_ok(top, "update-ref", f"refs/heads/{branch}", base, tip)
    return f"moved {branch} back to {short(base)}" if ok else \
        f"couldn't move {branch} back: do it with git reset --keep {short(base)} on that branch"


def say_results(results: list[dict]) -> None:
    for r in results:
        print(f"  {r['line']}")
        if not r["ok"] and r.get("detail"):
            print("    " + r["detail"].replace("\n", "\n    "))


def cmd_land(a) -> None:
    ws, cfg, st = load(a)
    ensure_ready(st)
    if not cfg["git"] or not cfg["base_rev"]:
        kit.fail("land needs a git repository with commits; this run can only report")
    chain = list(st["chain"])
    if not chain:
        kit.fail("no accepted steps to land")
    for cid in chain:
        if get_cand(st, cid)["status"] != "accepted":
            kit.fail(f"{cid} is '{get_cand(st, cid)['status']}', not accepted")
    top, base = Path(cfg["repo"]), cfg["base_rev"]
    cands = [get_cand(st, cid) for cid in chain]
    pins = pinned(st) if cfg["pins"] == "keep" else []
    msgs = {c["id"]: commit_message(c) for c in cands}
    for c in cands:
        hits = attribution_hits(msgs[c["id"]])
        if not c.get("allow_mention"):
            hits += attribution_hits(added_lines(kit.read_patch(ws / c["patch"])))
        if hits:
            kit.fail(f"{c['id']}'s commit would mention {', '.join(sorted(set(hits)))}. Nothing in the project's history "
                     "may say who or what made it: fix the title or summary (candidate add again).")
    if a.uncommitted:
        return land_uncommitted(ws, cfg, st, chain, pins)
    current = kit.git(top, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip() or None
    branch = a.branch or cfg.get("branch_name") or next_branch(top)
    if not kit.git_ok(top, "check-ref-format", "--branch", branch):
        kit.fail(f"{branch!r} isn't a valid branch name")
    exists = kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")
    if exists:
        tip = kit.git(top, "rev-parse", f"refs/heads/{branch}").strip()
        if tip != base:
            kit.fail(f"{branch} is at {short(tip)}, not this run's base {short(base)}: the checks don't hold there. "
                     f"Start a run from it (init --rev {branch}).")
    on_current = exists and branch == current
    if on_current:
        dirty = [ln for ln in kit.git_status(top).splitlines() if ln and not ln.startswith("??")]
        if dirty:
            kit.fail(f"{branch} is checked out with uncommitted changes; commit or stash them first")
    # The commits are made in a separate worktree, on no branch. Only when every one of them exists does the
    # branch move, so a failing hook or a patch that won't apply leaves the user's checkout and branches alone.
    worktree = Path(cfg["sandboxes_home"]) / cfg["run_id"] / f"land-{secrets.token_hex(3)}"
    worktree.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "-C", str(top), "worktree", "add", "-q", "--detach", str(worktree), base],
                       capture_output=True, text=True)
    if r.returncode:
        kit.fail(f"git worktree add: {r.stderr.strip()}")
    workdir, commits = worktree, []
    try:
        pin_files = [f for p in pins for f in p["files"]]
        if pins:
            for p in pins:
                copy_renamed(ws / "pins" / p["id"] / "files", workdir)
            kit.stage_exact(workdir, pin_files)   # by content: copied files keep their old timestamps
            kit.git(workdir, "commit", "-q", "-F", "-", data=pins_message(pins))
            commits.append(f"{kit.git(workdir, 'rev-parse', 'HEAD').strip()[:10]} {pins_message(pins).splitlines()[0]}")
        for c in cands:
            patch = kit.read_patch(ws / c["patch"])
            done, msg = kit.apply_patch(workdir, patch)
            if not done:
                kit.fail(f"{c['id']} doesn't apply on {short(base)} in a clean checkout: {msg}. If only line endings "
                         "differ, this repository's settings check files out differently from a plain clone: apply it "
                         "by hand with git apply. Nothing was committed.")
            files = list(kit.changed_files(patch))
            ren = renames_for(st, [c["id"]])
            if pins and ren:   # the pins call the renamed names too
                for f in pin_files:
                    q = workdir / f
                    new_bytes = ren.data(q.read_bytes(), f)
                    if new_bytes != q.read_bytes():
                        q.write_bytes(new_bytes)
                        files.append(f)
            for i in range(0, len(files), 100):
                kit.git(workdir, "add", "-A", "--", *files[i:i + 100])
            kit.git(workdir, "commit", "-q", "-F", "-", data=msgs[c["id"]])
            commits.append(f"{kit.git(workdir, 'rev-parse', 'HEAD').strip()[:10]} {msgs[c['id']].splitlines()[0]}")
        tip = kit.git(workdir, "rev-parse", "HEAD").strip()
    finally:
        subprocess.run(["git", "-C", str(top), "worktree", "remove", "--force", str(worktree)], capture_output=True)
        subprocess.run(["git", "-C", str(top), "worktree", "prune"], capture_output=True)
        kit.rmtree(worktree)
    if not exists:
        moved = subprocess.run(["git", "-C", str(top), "branch", branch, tip], capture_output=True, text=True)
    elif on_current:   # fast-forward the checked-out branch: git refuses rather than overwrite anything
        moved = subprocess.run(["git", "-C", str(top), "merge", "--ff-only", "-q", tip], capture_output=True, text=True)
    else:
        moved = subprocess.run(["git", "-C", str(top), "update-ref", f"refs/heads/{branch}", tip, base],
                               capture_output=True, text=True)
    if moved.returncode:
        kit.fail(f"couldn't move {branch} to the new commits ({(moved.stderr or moved.stdout).strip()}). Nothing "
                 f"changed; the commits are {short(tip)}, and git branch <name> {short(tip)} would keep them.")
    for line in commits:
        print(f"Committed on {branch}: {line}")
    print(f"Final check: {branch} ({short(tip)}) as a whole, in a fresh copy ...", flush=True)
    root = fresh_box(ws, cfg, st, "final", rev=tip, commit=False)
    try:
        results = check_all(ws, cfg, st, root, chain, pins_in_tree=bool(pins))
    finally:
        kit.drop_sandbox(ws, cfg, root.name)
    say_results(results)
    verdict = verdict_of(results)
    undone = undo_land(top, branch, base, tip, exists, on_current) if verdict != "verified" else None
    rec = {"branch": branch, "base": base, "tip": tip, "commits": commits, "candidates": chain,
           "pins": [p["id"] for p in pins], "on_current": on_current, "final": verdict, "undone": undone,
           "results": [{k: v for k, v in r.items() if k != "detail"} for r in results], "at": kit.now()}
    mutate(ws, lambda s: s["landed"].append(rec))
    if undone:
        print(f"{branch}: the final check said {verdict}, so the commits were undone ({undone}). Nothing landed; say "
              "so in the report.")
        sys.exit(1)
    print(f"{branch}: {plural(len(commits), 'commit')}, nothing pushed. Final check: every check held on the branch.")


def land_uncommitted(ws: Path, cfg: dict, st: dict, chain: list[str], pins: list[dict]) -> None:
    top, base = Path(cfg["repo"]), cfg["base_rev"]
    head = kit.git(top, "rev-parse", "HEAD").strip()
    if head != base:
        kit.fail(f"the project is at {short(head)}, not this run's base {short(base)}: the steps were checked on "
                 f"{short(base)}. Check out {short(base)} first, or land them on a branch instead.")
    ren = renames_for(st, chain)
    print("Checking the steps together, in a fresh copy, before anything touches the project ...", flush=True)
    root = fresh_box(ws, cfg, st, "final-uncommitted", chain, commit=False)
    try:
        if pins:
            write_pins(ws, root, pins, ren)
        combined = kit.box_diff(root)
        commit_state(root)   # a recorded run's clean-up resets the copy to its last commit
        results = check_all(ws, cfg, st, root, chain, pins_in_tree=bool(pins))
    finally:
        kit.drop_sandbox(ws, cfg, root.name)
    say_results(results)
    verdict = verdict_of(results)
    if verdict != "verified":
        kit.fail(f"together they fail the final check ({verdict}), so nothing was changed in the project", code=1)
    touched = set(kit.changed_files(combined))
    dirty = [ln[3:] for ln in kit.git_status(top).splitlines() if ln[3:].strip('"') in touched]
    if dirty:
        kit.fail(f"these files have uncommitted changes in the project: {', '.join(dirty)}; commit or stash them first")
    ok, msg = kit.apply_patch(top, combined, check_only=True)
    if not ok:
        kit.fail(f"the steps don't apply to the project's working tree: {msg}")
    ok, msg = kit.apply_patch(top, combined)
    if not ok:
        kit.fail(f"git apply: {msg}")
    rec = {"branch": None, "base": base, "tip": None, "commits": [], "candidates": chain,
           "pins": [p["id"] for p in pins], "uncommitted": True, "final": verdict, "undone": None,
           "results": [{k: v for k, v in r.items() if k != "detail"} for r in results], "at": kit.now()}
    mutate(ws, lambda s: s["landed"].append(rec))
    print(f"The {plural(len(chain), 'step')}" + (f" and {plural(len(pins), 'pin')}" if pins else "")
          + " are in the project's working tree, uncommitted. Nothing was committed or pushed.")


# ---------------------------------------------------------------- books

def cmd_status(a) -> None:
    ws, cfg, st = load(a)
    b = st.get("baseline")
    print("Baseline: " + (f"{b['verdict']}" + (f", flaky left out: {', '.join(b['flaky'])}" if b["flaky"] else "")
                          if b else "not run"))
    print(f"Recorded runs: {len(st['golden'])}. Interface: "
          + ((f"{len(st['surface'].get('lines') or [])} entries" if st['surface']['kind'] != 'none' else "not checked")
             if st.get("surface") else "not recorded"))
    print(f"Pins: {sum(1 for p in st['pins'] if p['status'] == 'pinned')} pinned, "
          f"{sum(1 for p in st['pins'] if p['status'] == 'refused')} refused, "
          f"{sum(1 for p in st['pins'] if p['status'] == 'new')} not checked.")
    for step in st["plan"]:
        cs = ", ".join(f"{cid} {get_cand(st, cid)['status']}" for cid in step["candidates"])
        print(f"  {step['id']}. {step['title']} [{step_status(st, step)}]" + (f": {cs}" if cs else ""))
    print(f"Accepted, in order: {and_list(st['chain']) or 'none'}.")
    if not cfg["git"]:
        print("Not a git repository, so there's no status to compare.")
        return
    now, then = project_status(cfg) or "", cfg.get("status_at_init") or ""
    if now == then:
        print("The real project's working tree is as it was at init.")
        return
    a_, b_ = set(then.splitlines()), set(now.splitlines())
    print("The real project's working tree has changed since init:")
    for ln in sorted(b_ - a_):
        print(f"  now:      {ln}")
    for ln in sorted(a_ - b_):
        print(f"  was:      {ln}")
    print("If nobody meant to change it, find which step did, undo it, and say so in the report.")
    sys.exit(1)


def cmd_decide(a) -> None:
    ws, _cfg, _st = load(a)
    decide_now(ws, a.text)
    print(f"Recorded under decisions to check: {a.text}")


def cmd_wait(a) -> None:
    ws, _cfg, _st = load(a)
    mutate(ws, lambda s: s["waiting"].append({"text": a.text, "command": a.command, "at": kit.now()}))
    print(f"Recorded as waiting for the user's OK: {a.text}" + (f" ({a.command})" if a.command else ""))


def cmd_clean(a) -> None:
    ws, cfg, _st = load(a)
    n = kit.clean_sandboxes(ws, cfg)
    if cfg["git"]:
        subprocess.run(["git", "-C", cfg["repo"], "worktree", "prune"], capture_output=True)
    mutate(ws, lambda s: s.__setitem__("boxes", {}))
    print(f"Deleted {plural(n, 'sandbox', 'sandboxes')}.")


# ---------------------------------------------------------------- report

def md(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def cand_block(ws: Path, st: dict, c: dict, landed_in: dict) -> list[str]:
    step = get_step(st, c["step"])
    out = [f"### {c['id']}. {c['title']} · {c['status']}", "", f"- **Step:** {step['id']}. {step['title']}"]
    if c.get("summary"):
        out.append(f"- **The refactorer's account:** {c['summary']}")
    out.append(f"- **Files:** {', '.join(f'`{f}`' for f in c['files'])}; the patch is `{c['patch']}`")
    if c.get("allowed_protected"):
        out.append(f"- **Allowed to change protected files:** {', '.join(c['allowed_protected'])}")
    v = c.get("verify")
    if v:
        out.append("- **Verify, in a fresh copy:**")
        out += [f"  - {r['line']}" for r in v["results"]]
    for b in c.get("probes") or []:
        out.append(f"- **Probes (batch {b['batch']}, files in `{b['scratch']}/`):**")
        for r in b["results"]:
            what = f" ({r['what']})" if r.get("what") else ""
            res = {"same": "same output on the original and the changed code",
                   "broken": "couldn't run on the original code (a missing module or file), so it didn't count",
                   "renamed": f"differed only by the planned renames ({r['difference']}), so it didn't count",
                   "unstable": "output varied between runs of the original code, so it didn't count",
                   "different": f"DIFFERENT: {r['difference']}" + (f"; caused by {r['blame']}" if r.get('blame') else "")
                   }[r["outcome"]]
            out.append(f"  - `{show_cmd(r['command'])}`{what}: {res}")
    for n in c.get("notes") or []:
        out.append(f"- **Note:** {n}")
    if c["id"] in landed_in:
        out.append(f"- **Landed:** {landed_in[c['id']]}")
    out.append("")
    return out


def cmd_report(a) -> None:
    ws, cfg, st = load(a)
    plan = [s for s in st["plan"] if s.get("status") != "dropped"]
    cands = st["candidates"]
    accepted = [get_cand(st, cid) for cid in st["chain"]]
    rejected = [c for c in cands if c["status"].startswith(REJECTED)]
    stale = [c for c in cands if c["status"] == "stale"]
    waiting_probe = [c for c in cands if c["status"] == "verified"]
    unverified = [c for c in cands if c["status"] == "new"]
    kept = [x for x in st["landed"] if not x.get("undone")]
    landed = kept[-1] if kept else None
    undone = [x for x in st["landed"] if x.get("undone")]
    b = st.get("baseline")
    surface = st.get("surface")
    pins_ok = pinned(st)
    pins_bad = [p for p in st["pins"] if p["status"] == "refused"]
    probes = [r for c in cands for bt in c.get("probes") or [] for r in bt["results"]]
    diffs = [r for r in probes if r["outcome"] == "different"]
    agents = sum(st["agents"].values())
    steps_open = [s for s in plan if step_status(st, s) != "accepted"]

    blocked = []
    if not st.get("tests"):
        blocked.append("no test command was set")
    elif not b:
        blocked.append("the tests never ran on the original code")
    elif not b["ok"]:
        blocked.append(f"the tests are {b['verdict']} on the original code")
    if not st["golden"]:
        blocked.append("no recorded runs were set up")
    if not surface:
        blocked.append("the interface wasn't recorded")
    partial = []
    if steps_open:
        partial.append(f"{plural(len(steps_open), 'planned step')} not accepted")
    if waiting_probe:
        partial.append(f"{plural(len(waiting_probe), 'verified step')} still waiting for a challenger")
    if undone:
        partial.append("a landing failed its final check and was undone")
    if cfg["after"] == "commit" and accepted and not landed:
        partial.append("accepted steps not landed")
    status = "blocked" if blocked else ("partial" if partial else "done")
    changes = metric_changes(st.get("metrics"))
    if blocked:
        headline = "Blocked: " + "; ".join(blocked)
    elif landed and landed.get("branch"):
        headline = (f"{len(accepted)} of {plural(len(plan), 'planned step')} proven to keep the behaviour and "
                    f"committed on {landed['branch']} ({plural(len(landed['commits']), 'commit')})")
    elif landed:
        headline = f"{len(accepted)} of {plural(len(plan), 'planned step')} proven and left uncommitted in the project"
    elif accepted:
        headline = f"{len(accepted)} of {plural(len(plan), 'planned step')} proven to keep the behaviour, not landed"
    else:
        headline = f"No step proven yet: {plural(len(cands), 'candidate')} checked, {len(rejected)} rejected"
    if changes and accepted:
        headline += "; " + ", ".join(changes[:2])
    landed_in = {cid: f"`{landed['commits'][i + (1 if landed.get('pins') else 0)]}` on `{landed['branch']}`"
                 for i, cid in enumerate(landed["candidates"])
                 if landed.get("branch") and i + (1 if landed.get("pins") else 0) < len(landed["commits"])} \
        if landed else {}

    L = [f"# Refactor report: {cfg['target'] or Path(cfg['repo']).name}", "",
         f"**Project:** `{cfg['repo']}`" + (f" at `{short(cfg['base_rev'])}`" if cfg.get("base_rev") else ""),
         f"**Result:** {headline}.",
         f"**Status:** {status}" + (f": {'; '.join(blocked or partial)}" if blocked or partial else ""),
         f"**Interface:** {INTERFACES[cfg['interface']]}. **Depth:** {cfg['depth']}. "
         f"**Pins:** {'kept in the project' if cfg['pins'] == 'keep' else 'this run only'}.", ""]
    L += ["## The plan", ""]
    for s in st["plan"]:
        L.append(f"{s['id'][1:]}. {s['title']} · {step_status(st, s)}"
                 + (f" (may: {'; '.join(s['allow'])})" if s["allow"] else ""))
    L.append("")
    if landed:
        L += ["## Landed", ""]
        if landed.get("branch"):
            L += [f"`{landed['branch']}` from `{short(landed['base'])}`, nothing pushed:", ""]
            L += [f"- `{x}`" for x in landed["commits"]]
            L += ["", "The branch as a whole was checked again in a fresh copy:", ""]
        else:
            L += ["The accepted steps were applied to the project's working tree, uncommitted, after this check of "
                  "them together in a fresh copy:", ""]
        L += [f"- {r['line']}" for r in landed["results"]] + [""]
    for x in undone:
        L += [f"A landing on `{x['branch']}` failed its final check ({x['final']}) and was undone: {x['undone']}.", ""]

    L += ["## What was checked", ""]
    if b:
        L.append(f"- **Baseline:** the tests (`{md(show_cmd(st['tests']['cmd']))}`) ran {len(b['runs'])} times on the "
                 f"original code: {b['verdict']}."
                 + (f" These flipped between runs and were left out of every verdict: {', '.join(b['flaky'])}."
                    if b["flaky"] else ""))
    if st["golden"]:
        L.append(f"- **Recorded runs:** {len(st['golden'])}, each compared after normalising times, durations, "
                 "temporary paths and ids:")
        for g in st["golden"].values():
            extra = []
            if g["masks"]:
                extra.append("masking " + ", ".join(f"`{md(m)}`" for m in g["masks"]))
            if g.get("exact"):
                extra.append("no default masks")
            if g.get("kind") == "probe":
                extra.append(f"made from a probe that caught a change in {g.get('from')}")
            L.append(f"  - `{g['name']}`: `{md(show_cmd(g['cmd']))}` ({describe_record(g['expected'])}"
                     + ("; " + "; ".join(extra) if extra else "") + ")")
    if surface:
        if surface["kind"] == "none":
            L.append(f"- **Interface:** not checked" + (f": {surface.get('why')}" if surface.get("why") else ""))
        else:
            how = "a Python probe that imports the code and lists public names and signatures" \
                if surface["kind"] == "python" else f"`{md(show_cmd(surface['command']))}`"
            L.append(f"- **Interface:** {len(surface['lines'])} entries, recorded with {how}.")
    if st.get("survey"):
        rows = st["survey"]["rows"]
        weak = [r for r in rows if r["tried"] and not r["caught"]]
        L.append(f"- **Survey:** faults planted in {plural(len(rows), 'function')}; the tests caught none of them in "
                 f"{len(weak)}" + (f" ({', '.join(r['file'] + ':' + r['function'] for r in weak)})" if weak else "")
                 + ".")
    if st["pins"]:
        L.append(f"- **Pins:** {len(pins_ok)} counted, {len(pins_bad)} refused.")
        for p in st["pins"]:
            chk = p.get("check") or {}
            L.append(f"  - {p['id']} (`{'`, `'.join(p['files'])}`): {p['status']}"
                     + (f": {chk['reason']}" if chk.get("reason") else ""))
    L.append(f"- **Probes:** {len(probes)} run, {len(diffs)} found a difference.")
    if not st.get("metrics"):
        L.append("- **Metrics:** not counted, so this report makes no claim that the code got simpler.")
    L.append("")
    if accepted:
        L += ["## Accepted steps", ""] + sum((cand_block(ws, st, c, landed_in) for c in accepted), [])
    if waiting_probe or unverified:
        L += ["## Not finished", ""] + sum((cand_block(ws, st, c, landed_in) for c in waiting_probe + unverified), [])
    if rejected or stale:
        L += ["## Rejected, and why", ""] + sum((cand_block(ws, st, c, landed_in) for c in rejected + stale), [])
    m = st.get("metrics")
    if m:
        L += ["## Before and after", "",
              f"For the code in scope, {plural(m['before']['files'], 'file')} before and "
              f"{plural(m['after']['files'], 'file')} after {and_list(m['chain']) or 'no steps'}. Counted by "
              "`refactor.py metrics`; claims about simpler code come from this table only.", "",
              "| Measure | Before | After |", "|---|---|---|"]
        for key, label in METRIC_ROWS:
            x, y = m["before"].get(key), m["after"].get(key)
            if x is None and y is None:
                continue
            L.append(f"| {label} | {'' if x is None else x} | {'' if y is None else y} |")
        L.append("")
        if m["chain"] != st["chain"]:
            L += ["The accepted steps changed after these were counted: run `refactor.py metrics` again.", ""]
    if st["decisions"]:
        L += ["## Decisions to check", ""] + [f"- {d['text']}" for d in st["decisions"]] + [""]
    if st["waiting"]:
        L += ["## Waiting for your OK", ""] + [f"- {w['text']}" + (f": `{w['command']}`" if w.get("command") else "")
                                               for w in st["waiting"]] + [""]
    roles = ", ".join(f"{n} {r}{'s' if n != 1 else ''}" for r, n in sorted(st["agents"].items())) or "none"
    L += ["## How it was checked", "",
          "- Every check ran in a fresh copy of the project made from the base commit, with the accepted steps "
          "applied in order, its own home and temp folders, no credentials, and the network "
          + ("on." if cfg["network"] else "off."),
          "- A step was verified only when the tests passed, every recorded run gave the same stdout, stderr, exit "
          "code and files as on the original code, the interface matched the recorded one apart from the changes "
          "its plan allowed, and every pin passed.",
          "- A pin counted only when it passed 3 runs of 3 on the original code and failed with at least one small "
          "fault planted in the code it pins.",
          f"- At {cfg['depth']} depth a step was accepted only after {MIN_PROBES[cfg['depth']]} challenger probes ran "
          "in two fresh copies each, the original and the changed code, and gave the same output."
          if MIN_PROBES[cfg["depth"]] else "- Quick depth: no challenger probes.",
          f"- Agents sent: {agents} ({roles}).", "",
          "## Run details", "", f"Run folder: `{ws}`. Step patches are in `candidates/`, recorded runs in `golden/`, "
          "pins in `pins/`, probes in `probes/`.", ""]
    (ws / "report.md").write_text("\n".join(L), encoding="utf-8")

    numbers = {"steps": len(plan), "accepted": len(accepted), "rejected": len(rejected), "candidates": len(cands),
               "recorded_runs": len(st["golden"]), "interface_entries": len((surface or {}).get("lines") or []),
               "pins": len(pins_ok), "pins_refused": len(pins_bad), "probes": len(probes), "differences": len(diffs),
               "flaky_tests": len((b or {}).get("flaky") or []), "agents": agents,
               "commits": len(landed["commits"]) if landed else 0}
    if m:
        for key, _ in METRIC_ROWS:
            numbers[f"{key}_before"] = m["before"].get(key)
            numbers[f"{key}_after"] = m["after"].get(key)
    summary = {"skill": "killer-refactor", "status": status, "headline": headline, "report": str(ws / "report.md"),
               "branch": landed.get("branch") if landed else None, "commits": landed["commits"] if landed else [],
               "decisions": [d["text"] for d in st["decisions"]],
               "waiting": [w["text"] + (f": {w['command']}" if w.get("command") else "") for w in st["waiting"]],
               "numbers": numbers}
    kit.save_json(ws / "summary.json", summary)
    print(f"Wrote {ws / 'report.md'} and summary.json.")
    print(f"killer-refactor: {status} · {headline} · {ws / 'report.md'}")


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="refactor.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def cmd(parent, name, func, help_, ws=True, takes_cmd=False):
        sp = parent.add_parser(name, help=help_, description=help_)
        if ws:
            sp.add_argument("--workspace", "-w", required=True, help="the run folder")
        sp.set_defaults(func=func, takes_cmd=takes_cmd)
        return sp

    s = cmd(sub, "init", cmd_init, "Create the run folder (the test command as --test, or after --).", ws=False,
            takes_cmd=True)
    s.add_argument("--repo", default=".", help="the project folder (default: here)")
    s.add_argument("--scope", default="repo", help="the code to refactor: path:<folder or file>, branch[:<base>], "
                                                   "changes or repo")
    s.add_argument("--target", default="", help="the refactor, in the user's words, in one line")
    s.add_argument("--interface", choices=list(INTERFACES), default="same",
                   help="same (default): nothing public changes; planned: only the plan's renames and moves; "
                        "free: internal code")
    s.add_argument("--depth", choices=list(MIN_PROBES), default="standard")
    s.add_argument("--pins", choices=["keep", "run"], default="keep",
                   help="keep (default): land the pins in the project's tests; run: this run only")
    s.add_argument("--after", choices=["commit", "leave", "report"], default="commit")
    s.add_argument("--test", help="the test command as one string (a JSON list, or forward slashes)")
    s.add_argument("--test-timeout", type=float, default=600)
    s.add_argument("--cwd", help="folder inside the project to run the tests from")
    s.add_argument("--rev", help="start from this commit (default HEAD; uncommitted changes are left out)")
    s.add_argument("--branch", help="commit here when landing (killer-goal's branch)")
    s.add_argument("--network", action="store_true", help="let sandboxes use the network")
    s.add_argument("--protect", nargs="*", help="more paths a step may not change")
    s.add_argument("--include", nargs="*", help="ignored paths every sandbox needs, like .venv or node_modules")
    s.add_argument("--exclude", nargs="*", help="leave these out of scope")
    s.add_argument("--env", nargs="*", help="NAME=VALUE set in every sandbox")
    s.add_argument("--slug", help="short name for the run folder")
    s.add_argument("--workspace", "-w", help="use this run folder instead of the default")
    s.add_argument("--force", action="store_true", help="start over in an existing run folder")

    s = cmd(sub, "baseline", cmd_baseline, "Run the tests 3 times in a fresh copy (a new command after -- replaces it).",
            takes_cmd=True)
    s.add_argument("--cwd")
    s.add_argument("--timeout", type=float)

    golden = sub.add_parser("golden", help="Recorded runs.").add_subparsers(dest="golden_command", required=True)
    s = cmd(golden, "add", cmd_golden_add, "Record a command's output (the command after --), run twice.", takes_cmd=True)
    s.add_argument("name")
    s.add_argument("--mask", action="append", help="a regex for output that legitimately varies (repeatable)")
    s.add_argument("--give", action="append", help="a file (a driver script, an input) copied into the scratch "
                                                   "folder; the command reaches it as ../scratch/<name>")
    s.add_argument("--stdin", help="a file fed to the command's standard input")
    s.add_argument("--cwd", help="folder inside the project to run it from")
    s.add_argument("--timeout", type=float, default=120)
    s.add_argument("--exact", action="store_true", help="don't mask times, durations and ids (paths still are)")
    s.add_argument("--replace", action="store_true")
    cmd(golden, "list", cmd_golden_list, "The recorded runs.")
    s = cmd(golden, "run", cmd_golden_run, "Rerun recorded runs on the original code, or with the accepted steps.")
    s.add_argument("names", nargs="*")
    s.add_argument("--chain", action="store_true", help="with the accepted steps applied")
    s.add_argument("--candidate", help="with the accepted steps and this candidate applied")

    surface = sub.add_parser("surface", help="The public interface.").add_subparsers(dest="surface_command", required=True)
    s = cmd(surface, "record", cmd_surface_record, "Record the public interface on the original code, twice.")
    s.add_argument("--module", action="append", help="a Python module or package to list (repeatable)")
    s.add_argument("--folder", action="append", help="a folder to find Python modules in (default: from the scope)")
    s.add_argument("--python", help="the Python to import the code with (default: this one)")
    s.add_argument("--command", help="for other languages: a command that prints the interface, one entry a line")
    s.add_argument("--mask", action="append", help="with --command: a regex for parts that vary")
    s.add_argument("--give", action="append", help="with --command: a file it needs (a listing script), copied into "
                                                   "the scratch folder and reached as ../scratch/<name>")
    s.add_argument("--cwd")
    s.add_argument("--timeout", type=float, default=300)
    s.add_argument("--none", action="store_true", help="there's no interface to check (recorded as a decision)")
    s.add_argument("--why")
    s.add_argument("--show", type=int, default=40, help="how many entries to print")
    s = cmd(surface, "compare", cmd_surface_compare, "Compare the interface with the accepted steps (and a candidate).")
    s.add_argument("--candidate")

    s = cmd(sub, "plan", cmd_plan, "Add, replace, drop or show the plan's steps.")
    s.add_argument("--add", help="a new step, in a line")
    s.add_argument("--replace", help="redefine this step (with --title and --allow)")
    s.add_argument("--title")
    s.add_argument("--drop", help="drop this step")
    s.add_argument("--allow", action="append", help="an interface change the step may make: 'rename <old> <new>', "
                                                    "'move <old> <new>', 'add <pattern>', 'remove <pattern>', "
                                                    "'change <pattern>' (repeatable)")

    s = cmd(sub, "sandbox", cmd_sandbox, "Make an isolated copy of the project for one agent.")
    s.add_argument("name", help="ref-S1 for a refactorer, pin-<x> for a pin writer, chal-C3 for a challenger")
    s.add_argument("--role", help="refactorer, pin, challenger or lead (default: from the name)")
    s.add_argument("--candidate", help="apply this candidate, uncommitted (a challenger's copy)")
    s.add_argument("--give", nargs="*", help="files to copy into its scratch folder")

    pin = sub.add_parser("pin", help="Pinning tests.").add_subparsers(dest="pin_command", required=True)
    s = cmd(pin, "survey", cmd_pin_survey, "Plant faults function by function and run the tests with each.")
    s.add_argument("--per-function", type=int, help="faults per function (default from --depth: 2, 3 or 5)")
    s.add_argument("--files", nargs="*", help="only these files (globs)")
    s = cmd(pin, "collect", cmd_pin_collect, "Take a pin writer's new test files from its sandbox.")
    s.add_argument("reply", nargs="?")
    s.add_argument("--sandbox", required=True)
    s.add_argument("--timeout", type=float, default=300)
    s.add_argument("--keep", action="store_true")
    s = cmd(pin, "check", cmd_pin_check, "Check a pin: 3 of 3 on the original, and fails on a planted fault.")
    s.add_argument("id")
    s.add_argument("--target", action="append", help="the code it pins: <file>:<function> or <file>:<first>-<last>")
    s.add_argument("--command", help="the command that runs only the pin's files (default: the writer's, or made "
                                     "from a unittest or pytest test command)")
    s.add_argument("--faults", type=int, help="how many faults to plant (default from --depth: 4, 6 or 10)")

    cand = sub.add_parser("candidate", help="Steps from refactorers.").add_subparsers(dest="cand_command", required=True)
    s = cmd(cand, "add", cmd_candidate_add, "Save a refactorer's step from its sandbox, or a patch file.")
    s.add_argument("--step", required=True, help="the plan's step it does, like S2")
    s.add_argument("--sandbox")
    s.add_argument("--patch")
    s.add_argument("--title", help="the commit subject (default: the refactorer's, else the step's)")
    s.add_argument("--summary", help="the commit body (default: the refactorer's)")
    s.add_argument("--allow-protected", action="store_true", help="let it change tests or dependency files (recorded)")
    s.add_argument("--allow-mention", action="store_true", help="the project itself mentions AI tools")
    s.add_argument("--force", action="store_true", help="keep it though the refactorer said it isn't done")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")
    s = cmd(cand, "show", cmd_candidate_show, "Print a step's plan and diff, for a challenger's brief.")
    s.add_argument("id")
    s.add_argument("--with-summary", action="store_true", help="include the refactorer's account (not for challengers)")

    s = cmd(sub, "verify", cmd_verify, "Check a candidate in a fresh copy: recorded runs, interface, pins, tests.")
    s.add_argument("id")
    s = cmd(sub, "probe", cmd_probe, "Run a challenger's probes on the original and the changed code.")
    s.add_argument("id")
    s.add_argument("reply", nargs="?", help="a reply saved to a file (or use --sandbox)")
    s.add_argument("--sandbox")
    s.add_argument("--timeout", type=float, default=120)
    s.add_argument("--keep", action="store_true")
    cmd(sub, "metrics", cmd_metrics, "Lines, long functions, nesting, duplicates and complexity, before and after.")
    s = cmd(sub, "land", cmd_land, "Commit each accepted step on a local branch, then check the branch.")
    s.add_argument("--branch", help="commit here (default: a new killer-refactor/<date>); if it's checked out, on it")
    s.add_argument("--uncommitted", action="store_true", help="apply the steps to the working tree instead, uncommitted")
    cmd(sub, "status", cmd_status, "The run's progress, and whether the real project changed.")
    s = cmd(sub, "decide", cmd_decide, "Record a call the user should check.")
    s.add_argument("text")
    s = cmd(sub, "wait", cmd_wait, "Record an outward action prepared but not done.")
    s.add_argument("text")
    s.add_argument("--command", help="the exact command that would do it")
    cmd(sub, "report", cmd_report, "Write report.md and summary.json.")
    cmd(sub, "clean", cmd_clean, "Delete this run's sandboxes.")
    return p


def main(argv=None) -> None:
    kit.utf8_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    after = None
    if "--" in argv:
        i = argv.index("--")
        argv, after = argv[:i], argv[i + 1:]
    p = build_parser()
    a = p.parse_args(argv)
    if after is not None and not a.takes_cmd:
        p.error(f"{a.command} takes no command after --")
    a.argv_cmd = after or []
    a.func(a)


if __name__ == "__main__":
    main()
