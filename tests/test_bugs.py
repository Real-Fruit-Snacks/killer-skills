"""Scenario tests for killer-bugs' scripts (bugs.py and run.py). Run: python -I tests/test_bugs.py"""
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
BUGS = ROOT / "skills" / "killer-bugs" / "scripts" / "bugs.py"
PY = sys.executable
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


tmp = Path(tempfile.mkdtemp())
ENV = dict(os.environ, KILLER_BUGS_HOME=str(tmp / "runs"), KILLER_BUGS_SANDBOXES=str(tmp / "boxes"),
           GH_TOKEN="test-token-value", MY_API_KEY="test-key-value", PYTHONIOENCODING="utf-8",
           PYTHONPATH=str(tmp / "host-site"))
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    ENV.pop(k, None)


def bugs(*args, ok=(0,)):
    r = subprocess.run([PY, str(BUGS), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    out = r.stdout + r.stderr
    if r.returncode not in ok:
        raise AssertionError(f"exit {r.returncode} for bugs.py {' '.join(map(str, args))}:\n{out}")
    return out, r.returncode


def runpy(box, *args):
    r = subprocess.run([PY, str(Path(box) / "run.py"), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    return r.stdout + r.stderr, r.returncode


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout


def run_folder(out):
    return Path(re.search(r"Run folder: (.+)", out).group(1).strip())


def box_path(out):
    return Path(re.search(r"project copy: (.+?)(?: \(at .*\))?$", out, re.M).group(1).strip()).parent


BUGGY = "def mean(xs):\n    return sum(xs) / (len(xs) - 1)\n"
FIXED = "def mean(xs):\n    return sum(xs) / len(xs)\n"
TEST_MEAN = ("import sys\nsys.path.insert(0, '.')\nfrom calc import mean\n"
             "got = mean([2, 4])\nprint('EXPECTED: 3.0')\nprint(f'OBSERVED: {got}')\nsys.exit(0 if got == 3 else 1)\n")

try:
    # ---------- a project in every working-tree state
    proj = tmp / "proj"
    proj.mkdir()
    g(proj, "init", "-q", "-b", "main")
    g(proj, "config", "user.name", "Test Person")
    g(proj, "config", "user.email", "test@example.com")
    g(proj, "remote", "add", "origin", "https://example.invalid/proj.git")
    (proj / ".gitignore").write_text("ignored/\n*.log\n")
    (proj / "calc.py").write_text(BUGGY)
    (proj / "README.md").write_text("# calc\n")
    (proj / "blob.bin").write_bytes(b"\0\1\2")
    (proj / "gone.txt").write_text("to delete")
    (proj / "staged.txt").write_text("v1")
    (proj / "tests").mkdir()
    (proj / "tests" / "test_other.py").write_text("print('fine')\n")
    g(proj, "add", "-A")
    g(proj, "commit", "-q", "-m", "first")
    (proj / "README.md").write_text("# calc\nchanged\n")
    (proj / "new.txt").write_text("untracked")
    (proj / "ignored").mkdir()
    (proj / "ignored" / "x.txt").write_text("ignored")
    (proj / "gone.txt").unlink()
    (proj / "staged.txt").write_text("v2")
    g(proj, "add", "staged.txt")

    # ---------- init and scopes
    out, _ = bugs("init", "--repo", proj, "--target", "a calculator")
    ws = run_folder(out)
    scope = (ws / "scope.txt").read_text().split()
    check("init: the run folder sits under KILLER_BUGS_HOME, keyed by project",
          ws.parent.parent == tmp / "runs" and ws.parent.name.startswith("proj-"), ws)
    check("init: whole-repo scope has tracked and untracked text files, not ignored, deleted or binary ones",
          scope == sorted([".gitignore", "README.md", "calc.py", "new.txt", "staged.txt", "tests/test_other.py"])
          and "1 binary or over 1 MB left out" in out, (scope, out))
    out, _ = bugs("init", "--repo", proj, "--scope", "changes", "--slug", "changes")
    check("init: changes scope is what differs from HEAD, deletions left out",
          (run_folder(out) / "scope.txt").read_text().split() == ["README.md", "new.txt", "staged.txt"], out)
    out, _ = bugs("init", "--repo", proj / "tests", "--scope", "path:tests", "--slug", "tests")
    check("init: path scope, from a subfolder of the repo",
          (run_folder(out) / "scope.txt").read_text().split() == ["tests/test_other.py"], out)
    out, _ = bugs("init", "--repo", proj, "--slug", "changes")
    check("init: a second run on the same day gets its own folder", run_folder(out).name.endswith("-changes-2"), out)
    out, _ = bugs("init", "--repo", proj, "--slug", "deps", "--include", "ignored")
    wd = run_folder(out)
    out, _ = bugs("sandbox", "-w", wd, "s")
    check("init --include: every sandbox in the run gets those ignored paths",
          (box_path(out) / "repo" / "ignored" / "x.txt").exists(), out)
    bugs("clean", "-w", wd)
    out, code = bugs("init", "--repo", proj, "--scope", "everything", ok=(2,))
    check("init: an unknown scope fails and names the choices", "repo, path:<folder>, changes" in out, out)

    # ---------- sandboxes
    out, _ = bugs("sandbox", "-w", ws, "hunt-1")
    box = box_path(out)
    copy = box / "repo"
    check("sandbox: outside the run folder, under KILLER_BUGS_SANDBOXES",
          str(box).startswith(str(tmp / "boxes")) and str(ws) not in out.split("run things")[0], out)
    check("sandbox: has the uncommitted edits and new files, not ignored or deleted ones",
          (copy / "README.md").read_text() == "# calc\nchanged\n" and (copy / "new.txt").exists()
          and not (copy / "ignored").exists() and not (copy / "gone.txt").exists(), sorted(os.listdir(copy)))
    check("sandbox: staged work is staged there too",
          g(copy, "diff", "--cached", "--name-only").split() == ["staged.txt"]
          and (copy / "staged.txt").read_text() == "v2", g(copy, "status", "--short"))
    check("sandbox: no remote to push to, the user's identity, the same branch",
          g(copy, "remote").strip() == "" and g(copy, "config", "user.name").strip() == "Test Person"
          and g(copy, "branch", "--show-current").strip() == "main")
    check("sandbox: the real project is untouched", g(proj, "remote").strip() == "origin"
          and (proj / "gone.txt").exists() is False and g(proj, "diff", "--cached", "--name-only").split() == ["staged.txt"])
    out, _ = bugs("sandbox", "-w", ws, "with-ignored", "--include", "ignored")
    check("sandbox --include copies an ignored folder", (box_path(out) / "repo" / "ignored" / "x.txt").exists(), out)
    out, _ = bugs("sandbox", "-w", ws, "old", "--rev", "HEAD")
    old = box_path(out) / "repo"
    check("sandbox --rev: the commit as it was, no working-tree changes",
          (old / "README.md").read_text() == "# calc\n" and not (old / "new.txt").exists() and (old / "gone.txt").exists())
    lf = tmp / "proj-lf"   # a project that keeps LF files on a machine whose git turns them into CRLF
    lf.mkdir()
    g(lf, "init", "-q", "-b", "main")
    g(lf, "config", "core.autocrlf", "false")
    (lf / "data.txt").write_bytes(b"a\nb\n")
    g(lf, "add", "-A")
    g(lf, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "lf")
    wl = run_folder(bugs("init", "--repo", lf, "--slug", "lf")[0])
    lf_now = box_path(bugs("sandbox", "-w", wl, "now")[0]) / "repo" / "data.txt"
    lf_old = box_path(bugs("sandbox", "-w", wl, "old", "--rev", "HEAD")[0]) / "repo" / "data.txt"
    check("sandbox: the project's own core.autocrlf carries over, so its files keep their line endings",
          lf_now.read_bytes() == b"a\nb\n" and lf_old.read_bytes() == b"a\nb\n", (lf_now.read_bytes(), lf_old.read_bytes()))
    bugs("clean", "-w", wl)
    out, _ = bugs("sandbox", "-w", ws, "hunt-1", ok=(2,))
    check("sandbox: a name can't be used twice", "already exists" in out, out)
    out, _ = bugs("sandbox", "-w", ws, "a/b", ok=(2,))
    check("sandbox: names are plain", "a sandbox name is" in out, out)

    # ---------- run.py inside a sandbox
    probe = ("import os; print('VALS=' + '|'.join(os.environ.get(k, '-') for k in "
             "('HOME', 'USERPROFILE', 'APPDATA', 'TEMP', 'GH_TOKEN', 'MY_API_KEY', 'HTTPS_PROXY', 'KB_REPRO', "
             "'PYTHONPATH', 'CARGO_INSTALL_ROOT', 'GOBIN', 'npm_config_prefix')))")
    out, code = runpy(box, "--", PY, "-c", probe)
    vals = re.search(r"^VALS=(.+)$", out, re.M).group(1).split("|")
    inside = lambda v: Path(v).resolve().is_relative_to(box.resolve()) if v != "-" else False
    check("run.py: home, profile, app-data and temp are the sandbox's own",
          code == 0 and all(inside(v) for v in vals[:4]), vals)
    check("run.py: tokens and keys are dropped", vals[4] == "-" and vals[5] == "-", vals)
    check("run.py: the network goes to a dead proxy", vals[6] == "http://127.0.0.1:9", vals)
    check("run.py: KB_REPRO points at the scratch folder", Path(vals[7]) == box / "repro", vals)
    check("run.py: the host's PYTHONPATH is dropped, so the real project's copy can't be imported", vals[8] == "-", vals)
    check("run.py: cargo install, go install and npm -g land in the sandbox", all(inside(v) for v in vals[9:12]), vals)
    node = shutil.which("node")
    nv = subprocess.run([node, "-p", "process.versions.node"], capture_output=True, text=True).stdout.strip() if node else ""
    if nv and tuple(map(int, nv.split(".")[:2])) >= (24, 5):   # NODE_USE_ENV_PROXY arrived in 24.5
        out, code = runpy(box, "--timeout", "60", "--", node, "-e",
                          "fetch('https://example.com').then(() => console.log('REACHED'))"
                          ".catch(e => console.log('BLOCKED', e.cause && e.cause.code))")
        check("run.py: Node.js's fetch goes to the dead proxy too", out.strip().splitlines()[-1].startswith("BLOCKED"), out)
    out, code = runpy(box, "--", PY, "-c", "import os; print(os.getcwd())")
    check("run.py: runs in the project copy", code == 0 and Path(out.strip().splitlines()[-1]).resolve() == copy.resolve(), out)
    out, code = runpy(box, "--cwd", "tests", "--", PY, "-c", "import os; print(os.path.basename(os.getcwd()))")
    check("run.py --cwd", code == 0 and "\ntests\n" in out, out)
    out, code = runpy(box, "--", PY, "-c", "import sys; print('noise'); print('EXPECTED: 3'); print('OBSERVED: 6'); sys.exit(1)")
    check("run.py: a failing command fails, and EXPECTED/OBSERVED lines lead the summary",
          code == 1 and "FAILED: exit 1" in out and out.index("OBSERVED: 6") < out.index("--- last"), out)
    logs = sorted((box / "logs").glob("*.log"))
    check("run.py: every run's output is logged", logs and "OBSERVED: 6" in logs[-1].read_text(), logs)
    (box / "repro" / "flaky.py").write_text(
        "import pathlib, sys\np = pathlib.Path(__file__).with_name('n')\n"
        "n = int(p.read_text()) + 1 if p.exists() else 1\np.write_text(str(n))\nsys.exit(1 if n % 3 == 0 else 0)\n")
    out, code = runpy(box, "--repeat", "6", "--", PY, "../repro/flaky.py")
    check("run.py --repeat counts failures", code == 1 and "2 of 6 runs failed" in out and "first failure: run 3" in out, out)
    t0 = time.monotonic()
    out, code = runpy(box, "--timeout", "2", "--", PY, "-c", "import time; time.sleep(60)")
    check("run.py --timeout kills a hung command", code == 1 and "timed out" in out and time.monotonic() - t0 < 30, out)
    out, code = runpy(box, "--", "no-such-command-anywhere")
    check("run.py: a missing command fails cleanly", code == 1 and "could not start" in out, out)
    out, _ = bugs("sandbox", "-w", ws, "online", "--net")
    out, code = runpy(box_path(out), "--", PY, "-c", "import os; print(os.environ.get('HTTPS_PROXY', 'none'))")
    check("sandbox --net leaves the network alone", "127.0.0.1:9" not in out, out)

    # ---------- collecting, merging, verdicts
    reply = tmp / "hunter1.txt"
    reply.write_text("Here's what I found.\n\n```json\n" + json.dumps({
        "candidates": [
            {"title": "mean divides by n-1", "claim": "mean([2, 4]) returns 6.0, not 3.0",
             "trigger": "call mean([2, 4])", "expected": "3.0", "where": ["calc.py:2"], "confidence": "high"},
            {"title": "README is short", "claim": "README doesn't say how to run it", "kind": "mismatch"},
            {"title": "no claim here"},
        ],
        "covered": [str(copy / "calc.py"), "README.md:1", "./tests/test_other.py"],
    }) + "\n```\n")
    out, _ = bugs("collect", "-w", ws, reply, "--source", "hunter:failures#1")
    check("collect: adds candidates from the reply's JSON block and skips empty ones",
          "added B1-B2" in out and "read 3 files" in out and "skipped 1" in out, out)
    reply2 = tmp / "hunter2.json"
    reply2.write_text(json.dumps({"candidates": [{"title": "average wrong", "claim": "mean is too big",
                                                  "where": "calc.py:2"}], "covered": ["calc.py"]}))
    bugs("collect", "-w", ws, reply2, "--source", "hunter:input#1")
    out, _ = bugs("merge", "-w", ws, "B1", "B3")
    out, _ = bugs("add", "-w", ws, "--claim", "mean([]) crashes with ZeroDivisionError", "--title", "empty list",
                  "--source", "user")
    db = json.loads((ws / "bugs.json").read_text())
    b1 = db["bugs"][0]
    check("merge: one bug, two finders, the duplicate points at it",
          b1["found_by"] == ["hunter:failures#1", "hunter:input#1"] and db["bugs"][2]["status"] == "merged"
          and db["bugs"][2]["merged_into"] == "B1")
    check("add: the user's suspicion is a candidate like any other", db["bugs"][3]["found_by"] == ["user"]
          and db["bugs"][3]["id"] == "B4")
    out, _ = bugs("coverage", "-w", ws)
    check("coverage: paths from sandboxes and file:line references count; the rest are listed",
          "3 of 6 files" in out and "new.txt" in out and "staged.txt" in out and ".gitignore" in out, out)

    out, _ = bugs("sandbox", "-w", ws, "prove-B1")
    pbox = box_path(out)
    (pbox / "repro" / "repro.py").write_text(TEST_MEAN)
    (pbox / "repro" / "reply.json").write_text(json.dumps({
        "verdict": "reproduced", "command": [PY, "../repro/repro.py"], "expected": "3.0", "observed": "6.0",
        "runs": None, "entry_point": "calc.mean"}))
    out, _ = bugs("update", "-w", ws, "B1", "--as", "prover", "--sandbox", "prove-B1")
    check("update --as prover --sandbox: reads the agent's reply.json, keeps its repro files, not the reply",
          "B1: proven" in out and (ws / "repros" / "B1" / "repro.py").exists()
          and not (ws / "repros" / "B1" / "reply.json").exists(), out)
    check("update --sandbox: the sandbox is deleted afterwards", not pbox.exists(), pbox)
    prover = tmp / "prover.json"
    check("update --as prover: the reproduction is rerun in a fresh copy", "showed in 1 of 1" in out, out)
    prover.write_text(json.dumps({"verdict": "not-reproduced", "notes": "the README covers it"}))
    out, _ = bugs("update", "-w", ws, "B2", "--from", prover, "--as", "prover")
    check("update: not reproduced means suspected", "B2: suspected" in out, out)
    chal = tmp / "challenger.json"
    chal.write_text(json.dumps({"verdict": "stands", "reason": "documented as the average", "severity": "high",
                                "likelihood": "common"}))
    out, _ = bugs("update", "-w", ws, "B1", "--from", chal, "--as", "challenger")
    check("update --as challenger: stands means confirmed, with severity", "B1: confirmed (high/common)" in out, out)
    prover.write_text(json.dumps({"verdict": "reproduced",
                                  "command": [PY, "-c", "import no_such_module_here"]}))
    out, _ = bugs("update", "-w", ws, "B4", "--from", prover, "--as", "prover")
    check("update: a reproduction that crashes without OBSERVED: is inconclusive, not proof",
          "was inconclusive" in out and "without an OBSERVED: line" in out and "B4: proven" in out, out)
    bugs("add", "-w", ws, "--claim", "mean([1]) is wrong", "--title", "one item")
    out, _ = bugs("sandbox", "-w", ws, "prove-B5")
    (box_path(out) / "repro" / "r.py").write_text("print('EXPECTED: 1.0')\nprint('OBSERVED: 1.0')\n")
    (box_path(out) / "repro" / "reply.json").write_text(json.dumps({"verdict": "reproduced", "command": [PY, "../repro/r.py"]}))
    out, _ = bugs("update", "-w", ws, "B5", "--as", "prover", "--sandbox", "prove-B5")
    check("update: a prover's 'reproduced' that doesn't show in a fresh copy is only suspected",
          "did not show" in out and "B5: suspected" in out, out)
    chal.write_text(json.dumps({"verdict": "question", "reason": "maybe raising is intended",
                                "question": "Should mean([]) raise?", "severity": "low", "likelihood": "rare"}))
    out, _ = bugs("update", "-w", ws, "B4", "--from", chal, "--as", "challenger")
    check("update: a question for the user", "B4: question" in out, out)
    chal.write_text(json.dumps({"verdict": "maybe"}))
    out, _ = bugs("update", "-w", ws, "B4", "--from", chal, "--as", "challenger", ok=(2,))
    check("update: an unknown verdict is refused", "stands, question, knocked-down or docs-stale" in out, out)
    out, _ = bugs("list", "-w", ws)
    check("list: by status, then rank",
          out.index("B1") < out.index("B4") < out.index("B2") < out.index("B5") < out.index("B3"), out)

    # ---------- check-fix
    (proj / "tests" / "test_mean.py").write_text(TEST_MEAN)
    test_cmd = ["--tests", "tests/test_mean.py", "--", PY, "tests/test_mean.py"]
    out, code = bugs("check-fix", "-w", ws, *test_cmd, ok=(1,))
    check("check-fix: a test that fails with the 'fix' in place is a FAIL", "fails even with the fix" in out, out)
    (proj / "calc.py").write_text(FIXED)
    out, code = bugs("check-fix", "-w", ws, "--bug", "B1", *test_cmd)
    check("check-fix: fails without the fix, passes with it", "PASS: the test fails without the fix" in out
          and "OBSERVED: 6.0" in out.split("== without the fix ==")[1], out)
    check("check-fix: names the fix files and the kept tests",
          re.search(r"Fix: .*calc\.py", out) and "Tests kept in both runs: tests/test_mean.py" in out, out)
    (proj / "tests" / "test_weak.py").write_text("print('ok')\n")
    out, code = bugs("check-fix", "-w", ws, "--tests", "tests/", "--", PY, "tests/test_weak.py", ok=(1,))
    check("check-fix: a test that passes without the fix proves nothing", "passes without the fix too" in out, out)
    out, code = bugs("check-fix", "-w", ws, "--repro", "B1")
    check("check-fix --repro: uses the bug's saved reproduction", "PASS" in out, out)
    out, code = bugs("check-fix", "-w", ws, "--tests", "tests/*", "--", PY, "tests/test_mean.py", ok=(0, 1))
    check("check-fix: --tests takes globs", code == 0 and "Tests kept in both runs: tests/test_mean.py, tests/test_weak.py" in out, out)
    (proj / "tests" / "test_weak.py").unlink()
    g(proj, "add", "-A")
    g(proj, "commit", "-q", "-m", "fix mean")
    out, code = bugs("check-fix", "-w", ws, "--base", "HEAD~1", "--tests", "tests/test_mean.py", "--", PY, "tests/test_mean.py")
    check("check-fix --base: a committed fix", "PASS" in out, out)
    out, code = bugs("check-fix", "-w", ws, "--tests", "tests", "--", PY, "tests/test_mean.py", ok=(2,))
    check("check-fix: no changes outside the tests means no fix to check", "no fix to check" in out, out)
    cfg = json.loads((ws / "config.json").read_text())
    check("check-fix: its sandboxes are removed afterwards", not any(n.startswith("check-") for n in cfg["sandboxes"]),
          list(cfg["sandboxes"]))
    db = json.loads((ws / "bugs.json").read_text())
    check("check-fix --bug: the result is on the bug", db["bugs"][0].get("fix_check", {}).get("verdict") == "PASS",
          db["bugs"][0].get("fix_check"))

    # ---------- report, record, recheck
    bugs("update", "-w", ws, "B1", "--status", "fixed", "--note", "fixed in the working tree")
    out, _ = bugs("report", "-w", ws)
    report = (ws / "report.md").read_text(encoding="utf-8")
    check("report: sections in order, with the reproduction and both finders",
          report.index("## Proven bugs") < report.index("## Questions for you") < report.index("## Suspected, not proven")
          and "### B1. mean divides by n-1 (high severity, common) · fixed" in report
          and "**Reproduce:**" in report and "2 finders separately (failures, input)" in report, report)
    check("report: the question and the coverage", "Should mean([]) raise?" in report and "3 of 6 files" in report, report)
    out, _ = bugs("close", "-w", ws)
    rec = json.loads((tmp / "runs" / ws.parent.name / "record.json").read_text())
    check("close: proven bugs go on the record with their reproduction; open questions don't",
          len(rec["entries"]) == 1 and rec["entries"][0]["status"] == "fixed"
          and (tmp / "runs" / ws.parent.name / "repros" / "R1" / "repro.py").exists(), rec)
    out, _ = bugs("init", "--repo", proj, "--slug", "next")
    ws2 = run_folder(out)
    check("init: says what the record holds", "0 open, 1 fixed, 0 intended" in out, out)
    out, _ = bugs("recheck", "-w", ws2)
    check("recheck: a fixed bug still fixed", "still fixed" in out, out)
    (proj / "calc.py").write_text(BUGGY)
    out, _ = bugs("recheck", "-w", ws2)
    check("recheck: a fixed bug that came back", "CAME BACK" in out, out)
    (proj / "calc.py").write_text(FIXED)

    # ---------- clean
    out, _ = bugs("clean", "-w", ws)
    check("clean: deletes the run's sandboxes, git's read-only files included",
          "Deleted 4 sandboxes" in out and not (tmp / "boxes" / "kb" / json.loads((ws / "config.json").read_text())["run_id"]).exists(), out)

    # ---------- finders that reproduce what they find, triage, settings, parallel updates
    out, _ = bugs("init", "--repo", proj, "--slug", "finders", "--env", "FOO_SETTING=from-init",
                  "--exclude", "tests/", "*.txt")
    ws3 = run_folder(out)
    check("init --exclude: those paths are out of scope",
          (ws3 / "scope.txt").read_text().split() == [".gitignore", "README.md", "calc.py"],
          (ws3 / "scope.txt").read_text())
    out, _ = bugs("sandbox", "-w", ws3, "find-failures")
    fbox = box_path(out)
    out, code = runpy(fbox, "--", PY, "-c", "import os; print('SETTING=' + os.environ.get('FOO_SETTING', '-'))")
    check("init --env: the setting reaches every sandbox", "SETTING=from-init" in out, out)
    import http.server
    import threading
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out, code = runpy(fbox, "--timeout", "30", "--", PY, "-c",
                      f"import urllib.request; print('LOCAL', urllib.request.urlopen('http://127.0.0.1:{srv.server_port}/', timeout=10).status)")
    srv.shutdown()
    check("sandbox: with the network off, a server on this machine is still reachable", "LOCAL 200" in out, out)
    (fbox / "repro" / "empty.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom calc import mean\n"
        "try:\n    mean([])\n    got = 'no error'\nexcept ZeroDivisionError:\n    got = 'ZeroDivisionError'\n"
        "print('EXPECTED: a clear error for an empty list')\nprint(f'OBSERVED: {got}')\n"
        "sys.exit(1 if got == 'ZeroDivisionError' else 0)\n")
    (fbox / "repro" / "fine.py").write_text("print('EXPECTED: 2')\nprint('OBSERVED: 2')\n")
    (fbox / "repro" / "reply.json").write_text(json.dumps({"candidates": [
        {"title": "empty list crashes", "claim": "mean([]) raises ZeroDivisionError", "severity": "medium",
         "command": [PY, "../repro/empty.py"], "expected": "a clear error", "observed": "ZeroDivisionError",
         "where": ["calc.py:2"]},
        {"title": "not really a bug", "claim": "mean([1, 2, 3]) is wrong", "severity": "high",
         "command": [PY, "../repro/fine.py"]},
        {"title": "style nit", "claim": "calc.py has no docstring", "severity": "low"},
        {"title": "unsure", "claim": "mean of a huge list overflows", "severity": "medium"},
    ], "covered": ["P3 calc.py:1 'def mean'", "README.md (lines 1-2)"]}))
    out, _ = bugs("collect", "-w", ws3, "--sandbox", "find-failures", "--source", "hunter:failures#1")
    check("collect --sandbox: reads reply.json and reruns each finder reproduction in a fresh copy",
          "added B1-B4" in out and "1 proven, 1 didn't show, 0 inconclusive" in out, out)
    check("collect --sandbox: keeps the finder's scratch files but not its reply, and deletes the sandbox",
          (ws3 / "scratch" / "find-failures" / "empty.py").exists()
          and not (ws3 / "scratch" / "find-failures" / "reply.json").exists() and not fbox.exists())
    db3 = json.loads((ws3 / "bugs.json").read_text())
    check("collect: a reproduction that shows makes it proven; one that doesn't stays a candidate, with a note",
          db3["bugs"][0]["status"] == "proven" and db3["bugs"][1]["status"] == "candidate"
          and any("didn't show the bug" in n for n in db3["bugs"][1]["notes"]), [b["status"] for b in db3["bugs"]])
    out, _ = bugs("list", "-w", ws3)
    check("list: severity guesses show until a challenger rates them", "~medium" in out and "~low" in out, out)
    out, _ = bugs("coverage", "-w", ws3)
    check("coverage: labels and line ranges around file names don't hide them", "2 of 3 files" in out, out)
    bugs("merge", "-w", ws3, "B4", "B1")
    b4 = json.loads((ws3 / "bugs.json").read_text())["bugs"][3]
    check("merge: a working reproduction carries over to the bug that's kept",
          b4["status"] == "proven" and b4.get("repro_dir") == "repros/B1", b4)
    procs = [subprocess.Popen([PY, str(BUGS), "update", "-w", str(ws3), "B3", "--note", f"note {i}"], env=ENV,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for i in range(8)]
    for pr in procs:
        pr.wait()
    notes = json.loads((ws3 / "bugs.json").read_text())["bugs"][2]["notes"]
    check("update: eight at once don't lose each other's changes",
          sorted(n for n in notes if n.startswith("note ")) == sorted(f"note {i}" for i in range(8)), notes)
    bugs("report", "-w", ws3)
    rep3 = (ws3 / "report.md").read_text(encoding="utf-8")
    check("report: low-severity suspicions get their own section; a finder reproduction that didn't show stays suspected",
          "## Low severity, not checked" in rep3 and "no docstring" in rep3.split("## Low severity")[1]
          and "didn't show the bug" in rep3.split("## Suspected, not proven")[1].split("\n## ")[0]
          and "## Reproduced, not challenged" in rep3, rep3)
    bugs("decide", "-w", ws3, "Took the worst 15 first")
    bugs("wait", "-w", ws3, "Open an issue for B1", "--command", "gh issue create --title B1")
    out, _ = bugs("report", "-w", ws3)
    summ = json.loads((ws3 / "summary.json").read_text(encoding="utf-8"))
    rep3 = (ws3 / "report.md").read_text(encoding="utf-8")
    check("report: writes summary.json in the suite's shape, with decisions and waiting items",
          summ["skill"] == "killer-bugs" and summ["status"] == "partial" and summ["numbers"]["unchallenged"] == 1
          and "1 reproduced but not challenged" in summ["headline"]
          and summ["decisions"] == ["Took the worst 15 first"] and "gh issue create" in summ["waiting"][0]
          and summ["report"].endswith("report.md") and "killer-bugs: " in out, (summ, out))
    check("report: lists decisions and waiting items for the user",
          "## Decisions to check" in rep3 and "## Waiting for your OK" in rep3 and "gh issue create --title B1" in rep3, rep3)
    bugs("clean", "-w", ws3)

    # ---------- the budget: what goes forward, argued bugs, agent counts, findings for the host
    out, _ = bugs("init", "--repo", proj, "--slug", "budget", "--depth", "quick")
    ws4 = run_folder(out)
    for name in ("map", "promises", "read-core"):
        bugs("sandbox", "-w", ws4, name)
    bugs("add", "-w", ws4, "--claim", "high, one finder", "--severity", "high", "--where", "calc.py:2", "--source", "hunter:a#1")
    bugs("add", "-w", ws4, "--claim", "high, two finders", "--severity", "high", "--source", "hunter:a#1")
    bugs("add", "-w", ws4, "--claim", "medium one", "--severity", "medium")
    bugs("add", "-w", ws4, "--claim", "low one", "--severity", "low")
    bugs("add", "-w", ws4, "--claim", "same as B2", "--severity", "high", "--source", "hunter:b#1")
    bugs("add", "-w", ws4, "--claim", "nobody rated this")
    bugs("merge", "-w", ws4, "B2", "B5")
    out, _ = bugs("forward", "-w", ws4, "--limit", "1")
    plan = json.loads((ws4 / "forward.json").read_text())
    check("forward: worst first, and a bug found by two finders separately goes ahead of one found by one",
          plan["chosen"] == ["B2"] and plan["over"] == ["B1"] and plan["prove"] == ["B2"], plan)
    check("forward: quick depth leaves medium and low out, and asks for the unrated ones to be rated",
          sorted(plan["below"]) == ["B3", "B4"] and plan["untriaged"] == ["B6"] and "rate these first" in out, (plan, out))
    out, _ = bugs("sandbox", "-w", ws4, "prove-B1")
    (box_path(out) / "repro" / "reply.json").write_text(json.dumps({
        "verdict": "impractical", "notes": "needs the disk to fill up between two writes",
        "would_take": "a disk that fills up mid-write"}))
    out, _ = bugs("update", "-w", ws4, "B1", "--as", "prover", "--sandbox", "prove-B1")
    check("update: an impractical verdict leaves the bug suspected, and says to send a code checker",
          "B1: suspected" in out and "code checker" in out, out)
    bugs("forward", "-w", ws4, "--limit", "1")
    check("forward: an impractical high-severity bug is offered to a code checker",
          json.loads((ws4 / "forward.json").read_text())["code_check"] == ["B1"])
    out, _ = bugs("sandbox", "-w", ws4, "code-B1")
    cbox = box_path(out)
    (cbox / "repro" / "reply.json").write_text(json.dumps({"verdict": "holds", "reason": "every step is there"}))
    out, _ = bugs("update", "-w", ws4, "B1", "--as", "code-check", "--sandbox", "code-B1", "--keep", ok=(2,))
    check("update --as code-check: 'holds' without its chain is refused", "needs its chain" in out, out)
    (cbox / "repro" / "reply.json").write_text(json.dumps({
        "verdict": "holds", "reason": "every step is there", "severity": "high", "likelihood": "rare",
        "chain": ["cli.py:10 calls save()", "store.py:40 writes the index after deleting the old one"]}))
    out, _ = bugs("update", "-w", ws4, "B1", "--as", "code-check", "--sandbox", "code-B1")
    check("update --as code-check: a chain that holds makes it argued, never proven", "B1: argued (high/rare)" in out, out)
    bugs("report", "-w", ws4)
    rep4 = (ws4 / "report.md").read_text(encoding="utf-8")
    check("report: argued bugs get their own section, with the chain and what it would take to see",
          "## Argued, not reproduced" in rep4 and "**The chain:**" in rep4 and "1. cli.py:10 calls save()" in rep4
          and "a disk that fills up mid-write" in rep4 and "1 argued but not reproduced" in rep4, rep4)
    check("report: run details give the depth and the agents actually started, by role",
          "**Depth:** quick" in rep4 and "**Agents:** 4 (" in rep4 and "1 code checker" in rep4
          and "1 read-through" in rep4 and "map" not in rep4.split("**Agents:**")[1].split("\n")[0], rep4)
    summ4 = json.loads((ws4 / "summary.json").read_text(encoding="utf-8"))
    check("summary.json: argued, over-the-limit and agent counts",
          summ4["numbers"]["argued"] == 1 and summ4["numbers"]["over_limit"] == 0 and summ4["agents"]["total"] == 4,
          summ4)
    out, _ = bugs("findings", "-w", ws4)
    found = json.loads(out)
    check("findings: the host's ReportFindings shape, argued as PLAUSIBLE, with the file and line",
          found and found[0]["verdict"] == "PLAUSIBLE" and found[0]["file"] == "calc.py" and found[0]["line"] == 2
          and all(len(f["short_summary"]) <= 60 for f in found), found)
    out, _ = bugs("sandbox", "-w", ws4, "read-tail")
    (box_path(out) / "repro" / "reply.json").write_text(json.dumps({"candidates": [
        {"title": "made-up place", "claim": "something breaks", "severity": "low",
         "where": ["calc.py:999", "nope/missing.py:3", "calc.py:1"]}], "covered": []}))
    out, _ = bugs("collect", "-w", ws4, "--sandbox", "read-tail", "--source", "hunter:read#2")
    newest = json.loads((ws4 / "bugs.json").read_text())["bugs"][-1]
    check("collect: locations that don't exist are flagged on the bug and in the output",
          "2 locations it gave don't exist" in out and "past the end" in newest["notes"][0]
          and "no such file" in newest["notes"][0] and "calc.py:1" not in newest["notes"][0], (out, newest["notes"]))
    bugs("close", "-w", ws4)
    rec4 = json.loads((Path(json.loads((ws4 / "config.json").read_text())["record"])).read_text())
    check("close: an argued bug goes on the record as open", any(e["status"] == "open" and e["claim"] == "high, one finder"
                                                                 for e in rec4["entries"]), rec4)
    bugs("clean", "-w", ws4)

    # ---------- blind reproductions, causes, inferred promises, docs out of date, stand-ins, freshness
    standin = tmp / "standin"
    standin.mkdir()
    if os.name == "nt":
        (standin / "gh.cmd").write_text("@echo stand-in gh refuses %*\r\n@exit /b 3\r\n")
    else:
        (standin / "gh").write_text("#!/bin/sh\necho stand-in gh refuses \"$@\"\nexit 3\n")
        (standin / "gh").chmod(0o755)
    (proj / "Get-Thing.ps1").write_text("param()\n'one'\n'two'\n")
    out, _ = bugs("init", "--repo", proj, "--slug", "blind", "--env", "TRAP={sandbox}/projects",
                  "--path-first", standin)
    ws5 = run_folder(out)
    cfg5 = json.loads((ws5 / "config.json").read_text())
    check("init: records which version of the scripts the run used", len(cfg5.get("script") or "") == 10, cfg5)
    out, _ = bugs("sandbox", "-w", ws5, "read-one")
    fb = box_path(out)
    r, code = runpy(fb, "--", PY, "-c", "import os; print('TRAP=' + os.environ['TRAP'])")
    check("init --env: {sandbox} is each sandbox's own folder",
          f"TRAP={fb}/projects".replace("\\", "/") in r.replace("\\", "/"), r)
    r, code = runpy(fb, "--", "gh", "auth", "status")
    check("init --path-first: a stand-in answers instead of the real tool", "stand-in gh refuses" in r and code == 1, r)
    show = lambda name: f"import sys\nprint('EXPECTED: fine')\nprint('OBSERVED: {name} broke')\nsys.exit(1)\n"
    for name in ("one", "two"):
        (fb / "repro" / name).mkdir()
        (fb / "repro" / name / "r.py").write_text(show(name))
    (fb / "repro" / "one" / "helper.txt").write_text("a helper one needs\n")
    (fb / "repro" / "loose.py").write_text(show("loose"))
    (fb / "repro" / "reply.json").write_text(json.dumps({"candidates": [
        {"title": "one", "claim": "one breaks on save", "cause": "save() writes before it checks",
         "severity": "high", "command": [PY, "../repro/one/r.py"], "where": ["Get-Thing.ps1:3"]},
        {"title": "two", "claim": "two breaks", "severity": "medium", "command": [PY, "../repro/two/r.py"],
         "where": ["P3 inferred from README.md:1"]},
        {"title": "loose", "claim": "loose breaks", "severity": "medium", "command": [PY, "../repro/loose.py"]},
    ], "covered": ["calc.py"]}))
    bugs("collect", "-w", ws5, "--sandbox", "read-one", "--source", "hunter:read-one#1")
    db5 = json.loads((ws5 / "bugs.json").read_text())["bugs"]
    rp = ws5 / "repros"
    check("collect: each reproduction is kept in its own folder, helpers and all, without the others",
          (rp / "B1" / "one" / "r.py").exists() and (rp / "B1" / "one" / "helper.txt").exists()
          and not (rp / "B1" / "two").exists() and not (rp / "B1" / "loose.py").exists()
          and db5[0]["status"] == "proven", sorted(str(x.relative_to(rp)) for x in rp.rglob("*")))
    check("collect: a loose reproduction file is kept alone, with a note saying so",
          (rp / "B3" / "loose.py").exists() and any("kept only loose.py" in n for n in db5[2]["notes"]), db5[2])
    check("collect: the cause is kept apart from the claim, for the lead and the fixer",
          db5[0].get("cause") == "save() writes before it checks" and "writes" not in db5[0]["claim"], db5[0])
    check("collect: a suspect resting on an inferred promise is flagged", db5[1].get("inferred") is True, db5[1])
    out, _ = bugs("sandbox", "-w", ws5, "challenge-B1")
    cb = box_path(out)
    out, _ = bugs("repro", "-w", ws5, "B1", "--into", "challenge-B1")
    check("repro --into: copies only that bug's reproduction, and says how to run it",
          (cb / "repro" / "one" / "r.py").exists() and not (cb / "repro" / "two").exists()
          and "../repro/one/r.py" in out, out)
    bugs("add", "-w", ws5, "--claim", "one breaks too", "--severity", "high", "--source", "lead")
    bugs("merge", "-w", ws5, "B1", "B4")
    out, _ = bugs("forward", "-w", ws5)
    plan5 = json.loads((ws5 / "forward.json").read_text())
    check("forward: a suspect on an inferred promise waits for the user to confirm it",
          plan5["unconfirmed"] == ["B2"] and "B2" not in plan5["chosen"] and "promise to confirm" in out, (plan5, out))
    bugs("update", "-w", ws5, "B2", "--confirm-promise")
    bugs("forward", "-w", ws5)
    check("update --confirm-promise: then it goes forward",
          "B2" in json.loads((ws5 / "forward.json").read_text())["chosen"])
    (cb / "repro" / "reply.json").write_text(json.dumps({"verdict": "stands", "reason": "it breaks",
                                                         "severity": "high", "likelihood": "common"}))
    bugs("update", "-w", ws5, "B1", "--as", "challenger", "--sandbox", "challenge-B1")
    out, _ = bugs("sandbox", "-w", ws5, "challenge-B3")
    box_path(out).joinpath("repro", "reply.json").write_text(json.dumps({
        "verdict": "docs-stale", "reason": "The README describes the old flag; the code changed it on purpose.",
        "severity": "low", "likelihood": "occasional"}))
    out, _ = bugs("update", "-w", ws5, "B3", "--as", "challenger", "--sandbox", "challenge-B3")
    check("update --as challenger: docs-stale means the docs are out of date, not the code",
          "B3: stale-docs" in out, out)
    out, _ = bugs("forward", "-w", ws5, "--limit", "3")
    plan5 = json.loads((ws5 / "forward.json").read_text())
    check("forward: the limit is for the whole run, so what's been challenged comes out of it",
          plan5["used"] == 2 and len(plan5["chosen"]) == 1, plan5)
    bugs("report", "-w", ws5)
    rep5 = (ws5 / "report.md").read_text(encoding="utf-8")
    check("report: docs out of date get their own section, for killer-docs",
          "## Docs out of date" in rep5 and "killer-docs" in rep5 and "describes the old flag" in rep5, rep5)
    check("report: the lead doesn't count as a separate finder",
          "**Found by:** 1 finder (read-one, lead)" in rep5, rep5)
    check("report: run details give the scripts' version", f"version {cfg5['script']}" in rep5, rep5)
    out, _ = bugs("findings", "-w", ws5)
    check("findings: a file name with dashes in it is kept whole",
          any(f["file"] == "Get-Thing.ps1" and f.get("line") == 3 for f in json.loads(out)), out)
    with open(proj / "calc.py", "a") as f:
        f.write("# a later change to the code\n")
    g(proj, "add", "calc.py")
    g(proj, "commit", "-q", "-m", "touch the code")
    out, _ = bugs("freshness", "-w", ws5, "--behind", "1")
    check("freshness: a doc that code commits have passed since it changed is flagged",
          re.search(r"README\.md\s+\S+\s+1 code commit since", out) and "Behind the code" in out, out)
    bugs("clean", "-w", ws5)

    # partial only when something the depth meant to settle is left unsettled
    out, _ = bugs("init", "--repo", proj, "--slug", "partial")
    ws6 = run_folder(out)
    out, _ = bugs("sandbox", "-w", ws6, "read-x")
    xb = box_path(out)
    (xb / "repro" / "low").mkdir()
    (xb / "repro" / "low" / "r.py").write_text(show("low"))
    (xb / "repro" / "reply.json").write_text(json.dumps({"candidates": [
        {"title": "minor", "claim": "a minor thing", "severity": "low", "command": [PY, "../repro/low/r.py"]}]}))
    bugs("collect", "-w", ws6, "--sandbox", "read-x", "--source", "hunter:read-x#1")
    bugs("report", "-w", ws6)
    check("summary: a standard run with only low bugs unchallenged is done, not partial",
          json.loads((ws6 / "summary.json").read_text())["status"] == "done", (ws6 / "summary.json").read_text())
    bugs("update", "-w", ws6, "B1", "--triage", "medium")
    bugs("report", "-w", ws6)
    check("summary: a medium bug left unchallenged makes it partial",
          json.loads((ws6 / "summary.json").read_text())["status"] == "partial")

    # one challenger for a group of bugs
    out, _ = bugs("sandbox", "-w", ws6, "read-y")
    yb = box_path(out)
    for name in ("a", "b"):
        (yb / "repro" / name).mkdir()
        (yb / "repro" / name / "r.py").write_text(show(name))
    (yb / "repro" / "reply.json").write_text(json.dumps({"candidates": [
        {"title": f"bug {n}", "claim": f"{n} breaks", "severity": "medium", "command": [PY, f"../repro/{n}/r.py"]}
        for n in ("a", "b")]}))
    bugs("collect", "-w", ws6, "--sandbox", "read-y", "--source", "hunter:read-y#1")
    out, _ = bugs("sandbox", "-w", ws6, "challenge-g1")
    gb = box_path(out)
    out, _ = bugs("repro", "-w", ws6, "B2", "B3", "--into", "challenge-g1")
    check("repro --into, several bugs: each one's reproduction, and a command for each",
          (gb / "repro" / "a" / "r.py").exists() and (gb / "repro" / "b" / "r.py").exists()
          and "B2:" in out and "B3:" in out, out)
    out, _ = bugs("repro", "-w", ws6, "B1", "B2", "B2", "--into", "challenge-g1", ok=(2,))
    check("repro --into: two bugs whose files share a name are refused, not overwritten", "both have" in out, out)
    (gb / "repro" / "reply.json").write_text(json.dumps({"verdicts": [
        {"id": "B2", "verdict": "stands", "reason": "real", "severity": "medium", "likelihood": "common"},
        {"id": "B3", "verdict": "knocked-down", "reason": "documented as intended"}]}))
    out, _ = bugs("challenges", "-w", ws6, "--sandbox", "challenge-g1")
    check("challenges: one challenger's verdicts for a group, one per bug",
          "B2: confirmed (medium/common)" in out and "B3: dismissed" in out and not gb.exists(), out)
    out, _ = bugs("sandbox", "-w", ws6, "challenge-g2")
    box_path(out).joinpath("repro", "reply.json").write_text(json.dumps({"verdicts": [
        {"id": "B1", "verdict": "stands"}, {"id": "B99", "verdict": "stands"}]}))
    out, _ = bugs("challenges", "-w", ws6, "--sandbox", "challenge-g2", ok=(2,))
    db6 = json.loads((ws6 / "bugs.json").read_text())["bugs"]
    check("challenges: an unknown bug in the group changes nothing at all", "no bug B99" in out
          and not db6[0].get("challenge"), (out, db6[0]))
    bugs("clean", "-w", ws6)

    # stand-ins: {sandbox} in --path-first, and a sandbox's own stand-ins ahead of the run's
    ok_stub = tmp / "stub-ok"
    ok_stub.mkdir()
    if os.name == "nt":
        (ok_stub / "gh.cmd").write_text("@echo project stub gh %*\r\n@exit /b 0\r\n")
    else:
        (ok_stub / "gh").write_text("#!/bin/sh\necho project stub gh \"$@\"\n")
        (ok_stub / "gh").chmod(0o755)
    out, _ = bugs("init", "--repo", proj, "--slug", "stubs", "--path-first", standin, "{sandbox}/own")
    ws7 = run_folder(out)
    check("init --path-first: a {sandbox} folder is kept as is, to be filled in per sandbox",
          "{sandbox}/own" in json.loads((ws7 / "config.json").read_text())["path_first"])
    out, _ = bugs("sandbox", "-w", ws7, "read-s", "--path-first", ok_stub)
    sb = box_path(out)
    r, code = runpy(sb, "--", "gh", "api", "x")
    check("sandbox --path-first: the sandbox's own stand-in comes before the run's", "project stub gh api x" in r, r)
    spec = json.loads((sb / "env.json").read_text())
    check("sandbox: {sandbox} in a PATH folder is that sandbox's own folder",
          any(p.replace("\\", "/") == f"{sb}/own".replace("\\", "/") for p in spec["path_first"]), spec["path_first"])
    (sb / "repro" / "reply.json").write_text(json.dumps({"candidates": [], "covered": [str(sb / "repo" / "calc.py")]}))
    bugs("collect", "-w", ws7, "--sandbox", "read-s", "--source", "hunter:read-s#1")
    out, _ = bugs("coverage", "-w", ws7)
    check("coverage: a file named by its path inside the sandbox counts as read", "hunter:read-s#1: 1 in scope" in out, out)
    bugs("clean", "-w", ws7)

    # ---------- a folder that isn't a git repo
    plain = tmp / "plain"
    (plain / "node_modules" / "x").mkdir(parents=True)
    (plain / "app.py").write_text("print(1)\n")
    (plain / "node_modules" / "x" / "i.js").write_text("1\n")
    out, _ = bugs("init", "--repo", plain)
    wp = run_folder(out)
    check("plain folder: scope skips dependency folders", (wp / "scope.txt").read_text().split() == ["app.py"], out)
    out, _ = bugs("sandbox", "-w", wp, "s1")
    check("plain folder: the sandbox is a copy", (box_path(out) / "repo" / "app.py").exists(), out)
    out, _ = bugs("check-fix", "-w", wp, "--", PY, "app.py", ok=(2,))
    check("plain folder: check-fix needs git", "needs a git repository" in out, out)
    bugs("clean", "-w", wp)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
