"""Scenario tests for killer-perf's script (perf.py). Run: python -I tests/test_perf.py

The timed checks use effects far bigger than a busy machine's noise. The slow version sleeps
12 x 30 ms between readiness checks and dedupes with list membership; the fast one sleeps 1 ms
and uses a set. The script prints the time its work took as a METRIC line, so the decision
leaves out Python's start-up, which is what other load on a machine disturbs most; the work
itself is about 20 times faster. Wall-time measurement is checked separately, without any
assertion about how noisy this machine is. The statistics are checked against values worked
out by hand.
"""
import importlib.util
import json
import os
import random
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
PERF = ROOT / "skills" / "killer-perf" / "scripts" / "perf.py"
PY = sys.executable
failures = []
T0 = time.monotonic()


def check(name, cond, detail=""):
    when = f"  [{time.monotonic() - T0:.0f} s]" if os.environ.get("PERF_TEST_TIMES") else ""
    print(("PASS " if cond else "FAIL ") + name + when, flush=True)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


tmp = Path(tempfile.mkdtemp())
ORDER_LOG = tmp / "order.log"
FAIL_FLAG = tmp / "fail.flag"
ENV = dict(os.environ, KILLER_PERF_HOME=str(tmp / "runs"), KILLER_PERF_SANDBOXES=str(tmp / "boxes"),
           GH_TOKEN="test-token-value", PYTHONIOENCODING="utf-8")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    ENV.pop(k, None)


def perf(*args, ok=(0,)):
    r = subprocess.run([PY, str(PERF), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    out = r.stdout + r.stderr
    if r.returncode not in ok:
        raise AssertionError(f"exit {r.returncode} for perf.py {' '.join(map(str, args))}:\n{out}")
    return out, r.returncode


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def run_folder(out):
    return Path(re.search(r"Run folder: (.+)", out).group(1).strip())


def box_path(out):
    return Path(re.search(r"project copy: (.+?)(?: \(at .*\))?$", out, re.M).group(1).strip()).parent


def state(ws):
    return json.loads((ws / "perf.json").read_text(encoding="utf-8"))


# A project with a slow script. The embeddable Python leaves the script's folder off sys.path,
# so each script puts it back.
HEAD = "import os, sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
NAMES = (
    "import time\n\n\n"
    "def dedupe(items):\n"
    "    out = []\n"
    "    for x in items:\n"
    "        if x not in out:\n"
    "            out.append(x)\n"
    "    return out\n\n\n"
    "def wait_ready(checks=12):\n"
    "    done = 0\n"
    "    for _ in range(checks):\n"
    "        time.sleep(0.03)\n"
    "        done += 1\n"
    "    return done\n")
SLOW = HEAD + (
    "import time\nfrom pathlib import Path\nfrom names import dedupe, wait_ready\n\n"
    "if not Path('built.txt').exists():\n    print('not built')\n    sys.exit(3)\n"
    "log = os.environ.get('PERF_ORDER_LOG')\n"
    "if log:\n    with open(log, 'a') as f:\n        f.write(os.getcwd() + '\\n')\n"
    "t0 = time.perf_counter()\n"
    "unique = dedupe([f'user{i % 300}' for i in range(600)])\n"
    "checks = wait_ready()\n"
    "print(f'{len(unique)} unique names, first {unique[0]}, last {unique[-1]}')\n"
    "print(f'ready after {checks} checks')\n"
    "print(f'done in {(time.perf_counter() - t0) * 1000:.1f} ms')\n"
    "print(f'METRIC: work={(time.perf_counter() - t0) * 1000:.3f} ms')\n")
TEST = HEAD + (
    "flag = os.environ.get('PERF_FAIL_FLAG')\n"
    "if flag and os.path.exists(flag):\n    sys.exit('failing on purpose: the flag file is there')\n"
    "from names import dedupe, wait_ready\n"
    "got = dedupe(['b', 'a', 'b', 'c', 'a'])\n"
    "assert got == ['b', 'a', 'c'], got\n"
    "assert wait_ready(checks=2) == 2\n"
    "print('ok')\n")
FAST_NAMES = NAMES.replace(
    "    out = []\n    for x in items:\n        if x not in out:\n            out.append(x)\n    return out\n",
    "    seen, out = set(), []\n    for x in items:\n        if x not in seen:\n            seen.add(x)\n"
    "            out.append(x)\n    return out\n").replace("time.sleep(0.03)", "time.sleep(0.001)")
REPEAT = 8
BUSY_JS = ("function busy(n) { let s = 0; for (let i = 0; i < n; i++) s += Math.sqrt(i); return s; }\n"
           "console.log(busy(3e7) > 0 ? 'ok' : 'no');\n")
SETUP = json.dumps([PY, "-c", "open('built.txt', 'w').write('ok')"])

sys.dont_write_bytecode = True   # importing perf.py here mustn't leave __pycache__ in the skill
spec = importlib.util.spec_from_file_location("perf_under_test", PERF)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

try:
    # ---------- the statistics, against values worked out by hand
    check("median: odd and even counts", P.median([3, 1, 2]) == 2 and P.median([4, 1, 3, 2]) == 2.5)
    u, p = P.mann_whitney([1, 2, 3], [4, 5, 6])
    # U = 6 - 3*4/2 = 0; mean 4.5; var 3*3/12*7 = 5.25; z = (4.5 - 0.5)/sqrt(5.25) = 1.7457; p = 0.0809
    check("Mann-Whitney: complete separation, 3 against 3", u == 0 and abs(p - 0.0809) < 5e-4, (u, p))
    u, p = P.mann_whitney([1, 2, 2, 3], [2, 3, 4, 5])
    # ranks 1, 3, 3, 3, 5.5, 5.5, 7, 8; R1 = 12.5, U = 2.5; ties 3 and 2: sum(t^3 - t) = 30
    # var = 16/12 * (9 - 30/56) = 11.2857; z = (5.5 - 0.5)/3.3594 = 1.4884; p = 0.1367
    check("Mann-Whitney: tie correction", u == 2.5 and abs(p - 0.1367) < 5e-4, (u, p))
    check("Mann-Whitney: identical samples give p = 1", P.mann_whitney([5, 5, 5], [5, 5, 5])[1] == 1.0)
    u, p = P.mann_whitney([10.0 + i / 100 for i in range(6)], [1.0 + i / 100 for i in range(6)])
    check("Mann-Whitney: 6 against 6 fully separated reaches p < 0.01 (5 can't)",
          p < 0.01 and P.mann_whitney(list(range(10, 15)), list(range(5)))[1] > 0.01, p)
    check("bootstrap: constant samples give an exact interval", P.bootstrap_ratio_ci([10] * 7, [5] * 7) == (0.5, 0.5))
    # cand [1, 1, 3]: a resample's median is 1 with probability 20/27 and 3 otherwise; base is always 2.
    # So every ratio is 0.5 or 1.5, and the 2.5% and 97.5% points are 0.5 and 1.5.
    check("bootstrap: a hand-worked two-valued case", P.bootstrap_ratio_ci([2, 2, 2], [1, 1, 3]) == (0.5, 1.5),
          P.bootstrap_ratio_ci([2, 2, 2], [1, 1, 3]))
    rng = random.Random(7)
    a = [1 + rng.random() for _ in range(15)]
    b = [0.8 + rng.random() for _ in range(15)]

    def ref_ci(a, b, n=2000, seed=P.SEED):   # an independent restatement of the method
        r = random.Random(seed)
        med = lambda s: (sorted(s)[len(s) // 2] + sorted(s)[(len(s) - 1) // 2]) / 2
        rs = sorted(med(r.choices(b, k=len(b))) / med(r.choices(a, k=len(a))) for _ in range(n))
        return rs[round(0.025 * (n - 1))], rs[round(0.975 * (n - 1))]
    ci = P.bootstrap_ratio_ci(a, b)
    check("bootstrap: seeded, repeatable, matches a restatement, and holds the point estimate",
          ci == P.bootstrap_ratio_ci(a, b) and ci != P.bootstrap_ratio_ci(a, b, seed=1)
          and all(abs(x - y) < 1e-12 for x, y in zip(ci, ref_ci(a, b)))
          and ci[0] <= P.median(b) / P.median(a) <= ci[1], ci)
    check("ABBA order", "".join(P.schedule(4)) == "ABBAABBA" and len(P.schedule(15)) == 30)
    dv = P.decide_verdict
    check("verdict: the whole interval past the threshold, and p < 0.01",
          dv(0.70, 0.75, 0.001, 0.03, False) == "faster" and dv(0.70, 0.98, 0.001, 0.03, False) == "no clear change"
          and dv(0.70, 0.75, 0.02, 0.03, False) == "no clear change" and dv(1.10, 1.20, 0.001, 0.03, False) == "slower"
          and dv(1.10, 1.20, 0.001, 0.03, True) == "faster" and dv(0.97, 1.02, 0.5, 0.03, False) == "no clear change")
    check("noise band: the furthest reach from 1", abs(P.noise_band(0.97, 1.02) - 0.03) < 1e-12
          and abs(P.noise_band(0.99, 1.05) - 0.05) < 1e-12)
    norm = P.normalise("done in 512.3 ms\r\nMETRIC: x=3 ms\r\n12 items took 2.5s, id=ab12  \n", [r"id=\w+"])
    check("output check: timings, METRIC lines and masks are ignored; CRLF folds",
          norm == "done in <time>\n12 items took <time>, <masked>", repr(norm))
    check("METRIC lines parse", P.parse_metrics("METRIC: peak_mb=12.5 MB\nMETRIC: ops=3e3\n")
          == [("peak_mb", 12.5, "MB"), ("ops", 3000.0, None)])
    check("protected files: tests, benchmarks, dependency files; not the code",
          P.protected_hit("pkg/tests/x.py", P.DEFAULT_PROTECT) and P.protected_hit("test_names.py", P.DEFAULT_PROTECT)
          and P.protected_hit("package.json", P.DEFAULT_PROTECT) and not P.protected_hit("names.py", P.DEFAULT_PROTECT)
          and not P.protected_hit("src/latest.py", P.DEFAULT_PROTECT))
    check("profile: cProfile goes after the interpreter's own options",
          P.python_profile_cmd(["py", "-3.12", "-X", "utf8", "s.py", "a"], Path("o"))
          == ["py", "-3.12", "-X", "utf8", "-m", "cProfile", "-o", "o", "s.py", "a"]
          and P.python_profile_cmd(["python", "-c", "x"], Path("o")) is None)

    # ---------- the project
    proj = tmp / "proj"
    proj.mkdir()
    g(proj, "init", "-q", "-b", "main")
    g(proj, "config", "user.name", "Test Person")
    g(proj, "config", "user.email", "test@example.com")
    g(proj, "config", "core.autocrlf", "false")
    for name, text in (("names.py", NAMES), ("slow.py", SLOW), ("test_names.py", TEST), ("busy.js", BUSY_JS),
                       (".gitignore", "built.txt\n__pycache__/\n")):
        (proj / name).write_bytes(text.encode())
    g(proj, "add", "-A")
    g(proj, "commit", "-q", "-m", "first")
    head = g(proj, "rev-parse", "HEAD").strip()

    out, _ = perf("init", "--repo", proj, "--target", "the names script", "--rigor", "quick",
                  "--env", f"PERF_ORDER_LOG={ORDER_LOG}", f"PERF_FAIL_FLAG={FAIL_FLAG}")
    ws = run_folder(out)
    check("init: the run sits under KILLER_PERF_HOME, based on HEAD",
          ws.parent.parent == tmp / "runs" and f"Base: {head[:10]}" in out and "7 runs each" in out, out)
    out, _ = perf("compare", "-w", ws, "--candidate", "C1", ok=(2,))
    check("compare: refuses before there's a benchmark", "no benchmarks yet" in out, out)
    out, _ = perf("bench", "add", "-w", ws, "main", "--repeat", REPEAT, "--setup", SETUP, "--", PY, "slow.py")
    check("bench add: tried twice in a fresh copy, with the setup run first, and timed; its METRIC decides",
          "One run takes about" in out and "Output check: on" in out and "deciding on METRIC work" in out, out)
    out, _ = perf("compare", "-w", ws, "--candidate", "C1", ok=(2,))
    check("compare: refuses without a test command or --none", "set the test command first" in out, out)
    out, code = perf("tests", "-w", ws, "--", PY, "test_names.py")
    check("tests: run once on the unchanged project", "passed in" in out, out)
    out, _ = perf("compare", "-w", ws, "--candidate", "C1", ok=(2,))
    check("compare: refuses before the noise test", "no noise test yet" in out, out)
    out, _ = perf("bench", "add", "-w", ws, "broken", "--", PY, "-c", "import sys; sys.exit(4)", ok=(2,))
    check("bench add: a command that fails on the unchanged project is refused", "fails on the unchanged project" in out
          and "broken" not in state(ws)["benchmarks"], out)
    out, _ = perf("bench", "add", "-w", ws, "few", "--repeat", "5", "--", PY, "slow.py", ok=(2,))
    check("bench add: fewer than 6 runs can't reach p < 0.01, so they're refused", "at least 6" in out, out)

    # ---------- the A/A noise test, and the interleaving
    ORDER_LOG.unlink(missing_ok=True)
    out, code = perf("bench", "noise", "-w", ws, "main", ok=(0, 1))
    if code == 1 and "can't be trusted" in out:
        # A busy machine: do what the skill tells a lead to do, once, with more runs.
        print("note: the machine is busy; running the noise test again with more runs, as the skill would")
        REPEAT = 20
        ORDER_LOG.unlink(missing_ok=True)
        out, code = perf("bench", "noise", "-w", ws, "main", "--repeat", REPEAT, ok=(0, 1))
    noise = state(ws)["benchmarks"]["main"].get("noise") or {}
    check("bench noise: the unchanged project against itself gives a small, clean band",
          code == 0 and noise.get("state") == "clean" and noise["band"] < 0.10 and "Noise band ±" in out, out)
    seen = {}
    for ln in ORDER_LOG.read_text().splitlines():
        if ln and ln not in seen:
            seen[ln] = "ABCDEFGH"[len(seen)]
    letters = "".join(seen[ln] for ln in ORDER_LOG.read_text().splitlines() if ln)
    check("bench noise: two copies, run A B B A A B ... after one warm-up round",
          letters == "".join(P.schedule(1 + REPEAT)) and letters.startswith("ABBAABBA"), letters)

    # ---------- candidates from optimisers' sandboxes
    def optimiser(name, edits, reply=None):
        o, _ = perf("sandbox", "-w", ws, name)
        box = box_path(o)
        for f, text in edits.items():
            (box / "repo" / f).write_bytes(text.encode())
        if reply is not None:
            (box / "scratch" / "reply.json").write_text(json.dumps(reply))
        return box

    optimiser("opt-fast", {"names.py": FAST_NAMES},
              {"done": True, "title": "Use a set for the duplicate check and poll every millisecond",
               "mechanism": "Membership tests on a list made dedupe quadratic; the poll slept 30 ms between checks."})
    out, _ = perf("candidate", "add", "-w", ws, "--sandbox", "opt-fast")
    check("candidate add: the sandbox's diff becomes C1, titled from its reply, and the sandbox goes",
          "C1: Use a set" in out and (ws / "candidates" / "C1.patch").is_file()
          and "opt-fast" not in json.loads((ws / "config.json").read_text())["sandboxes"], out)
    optimiser("opt-output", {"slow.py": SLOW.replace("wait_ready()", "wait_ready(checks=1)")})
    perf("candidate", "add", "-w", ws, "--sandbox", "opt-output", "--title", "Check readiness once")
    optimiser("opt-break", {"names.py": NAMES.replace(
        "    out = []\n    for x in items:\n        if x not in out:\n            out.append(x)\n    return out\n",
        "    return sorted(set(items))\n")})
    perf("candidate", "add", "-w", ws, "--sandbox", "opt-break", "--title", "Dedupe with a set")
    # Far enough from C1's lines that the two patches stack.
    optimiser("opt-null", {"names.py": '"""Helpers for the names script."""\n' + NAMES})
    perf("candidate", "add", "-w", ws, "--sandbox", "opt-null", "--title", "Describe the module")
    # One copy for every refusal: a refused change leaves its sandbox where it was.
    bad = optimiser("opt-bad", {})
    out, _ = perf("candidate", "add", "-w", ws, "--sandbox", "opt-bad", ok=(2,))
    check("candidate add: a copy with no changes is refused", "nothing to measure" in out, out)
    (bad / "repo" / "test_names.py").write_bytes((TEST + "print('more')\n").encode())
    out, _ = perf("candidate", "add", "-w", ws, "--sandbox", "opt-bad", ok=(2,))
    check("candidate add: a change to the tests is refused", "files candidates must leave alone: test_names.py" in out, out)
    (bad / "repo" / "test_names.py").write_bytes(TEST.encode())
    (bad / "repo" / "names.py").write_bytes((NAMES + "# sped up by Claude\n").encode())
    out, _ = perf("candidate", "add", "-w", ws, "--sandbox", "opt-bad", ok=(2,))
    check("candidate add: a change that says who made it is refused", "Claude" in out and "take it out" in out, out)
    (bad / "repo" / "names.py").write_bytes((NAMES + "\n").encode())
    (bad / "scratch" / "reply.json").write_text(json.dumps({"done": False, "reason": "it would change the order"}))
    out, _ = perf("candidate", "add", "-w", ws, "--sandbox", "opt-bad", ok=(2,))
    check("candidate add: an optimiser that says it isn't done is believed", "no safe change" in out
          and len(state(ws)["candidates"]) == 4, out)

    # ---------- compare: the core check
    out, code = perf("compare", "-w", ws, "--candidate", "C1", ok=(0, 1))
    c1 = state(ws)["candidates"][0]
    check("compare: the real speedup is faster, with medians, interval, p, runs and noise in one line",
          code == 0 and c1["verdict"] == "faster" and c1["status"] == "faster"
          and re.search(r"main: [\d.]+ ms → [\d.]+ ms, \d+% faster \(95% CI \d+–\d+%\), p [<=] [\d.]+, \d+ runs each; "
                        r"noise band ±[\d.]+%\.", out), out)
    check("compare: the gate ran first: tests passed, output unchanged", "Tests: passed" in out
          and c1["results"]["main"]["output"] == "same" and c1["gate"]["tests"]["passed"], out)
    out, code = perf("compare", "-w", ws, "--candidate", "C4", ok=(0, 1))
    c4 = state(ws)["candidates"][3]
    check("compare: a change that does nothing for speed is no clear change", code == 1
          and c4["verdict"] == "no clear change" and c4["status"] == "no gain" and "no clear change (" in out, out)
    out, code = perf("compare", "-w", ws, "--candidate", "C2", ok=(0, 1))
    c2 = state(ws)["candidates"][1]
    check("compare: faster but the output changed is rejected, and says where",
          code == 1 and c2["status"] == "rejected: output changed" and "ready after 12 checks" in out
          and "ready after 1 checks" in out, out)
    out, code = perf("compare", "-w", ws, "--candidate", "C3", ok=(0, 1))
    c3 = state(ws)["candidates"][2]
    check("compare: faster but breaking a test is rejected before any timing",
          code == 1 and c3["status"] == "rejected: tests" and "AssertionError" in out and "runs each" not in out, out)
    bad_patch = tmp / "bad.patch"
    bad_patch.write_text("diff --git a/names.py b/names.py\n--- a/names.py\n+++ b/names.py\n@@ -1,1 +1,1 @@\n"
                         "-import nothing_like_this\n+import os\n")
    out, code = perf("compare", "-w", ws, "--candidate", bad_patch, ok=(0, 1))
    check("compare: a patch file is saved as a candidate; one that doesn't apply is rejected",
          "Saved bad.patch as C5" in out and state(ws)["candidates"][4]["status"] == "rejected: doesn't apply", out)

    # ---------- challenge
    out, _ = perf("sandbox", "-w", ws, "chal-C1", "--candidate", "C1")
    cbox = box_path(out)
    check("sandbox --candidate: the copy has the change applied, uncommitted",
          "seen, out = set(), []" in (cbox / "repo" / "names.py").read_text() and "names.py" in g(cbox / "repo", "diff", "--name-only"),
          out)
    out, _ = perf("challenge", "-w", ws, "C1", "--sandbox", "chal-C1", ok=(2,))
    check("challenge: no reply.json is an error, not a pass", "no reply at" in out, out)
    (cbox / "scratch" / "reply.json").write_text("I think it's fine, honestly.")
    out, _ = perf("challenge", "-w", ws, "C1", "--sandbox", "chal-C1", ok=(2,))
    check("challenge: a reply with no JSON is an error", "no JSON that parses" in out, out)
    (cbox / "scratch" / "reply.json").write_text(json.dumps({"verdict": "probably fine"}))
    out, _ = perf("challenge", "-w", ws, "C1", "--sandbox", "chal-C1", ok=(2,))
    check("challenge: an unknown verdict is refused", "stands, question or breaks" in out, out)
    (cbox / "scratch" / "reply.json").write_text(json.dumps({"verdict": "stands", "concerns": []}))
    out, _ = perf("challenge", "-w", ws, "C1", "--sandbox", "chal-C1")
    check("challenge: stands makes a faster candidate accepted", "C1: accepted" in out
          and state(ws)["candidates"][0]["status"] == "accepted", out)
    out, _ = perf("challenge", "-w", ws, "C2", tmp / "x.json", ok=(2,))
    check("challenge: only a measured gain gets challenged", "only a measured gain" in out, out)

    # ---------- combine
    out, code = perf("combine", "-w", ws, "C1", "C4", ok=(0, 1))
    k1 = next((c for c in state(ws)["candidates"] if c["id"] == "K1"), {})
    check("combine: C1 + C4 apply in order and measure faster on every benchmark",
          code == 0 and k1.get("verdict") == "faster" and k1.get("members") == ["C1", "C4"]
          and "note: C4 is 'no gain'" in out and "main:" in out, out)
    shows_both = tmp / "both.json"
    shows_both.write_text(json.dumps({"verdict": "breaks", "concerns": [{"what": "order may differ"}],
                                      "command": [PY, "-c", "print('EXPECTED: a'); print('OBSERVED: b'); exit(1)"]}))
    out, _ = perf("challenge", "-w", ws, "K1", shows_both)
    check("challenge: a reproduction that also fails without the change proves nothing; the concern is a question",
          "Without it: shows" in out and "K1: question" in out, out)
    out, _ = perf("sandbox", "-w", ws, "chal-K1", "--candidate", "K1")
    kbox = box_path(out)
    (kbox / "scratch" / "repro.py").write_text(
        "import pathlib, sys\nsrc = pathlib.Path('names.py').read_text()\nchanged = '0.03' not in src\n"
        "print('EXPECTED: a 30 ms pause between checks')\nprint('OBSERVED:', 'a shorter pause' if changed else 'the same')\n"
        "sys.exit(1 if changed else 0)\n")
    (kbox / "scratch" / "reply.json").write_text(json.dumps(
        {"verdict": "stands", "command": [PY, "../scratch/repro.py"], "expected": "30 ms", "observed": "1 ms"}))
    out, _ = perf("challenge", "-w", ws, "K1", "--sandbox", "chal-K1")
    check("challenge: a reproduction that shows with the change and not without proves it, whatever the verdict says",
          "K1: rejected: behaviour change" in out and (ws / "challenges" / "K1" / "repro.py").exists(), out)
    (tmp / "stands.json").write_text(json.dumps({"verdict": "stands"}))
    out, _ = perf("challenge", "-w", ws, "K1", tmp / "stands.json")
    check("challenge: a later 'stands' doesn't undo a proven behaviour change", "still stands" in out
          and next(c for c in state(ws)["candidates"] if c["id"] == "K1")["status"] == "rejected: behaviour change", out)

    # ---------- land
    out, _ = perf("land", "-w", ws, "C4", ok=(2,))
    check("land: refuses a candidate that isn't accepted", "C4 is 'no gain', not accepted" in out, out)
    out, _ = perf("candidate", "set", "-w", ws, "C4", "--status", "accepted", ok=(2,))
    check("candidate set: a change with no measured gain can't be accepted", "only a measured gain can" in out, out)
    perf("decide", "-w", ws, "Treated the poll interval as free to change")
    perf("wait", "-w", ws, "Push the branch", "--command", "git push -u origin killer-perf/x")
    before_status = g(proj, "status", "--porcelain")
    out, code = perf("land", "-w", ws, "C1", ok=(0, 1))
    branch = re.search(r"Committed C1 on (\S+):", out).group(1) if "Committed C1 on" in out else ""
    log = g(proj, "log", "-1", "--format=%B", branch) if branch else ""
    check("land: a new killer-perf/<date> branch from the base, one commit per candidate",
          branch.startswith("killer-perf/") and g(proj, "rev-parse", f"{branch}~1").strip() == head
          and log.startswith("Use a set for the duplicate check"), out)
    check("land: the commit body has the measured numbers and no attribution",
          re.search(r"main: [\d.]+ ms → [\d.]+ ms, \d+% faster \(95% CI", log) and f"{REPEAT} runs each" in log
          and "The tests pass" in log and not re.search(r"(?i)claude|co-authored|generated with|anthropic", log), log)
    check("land: the user's branch and working tree are untouched",
          g(proj, "branch", "--show-current").strip() == "main" and g(proj, "rev-parse", "HEAD").strip() == head
          and g(proj, "status", "--porcelain") == before_status
          and len(g(proj, "worktree", "list").strip().splitlines()) == 1, out)
    check("land: then a final check of the branch against the base", code == 0 and "Final check: faster" in out, out)
    # Landing on the branch that's checked out (killer-goal's way), when the branch then fails its final check:
    # the tests read a flag file outside the project, so they pass for compare and fail for this landing.
    FAIL_FLAG.write_text("fail")
    out, code = perf("land", "-w", ws, "C1", "--branch", "main", ok=(0, 1))
    FAIL_FLAG.unlink()
    made = re.search(r"Committed C1 on main: (\w+)", out)
    check("land --branch <the current branch>: commits on it, in place",
          made and g(proj, "rev-parse", f"{made.group(1)}~1").strip() == head
          and "seen, out = set(), []" in g(proj, "show", f"{made.group(1)}:names.py"), out)
    check("land: a branch that fails its final check has the commits taken back, and nothing else",
          code == 1 and "rejected: tests" in out and "commits were undone" in out
          and g(proj, "rev-parse", "HEAD").strip() == head and g(proj, "branch", "--show-current").strip() == "main"
          and (proj / "names.py").read_bytes() == NAMES.encode() and g(proj, "status", "--porcelain") == before_status, out)
    out, _ = perf("status", "-w", ws)
    check("status: the working tree is as it was at init", "as it was at init" in out, out)

    # ---------- report
    out, _ = perf("report", "-w", ws)
    report = (ws / "report.md").read_text(encoding="utf-8")
    summary = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
    check("report: before and after, benchmarks, candidates, rejections, decisions, waiting, machine",
          report.index("## Before and after") < report.index("## Benchmarks") < report.index("## Proven speedups")
          < report.index("## Rejected, and why") < report.index("## Decisions to check")
          < report.index("## Waiting for your OK") < report.index("## How it was measured")
          and "rejected: output changed" in report and "rejected: tests" in report
          and "numbers are relative" in report and "CPUs" in report, report)
    check("summary.json: the fields killer-goal reads, with numbers from the script; the undone landing is partial",
          summary["skill"] == "killer-perf" and summary["status"] == "partial" and summary["branch"] == branch
          and len(summary["commits"]) == 1 and summary["decisions"] == ["Treated the poll interval as free to change"]
          and summary["waiting"] == ["Push the branch: git push -u origin killer-perf/x"]
          and summary["numbers"]["main_change_pct"] > 50 and "faster" in summary["headline"]
          and "was undone" in report and out.strip().splitlines()[-1].startswith("killer-perf: partial · main:"),
          (summary, out))

    # ---------- profile
    out, _ = perf("profile", "-w", ws, "main")
    check("profile: Python under cProfile, top functions by cumulative and self time, saved for finders",
          "Top 25 by cumulative time" in out and "Top 25 by self time" in out and "names.py:4 dedupe" in out
          and (ws / "profiles" / "main.txt").is_file() and (ws / "profiles" / "main.prof").is_file(), out)

    # ---------- collect and hypotheses
    out, _ = perf("sandbox", "-w", ws, "find-algo", "--give", ws / "profiles" / "main.txt")
    fbox = box_path(out)
    check("sandbox --give: the profile is in the finder's scratch folder", (fbox / "scratch" / "main.txt").is_file(), out)
    (fbox / "scratch" / "reply.json").write_text(json.dumps({"hypotheses": [
        {"title": "quadratic dedupe", "claim": "dedupe() checks membership in a list", "where": ["names.py:4"],
         "why": "the profile shows it", "expected_effect": "about 10% of main", "confidence": "high"},
        {"title": "fixed poll", "claim": "wait_ready() sleeps 30 ms per check", "effect_pct": 80},
        {"title": "no claim"}], "covered": ["names.py", "slow.py"]}))
    out, _ = perf("collect", "-w", ws, "--sandbox", "find-algo")
    check("collect: adds hypotheses with a claim, skips the rest", "added H1-H2" in out and "skipped 1" in out, out)
    out, _ = perf("list", "-w", ws)
    check("list: hypotheses biggest expected effect first", out.index("H2") < out.index("H1"), out)
    out, _ = perf("hypothesis", "-w", ws, "H1")
    check("hypothesis: the claim for the optimiser, the finder's reasoning kept apart",
          out.index("**Claim:**") < out.index("For the lead only") < out.index("the profile shows it"), out)
    perf("clean", "-w", ws)

    # ---------- a second run, like a next round: metrics, varying output, very fast commands, Node.js
    out, _ = perf("init", "--repo", proj, "--slug", "round-2", "--rev", branch, "--like", ws)
    ws2 = run_folder(out)
    s2 = state(ws2)
    check("init --rev <branch> --like <run>: based on the branch, with the benchmarks and tests but not the noise, "
          "and the tests rerun there",
          f"Base: {g(proj, 'rev-parse', branch).strip()[:10]}" in out and "main" in s2["benchmarks"]
          and "noise" not in s2["benchmarks"]["main"] and s2["tests"]["cmd"] == [PY, "test_names.py"]
          and "Tests on the unchanged project" in out and "passed in" in out, out)
    out, _ = perf("bench", "add", "-w", ws2, "items", "--", PY, "-c", "print('METRIC: items=42 count'); print('hi')")
    b = state(ws2)["benchmarks"]["items"]
    check("bench add: a command that prints one METRIC is decided by it", b["metric"] == "items"
          and b["unit"] == "count" and b["inner"] == 1 and "deciding on METRIC items" in out, out)
    out, _ = perf("bench", "add", "-w", ws2, "two", "--", PY, "-c", "print('METRIC: a=1'); print('METRIC: b=2')", ok=(2,))
    check("bench add: two METRICs and no --metric is refused", "choose the one that decides" in out, out)
    varies = [PY, "-c", "import random; print('seed', random.random())"]
    out, _ = perf("bench", "add", "-w", ws2, "varies", "--", *varies, ok=(2,))
    check("bench add: output that varies between runs is caught", "differs between two runs" in out, out)
    out, _ = perf("bench", "add", "-w", ws2, "varies", "--output-normalise", r"0\.\d+", "--", *varies)
    check("bench add --output-normalise: masking the part that varies fixes it", "masking 1 pattern" in out, out)
    check("bench add: a very fast command is looped, a very slow one cut to the fewest runs that can decide",
          P.plan_runs(0.01, True, None, None, 15) == (25, 15) and P.plan_runs(0.3, True, None, None, 15) == (1, 15)
          and P.plan_runs(0.01, False, None, None, 15) == (1, 15) and P.plan_runs(200, True, None, None, 15) == (1, 6)
          and P.plan_runs(200, True, None, 9, 15) == (1, 9) and P.plan_runs(0.01, True, 3, None, 15) == (3, 15))
    out, _ = perf("bench", "add", "-w", ws2, "nocheck", "--no-trial", "--no-output-check", "--why", "prints the time",
                  "--", PY, "-c", "import time; print(time.time())")
    check("bench add --no-output-check: recorded as a decision",
          any("nocheck runs without the output check: prints the time" in d["text"] for d in state(ws2)["decisions"]), out)
    out, _ = perf("profile", "-w", ws2, "nocheck", ok=(2,))
    check("profile: python -c can't go under cProfile, and it says so", "can't profile `python -c`" in out, out)
    if shutil.which("node"):
        perf("bench", "add", "-w", ws2, "js", "--no-trial", "--no-output-check", "--", "node", "busy.js")
        out, _ = perf("profile", "-w", ws2, "js")
        check("profile: Node.js under --cpu-prof, self time by function and file",
              "Top 25 by self time" in out and re.search(r"busy \(busy\.js\)", out)
              and (ws2 / "profiles" / "js.cpuprofile").is_file(), out)
    else:
        print("SKIP profile: Node.js (node isn't installed)")
    out, _ = perf("bench", "add", "-w", ws2, "tool", "--no-trial", "--no-output-check", "--", "git", "--version")
    out, _ = perf("profile", "-w", ws2, "tool")
    check("profile: a command that isn't Python or Node.js gets advice, not a crash", "No built-in profiler" in out, out)
    out, _ = perf("tests", "-w", ws2, "--none", "--why", "it has none")
    out, _ = perf("report", "-w", ws2)
    s2 = json.loads((ws2 / "summary.json").read_text(encoding="utf-8"))
    check("report: a run with no noise test is blocked, and says why",
          s2["status"] == "blocked" and "no benchmark passed its noise test" in s2["headline"], s2)
    perf("bench", "add", "-w", ws2, "wall", "--", PY, "-c", "import time; time.sleep(0.05); print('ok')")
    out, code = perf("bench", "noise", "-w", ws2, "wall", "--repeat", 6, ok=(0, 1))
    wn = state(ws2)["benchmarks"]["wall"].get("noise") or {}
    check("bench noise on wall time: the whole process timed, medians in ms (no claim about this machine's noise)",
          state(ws2)["benchmarks"]["wall"]["metric"] == "wall" and wn.get("n") == [6, 6]
          and wn.get("state") in ("clean", "too noisy") and 0.03 < wn["before"] < 30
          and re.search(r"wall, the unchanged project against itself: [\d.]+ ms and [\d.]+ ms", out), out)
    (proj / "stray.txt").write_text("left by someone")
    out, code = perf("status", "-w", ws2, ok=(1,))
    check("status: a change to the real project is caught", "stray.txt" in out, out)
    perf("clean", "-w", ws2)
    leftover = [p for p in (tmp / "boxes").rglob("*") if p.is_dir() and p.parent.parent == tmp / "boxes"]
    check("clean: no sandboxes left behind", not leftover, leftover)
finally:
    if os.environ.get("PERF_TEST_KEEP"):
        print(f"kept {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"took {time.monotonic() - T0:.0f} s")
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
