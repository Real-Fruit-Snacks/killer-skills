"""Scenario tests for the killer-council bookkeeping script. Run: python -I tests/test_council.py"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "killer-council" / "scripts" / "council.py"
failures = []


def run(*args, ok=(0,)):
    r = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8")
    out = r.stdout + r.stderr
    if r.returncode not in ok:
        raise AssertionError(f"exit {r.returncode} for {args}:\n{out}")
    return out, r.returncode


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + detail.replace("\n", "\n    "))


def scores(ws, rnd, items):
    d = ws / f"round-{rnd}" / "scores"
    d.mkdir(parents=True, exist_ok=True)
    for seat, score, conf in items:
        (d / f"{seat}.json").write_text(json.dumps(
            {"seat": seat, "score": score, "confidence": conf, "reasoning": "x"}), encoding="utf-8")


tmp = Path(tempfile.mkdtemp())
try:
    # ---------- fixed threshold: veto, progress, regression + restore, pass
    work = tmp / "proj"
    work.mkdir()
    readme = work / "README.md"
    readme.write_text("v1\n", encoding="utf-8")
    ws = tmp / "run-fixed"
    out, _ = run("init", "-w", ws, "--threshold", 85, "--target", "test readme",
                 "--files", readme, "--max-rounds", 6)
    check("init prints fixed threshold", "Threshold 85" in out, out)
    cfg = json.loads((ws / "config.json").read_text())
    plain = {k: v for k, v in cfg.items() if k not in ("files", "git")}   # paths and hashes can hold "85" by chance
    check("config does not hold the threshold in plain text", "85" not in json.dumps(plain), json.dumps(cfg))

    run("snapshot", "-w", ws, "-r", 1)
    scores(ws, 1, [("a", 70, "high"), ("b", 75, "medium"), ("c", 80, "high"), ("d", 60, "high")])
    out, _ = run("tally", "-w", ws, "-r", 1)
    t1 = json.loads((ws / "round-1" / "tally.json").read_text())
    check("r1 median 72.5", t1["median"] == 72.5, out)
    check("r1 veto from d (60 < 65)", t1["vetoes"] == ["d"], out)
    check("r1 CONTINUE", t1["decision"] == "CONTINUE", out)
    check("tally.json has no threshold", "85" not in json.dumps({k: v for k, v in t1.items() if k != "scores"}))

    # fixes for round 2: edit README, create a new file (tracked first)
    newfile = work / "CONTRIBUTING.md"
    run("track", "-w", ws, newfile)
    readme.write_text("v2\n", encoding="utf-8")
    newfile.write_text("new\n", encoding="utf-8")
    run("snapshot", "-w", ws, "-r", 2)
    man1 = json.loads((ws / "round-1" / "artifact" / "manifest.json").read_text())
    check("track backfilled round 1 manifest as absent",
          any(e["path"] == str(newfile.resolve()) and not e["exists"] for e in man1), json.dumps(man1))
    scores(ws, 2, [("a", 82, "high"), ("b", 84, "medium"), ("c", 86, "high"), ("d", 79, "high")])
    out, _ = run("tally", "-w", ws, "-r", 2)
    t2 = json.loads((ws / "round-2" / "tally.json").read_text())
    check("r2 median 83, no veto, CONTINUE",
          t2["median"] == 83 and not t2["vetoes"] and t2["decision"] == "CONTINUE", out)

    # round 3 regresses
    readme.write_text("v3 worse\n", encoding="utf-8")
    run("snapshot", "-w", ws, "-r", 3)
    scores(ws, 3, [("a", 75, "high"), ("b", 76, "medium"), ("c", 78, "high"), ("d", 74, "high")])
    out, _ = run("tally", "-w", ws, "-r", 3)
    t3 = json.loads((ws / "round-3" / "tally.json").read_text())
    check("r3 flags regression vs round 2", t3["regression_vs_round"] == 2 and "REGRESSION" in out, out)
    check("r3 not a plateau (r2 beat r1 by 10.5)", not t3["plateau"], out)
    check("r3 best round is 2", t3["best_round"] == 2, out)

    out, _ = run("restore", "-w", ws, "-r", 2)
    check("restore brings README back to v2", readme.read_text() == "v2\n", out)

    out, _ = run("restore", "-w", ws, "-r", 1)
    check("restore to r1 brings README to v1", readme.read_text() == "v1\n", out)
    check("restore to r1 lists the file created later, without deleting it",
          "CONTRIBUTING.md" in out and newfile.exists(), out)
    run("restore", "-w", ws, "-r", 2)

    run("snapshot", "-w", ws, "-r", 4)
    scores(ws, 4, [("a", 86, "high"), ("b", 88, "medium"), ("c", 85, "high"), ("d", 90, "high")])
    out, _ = run("tally", "-w", ws, "-r", 4)
    t4 = json.loads((ws / "round-4" / "tally.json").read_text())
    check("r4 PASS at median 87", t4["decision"] == "PASS" and t4["median"] == 87, out)

    ledger = (ws / "ledger.md").read_text()
    check("ledger has 4 rounds", all(f"| {i} |" in ledger for i in range(1, 5)), ledger)

    # holdout: overfit
    (ws / "holdout").mkdir()
    (ws / "holdout" / "score.json").write_text(json.dumps({"seat": "holdout", "score": 72, "confidence": "high"}))
    out, _ = run("holdout", "-w", ws, "-r", 4)
    check("holdout 72 vs 87 warns overfit", "OVERFIT WARNING" in out, out)
    (ws / "holdout" / "score.json").write_text(json.dumps({"seat": "holdout", "score": 84, "confidence": "high"}))
    out, _ = run("holdout", "-w", ws, "-r", 4)
    check("holdout 84 vs 87 is consistent", "Consistent" in out and "passes" not in out.split("does not")[0] or "does not pass" in out, out)

    # ---------- summary.json for killer-goal
    out, code = run("summary", "-w", ws, ok=(2,))
    check("summary: refuses before report.md is written", "write it first" in out, out)
    (ws / "report.md").write_text("# Council report\n", encoding="utf-8")
    run("decide", "-w", ws, "Kept the shorter intro both seats argued over")
    run("wait", "-w", ws, "Push the README", "--command", "git push")
    out, _ = run("summary", "-w", ws)
    summ = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
    check("summary: a pass the holdout scored under the threshold is partial, and says why",
          summ["skill"] == "killer-council" and summ["status"] == "partial"
          and summ["headline"] == "passed in round 4: median 87 against 85; holdout 84, below the threshold", summ)
    check("summary: counts every scored seat and the holdout as agents, with the decisions and waiting items",
          summ["numbers"]["agents"] == 17 and summ["numbers"]["rounds"] == 4 and summ["numbers"]["threshold"] == 85
          and summ["decisions"] == ["Kept the shorter intro both seats argued over"]
          and summ["waiting"] == ["Push the README: git push"] and summ["commits"] == [] and summ["branch"] is None, summ)
    check("summary: prints the one line to end with",
          out.strip().splitlines()[-1] == f"killer-council: partial · {summ['headline']} · {summ['report']}"
          and Path(summ["report"]) == (ws / "report.md").resolve(), out)   # macOS: /var is /private/var

    # ---------- lint
    briefs = ws / "round-5" / "briefs"
    briefs.mkdir(parents=True)
    (briefs / "clean.md").write_text(
        "# Review brief\n\nYour seat is **Newcomer**.\n\n=== ARTIFACT START ===\n"
        "This was revised in round 3 to hit 85.\n=== ARTIFACT END ===\n\n"
        "<!-- SCALE START -->\n- **70-89, solid:** fine\n<!-- SCALE END -->\n"
        "Return a score from 1 to 100.\n", encoding="utf-8")
    out, code = run("lint", "-w", ws, briefs / "clean.md")
    check("lint ignores artifact and scale blocks", code == 0, out)

    (briefs / "leaky.md").write_text(
        "This is the revised version.\nIt needs to score at least 85 this round.\n"
        "Be strict.\nAim for 85.\nScore of at least something.\n", encoding="utf-8")
    out, code = run("lint", "-w", ws, briefs / "leaky.md", ok=(1,))
    for frag in ["revised", "round", "threshold number", "tells the member what to score", "pushes"]:
        check(f"lint catches: {frag}", frag in out, out)

    (briefs / "unclosed.md").write_text("=== ARTIFACT START ===\nthreshold 85\n", encoding="utf-8")
    out, code = run("lint", "-w", ws, briefs / "unclosed.md", ok=(1,))
    check("lint reports an unclosed block", "never closes" in out, out)

    out, code = run("lint", "-w", ws, str(briefs / "*.md"), ok=(1,))
    check("lint expands a literal glob (PowerShell style)", "leaky.md" in out and "clean.md" not in out, out)

    trace = work / "trace.py"
    trace.write_text("# addressed review feedback from the council\nx = 1\n", encoding="utf-8")
    out, code = run("lint", "-w", ws, "--work", trace, ok=(1,))
    check("lint --work catches traces in the work", "trace in the work" in out, out)

    # ---------- sealed range
    ws2 = tmp / "run-range"
    out, _ = run("init", "-w", ws2, "--range", "80-90", "--files", readme)
    check("range init does not print the value", "sealed" in out and not any(
        f" {n}" in out.replace("80-90", "") for n in range(80, 91)), out)
    run("snapshot", "-w", ws2, "-r", 1)
    scores(ws2, 1, [("a", 70, "high"), ("b", 72, "high"), ("c", 71, "low")])
    out, _ = run("tally", "-w", ws2, "-r", 1)
    secret = int(run("reveal", "-w", ws2)[0].split("was ")[1].split(" ")[0])
    check("range tally output hides the floor", f"below {secret - 20}" not in out and "sealed" in out, out)
    check("secret within range", 80 <= secret <= 90, str(secret))

    # ---------- plateau and cap
    ws3 = tmp / "run-plateau"
    run("init", "-w", ws3, "--threshold", 90, "--files", readme)
    for r, s in [(1, 80), (2, 81), (3, 82)]:
        run("snapshot", "-w", ws3, "-r", r)
        scores(ws3, r, [("a", s, "high"), ("b", s, "high"), ("c", s, "high")])
        out, _ = run("tally", "-w", ws3, "-r", r)
    t = json.loads((ws3 / "round-3" / "tally.json").read_text())
    check("plateau after 80, 81, 82", t["decision"] == "STOP_PLATEAU", out)

    ws4 = tmp / "run-cap"
    run("init", "-w", ws4, "--threshold", 90, "--files", readme, "--max-rounds", 2)
    for r, s in [(1, 70), (2, 80)]:
        run("snapshot", "-w", ws4, "-r", r)
        scores(ws4, r, [("a", s, "high"), ("b", s, "high"), ("c", s, "high")])
        out, _ = run("tally", "-w", ws4, "-r", r)
    t = json.loads((ws4 / "round-2" / "tally.json").read_text())
    check("cap stops at max rounds", t["decision"] == "STOP_CAP", out)

    # ---------- low-confidence score below the veto line doesn't block
    ws5 = tmp / "run-lowconf"
    run("init", "-w", ws5, "--threshold", 80, "--files", readme)
    run("snapshot", "-w", ws5, "-r", 1)
    scores(ws5, 1, [("a", 85, "high"), ("b", 88, "high"), ("c", 50, "low")])
    out, _ = run("tally", "-w", ws5, "-r", 1)
    t = json.loads((ws5 / "round-1" / "tally.json").read_text())
    check("low-confidence low score does not veto", t["decision"] == "PASS" and t["low_confidence_vetoes"] == ["c"], out)

    # ---------- quoted score is accepted
    ws6 = tmp / "run-quoted"
    run("init", "-w", ws6, "--threshold", 80, "--files", readme)
    run("snapshot", "-w", ws6, "-r", 1)
    d = ws6 / "round-1" / "scores"
    d.mkdir(parents=True)
    (d / "a.json").write_text(json.dumps({"score": "74", "confidence": "high"}))
    (d / "b.json").write_text(json.dumps({"score": 90, "confidence": "high"}))
    out, _ = run("tally", "-w", ws6, "-r", 1)
    check("quoted score \"74\" accepted as 74", json.loads((ws6 / "round-1" / "tally.json").read_text())["min"] == 74, out)

    # ---------- verify: clean, changed file, clutter
    out, code = run("verify", "-w", ws6, "-r", 1)
    check("verify clean right after snapshot", code == 0 and "Unchanged" in out, out)
    original = readme.read_text()
    readme.write_text("member scribbled here\n", encoding="utf-8")
    (work / "__pycache__").mkdir()
    out, code = run("verify", "-w", ws6, "-r", 1, ok=(1,))
    check("verify catches a changed tracked file", "changed:" in out and "README.md" in out, out)
    check("verify lists clutter like __pycache__", "__pycache__" in out, out)
    readme.write_text(original, encoding="utf-8")
    (work / "__pycache__").rmdir()

    # ---------- lint flags a path into the run folder
    leak = tmp / "pathleak.md"
    leak.write_text(f"See {ws6 / 'round-1'} for details.\nAlso ~/.claude/council-runs/x\n", encoding="utf-8")
    out, code = run("lint", "-w", ws6, leak, ok=(1,))
    check("lint flags run-folder paths", out.count("points into the run folder") == 2, out)
    resolved = ws6.resolve()
    if str(resolved) != str(ws6):  # e.g. an 8.3 short temp path on Windows
        leak.write_text(f"See {resolved / 'x'}.\n", encoding="utf-8")
        out, code = run("lint", "-w", ws6, leak, ok=(1,))
        check("lint flags the resolved form of a short run-folder path", "points into the run folder" in out, out)

    # ---------- bad score file
    (ws5 / "round-2" / "scores").mkdir(parents=True)
    (ws5 / "round-2" / "scores" / "x.json").write_text(json.dumps({"score": 101}))
    out, code = run("tally", "-w", ws5, "-r", 2, ok=(2,))
    check("invalid score rejected", "1 to 100" in out, out)

    # ---------- summary: a stop, and the commits made on the branch killer-goal named
    out, _ = run("summary", "-w", ws3, ok=(2,))
    (ws3 / "report.md").write_text("# Council report\n", encoding="utf-8")
    out, _ = run("summary", "-w", ws3)
    s3 = json.loads((ws3 / "summary.json").read_text(encoding="utf-8"))
    check("summary: a plateau is partial, with the best round",
          s3["status"] == "partial" and s3["headline"].startswith("stopped when the scores stopped rising: best median 82 in round 3"), s3)
    repo = tmp / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                              capture_output=True, text=True, check=True).stdout
    git("init", "-q", "-b", "killer-goal/x")
    (repo / "plan.md").write_text("draft\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "draft")
    ws7 = tmp / "run-git"
    run("init", "-w", ws7, "--threshold", 80, "--files", repo / "plan.md")
    run("snapshot", "-w", ws7, "-r", 1)
    scores(ws7, 1, [("a", 85, "high"), ("b", 88, "high"), ("you", 90, "high")])
    sp = ws7 / "round-1" / "scores" / "you.json"
    sp.write_text(json.dumps({**json.loads(sp.read_text()), "source": "human"}), encoding="utf-8")
    run("tally", "-w", ws7, "-r", 1)
    (repo / "plan.md").write_text("final\n", encoding="utf-8")
    git("commit", "-q", "-am", "Plan: tighten the rollout steps")
    (ws7 / "report.md").write_text("# Council report\n", encoding="utf-8")
    out, _ = run("summary", "-w", ws7)
    s7 = json.loads((ws7 / "summary.json").read_text(encoding="utf-8"))
    check("summary: lists the commits made since init and the branch, and doesn't count the user's seat",
          s7["status"] == "done" and s7["branch"] == "killer-goal/x" and len(s7["commits"]) == 1
          and s7["commits"][0].endswith("Plan: tighten the rollout steps") and s7["numbers"]["agents"] == 2, s7)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
