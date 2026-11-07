"""Tests for handoff.mjs, the books of killer-design and killer-repo (tools/shared/handoff.mjs).

Run: python -I tests/test_handoff.py

Starts runs for a git project and a plain folder, records decisions and waiting items,
and finishes with summary.json: the folder killer-goal looks in (the Python kit's project
key), the commits made since start, a failing check that turns done into partial, and the
errors for a missing handoff and a bad status. Both skills' copies are run, since each
names itself from its own SKILL.md.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "skills" / "killer-design" / "scripts" / "handoff.mjs"
REPO = ROOT / "skills" / "killer-repo" / "scripts" / "handoff.mjs"
failures = []

spec = importlib.util.spec_from_file_location("killer_kit", ROOT / "tools" / "shared" / "kit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


tmp = Path(tempfile.mkdtemp())
ENV = dict(os.environ, KILLER_DESIGN_HOME=str(tmp / "design-runs"), KILLER_REPO_HOME=str(tmp / "repo-runs"))


def node(script, *args, ok=(0,)):
    r = subprocess.run(["node", str(script), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    out = r.stdout + r.stderr
    if r.returncode not in ok:
        raise AssertionError(f"exit {r.returncode} for {script.name} {' '.join(map(str, args))}:\n{out}")
    return out


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout


def run_folder(out):
    return Path(next(ln for ln in out.splitlines() if ln.startswith("Run folder: "))[len("Run folder: "):].strip())


try:
    check("both skills carry the same handoff.mjs", DESIGN.read_bytes() == REPO.read_bytes()
          == (ROOT / "tools" / "shared" / "handoff.mjs").read_bytes())

    site = tmp / "site"
    site.mkdir()
    g(site, "init", "-q", "-b", "main")
    (site / "index.html").write_text("<h1>v1</h1>\n", encoding="utf-8")
    g(site, "add", "-A")
    g(site, "commit", "-q", "-m", "first")

    # ---------- start
    out = node(DESIGN, "start", "--project", site / ".", "--target", "a landing page")
    ws = run_folder(out)
    top, _ = kit.find_top(site.resolve())
    check("start: the run sits where killer-goal looks, under KILLER_DESIGN_HOME and the kit's project key",
          ws.parent == tmp / "design-runs" / kit.project_key(top, True) and ws.name == f"{kit.today()}-site", ws)
    cfg = json.loads((ws / "config.json").read_text(encoding="utf-8"))
    check("start: records the skill, the project, its branch and HEAD",
          cfg["skill"] == "killer-design" and cfg["branch"] == "main"
          and cfg["head"] == g(site, "rev-parse", "HEAD").strip() and Path(cfg["project"]) == top, cfg)
    check("start: a second run the same day gets its own folder",
          run_folder(node(DESIGN, "start", "--project", site)).name == f"{kit.today()}-site-2")

    # ---------- decide, wait
    node(DESIGN, "decide", "-w", ws, "Picked the warmer palette of the two")
    out = node(DESIGN, "wait", "-w", ws, "Deploy the site", "--command", "netlify deploy --prod")
    check("wait: says what it recorded", "netlify deploy --prod" in out, out)
    out = node(DESIGN, "decide", "-w", ws, ok=(2,))
    check("decide: needs the text", "say what it is" in out, out)

    # ---------- finish
    out = node(DESIGN, "finish", "-w", ws, "--headline", "built the page", ok=(2,))
    check("finish: refuses before the handoff is written", "write it first" in out, out)
    (ws / "report.md").write_text("# Handoff\n", encoding="utf-8")
    out = node(DESIGN, "finish", "-w", ws, "--headline", "built the page", "--status", "finished", ok=(2,))
    check("finish: a status is done, partial or blocked", "done, partial or blocked" in out, out)

    shots = tmp / "shots"
    shots.mkdir()
    (shots / "page-390-first.png").write_bytes(b"png")
    (shots / "page-1440-first.png").write_bytes(b"png")
    (shots / "report.json").write_text(json.dumps({"summary": {
        "FAIL": ["@390 page scrolls sideways"], "WARN": ["@1440 text under 12px", "@390 LCP late"], "INFO": []}}))
    (site / "index.html").write_text("<h1>v2</h1>\n", encoding="utf-8")
    g(site, "commit", "-q", "-am", "Landing page: the new hero")
    out = node(DESIGN, "finish", "-w", ws, "--headline", "built the page", "--checks", shots)
    s = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
    check("finish: a failing check makes done partial, and says so",
          s["status"] == "partial" and s["headline"] == "built the page; 1 check still failing"
          and "FAIL @390 page scrolls sideways" in out, (s, out))
    check("finish: the suite's summary shape, with the commits since start and the branch",
          s["skill"] == "killer-design" and s["branch"] == "main" and len(s["commits"]) == 1
          and s["commits"][0].endswith("Landing page: the new hero") and Path(s["report"]) == ws / "report.md", s)
    check("finish: decisions, waiting items and the numbers the reports hold",
          s["decisions"] == ["Picked the warmer palette of the two"]
          and s["waiting"] == ["Deploy the site: netlify deploy --prod"]
          and s["numbers"] == {"checks_failing": 1, "checks_warning": 2, "screenshots": 2, "decisions": 1, "waiting": 1}, s)
    check("finish: ends with the one line to end with",
          out.strip().splitlines()[-1] == f"killer-design: partial · {s['headline']} · {ws / 'report.md'}", out)

    # ---------- killer-repo's copy: its own name, its own runs, audit.mjs's report
    plain = tmp / "plain"
    plain.mkdir()
    out = node(REPO, "start", "--project", plain)
    rws = run_folder(out)
    check("killer-repo's copy names itself, and a folder that isn't a git repo works",
          rws.parent.parent == tmp / "repo-runs" and rws.name == f"{kit.today()}-repo-page"
          and rws.parent.name == kit.project_key(plain.resolve(), False), rws)
    audit = tmp / "audit.json"
    audit.write_text(json.dumps({"fail": [], "warn": ["no social preview"], "info": []}))
    (rws / "report.md").write_text("# Handoff\n", encoding="utf-8")
    out = node(REPO, "finish", "-w", rws, "--headline", "README rewritten", "--checks", audit)
    s = json.loads((rws / "summary.json").read_text(encoding="utf-8"))
    check("finish: a clean audit stays done, with no commits outside git",
          s["skill"] == "killer-repo" and s["status"] == "done" and s["commits"] == [] and s["branch"] is None
          and s["numbers"]["checks_warning"] == 1 and s["numbers"]["checks_failing"] == 0, s)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
