"""Scenario tests for killer-refactor's script (refactor.py). Run: python -I tests/test_refactor.py

The fixture is a tangled sales-report module with a command line, and unittest tests that
check only part of it. Refactorers, pin writers and challengers are played by the test,
writing into the sandboxes the script makes, so every check the script runs is real: fresh
copies, recorded runs, the interface probe, planted faults, probes on both sides, landing
through a worktree and the final check of the branch. Nothing depends on timing: the flaky
test flips on a counter in a file, not on the clock.
"""
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

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "killer-refactor" / "scripts" / "refactor.py"
PY = sys.executable
failures = []
T0 = time.monotonic()


def check(name, cond, detail=""):
    when = f"  [{time.monotonic() - T0:.0f} s]" if os.environ.get("REFACTOR_TEST_TIMES") else ""
    print(("PASS " if cond else "FAIL ") + name + when, flush=True)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


tmp = Path(tempfile.mkdtemp())   # unresolved on purpose: /var on macOS, an 8.3 name on Windows runners
FLIP = tmp / "flip.txt"
FAIL_FLAG = tmp / "fail.flag"
ENV = dict(os.environ, KILLER_REFACTOR_HOME=str(tmp / "runs"), KILLER_REFACTOR_SANDBOXES=str(tmp / "boxes"),
           PYTHONIOENCODING="utf-8")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    ENV.pop(k, None)


def rf(*args, ok=(0,)):
    r = subprocess.run([PY, str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    out = r.stdout + r.stderr
    if r.returncode not in ok:
        raise AssertionError(f"exit {r.returncode} for refactor.py {' '.join(map(str, args))}:\n{out}")
    return out, r.returncode


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def run_folder(out):
    return Path(re.search(r"Run folder: (.+)", out).group(1).strip())


def box_of(out):
    return Path(re.search(r"project copy: (.+?)(?: \(at .*\))?$", out, re.M).group(1).strip()).parent


def state(ws):
    return json.loads((ws / "refactor.json").read_text(encoding="utf-8"))


def cand(ws, cid):
    return next(c for c in state(ws)["candidates"] if c["id"] == cid)


def same_path(printed, want):
    return Path(printed).resolve() == Path(want).resolve()


def agent(name, *extra, files=None, reply=None):
    """Make a sandbox the way the lead does, and play the agent: write files and reply.json."""
    o, _ = rf("sandbox", "-w", ws, name, *extra)
    box = box_of(o)
    for rel, text in (files or {}).items():
        p = box / "repo" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())
    if reply is not None:
        (box / "scratch" / "reply.json").write_text(json.dumps(reply), encoding="utf-8")
    return box, o


HEAD = "import os\nimport sys\n\nsys.path.insert(0, os.getcwd())\n"
PARSERS = '''def parse_number(text):
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        return -int(text[1:-1])
    return int(text)


def parse_line(line, sep=","):
    return [field.strip() for field in line.split(sep)]
'''
LOOP = '''    counts = {}
    totals = {}
    for region, name, amount in rows:
        size = classify(amount)
        counts[size] = counts.get(size, 0) + 1
        totals[region] = totals.get(region, 0) + amount
        if amount < 0:
            totals["refunds"] = totals.get("refunds", 0) + 1
'''
MAIN = '''def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: tangled.py report <file> [--stamp] [--out <file>] [--width N] | classify <n>",
              file=sys.stderr)
        return 2
    command = argv[0]
    if command == "classify":
        if len(argv) != 2:
            print("classify takes one number", file=sys.stderr)
            return 2
        print(classify(parse_number(argv[1])))
        return 0
    if command != "report":
        print(f"unknown command: {command}", file=sys.stderr)
        return 2
    path = None
    stamp = False
    out = None
    width = DEFAULT_WIDTH
    args = iter(argv[1:])
    for arg in args:
        if arg == "--stamp":
            stamp = True
        elif arg == "--out":
            out = next(args, None)
            if out is None:
                print("--out needs a file", file=sys.stderr)
                return 2
        elif arg == "--width":
            value = next(args, None)
            if value is None or not value.isdigit():
                print("--width needs a number", file=sys.stderr)
                return 2
            width = int(value)
        elif path is None:
            path = arg
        else:
            print(f"unexpected argument: {arg}", file=sys.stderr)
            return 2
    if path is None:
        print("report needs a file", file=sys.stderr)
        return 2
    rows = []
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            line = line.rstrip("\\n")
            if not line or line.startswith("#"):
                continue
            fields = parse_line(line)
            if len(fields) != 3:
                print(f"line {number}: expected 3 fields, got {len(fields)}", file=sys.stderr)
                return 1
            region, name, amount = fields
            if region:
                if name:
                    try:
                        rows.append((region, name, parse_number(amount)))
                    except ValueError:
                        print(f"line {number}: bad amount {amount!r}", file=sys.stderr)
                        return 1
    report = Report(rows)
    counts, totals = summarise(rows)
    text = format_report(report, counts, totals, width)
    print(text)
    if stamp:
        print(f"generated {time.time():.6f}")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text + "\\n")
    return 0
'''
REST = '''def classify(n):
    if n >= 100:
        return "large"
    elif n >= 10:
        return "medium"
    return "small"


def shorten(name, width=12):
    if len(name) > width:
        return name[:width - 1] + "~"
    return name


class Report:
    def __init__(self, rows):
        self.rows = rows

    def total(self):
        return sum(amount for _, _, amount in self.rows)

    def regions(self):
        return sorted({region for region, _, _ in self.rows})


def summarise(rows):
''' + LOOP + '''    return counts, totals


def summarise_names(rows):
''' + LOOP + '''    return sorted(shorten(name) for _, name, _ in rows)


def format_report(report, counts, totals, width=DEFAULT_WIDTH):
    lines = ["=" * width, f"total: {report.total()}"]
    for region in report.regions():
        lines.append(f"{region:<10}{totals[region]:>8}")
    for size in ("small", "medium", "large"):
        lines.append(f"{size:<10}{counts.get(size, 0):>8}")
    if totals.get("refunds"):
        lines.append(f"refunds: {totals['refunds']}")
    lines.append("-" * width)
    return "\\n".join(lines)


''' + MAIN + '''

if __name__ == "__main__":
    sys.exit(main())
'''
# The tool adds its own folder to sys.path: the Windows Python pack is Python's embeddable package, whose
# ._pth file leaves the script's folder off sys.path, and the split below imports a module beside it.
TOP = ('"""Sales report tool: reads region,name,amount lines and prints a summary."""\nimport os\nimport sys\nimport time\n\n'
       'if os.path.dirname(os.path.abspath(__file__)) not in sys.path:\n'
       '    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n')
TANGLED = TOP + "\nDEFAULT_WIDTH = 40\n\n\n" + PARSERS + "\n\n" + REST
SPLIT = TOP + "\nfrom tangled_parse import parse_line, parse_number\n\nDEFAULT_WIDTH = 40\n\n\n" + REST
PARSE_MOD = '"""Parsing helpers for the sales report tool."""\n\n\n' + PARSERS
TESTS = '''import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tangled  # noqa: E402


class TangledTest(unittest.TestCase):
    def test_parse_line(self):
        self.assertEqual(tangled.parse_line("a, b ,c"), ["a", "b", "c"])

    def test_parse_number(self):
        self.assertEqual(tangled.parse_number("12"), 12)
        self.assertEqual(tangled.parse_number("(5)"), -5)

    def test_classify_ends(self):
        self.assertEqual(tangled.classify(3), "small")
        self.assertEqual(tangled.classify(500), "large")

    def test_report(self):
        rows = [("east", "ann", 5), ("west", "bob", 150)]
        counts, totals = tangled.summarise(rows)
        text = tangled.format_report(tangled.Report(rows), counts, totals, width=10)
        self.assertIn("total: 155", text)
        self.assertIn("east", text)

    def test_flaky(self):
        path = os.environ.get("REFAC_FLIP")
        if path and os.path.exists(path):
            n = int(open(path).read() or 0) + 1
            with open(path, "w") as fh:
                fh.write(str(n))
            self.assertNotEqual(n, 2, "flips on the second run")

    def test_flag(self):
        path = os.environ.get("REFAC_FAIL")
        self.assertFalse(path and os.path.exists(path), "failing on purpose: the flag file is there")


if __name__ == "__main__":
    unittest.main()
'''
DATA = "# region,name,amount\neast,ann,5\nwest,bob,150\neast,carol,10\nnorth,dave-with-a-long-name,(20)\nwest,eve,99\n"
DRIVER = HEAD + '''import tempfile

import tangled

rows = [("north", "a-very-long-customer-name", 10), ("south", "zed", 99), ("north", "amy", -3)]
counts, totals = tangled.summarise(rows)
print(tangled.format_report(tangled.Report(rows), counts, totals))
print(tangled.summarise_names(rows))
print("cwd", os.getcwd())
print("tmp", tempfile.gettempdir())
with open(os.path.join(os.path.expanduser("~"), ".tangled-history"), "a", encoding="utf-8") as fh:
    fh.write(f"{len(rows)} rows\\n")
'''
PIN_GOOD = '''import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tangled  # noqa: E402


class PinShorten(unittest.TestCase):
    def test_what_shorten_does_now(self):
        self.assertEqual(tangled.shorten("abcdefghijklmnop"), "abcdefghijk~")
        self.assertEqual(tangled.shorten("abcdefghijkl"), "abcdefghijkl")
        self.assertEqual(tangled.shorten("short"), "short")
        self.assertEqual(tangled.shorten("abcdef", width=3), "ab~")
'''
PIN_WEAK = PIN_GOOD.replace("PinShorten", "PinWeak").split("    def test_")[0] + \
    "    def test_it_exists(self):\n        self.assertTrue(callable(tangled.shorten))\n"
PIN_WRONG = PIN_GOOD.replace("PinShorten", "PinWrong").replace('"abcdefghijk~"', '"abcdefghijkl~"')
RENAMED = SPLIT.replace("format_report", "render_report")
OFF_BY_ONE = SPLIT.replace('''    if n >= 100:
        return "large"
    elif n >= 10:
        return "medium"
    return "small"''', '''    if n >= 100:
        return "large"
    return "medium" if n > 10 else "small"''').replace("format_report", "render_report")
DROPS_EMPTY = PARSE_MOD.replace("for field in line.split(sep)]", "for field in line.split(sep) if field.strip()]")


def probe_script(body):
    return HEAD + "import tangled\n\n" + body


try:
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("refactor_under_test", SCRIPT)
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)

    # ---------- pure parts, against values worked out by hand
    ren = R.Renames([("tangled.format_report", "tangled.render_report")])
    check("renames carry through: the dotted name and the bare one, not longer names that contain it",
          ren.code("tangled.format_report(x); format_report(y); format_reports(z)\n", "t.py")
          == "tangled.render_report(x); render_report(y); format_reports(z)\n"
          and ren.code("render_report(y)\n", "t.py", reverse=True) == "format_report(y)\n")
    base = ["module m", "function m.f(a, b=1)", "function m.g(x)", "class m.C(rows)", "method m.C.total(self)"]
    cmp = R.compare_surface("python", base, base, [], "same")
    check("interface: the same lines compare unchanged", cmp["ok"] and R.surface_line(cmp) == "unchanged")
    renamed = [ln.replace("m.f(", "m.h(") for ln in base]
    cmp = R.compare_surface("python", base, renamed, [], "planned")
    check("interface: an unplanned rename is a removal and an addition", not cmp["ok"]
          and cmp["missing"] == ["function m.f(a, b=1)"] and cmp["added"] == ["function m.h(a, b=1)"], cmp)
    check("interface: exactly the planned rename passes; a rename to another name doesn't",
          R.compare_surface("python", base, renamed, [["rename", "m.f", "m.h"]], "planned")["ok"]
          and not R.compare_surface("python", base, renamed, [["rename", "m.f", "m.k"]], "planned")["ok"])
    moved = [ln.replace("m.C", "m.Totals") for ln in base]
    check("interface: renaming a class carries its methods with it",
          R.compare_surface("python", base, moved, [["rename", "m.C", "m.Totals"]], "planned")["ok"])
    changed = [ln.replace("b=1", "b=2") for ln in base]
    check("interface: a changed default is caught, unless the plan allows that change",
          not R.compare_surface("python", base, changed, [], "same")["ok"]
          and R.compare_surface("python", base, changed, [["change", "m.f"]], "planned")["ok"])
    check("interface: new entries need an 'add' rule; 'free' reports but never refuses",
          not R.compare_surface("python", base, base + ["module m2"], [], "same")["ok"]
          and R.compare_surface("python", base, base + ["module m2"], [["add", "m2*"]], "same")["ok"]
          and R.compare_surface("python", base, renamed, [], "free")["ok"])
    norm = R.normalise("at 2026-11-07T10:20:30Z took 12 ms id 123e4567-e89b-12d3-a456-426614174000 <obj at "
                       "0x7f3a2b1c9d00> crc 0x1a2b3c4d  \r\nnext 1730977777.5\n\n", None, [r"next [\d.]+"])
    check("normalising: times, durations, ids and object addresses masked; a plain hex value, line endings, "
          "spaces and blank lines kept",
          norm == "at <time> took <duration> id <id> <obj at <address>> crc 0x1a2b3c4d  \r\n<masked>\n\n", repr(norm))
    rec = lambda out: {"exit": 0, "stdout": out, "stderr": "", "files": {}}
    check("comparing runs: the exit code, then stdout line by line, then files; long output by its hash",
          R.first_diff(rec("a\nb"), rec("a\nc")) == "stdout line 2 was 'b', now 'c'"
          and R.first_diff({**rec("a"), "exit": 1}, rec("a")) == "the exit code was 1, now 0"
          and R.first_diff(rec("a"), {**rec("a"), "files": {"repo/x.txt": "text:1"}}) == "it now writes repo/x.txt"
          and "too long to show" in R.first_diff(rec("sha256:" + "0" * 64 + " (300000 characters)"), rec("a"))
          and "line endings" in R.first_diff(rec("a\r\nb\r\n"), rec("a\nb\n")))
    ren = R.Renames([("calc.total", "calc.grand_total")])
    check("renames carry through names in code only: a string key, a comment and a data file stay as they are",
          ren.code('import calc\nr = {"total": 2}\ncalc.total(r)  # total\n', "t.py")
          == 'import calc\nr = {"total": 2}\ncalc.grand_total(r)  # total\n'
          and ren.data(b'{"total": 2}\n', "data.json") == b'{"total": 2}\n'
          and ren.code('s = "total"; lib.total(x); total(y);\n', "t.js") == 's = "total"; lib.total(x); grand_total(y);\n')
    check("renames: another module's function of the same name is left alone; a move rewrites a lone import; a "
          "method rename leaves bare names and keyword arguments alone",
          R.Renames([("pkg.config.load", "pkg.config.load_config")]).code("x = json.load(f)\ny = load(p)\n", "t.py")
          == "x = json.load(f)\ny = load_config(p)\n"
          and R.Renames([("pkg.a.f", "pkg.b.f")]).code("from pkg.a import f\nf(1)\n", "t.py") == "from pkg.b import f\nf(1)\n"
          and R.Renames([("m.R.total", "m.R.sum")], {"m.R"}).code("r.total(); total; g(total=1)\n", "t.py")
          == "r.sum(); total; g(total=1)\n")
    ok_old = rec("['a', '', 'b']\n")
    check("probes: same, different, broken, renamed and unstable are told apart by what ran",
          R.judge_probe(ok_old, ok_old, R.NO_RENAMES, None)[0] == "same"
          and R.judge_probe(ok_old, rec("['a', 'b']\n"), R.NO_RENAMES, lambda: (ok_old, rec("['a', 'b']\n")))[0] == "different"
          and R.judge_probe({**rec(""), "exit": 1, "stderr": "ModuleNotFoundError: No module named 'x'"},
                            {**rec(""), "exit": 1, "stderr": "ModuleNotFoundError: No module named 'x'"}, R.NO_RENAMES,
                            None)[0] == "broken"
          and R.judge_probe(rec("TypeError compute()\n"), rec("TypeError calculate()\n"),
                            R.Renames([("m.compute", "m.calculate")]), None)[0] == "renamed"
          and R.judge_probe(ok_old, rec("x\n"), R.NO_RENAMES, lambda: (rec("other\n"), rec("x\n")))[0] == "unstable")
    flip = iter([rec("['b', 'a']\n"), rec("['a', 'b']\n")])
    out_, why = R.judge_probe(rec("['a', 'b']\n"), next(flip), R.NO_RENAMES, lambda: (rec("['a', 'b']\n"), next(flip)))
    check("probes: output the changed code no longer repeats is a difference, not an unstable probe",
          out_ == "different" and "no longer gives the same output every time" in why, why)
    st_ = {"chain": ["C1", "C2"], "candidates": [{"id": "C3", "status": "verified", "on": ["C1"]},
                                                 {"id": "C4", "status": "verified", "on": ["C1", "C2"]}]}
    R.restale(st_)
    check("stale: a step verified on other accepted steps than the ones now is stale",
          [c["status"] for c in st_["candidates"]] == ["stale", "verified"])
    clash = R.compare_surface("python", ["function m.old_api(path, strict=False)", "function m.api(path)"],
                              ["function m.api(path, strict=False)"], [["rename", "m.old_api", "m.api"]], "planned")
    check("interface: a rename onto a name that already exists is refused", not clash["ok"]
          and "already exist" in R.surface_line(clash), clash)
    check("protected: Django-style tests.py, test configuration and build files",
          R.is_test("polls/tests.py") and R.matches("pytest.ini", R.DEP_PATTERNS) and R.matches("Makefile", R.DEP_PATTERNS)
          and not R.is_test("tangled.py"))
    fake = tmp / "fake-box"
    (fake / "repo").mkdir(parents=True)
    (fake / "env.json").write_text(json.dumps({"set": {}, "drop": []}), encoding="utf-8")
    raw = lambda b: R.execute(fake, [PY, "-c", f"import sys; sys.stdout.buffer.write({b!r})"])["out"]
    check("output that isn't UTF-8 keeps its bytes apart (café in one code page against cafè)",
          raw(b"caf\xe9") != raw(b"caf\xe8") and raw(b"caf\xc3\xa9") == "café")
    probe_dir = tmp / "probe-only"
    probe_dir.mkdir()
    (probe_dir / "deco.py").write_text("class Command:\n    def __init__(self, f):\n        self.f = f\n"
                                       "    def __call__(self, *a, **k):\n        return self.f(*a, **k)\n\n\n"
                                       "@Command\ndef serve(port, host='localhost'):\n    return port\n\n\n"
                                       "def plain(x):\n    return x\n", encoding="utf-8")
    (probe_dir / "probe.py").write_text(R.PROBE, encoding="utf-8")
    (probe_dir / "cfg.json").write_text(json.dumps({"folders": ["."], "modules": [], "skip_dirs": []}), encoding="utf-8")
    subprocess.run([PY, "-B", "probe.py", "cfg.json", "out.json"], cwd=probe_dir, check=True, capture_output=True)
    listed = json.loads((probe_dir / "out.json").read_text(encoding="utf-8"))["lines"]
    check("interface: a function a decorator turned into another callable is still listed",
          any(ln.startswith("callable deco.serve") for ln in listed) and "function deco.plain(x)" in listed, listed)
    F = R.faults
    text = "def f(n):\n    if n >= 10:\n        return n + 1\n    return 0\n"
    found = F.find(text, "python")
    check("faults.py: comparisons, arithmetic and constants, each one compiling",
          {x["detail"] for x in found} >= {">= → >", "+ → -", "10 → 11", "0 → 1"}
          and all(compile(F.plant(text, x), "x", "exec") for x in found))
    check("faults.py: nothing planted in strings, comments, f-strings or the main guard",
          F.find('x = "a < b"  # c == d\ny = f"{n + 1}"\nif __name__ == "__main__":\n    print(1 + 2)\n', "python") == [])
    check("faults.py: C-like code with strings and comments blanked first",
          [x["detail"] for x in F.find('if (a < b) { s = "x == y"; } // c && d\n', "clike")] == ["< → <="])
    lines = TANGLED.split("\n")
    m = R.totals({"tangled.py": TANGLED})
    check("metrics: worked out by hand for the fixture: one function over 50 lines (main), nesting 5, two "
          "duplicated blocks of 8 lines, main's complexity 24",
          m["long_functions"] == 1 and m["longest"].startswith("main (") and m["max_nesting"] == 5
          and m["duplicated_blocks"] == 2 and m["duplicated_lines"] == 16 and m["most_complex"] == "main (24)"
          and m["lines"] == sum(1 for ln in lines if ln.strip()) and m["functions"] == 11, m)

    # ---------- the project
    proj = tmp / "proj"
    (proj / "tests").mkdir(parents=True)
    g(proj, "init", "-q", "-b", "main")
    g(proj, "config", "user.name", "Test Person")
    g(proj, "config", "user.email", "test@example.com")
    g(proj, "config", "core.autocrlf", "false")
    for rel, text in (("tangled.py", TANGLED), ("tests/test_tangled.py", TESTS), ("data.csv", DATA),
                      ("requirements.txt", "\n"), (".gitignore", "__pycache__/\nsummary.txt\n")):
        (proj / rel).write_bytes(text.encode())
    g(proj, "add", "-A")
    g(proj, "commit", "-q", "-m", "first")
    head = g(proj, "rev-parse", "HEAD").strip()
    driver = tmp / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")

    test_cmd = json.dumps([PY, "-m", "unittest", "discover", "-s", "tests"])
    out, _ = rf("init", "--repo", proj, "--scope", "path:tangled.py", "--target", "untangle tangled.py",
                "--interface", "planned", "--test", test_cmd, "--env", f"REFAC_FLIP={FLIP}", f"REFAC_FAIL={FAIL_FLAG}")
    ws = run_folder(out)
    check("init: the run sits under KILLER_REFACTOR_HOME, based on HEAD, with the code in scope",
          same_path(ws.parent.parent, tmp / "runs") and f"Base: {head[:10]}" in out
          and "Code in scope: 1 file (tangled.py)" in out, out)
    out, _ = rf("verify", "-w", ws, "C1", ok=(2,))
    check("verify: refused before the tests have run on the original code", "run baseline first" in out, out)

    # ---------- baseline, and a test that flips
    out, _ = rf("baseline", "-w", ws)
    check("baseline: the tests pass 3 of 3 in a fresh copy", "passed 3 of 3 runs" in out
          and state(ws)["baseline"]["verdict"] == "clean", out)
    FLIP.write_text("0")
    out, code = rf("baseline", "-w", ws)
    FLIP.unlink()
    b = state(ws)["baseline"]
    check("baseline: a test that flips is recorded as flaky, left out, and listed under decisions",
          code == 0 and b["verdict"] == "flaky" and len(b["flaky"]) == 1 and b["flaky"][0].startswith("test_flaky")
          and any("flipped" in d["text"] for d in state(ws)["decisions"]), out)

    # ---------- recorded runs
    stamped = [PY, "tangled.py", "report", "data.csv", "--stamp"]
    out, _ = rf("golden", "add", "-w", ws, "stamped", "--", *stamped, ok=(2,))
    check("golden add: output with a timestamp is refused until masked", "differs between two runs" in out
          and "generated" in out and "stamped" not in state(ws)["golden"], out)
    out, _ = rf("golden", "add", "-w", ws, "stamped", "--mask", r"generated [0-9.]+", "--", *stamped)
    check("golden add --mask: masking the timestamp makes it repeatable", "the same in both copies" in out
          and state(ws)["golden"]["stamped"]["expected"]["stdout"].rstrip().endswith("<masked>"), out)
    out, _ = rf("golden", "add", "-w", ws, "report", "--", PY, "tangled.py", "report", "data.csv", "--width", "30",
                "--out", "summary.txt")
    rec = state(ws)["golden"]["report"]["expected"]
    check("golden add: stdout, exit code and the files a command writes are kept",
          rec["exit"] == 0 and "medium" in rec["stdout"] and "total: 244" in rec["stdout"]
          and rec["files"].get("repo/summary.txt", "").startswith("text:="), rec)
    out, _ = rf("golden", "add", "-w", ws, "driver", "--give", driver, "--", PY, "../scratch/driver.py")
    rec = state(ws)["golden"]["driver"]["expected"]
    check("golden add: a file written into the sandbox's home folder is kept too",
          rec["files"].get("home/.tangled-history", "").startswith("text:3 rows"), rec["files"])
    out, _ = rf("golden", "add", "-w", ws, "padded", "--", PY, "-c", "import os; print(f'{os.getcwd():<300}|')", ok=(2,))
    check("golden add: output that depends on where the copy is (a padded path) is refused, so it can't fail every "
          "step later", "two fresh copies" in out and "padded" not in state(ws)["golden"], out)
    check("golden add: a driver script kept in the run; the copy's folders masked in every spelling",
          "cwd <sandbox>" in rec["stdout"] and "tmp <sandbox>" in rec["stdout"]
          and (ws / "golden" / "driver" / "files" / "driver.py").is_file()
          and not (proj / "driver.py").exists(), rec)
    out, _ = rf("golden", "add", "-w", ws, "nothing", "--", "no-such-program-here", ok=(2,))
    check("golden add: a command that can't start is refused, not recorded as exit 127",
          "could not start" in out and "nothing" not in state(ws)["golden"], out)
    rf("golden", "add", "-w", ws, "usage", "--", PY, "tangled.py")
    check("golden add: an error path is a recorded run too (exit 2, stderr kept)",
          state(ws)["golden"]["usage"]["expected"]["exit"] == 2
          and "usage:" in state(ws)["golden"]["usage"]["expected"]["stderr"])

    # ---------- the interface
    out, _ = rf("surface", "record", "-w", ws)
    lines = state(ws)["surface"]["lines"]
    check("surface record: modules, functions with their signatures, classes, methods and constants",
          {"module tangled", "function tangled.parse_line(line, sep=',')", "function tangled.shorten(name, width=12)",
           "function tangled.format_report(report, counts, totals, width=40)", "class tangled.Report(rows)",
           "method tangled.Report.total(self)", "constant tangled.DEFAULT_WIDTH: int"} <= set(lines)
          and not any(".sys" in ln or ".time" in ln or "test_" in ln for ln in lines), lines)

    # ---------- the plan
    rf("plan", "-w", ws, "--add", "Move the parsing helpers into tangled_parse.py", "--allow", "add tangled_parse*")
    rf("plan", "-w", ws, "--add", "Rename format_report to render_report")
    rf("plan", "-w", ws, "--add", "Simplify classify")
    rf("plan", "-w", ws, "--add", "Simplify parse_line")
    rf("plan", "-w", ws, "--add", "Simplify summarise_names")
    out, _ = rf("plan", "-w", ws, "--add", "Tidy Report.regions")
    check("plan: numbered steps, each with the interface changes it may make",
          "S1. Move the parsing helpers" in out and "may: add tangled_parse*" in out and "S6. Tidy Report.regions" in out,
          out)
    out, _ = rf("plan", "-w", ws, "--add", "x", "--allow", "rename onlyone", ok=(2,))
    check("plan: a malformed rule is refused", "an allowed change is" in out, out)

    # ---------- pins
    out, _ = rf("pin", "survey", "-w", ws)
    weak = {r["function"] for r in state(ws)["survey"]["rows"] if r["tried"] and not r["caught"]}
    check("pin survey: faults planted function by function; the code the tests barely check is named",
          {"shorten", "classify", "main"} <= weak and "parse_number" not in weak
          and "BARELY TESTED" in out and "tangled.py:shorten" in out, out)
    # discover, not "-m unittest <file>": the Windows Python pack (embeddable) leaves the current folder
    # off sys.path, and discover adds the start folder itself
    cmd_pin = lambda f: [PY, "-m", "unittest", "discover", "-s", "tests", "-p", Path(f).name]
    agent("pin-shorten", files={"tests/test_pin_shorten.py": PIN_GOOD},
          reply={"command": cmd_pin("tests/test_pin_shorten.py"), "targets": ["tangled.py:shorten"]})
    out, _ = rf("pin", "collect", "-w", ws, "--sandbox", "pin-shorten")
    out, code = rf("pin", "check", "-w", ws, "N1", ok=(0, 1))
    check("pin check: a pin that passes 3 of 3 on the original and fails on a planted fault counts",
          code == 0 and "N1: pinned" in out and "passes 3 of 3 runs" in out and "caught" in out, out)
    agent("pin-weak", files={"tests/test_pin_weak.py": PIN_WEAK},
          reply={"command": cmd_pin("tests/test_pin_weak.py"), "targets": ["tangled.py:shorten"]})
    rf("pin", "collect", "-w", ws, "--sandbox", "pin-weak")
    out, code = rf("pin", "check", "-w", ws, "N2", ok=(0, 1))
    check("pin check: a pin that passes with every planted fault is refused",
          code == 1 and "N2: refused" in out and "still passes with every one" in out, out)
    agent("pin-wrong", files={"tests/test_pin_wrong.py": PIN_WRONG},
          reply={"command": cmd_pin("tests/test_pin_wrong.py"), "targets": ["tangled.py:shorten"]})
    rf("pin", "collect", "-w", ws, "--sandbox", "pin-wrong")
    out, code = rf("pin", "check", "-w", ws, "N3", ok=(0, 1))
    check("pin check: a pin that fails on the original code is refused", code == 1 and "fails on the original" in out,
          out)
    box, _ = agent("pin-bad", files={"tangled.py": TANGLED + "\n"})
    out, _ = rf("pin", "collect", "-w", ws, "--sandbox", "pin-bad", ok=(2,))
    check("pin collect: a pin may not change existing files", "already exist: tangled.py" in out, out)
    (box / "repo" / "tangled.py").write_bytes(TANGLED.encode())
    (box / "repo" / "helper.py").write_text("x = 1\n")
    out, _ = rf("pin", "collect", "-w", ws, "--sandbox", "pin-bad", ok=(2,))
    check("pin collect: a pin's files must be test files", "aren't test files: helper.py" in out, out)
    (box / "repo" / "helper.py").unlink()
    out, _ = rf("pin", "check", "-w", ws, "N1", "--command", json.dumps([PY, "-m", "unittest", "discover", "-s", "tests"]),
                ok=(2,))
    check("pin check: a command that doesn't name the pin's files is refused (it could count the suite's tests)",
          "doesn't name tests/test_pin_shorten.py" in out, out)

    # ---------- step S1: a behaviour-preserving split
    box, _ = agent("ref-bad")
    out, _ = rf("candidate", "add", "-w", ws, "--step", "S1", "--sandbox", "ref-bad", ok=(2,))
    check("candidate add: a copy with no changes is refused", "nothing to check" in out, out)
    (box / "repo" / "requirements.txt").write_bytes(b"requests\n")
    out, _ = rf("candidate", "add", "-w", ws, "--step", "S1", "--sandbox", "ref-bad", ok=(2,))
    check("candidate add: a step that changes dependency files is refused", "leaves alone: requirements.txt" in out, out)
    (box / "repo" / "requirements.txt").write_bytes(b"\n")
    (box / "repo" / "tangled.py").write_bytes((TANGLED + "# tidied up by Claude\n").encode())
    out, _ = rf("candidate", "add", "-w", ws, "--step", "S1", "--sandbox", "ref-bad", ok=(2,))
    check("candidate add: a step that says who made it is refused", "Claude" in out and "take it out" in out, out)
    agent("ref-S1", files={"tangled.py": SPLIT, "tangled_parse.py": PARSE_MOD},
          reply={"done": True, "title": "Move the parsing helpers into tangled_parse.py",
                 "summary": "tangled.py keeps importing them, so callers see the same names."})
    out, _ = rf("candidate", "add", "-w", ws, "--step", "S1", "--sandbox", "ref-S1")
    check("candidate add: the sandbox's diff becomes C1, titled from the reply", "C1 for S1: Move the parsing" in out
          and (ws / "candidates" / "C1.patch").is_file(), out)
    FLIP.write_text("1")   # the next test run is the counter's second: the flaky test fails there
    out, code = rf("verify", "-w", ws, "C1", ok=(0, 1))
    FLIP.unlink()
    check("verify: a behaviour-preserving split passes every check in a fresh copy",
          code == 0 and "C1: verified" in out and "recorded run report: same" in out
          and "recorded run driver: same" in out and "pin N1" in out and "passes" in out, out)
    check("verify: the new module is allowed by the plan's add rule; the flaky test is left out",
          "changed only as planned" in out and "new, as the plan allows" in out
          and "apart from 1 flaky test left out" in out, out)

    # ---------- the challenger for C1
    box, out = agent("chal-C1", "--candidate", "C1")
    check("sandbox --candidate: the challenger's copy has the step applied, uncommitted",
          (box / "repo" / "tangled_parse.py").is_file() and "tangled_parse.py" in g(box / "repo", "status", "--porcelain"),
          out)
    out, _ = rf("probe", "-w", ws, "C1", "--sandbox", "chal-C1", "--keep", ok=(2,))
    check("probe: no reply.json is an error, not a pass", "no reply at" in out, out)
    (box / "scratch" / "reply.json").write_text("Looked hard, found nothing.")
    out, _ = rf("probe", "-w", ws, "C1", "--sandbox", "chal-C1", "--keep", ok=(2,))
    check("probe: a reply with no JSON is an error", "no JSON that parses" in out, out)
    (box / "scratch" / "reply.json").write_text(json.dumps({"probes": [], "verdict": "no difference"}))
    out, _ = rf("probe", "-w", ws, "C1", "--sandbox", "chal-C1", "--keep", ok=(2,))
    check("probe: a challenge with no probes proves nothing and is refused", "no probes with a command" in out
          and cand(ws, "C1")["status"] == "verified", out)
    probes = {"p1.py": probe_script("for t in ['7', ' (3) ', '0']:\n    print(tangled.parse_number(t))\n"),
              "p2.py": probe_script("print([tangled.classify(n) for n in range(-2, 120, 7)])\n"),
              "p3.py": probe_script("print(tangled.main(['classify', '(10)']))\n"),
              "p4.py": "import tangled\nprint(tangled.classify(5))\n"}   # forgets to put the project on sys.path
    for name, text in probes.items():
        (box / "scratch" / name).write_text(text)
    (box / "scratch" / "reply.json").write_text(json.dumps({"probes": [
        {"name": n[:-3], "what": "parsing and classifying", "command": [PY, f"../scratch/{n}"]} for n in probes]}))
    out, code = rf("probe", "-w", ws, "C1", "--sandbox", "chal-C1")
    check("probe: probes run on the original and the changed code; no difference makes the step accepted",
          code == 0 and "C1: accepted" in out and out.count("same output on both") == 3
          and state(ws)["chain"] == ["C1"], out)
    check("probe: a probe that can't import the code doesn't count as finding no difference",
          "p4: it can't run on the original code" in out and "3 probes found no difference" in out, out)
    stale_box, _ = agent("ref-S3")   # made now, on top of C1 only: it goes stale when C2 is accepted

    # ---------- step S2: renaming a public function
    agent("ref-S2", files={"tangled.py": RENAMED, "tests/test_tangled.py": TESTS.replace("format_report", "render_report")},
          reply={"done": True, "title": "Rename format_report to render_report"})
    rf("candidate", "add", "-w", ws, "--step", "S2", "--sandbox", "ref-S2")
    out, code = rf("verify", "-w", ws, "C2", ok=(0, 1))
    check("verify: renaming a public function fails the interface check when the plan doesn't allow it",
          code == 1 and "rejected: test files" in cand(ws, "C2")["status"]
          and "removed `function tangled.format_report" in out and "added `function tangled.render_report" in out, out)
    rf("plan", "-w", ws, "--replace", "S2", "--allow", "rename tangled.format_report tangled.make_report")
    out, code = rf("verify", "-w", ws, "C2", ok=(0, 1))
    check("verify: a plan that allows a different rename doesn't pass it either",
          code == 1 and "added `function tangled.render_report" in out and "removed `function tangled.make_report" in out,
          out)
    rf("plan", "-w", ws, "--replace", "S2", "--allow", "rename tangled.format_report tangled.render_report")
    out, code = rf("verify", "-w", ws, "C2", ok=(0, 1))
    check("verify: exactly the planned rename passes, with the tests changed by that rename only and the driver "
          "carried through", code == 0 and "renamed tangled.format_report to tangled.render_report" in out
          and "changed only by the planned renames" in out and "recorded run driver: same" in out, out)
    box, _ = agent("chal-C2", "--candidate", "C2")
    probes = {"q1.py": probe_script("rows = [('a', 'x', 1), ('b', 'y', 200)]\nc, t = tangled.summarise(rows)\n"
                                    "print(tangled.render_report(tangled.Report(rows), c, t, width=5))\n"),
              "q2.py": probe_script("print(tangled.render_report(tangled.Report([]), {}, {}))\n"),
              "q3.py": probe_script("print(tangled.main(['report', 'data.csv', '--width', '3']))\n")}
    for name, text in probes.items():
        (box / "scratch" / name).write_text(text)
    (box / "scratch" / "reply.json").write_text(json.dumps({"probes": [
        {"name": n[:-3], "command": [PY, f"../scratch/{n}"]} for n in probes]}))
    out, code = rf("probe", "-w", ws, "C2", "--sandbox", "chal-C2")
    check("probe: probes written against the new name run on the original code under the old one",
          code == 0 and "C2: accepted" in out and out.count("same output on both") == 3
          and "render_report" not in (ws / "probes" / "C2" / "1-original" / "files" / "q1.py").read_text(), out)

    # ---------- step S3: an off-by-one the tests miss
    (stale_box / "repo" / "tangled.py").write_bytes(OFF_BY_ONE.encode())
    out, _ = rf("candidate", "add", "-w", ws, "--step", "S3", "--sandbox", "ref-S3", ok=(2,))
    check("candidate add: a copy made before the last step was accepted is refused (it's stale)",
          "the accepted steps are now C1 and C2" in out, out)
    agent("ref-S3b", files={"tangled.py": OFF_BY_ONE}, reply={"done": True, "title": "Simplify classify"})
    rf("candidate", "add", "-w", ws, "--step", "S3", "--sandbox", "ref-S3b")
    out, code = rf("verify", "-w", ws, "C3", ok=(0, 1))
    check("verify: an off-by-one in an untested branch passes the tests but is rejected by a recorded run",
          code == 1 and "tests: passed" in out and "recorded run report: DIFFERENT" in out
          and re.search(r"was 'small +2', now 'small +3'", out)
          and cand(ws, "C3")["status"] == "rejected: recorded run", out)

    # ---------- step S4: a change only a probe reaches
    agent("ref-S4", files={"tangled_parse.py": DROPS_EMPTY}, reply={"done": True, "title": "Simplify parse_line"})
    rf("candidate", "add", "-w", ws, "--step", "S4", "--sandbox", "ref-S4")
    out, code = rf("verify", "-w", ws, "C4", ok=(0, 1))
    check("verify: a change the tests, recorded runs, interface and pins don't reach passes verify", code == 0
          and "C4: verified" in out, out)
    box, _ = agent("chal-C4", "--candidate", "C4")
    probes = {"r1.py": probe_script("print(tangled.parse_line('a, b'))\n"),
              "r2.py": probe_script("print(tangled.parse_line('a,,b'))\n"),
              "r3.py": probe_script("print(tangled.parse_line('x;y', sep=';'))\n")}
    for name, text in probes.items():
        (box / "scratch" / name).write_text(text)
    (box / "scratch" / "reply.json").write_text(json.dumps({"probes": [
        {"name": n[:-3], "what": "fields", "command": [PY, f"../scratch/{n}"]} for n in probes]}))
    out, code = rf("probe", "-w", ws, "C4", "--sandbox", "chal-C4", ok=(0, 1))
    st = state(ws)
    check("probe: a step that only a probe catches is rejected, and the earlier steps aren't blamed",
          code == 1 and "r2: DIFFERENT" in out and "['a', '', 'b']" in out and "C4: rejected: behaviour change" in out
          and cand(ws, "C4")["status"] == "rejected: behaviour change" and st["chain"] == ["C1", "C2"], out)
    check("probe: the probe that showed the difference is now a recorded run, checked from now on",
          "probe-C4-1-2" in st["golden"] and st["golden"]["probe-C4-1-2"]["kind"] == "probe"
          and "['a', '', 'b']" in st["golden"]["probe-C4-1-2"]["expected"]["stdout"], list(st["golden"]))
    out, _ = rf("verify", "-w", ws, "C4", ok=(2,))
    check("verify: a probe's proof of a behaviour change can't be verified away", "that stands" in out, out)

    # ---------- a change that slips past its own challenger is caught later, and blamed on the right step
    agent("ref-S5", files={"tangled.py": RENAMED.replace("sorted(shorten(name) for _, name, _ in rows)",
                                                         "sorted({shorten(name) for _, name, _ in rows})")},
          reply={"done": True, "title": "Simplify summarise_names"})
    rf("candidate", "add", "-w", ws, "--step", "S5", "--sandbox", "ref-S5")
    rf("verify", "-w", ws, "C5")
    box, _ = agent("chal-C5", "--candidate", "C5")
    probes = {"s1.py": probe_script("print(tangled.summarise_names([('a', 'x', 1), ('b', 'y', 2)]))\n"),
              "s2.py": probe_script("print(tangled.summarise_names([]))\n"),
              "s3.py": probe_script("print(tangled.summarise_names([('a', 'a-name-longer-than-twelve', 1)]))\n")}
    for name, text in probes.items():
        (box / "scratch" / name).write_text(text)
    (box / "scratch" / "reply.json").write_text(json.dumps({"probes": [
        {"name": n[:-3], "command": [PY, f"../scratch/{n}"]} for n in probes]}))
    rf("probe", "-w", ws, "C5", "--sandbox", "chal-C5")
    check("probe: a challenger that misses a change lets the step in", state(ws)["chain"] == ["C1", "C2", "C5"])
    out, _ = rf("golden", "run", "-w", ws, "--chain")
    check("golden run --chain: every recorded run, rerun with the accepted steps", "Rerunning 5 recorded runs with "
          "C1, C2 and C5" in out and out.count(": same") == 5, out)
    agent("ref-S6", files={"tangled.py": RENAMED.replace("sorted(shorten(name) for _, name, _ in rows)",
                                                         "sorted({shorten(name) for _, name, _ in rows})").replace(
        "sorted({region for region, _, _ in self.rows})", "sorted({row[0] for row in self.rows})")},
          reply={"done": True, "title": "Tidy Report.regions", "summary": "Index the row instead of unpacking it."})
    rf("candidate", "add", "-w", ws, "--step", "S6", "--sandbox", "ref-S6")
    rf("verify", "-w", ws, "C6")
    out, _ = rf("candidate", "show", "-w", ws, "C6")
    check("candidate show: the step and its diff for the challenger, without the refactorer's account",
          "S6. Tidy Report.regions" in out and "```diff" in out and "row[0]" in out and "Index the row" not in out, out)
    box, _ = agent("chal-C6", "--candidate", "C6")
    probes = {"t1.py": probe_script("print(tangled.Report([('b', 'x', 1), ('a', 'y', 2)]).regions())\n"),
              "t2.py": probe_script("print(tangled.summarise_names([('a', 'bob', 1), ('b', 'bob', 2)]))\n"),
              "t3.py": probe_script("print(tangled.Report([]).regions())\n")}
    for name, text in probes.items():
        (box / "scratch" / name).write_text(text)
    (box / "scratch" / "reply.json").write_text(json.dumps({"probes": [
        {"name": n[:-3], "command": [PY, f"../scratch/{n}"]} for n in probes]}))
    out, code = rf("probe", "-w", ws, "C6", "--sandbox", "chal-C6", ok=(0, 1))
    check("probe: a difference an earlier accepted step brought in is blamed on that step; later ones go stale",
          code == 1 and "t2: DIFFERENT" in out and "C5: rejected: behaviour change" in out
          and "comes from C5, accepted earlier, not from C6" in out and state(ws)["chain"] == ["C1", "C2"]
          and cand(ws, "C5")["status"] == "rejected: behaviour change" and cand(ws, "C6")["status"] == "stale", out)

    # ---------- metrics
    out, _ = rf("metrics", "-w", ws)
    m = state(ws)["metrics"]
    after_lines = sum(1 for t in (SPLIT.replace("format_report", "render_report"), PARSE_MOD)
                      for ln in t.split("\n") if ln.strip())
    check("metrics: before and after from the code, the new module counted after",
          m["before"]["files"] == 1 and m["after"]["files"] == 2 and m["after"]["lines"] == after_lines
          and m["before"]["long_functions"] == 1 and m["after"]["duplicated_blocks"] == 2
          and m["before"]["complexity_max"] == 24 and "functions over 50 lines" in out, (m, out))

    # ---------- land
    rf("decide", "-w", ws, "Kept summarise_names although nothing calls it")
    rf("wait", "-w", ws, "Push the branch", "--command", "git push -u origin killer-refactor/x")
    before_status = g(proj, "status", "--porcelain")
    hook_flag = tmp / "hook.flag"
    hook = proj / ".git" / "hooks" / "commit-msg"
    hook.write_bytes(f'#!/bin/sh\nif [ -f "{hook_flag.as_posix()}" ]; then echo "hook says no"; exit 1; fi\nexit 0\n'
                     .encode())
    hook.chmod(0o755)
    hook_flag.write_text("no")
    out, code = rf("land", "-w", ws, "--branch", "main", ok=(1, 2))
    hook_flag.unlink()
    check("land: a commit hook that refuses leaves the checked-out branch and its files exactly as they were",
          "hook says no" in out and g(proj, "rev-parse", "HEAD").strip() == head
          and (proj / "tangled.py").read_bytes() == TANGLED.encode() and not (proj / "tangled_parse.py").exists()
          and g(proj, "status", "--porcelain") == before_status
          and len(g(proj, "worktree", "list").strip().splitlines()) == 1, out)
    FAIL_FLAG.write_text("fail")
    out, code = rf("land", "-w", ws, "--branch", "main", ok=(0, 1))
    FAIL_FLAG.unlink()
    check("land --branch <the current branch>: commits on it, and a failed final check takes them back",
          code == 1 and "Committed on main" in out and "commits were undone" in out
          and g(proj, "rev-parse", "HEAD").strip() == head and (proj / "tangled.py").read_bytes() == TANGLED.encode()
          and g(proj, "status", "--porcelain") == before_status, out)
    out, code = rf("land", "-w", ws, ok=(0, 1))
    branch = re.search(r"Committed on (\S+):", out).group(1) if "Committed on" in out else ""
    log = g(proj, "log", "--format=%H%n%B%n--end--", f"{head}..{branch}") if branch else ""
    subjects = g(proj, "log", "--format=%s", f"{head}..{branch}").splitlines() if branch else []
    check("land: a new killer-refactor/<date> branch: the pins, then one commit per accepted step",
          code == 0 and branch.startswith("killer-refactor/") and subjects == [
              "Rename format_report to render_report", "Move the parsing helpers into tangled_parse.py",
              "Add tests that pin the current behaviour of shorten in tangled.py"], (out, subjects))
    check("land: commit messages say what was checked, with no attribution",
          "Checked in a fresh copy: the tests pass" in log and "renamed tangled.format_report to tangled.render_report" in log
          and not re.search(r"(?i)claude|co-authored|generated with|anthropic|\bagent\b", log), log)
    check("land: the pins are in the project's tests, and the final check of the branch passed",
          "tests/test_pin_shorten.py" in g(proj, "ls-tree", "-r", "--name-only", branch)
          and "every check held on the branch" in out and "recorded run probe-C4-1-2: same" in out, out)
    check("land: the user's branch, working tree and worktrees are untouched",
          g(proj, "branch", "--show-current").strip() == "main" and g(proj, "rev-parse", "HEAD").strip() == head
          and g(proj, "status", "--porcelain") == before_status
          and len(g(proj, "worktree", "list").strip().splitlines()) == 1, out)
    out, _ = rf("status", "-w", ws)
    check("status: the run's steps and the real project as it was at init",
          "as it was at init" in out and "Accepted, in order: C1 and C2" in out, out)

    # ---------- report and summary
    out, _ = rf("report", "-w", ws)
    report = (ws / "report.md").read_text(encoding="utf-8")
    summary = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
    n_agents = len(["pin-shorten", "pin-weak", "pin-wrong", "pin-bad", "ref-bad", "ref-S1", "chal-C1", "ref-S3",
                    "ref-S2", "chal-C2", "ref-S3b", "ref-S4", "chal-C4", "ref-S5", "chal-C5", "ref-S6", "chal-C6"])
    check("report: the plan, what landed, what was checked, steps, rejections, metrics, decisions, waiting",
          report.index("## The plan") < report.index("## Landed") < report.index("## What was checked")
          < report.index("## Accepted steps") < report.index("## Rejected, and why") < report.index("## Before and after")
          < report.index("## Decisions to check") < report.index("## Waiting for your OK")
          and "rejected: recorded run" in report and "rejected: behaviour change" in report
          and "test_flaky" in report and "N2" in report and "refused" in report, report)
    check("summary.json: every field killer-goal reads, numbers from the script, agents counted",
          set(summary) == {"skill", "status", "headline", "report", "branch", "commits", "decisions", "waiting", "numbers"}
          and summary["skill"] == "killer-refactor" and summary["status"] == "partial" and summary["branch"] == branch
          and len(summary["commits"]) == 3 and summary["numbers"]["agents"] == n_agents
          and summary["numbers"]["accepted"] == 2 and summary["numbers"]["rejected"] == 3
          and summary["numbers"]["pins"] == 1 and summary["numbers"]["pins_refused"] == 2
          and summary["numbers"]["differences"] == 2 and summary["numbers"]["long_functions_before"] == 1
          and "Push the branch: git push -u origin killer-refactor/x" in summary["waiting"]
          and "Kept summarise_names although nothing calls it" in summary["decisions"]
          and same_path(summary["report"], ws / "report.md") and "2 of 6 planned steps" in summary["headline"]
          and out.strip().splitlines()[-1].startswith("killer-refactor: partial · 2 of 6"), (summary, out))

    # ---------- leaving the steps uncommitted instead (the "Leave them uncommitted" answer)
    out, _ = rf("land", "-w", ws, "--uncommitted")
    changed = g(proj, "status", "--porcelain")
    check("land --uncommitted: checked together in a fresh copy first, then applied to the working tree, uncommitted",
          "Checking the steps together" in out and "tests: passed" in out and "uncommitted. Nothing was committed" in out
          and g(proj, "rev-parse", "HEAD").strip() == head and (proj / "tangled_parse.py").is_file()
          and "render_report" in (proj / "tangled.py").read_text() and (proj / "tests" / "test_pin_shorten.py").is_file()
          and " M tangled.py" in changed, (out, changed))
    g(proj, "checkout", "--", ".")
    (proj / "tangled_parse.py").unlink()
    (proj / "tests" / "test_pin_shorten.py").unlink()
    rf("clean", "-w", ws)
    left = [p for p in (tmp / "boxes").rglob("*") if p.is_dir() and p.parent.parent == tmp / "boxes"]
    check("clean: no sandboxes left behind", not left, left)

    # ---------- a run where the interface must stay the same, and it's listed by a command
    out, _ = rf("init", "--repo", proj, "--scope", "path:tangled.py", "--slug", "same", "--test", test_cmd)
    ws2 = run_folder(out)
    out, _ = rf("plan", "-w", ws2, "--add", "Rename it", "--allow", "rename tangled.shorten tangled.trim", ok=(2,))
    check("plan: with the interface set to stay the same, a rename is refused", "may only add new names" in out, out)
    lister = tmp / "lister.py"
    lister.write_text(HEAD + "import inspect\n\nimport tangled\n\nfor n in sorted(dir(tangled)):\n"
                      "    if inspect.isfunction(getattr(tangled, n)):\n"
                      "        print(n + str(inspect.signature(getattr(tangled, n))))\n", encoding="utf-8")
    out, _ = rf("surface", "record", "-w", ws2, "--command", json.dumps([PY, "../scratch/lister.py"]), "--give", lister)
    out2, code = rf("surface", "compare", "-w", ws2)
    check("surface record --command: another language's listing, one entry a line, compared the same way",
          "shorten(name, width=12)" in out and code == 0 and "unchanged" in out2
          and R.compare_surface("command", ["shorten(name)"], ["trim(name)"], [["rename", "shorten", "trim"]], "planned")["ok"]
          and not R.compare_surface("command", ["shorten(name)"], ["trim(name)"], [], "planned")["ok"], (out, out2))
    (proj / "stray.txt").write_text("left by someone")
    out, _ = rf("status", "-w", ws2, ok=(1,))
    check("status: a change to the real project is caught", "stray.txt" in out, out)
    (proj / "stray.txt").unlink()
finally:
    if os.environ.get("REFACTOR_TEST_KEEP"):
        print(f"kept {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"took {time.monotonic() - T0:.0f} s")
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
