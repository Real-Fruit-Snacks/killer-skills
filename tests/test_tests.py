"""Scenario tests for killer-tests' script (mutants.py). Run: python -I tests/test_tests.py"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUT = ROOT / "skills" / "killer-tests" / "scripts" / "mutants.py"
PY = sys.executable
failures = []
started = time.monotonic()
for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8", errors="replace")


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name, flush=True)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


tmp = Path(tempfile.mkdtemp())
ENV = dict(os.environ, KILLER_TESTS_HOME=str(tmp / "runs"), KILLER_TESTS_SANDBOXES=str(tmp / "boxes"),
           PYTHONIOENCODING="utf-8")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    ENV.pop(k, None)


def mt(*args, ok=(0,)):
    r = subprocess.run([PY, str(MUT), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    out = r.stdout + r.stderr
    if r.returncode not in ok:
        raise AssertionError(f"exit {r.returncode} for mutants.py {' '.join(map(str, args))}:\n{out}")
    return out, r.returncode


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def run_folder(out):
    return Path(re.search(r"Run folder: (.+)", out).group(1).strip())


def box_of(out):
    return Path(re.search(r"project copy: (.+?)(?: \(at .*\))?$", out, re.M).group(1).strip()).parent


def results(ws):
    out = {}
    for line in (ws / "results.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            out[r["id"]] = r
        except (json.JSONDecodeError, KeyError):
            pass
    return out


def find(db, file, original, detail):
    hits = [m for m in db["mutants"] if m["file"] == file and m["original"].strip() == original and m["detail"] == detail]
    assert len(hits) == 1, (file, original, detail, hits)
    return hits[0]["id"]


def write_reply(box, data):
    (box / "scratch" / "reply.json").write_text(json.dumps(data), encoding="utf-8")


spec = importlib.util.spec_from_file_location("killer_tests_mutants", MUT)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

CALC = '''"""Small helpers for a shop: x < y and == in a docstring."""


def clamp(x, lo, hi):
    # x < lo means too small; == and True in a comment
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def is_adult(age):
    return age >= 18


def label(n):
    note = "a < b and c == d or True"  # and here: x + 1
    if n == 0:
        return "none"
    return note


def total(xs):
    s = 0
    for x in xs:
        s += x
    return s
'''
TESTS = '''import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from calc import clamp, label, total  # noqa: E402


class CalcTest(unittest.TestCase):
    def test_clamp_inside(self):
        self.assertEqual(clamp(5, 0, 10), 5)

    def test_clamp_low(self):
        self.assertEqual(clamp(-1, 0, 10), 0)

    def test_label(self):
        self.assertEqual(label(0), "none")

    def test_total(self):
        self.assertEqual(total([1, 2]), 3)
'''
HEAD = ("import os\nimport sys\nimport unittest\n\n"
        "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n")


def test_file(cls, body, imports="clamp, is_adult, label"):
    return HEAD + f"from calc import {imports}  # noqa: E402\n\n\nclass {cls}(unittest.TestCase):\n" + body


ADULT = test_file("AdultTest", "    def test_eighteen_is_adult(self):\n        self.assertTrue(is_adult(18))\n\n"
                               "    def test_seventeen_is_not(self):\n        self.assertFalse(is_adult(17))\n")
HIGH = test_file("HighTest", "    def test_clamp_high(self):\n        self.assertEqual(clamp(11, 0, 10), 10)\n")
NOTE = test_file("NoteTest", "    def test_label_other(self):\n"
                             "        self.assertEqual(label(3), 'a < b and c == d or True')\n")
EDGE = test_file("EdgeTest", "    def test_clamp_at_top(self):\n        self.assertEqual(clamp(10, 0, 10), 10)\n")
FLAKY = HEAD + ("from calc import clamp  # noqa: E402\n\n\nclass FlakyTest(unittest.TestCase):\n"
                "    def test_sometimes(self):\n"
                "        p = os.path.join(os.path.expanduser('~'), 'kt-count')\n"
                "        n = int(open(p).read()) + 1 if os.path.exists(p) else 1\n"
                "        open(p, 'w').write(str(n))\n"
                "        self.assertNotEqual(n, 2)\n"
                "        self.assertEqual(clamp(11, 0, 10), 10)\n")
CMD = ["--", PY, "-m", "unittest", "discover", "-s", "tests", "-q"]

try:
    # ---------- the engine: operators found, strings and comments never touched
    ms, problem, dropped, _ = M.py_mutants(CALC, "calc.py")
    ops = {m["op"] for m in ms}
    check("python: finds comparison, condition, return, constant and arithmetic mutants",
          problem is None and {"comparison", "condition", "return", "constant", "arithmetic"} <= ops, ops)
    check("python: nothing inside the docstring, the comment or the string",
          not any("note =" in m["original"] or "#" in m["original"] or '"""' in m["original"] for m in ms),
          [m["original"] for m in ms])
    src = ('LIMIT = 10\n\ndef f(a, b, ok=True):\n    s = f"{a < b} {a + 1}"\n    print("a == b")\n    log.debug("x")\n'
           '    save(a)\n    if not ok and a != 0:\n        return "x" + s\n    return a * b\n\n'
           'if __name__ == "__main__":\n    f(1, 2)\n')
    ms, _, _, _ = M.py_mutants(src, "f.py")
    lines = {m["line"] for m in ms}
    check("python: f-strings, print and logging calls, UPPER_CASE constants and the main block are left alone",
          not lines & {1, 4, 5, 6, 12, 13} and 7 in lines and 10 in lines, sorted((m["line"], m["detail"]) for m in ms))
    check("python: and/or, not x -> x, True/False and call removal",
          {"and → or", "not x → x", "True → False", "call removed"} <= {m["detail"] for m in ms},
          sorted(m["detail"] for m in ms))
    check("python: a string concatenation isn't turned into a subtraction",
          not any(m["line"] == 9 and m["op"] == "arithmetic" for m in ms))
    js = ('#!/usr/bin/env node\nconst s = "a < b"; // x == y && z\nconst r = /a+b*c/g;\nconst t = `t ${a < b} x == y`;\n'
          'function f(x) {\n  if (x >= 0 && x < 10) {\n    return true;\n  }\n  save(x);\n  console.log("hi");\n'
          '  const y = !x;\n  return x + 1;\n}\nconst h = total / 2 / count;\n')
    ms, _ = M.regex_mutants(js, "js")
    check("js: strings, comments, regex literals and template literals are masked",
          not {m["line"] for m in ms} & {1, 2, 3, 4} and {6, 7, 9, 11, 12, 14} <= {m["line"] for m in ms},
          sorted((m["line"], m["detail"]) for m in ms))
    check("js: console.log isn't removed; if (x) -> if (!(x)); return -> undefined",
          not any(m["line"] == 10 for m in ms) and "if (x) → if (!(x))" in {m["detail"] for m in ms}
          and any(m["mutated"].strip() == "return undefined;" for m in ms))
    go = 'package p\n\nvar s = `a < b`\n\nfunc F(x int) bool {\n\tv := <-ch\n\tif err := g(); err != nil {\n\t\treturn x > 1\n\t}\n\treturn false\n}\n'
    ms, _ = M.regex_mutants(go, "go")
    det = {(m["line"], m["mutated"].strip()) for m in ms}
    check("go: raw strings masked, <- untouched, the condition after ; negated",
          not any(m["line"] in (3, 6) for m in ms) and (7, "if err := g(); !(err != nil) {") in det, sorted(det))
    rs = 'fn f<\'a>(x: &\'a str, n: i32) -> bool {\n    let s = r#"a < b"#;\n    if n > 0 { return true; }\n    x.len() > 3\n}\n'
    ms, _ = M.regex_mutants(rs, "rust")
    check("rust: lifetimes don't open a string; raw strings are masked; -> isn't a comparison",
          not any(m["line"] in (1, 2) for m in ms) and any(m["line"] == 4 and m["detail"] == "> → >=" for m in ms),
          sorted((m["line"], m["detail"]) for m in ms))
    sh = ('#!/bin/sh\n# a && b\nif [ "$a" -lt 3 ]; then\n  echo "x && y -eq z"\nfi\ncat <<EOF\nnot -eq code && here\nEOF\n'
          'make && make test\nexit 1\n')
    ms, _ = M.regex_mutants(sh, "shell")
    det = {(m["line"], m["detail"]) for m in ms}
    check("shell: test operators, &&, exit codes and if negation; not in quotes, comments or heredocs",
          {(3, "-lt → -le"), (3, "if x → if ! x"), (9, "&& → ||"), (10, "1 → 0")} <= det
          and not any(m["line"] in (1, 2, 4, 7) for m in ms), sorted(det))
    ps = 'if ($a -eq 1) { "x -eq y" }  # $true -and\n$b = $true\n'
    ms, _ = M.regex_mutants(ps, "powershell")
    check("powershell: -eq, $true and if (x) in code, not in strings or comments",
          sorted(m["mutated"] for m in ms) ==
          sorted(['if ($a -ne 1) { "x -eq y" }  # $true -and', 'if (-not ($a -eq 1)) { "x -eq y" }  # $true -and',
                  '$b = $false']), [m["mutated"] for m in ms])

    # ---------- a small Python project with weak tests
    proj = tmp / "shop"
    (proj / "tests").mkdir(parents=True)
    (proj / "docs").mkdir()
    g(proj, "init", "-q", "-b", "main")
    g(proj, "config", "user.name", "Test Person")
    g(proj, "config", "user.email", "test@example.com")
    (proj / ".gitignore").write_text("__pycache__/\n")
    (proj / "calc.py").write_text(CALC)
    (proj / "constants.py").write_text("RATE = 2\n")
    (proj / "docs" / "example.py").write_text("print(1 + 1)\n")
    (proj / "README.md").write_text("# shop\n")
    (proj / "tests" / "test_calc.py").write_text(TESTS)
    g(proj, "add", "-A")
    g(proj, "commit", "-q", "-m", "first")
    main_sha = g(proj, "rev-parse", "main").strip()

    out, code = mt("init", "--repo", proj, ok=(2,))
    check("init: refuses without a test command", "give the test command after --" in out, out)
    out, _ = mt("init", "--repo", proj, "--target", "a shop", "--jobs", "2", *CMD)
    ws = run_folder(out)
    check("init: the run sits under KILLER_TESTS_HOME and finds source and test files",
          ws.is_relative_to(tmp / "runs") and "Source files it can mutate: 1; test files: 1" in out, out)

    out, _ = mt("generate", "-w", ws, "--max", "6")
    db = json.loads((ws / "mutants.json").read_text(encoding="utf-8"))
    sample = db["sample"]
    check("generate --max: a sample of 6 that covers every operator group",
          len(sample) == 6 and len({db_m["op"] for db_m in db["mutants"] if db_m["id"] in sample}) == 5, out)
    check("generate: says what it left out and why",
          "test files: tests/test_calc.py" in out and "constants files: constants.py" in out
          and "docs, examples or benchmarks: docs/example.py" in out, out)
    mt("generate", "-w", ws, "--max", "6")
    db2 = json.loads((ws / "mutants.json").read_text(encoding="utf-8"))
    check("generate: ids and the sample are the same on a second run",
          db2["sample"] == sample and [m["id"] for m in db2["mutants"]] == [m["id"] for m in db["mutants"]])
    out, _ = mt("generate", "-w", ws, "--all")
    db = json.loads((ws / "mutants.json").read_text(encoding="utf-8"))
    check("generate --all: 18 mutants in calc.py, all in the sample",
          len(db["mutants"]) == 18 and len(db["sample"]) == 18, out)
    # a mutant that breaks the build, slipped into the sample
    db["mutants"].append({"id": "M99", "file": "calc.py", "line": 4, "col": 0, "op": "statement", "detail": "broken",
                          "original": "def clamp(x, lo, hi):", "mutated": "def clamp(x, lo, hi:", "lang": "python",
                          "func": "clamp"})
    db["sample"].append("M99")
    (ws / "mutants.json").write_text(json.dumps(db), encoding="utf-8")

    out, code = mt("run", "-w", ws, ok=(2,))
    check("run: refuses before a baseline", "run baseline first" in out, out)
    out, _ = mt("baseline", "-w", ws)
    bl = json.loads((ws / "baseline.json").read_text())
    check("baseline: 3 of 3 passes, a timeout of at least 10 s, 2 copies at once checked",
          "passed 3 of 3 runs" in out and bl["timeout"] >= 10 and bl["jobs"] == 2 and "2 copies at once" in out, out)

    out, _ = mt("run", "-w", ws)
    res = results(ws)
    ids = {
        "le": find(db, "calc.py", "if x < lo:", "< → <="),
        "neg": find(db, "calc.py", "if x < lo:", "if x → if not (x)"),
        "hi": find(db, "calc.py", "return hi", "return x → return None"),
        "ge": find(db, "calc.py", "if x > hi:", "> → >="),
        "adult": find(db, "calc.py", "return age >= 18", "return x → return None"),
        "adult_gt": find(db, "calc.py", "return age >= 18", ">= → >"),
        "adult_19": find(db, "calc.py", "return age >= 18", "18 → 19"),
        "note": find(db, "calc.py", "return note", "return x → return None"),
        "sum": find(db, "calc.py", "s += x", "+= → -="),
    }
    st = {k: res.get(v, {}).get("status") for k, v in ids.items()}
    check("run: killed and survived as the tests deserve",
          st == {"le": "survived", "neg": "killed", "hi": "survived", "ge": "survived", "adult": "survived",
                 "adult_gt": "survived", "adult_19": "survived", "note": "survived", "sum": "killed"}, st)
    check("run: a mutant that breaks the build is not viable, and left out of the score",
          res["M99"]["status"] == "error" and "Mutation score: 61% (11 of 18 viable" in out
          and "1 not viable" in out, (res["M99"], out))
    check("run: the real project is untouched, and the pool's sandboxes are gone",
          g(proj, "status", "--porcelain") == "" and not json.loads((ws / "config.json").read_text())["sandboxes"],
          g(proj, "status", "--porcelain"))

    out, _ = mt("status", "-w", ws)
    check("status: the project's git status is as it was at init", "is the same as the reference" in out, out)
    (proj / "stray.txt").write_text("left by something\n")
    out, code = mt("status", "-w", ws, ok=(1,))
    check("status: a file that appeared in the project is caught", "CHANGED" in out and "stray.txt" in out, out)
    (proj / "stray.txt").unlink()

    # ---------- resume
    out, code = mt("run", "-w", ws, ok=(2,))
    check("run: a second run without --resume or --fresh is refused", "--resume runs the rest" in out, out)
    lines = (ws / "results.jsonl").read_text(encoding="utf-8").splitlines()
    (ws / "results.jsonl").write_text("\n".join(lines[:15]) + '\n{"id": "M1', encoding="utf-8")
    out, _ = mt("run", "-w", ws, "--resume", "--jobs", "1")
    check("run --resume: runs only what's missing, past a torn last line",
          "Running 4 mutants (15 done already)" in out and len(results(ws)) == 19
          and {k: results(ws)[v]["status"] for k, v in ids.items()} == st, out)

    # ---------- the flaky baseline
    flaky = tmp / "flaky"
    (flaky / "tests").mkdir(parents=True)
    (flaky / "calc.py").write_text(CALC)
    (flaky / "tests" / "test_calc.py").write_text(FLAKY.replace("        self.assertEqual(clamp(11, 0, 10), 10)\n", ""))
    out, _ = mt("init", "--repo", flaky, "--slug", "flaky", *CMD)
    wf = run_folder(out)
    out, code = mt("baseline", "-w", wf, ok=(1,))
    check("baseline: a test that fails every other run is flaky, and the run stops there",
          "FLAKY: the suite failed 1 of 3 runs" in out and "test_sometimes" in out, out)
    mt("generate", "-w", wf)
    out, _ = mt("run", "-w", wf, ok=(2,))
    check("run: refuses on a flaky baseline", "baseline was flaky" in out, out)
    mt("report", "-w", wf)
    sf = json.loads((wf / "summary.json").read_text(encoding="utf-8"))
    check("report: a flaky suite is blocked", sf["status"] == "blocked" and "flaky" in sf["headline"], sf)

    # ---------- slices, writer sandboxes, malformed replies
    out, _ = mt("slices", "-w", ws)
    slices = json.loads((ws / "state.json").read_text(encoding="utf-8"))["slices"]
    check("slices: the 7 survivors in one slice for calc.py", len(slices) == 1 and len(slices["S1"]["mutants"]) == 7, out)
    out, _ = mt("sandbox", "-w", ws, "write-S1", "--slice", "S1")
    box = box_of(out)
    out, code = mt("collect", "-w", ws, "--sandbox", "write-S1", ok=(2,))
    check("collect: a missing reply is an error, and the sandbox stays", "no reply at" in out and box.exists(), out)
    (box / "scratch" / "reply.json").write_text("not json {", encoding="utf-8")
    out, code = mt("collect", "-w", ws, "--sandbox", "write-S1", ok=(2,))
    check("collect: a reply that isn't JSON is an error", "no JSON that parses" in out, out)
    md = (box / "scratch" / "mutants" / "mutants.md").read_text(encoding="utf-8")
    check("sandbox --slice: the faults as diffs in the scratch folder",
          f"## {ids['adult']}: calc.py line 14, in is_adult()" in md and "-    return age >= 18" in md, md[:600])
    helper = [PY, str(box / "scratch" / "mutants" / "mutant.py")]
    r = subprocess.run(helper + [ids["adult_gt"]], capture_output=True, text=True, cwd=box / "repo")
    check("mutant.py puts a fault in", r.returncode == 0 and "age > 18" in (box / "repo" / "calc.py").read_text(), r.stdout)
    # the writer adds a good test but leaves the fault in and edits calc.py too: refused
    (box / "repo" / "tests" / "test_adult.py").write_text(ADULT)
    (box / "repo" / "calc.py").write_text((box / "repo" / "calc.py").read_text() + "\n# tweak\n")
    write_reply(box, {"tests": [{"file": "tests/test_adult.py", "name": "test_eighteen_is_adult",
                                 "checks": "18 counts as an adult", "targets": [ids["adult"], ids["adult_gt"],
                                                                                ids["adult_19"]]}]})
    out, code = mt("collect", "-w", ws, "--sandbox", "write-S1", "--keep", ok=(1,))
    check("check-test: a patch that touches a non-test file is refused, nothing run",
          "FAIL: it changes files that aren't tests: calc.py" in out and "the real code" not in out, out)
    # now only the fault is left behind: put back, and the test is checked
    (box / "repo" / "calc.py").write_text(CALC.replace("return age >= 18", "return age > 18"))
    out, code = mt("collect", "-w", ws, "--sandbox", "write-S1", "--accept")
    check("collect: a fault the writer left in is put back, and the tests pass the check",
          "was still in calc.py; the line was put back" in out and "PASS: P2's tests pass 3 of 3 runs on the real code "
          "and kill 3 of its 3 targets" in out and "Accepted P2" in out, out)
    check("collect: deletes the sandbox; the real project is untouched",
          not box.exists() and g(proj, "status", "--porcelain") == "")

    # ---------- one writer sandbox, kept between attempts: a test that kills nothing, a flaky one, two good ones
    out, _ = mt("sandbox", "-w", ws, "write-more", "--mutants", ids["ge"], ids["le"], ids["hi"], ids["note"])
    box = box_of(out)
    check("sandbox: has the accepted tests in it", (box / "repo" / "tests" / "test_adult.py").exists())

    def attempt(fname, body, reply, ok=(0,)):
        for f in ("test_edge.py", "test_flaky.py", "test_hi.py", "test_note.py"):
            (box / "repo" / "tests" / f).unlink(missing_ok=True)
        (box / "repo" / "tests" / fname).write_text(body)
        write_reply(box, reply)
        out, _ = mt("collect", "-w", ws, "--sandbox", "write-more", "--keep", ok=ok)
        return out, re.search(r"^(P\d+) from write-more", out, re.M).group(1)

    out, _ = attempt("test_edge.py", EDGE, {
        "tests": [{"file": "tests/test_edge.py", "name": "test_clamp_at_top", "checks": "10 stays 10",
                   "targets": [ids["ge"]]}],
        "equivalent": [{"mutant": ids["le"], "reason": "x == lo returns lo, which is x"}]}, ok=(1,))
    check("check-test: FAIL for a test that passes but kills none of its targets",
          "FAIL: the tests pass on the real code but kill none of their targets" in out and "1 claimed equivalent" in out,
          out)
    out, _ = attempt("test_flaky.py", FLAKY, {"tests": [{"file": "tests/test_flaky.py", "targets": [ids["hi"]]}]},
                     ok=(1,))
    check("check-test: FAIL for a new test that's flaky on the real code",
          "FAIL: the new tests are flaky: the suite failed 1 of 3 runs on the real code" in out, out)

    # two patches checked against the same accepted list: the second one's check goes stale
    out, p_hi = attempt("test_hi.py", HIGH, {"tests": [{"file": "tests/test_hi.py", "name": "test_hi",
                                                        "checks": "covers hi", "targets": [ids["hi"]]}]})
    out, p_note = attempt("test_note.py", NOTE, {"tests": [{"file": "tests/test_note.py", "name": "test_note",
                                                            "checks": "covers note", "targets": [ids["note"]]}]})
    check("check-test: PASS for a test that kills its survivor", "PASS:" in out, out)
    mt("clean", "-w", ws)
    mt("accept", "-w", ws, p_hi)
    out, code = mt("accept", "-w", ws, p_note, ok=(1,))
    check("accept: refused when another patch was accepted after this one was checked",
          f"Run check-test {p_note} again" in out, out)
    out, _ = mt("check-test", "-w", ws, p_note, "--accept")
    check("check-test again on top of both, then accepted", "PASS" in out and f"Accepted {p_note}" in out, out)
    out, _ = mt("score", "-w", ws)
    check("score: with the accepted tests, 16 of 18 viable mutants killed", "(16 of 18 killed)" in out, out)

    judge = tmp / "judge.json"
    judge.write_text(json.dumps({"verdict": "equivalent", "reason": "at x == lo both return the same value"}))
    out, _ = mt("equivalent", "-w", ws, ids["le"], "--from", judge)
    check("equivalent: a judge's verdict leaves the mutant out of the score", "left out of the score" in out, out)
    review = tmp / "review.json"
    review.write_text(json.dumps({"verdict": "ok", "issues": []}))
    mt("review", "-w", ws, "--from", review)
    mt("decide", "-w", ws, "Left the clamp >= mutant: it's equivalent too")
    mt("wait", "-w", ws, "Push the branch", "--command", "git push -u origin killer-tests/x")

    # ---------- land, rescore, report
    head_before = g(proj, "rev-parse", "HEAD").strip()
    out, _ = mt("land", "-w", ws)
    branch = re.search(r"Landed on (\S+):", out).group(1)
    log = g(proj, "log", "--format=%an|%s%n%b", f"main..{branch}")
    check("land: a new local branch, one commit per test file, the suite passes there",
          branch.startswith("killer-tests/") and "3 commits" in out and "passed" in out.split("fresh copy")[-1], out)
    check("land: the original branch and working tree are untouched",
          g(proj, "rev-parse", "main").strip() == main_sha == head_before
          and g(proj, "branch", "--show-current").strip() == "main" and g(proj, "status", "--porcelain") == ""
          and not (proj / "tests" / "test_adult.py").exists(), g(proj, "status", "--porcelain"))
    check("land: the commits are the user's, plain, with nothing about agents",
          log.count("Test Person|Add tests for calc.py") == 3
          and not re.search(r"co-authored|claude|generated|agent|mutant", log, re.I), log)
    check("land: only test files changed on the branch",
          sorted(g(proj, "diff", "--name-only", f"main..{branch}").split())
          == ["tests/test_adult.py", "tests/test_hi.py", "tests/test_note.py"])
    out, _ = mt("rescore", "-w", ws)
    check("rescore: the same sample against the branch, before and after",
          f"against branch {branch}" in out and "After the new tests (rescored): 94% (16 of 17 killed)" in out, out)
    out, _ = mt("report", "-w", ws)
    summary = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
    report = (ws / "report.md").read_text(encoding="utf-8")
    n = summary["numbers"]
    check("report: summary.json has the before and after, the branch and commits",
          summary["status"] == "done" and n["score_before"] == 64.7 and n["score_after"] == 94.1
          and n["tests_added"] == 4 and n["survivors_left"] == 1 and summary["branch"] == branch
          and len(summary["commits"]) == 3, summary)
    check("report: report.md has the same numbers, the survivor left and the equivalent",
          "| **All** | 19 | **65%** | **94%** |" in report and "Mutation score 65% -> 94%" in report
          and "Test written but didn't kill it" in report and "## Left out as equivalent" in report
          and "(failures=" not in report and "Push the branch" in summary["waiting"][0] and summary["decisions"], report)
    check("report: the last line is the one-line summary",
          out.strip().splitlines()[-1].startswith("killer-tests: done · Mutation score 65% -> 94%"), out)
    out, _ = mt("drop", "-w", ws, p_hi, "--reason", "testing drop")
    stj = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    check("drop: the patches accepted after it are out too, until checked again",
          f"{p_note} were checked on top of it" in out and stj["accepted"] == ["P2"]
          and stj["patches"][p_note]["status"] == "needs recheck" and ids["note"] not in stj["kills"], out)
    out, code = mt("accept", "-w", ws, p_note, ok=(1,))
    check("accept: a patch whose check went stale with a drop has to be checked again",
          "hasn't passed check-test" in out, out)
    out, _ = mt("sandbox", "-w", ws, "fix-hi", "--patch", p_hi)
    fix = box_of(out)
    check("sandbox --patch: a dropped patch's changes are in the copy, uncommitted, to fix",
          (fix / "repo" / "tests" / "test_hi.py").exists()
          and "tests/test_hi.py" in g(fix / "repo", "status", "--porcelain", "--untracked-files=all"), out)
    mt("clean", "-w", ws)

    # ---------- the C-like path end to end, when Node.js is here
    node = shutil.which("node")
    nv = subprocess.run([node, "-p", "process.versions.node"], capture_output=True, text=True).stdout.strip() if node else ""
    if nv and int(nv.split(".")[0]) >= 18:
        jsp = tmp / "jsproj"
        (jsp / "test").mkdir(parents=True)
        (jsp / "lib.js").write_text('"use strict";\nconst NOTE = "x < y && z"; // a < b\n\nfunction inRange(x) {\n'
                                    '  return x >= 0 && x < 10;\n}\n\nfunction double(x) {\n  return x * 2;\n}\n\n'
                                    'module.exports = { inRange, double, NOTE };\n')
        (jsp / "test" / "lib.test.js").write_text(
            'const test = require("node:test");\nconst assert = require("node:assert");\n'
            'const { inRange } = require("../lib.js");\n'
            'test("inRange", () => {\n  assert.strictEqual(inRange(5), true);\n  assert.strictEqual(inRange(-1), false);\n});\n')
        out, _ = mt("init", "--repo", jsp, "--jobs", "1", "--", node, "--test", "test/lib.test.js")
        wj = run_folder(out)
        mt("baseline", "-w", wj)
        mt("generate", "-w", wj)
        dj = json.loads((wj / "mutants.json").read_text(encoding="utf-8"))
        mt("run", "-w", wj)
        rj = results(wj)
        by = {(m["line"], m["detail"]): rj[m["id"]]["status"] for m in dj["mutants"]}
        check("js end to end: mutants only in code, killed and survived as deserved",
              not any(m["line"] in (1, 2, 12) for m in dj["mutants"]) and by.get((5, "&& → ||")) == "killed"
              and by.get((5, "return x → return undefined")) == "killed" and by.get((5, ">= → >")) == "survived"
              and by.get((9, "* → /")) == "survived" and "error" not in by.values(), by)
    else:
        print("SKIP js end to end: no Node.js 18 or later")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"took {time.monotonic() - started:.0f} s")
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
