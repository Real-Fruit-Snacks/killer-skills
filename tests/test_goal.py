"""Scenario tests for killer-goal's goal.py. Run: python -I tests/test_goal.py

Most commands run in this process (goal.py's main(), with its output captured), because a
Python start costs most of a second on a busy Windows machine; the keep-going hook also runs
as a real process, the way Claude Code runs it. Nothing here touches the real ~/.claude:
HOME, USERPROFILE, CLAUDE_CONFIG_DIR, KILLER_GOAL_HOME and KILLER_GOAL_SANDBOXES all point
into a temp folder, and the real settings.json is compared before and after."""
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
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
GOAL = ROOT / "skills" / "killer-goal" / "scripts" / "goal.py"
PY = sys.executable
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


def fingerprint_of(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


REAL_CLAUDE = Path.home() / ".claude"
REAL_SETTINGS = REAL_CLAUDE / "settings.json"
real_before = fingerprint_of(REAL_SETTINGS)
real_goal_home = (REAL_CLAUDE / "killer-goal").exists()

tmp = Path(tempfile.mkdtemp())
(tmp / "home").mkdir()
os.environ.update(KILLER_GOAL_HOME=str(tmp / "runs"), KILLER_GOAL_SANDBOXES=str(tmp / "boxes"),
                  CLAUDE_CONFIG_DIR=str(tmp / "claude"), HOME=str(tmp / "home"), USERPROFILE=str(tmp / "home"),
                  KILLER_TESTS_HOME=str(tmp / "sub-tests"), PYTHONIOENCODING="utf-8", GIT_TERMINAL_PROMPT="0")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "KILLER_GOAL_NOW", "KILLER_GOAL_GH",
          "KILLER_GOAL_SETTINGS"):
    os.environ.pop(k, None)

spec = importlib.util.spec_from_file_location("killer_goal_under_test", GOAL)
goalmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(goalmod)


def goal(*args, ok=(0,), cwd=None, now=None):
    """Run goal.py's main() here and return (output, exit code)."""
    out, err = io.StringIO(), io.StringIO()
    old = os.getcwd()
    if now:
        os.environ["KILLER_GOAL_NOW"] = now
    try:
        if cwd:
            os.chdir(cwd)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                goalmod.main([str(x) for x in args])
                code = 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else (0 if e.code is None else 2)
    finally:
        os.chdir(old)
        if now:
            os.environ.pop("KILLER_GOAL_NOW", None)
    text = out.getvalue() + err.getvalue()
    if code not in ok:
        raise AssertionError(f"exit {code} for goal.py {' '.join(map(str, args))}:\n{text}")
    return text, code


def hook(payload, now=None):
    """The Stop hook, in this process: what it prints for this stdin."""
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    old_stdin = sys.stdin
    sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    try:
        text, code = goal("hook", "stop", now=now)
    finally:
        sys.stdin = old_stdin
    return text, code


def hook_proc(payload, env_extra=None):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    t0 = time.monotonic()
    r = subprocess.run([PY, "-I", str(GOAL), "hook", "stop"], input=raw, capture_output=True,
                       env=dict(os.environ, **(env_extra or {})))
    return r.stdout.decode("utf-8", "replace"), r.returncode, time.monotonic() - t0, r.stderr.decode("utf-8", "replace")


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *map(str, args)], check=True, capture_output=True,
                          text=True).stdout


def sha(cwd, ref):
    return g(cwd, "rev-parse", ref).strip()


def run_folder(out):
    return Path(re.search(r"Run folder: (.+)", out).group(1).strip())


def lead_token(out):
    return re.search(r"Lead session marker: (\S+)", out).group(1)


def ledger(ws):
    return json.loads((ws / "goal.json").read_text(encoding="utf-8"))


def make_repo(path, origin=None):
    path.mkdir(parents=True)
    g(path, "init", "-q", "-b", "main")
    g(path, "config", "user.name", "Test Person")
    g(path, "config", "user.email", "test@example.com")
    g(path, "config", "commit.gpgsign", "false")
    (path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (path / "tests").mkdir()
    (path / "tests" / "test_calc.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom calc import add\nassert add(2, 3) == 5\nprint('ok')\n")
    (path / "README.md").write_text("# calc\n")
    g(path, "add", "-A")
    g(path, "commit", "-q", "-m", "First version")
    if origin:
        g(path, "remote", "add", "origin", origin)
        g(path, "push", "-q", "origin", "main")
    return path


EXPORT_TEST = ("import sys\nsys.path.insert(0, '.')\nimport calc\n"
               "got = calc.export_csv([(1, 2), (3, 4)]) if hasattr(calc, 'export_csv') else None\n"
               "print('EXPECTED: a,b rows')\nprint(f'OBSERVED: {got!r}')\nsys.exit(0 if got == '1,2\\n3,4\\n' else 1)\n")
EXPORT_CODE = ("\n\ndef export_csv(rows):\n    return ''.join(','.join(map(str, r)) + '\\n' for r in rows)\n")

t_start = time.monotonic()
try:
    origin = tmp / "origin.git"
    origin.mkdir()
    g(origin, "init", "-q", "--bare")
    proj = make_repo(tmp / "proj", origin.as_posix())
    start_sha = sha(proj, "main")
    g(proj, "branch", "feature")
    g(proj, "branch", "killer-bugs/old")
    g(proj, "tag", "v0.1")
    (proj / "README.md").write_text("# calc\nstashed edit\n")
    g(proj, "stash", "push", "-q", "-m", "the user's stash")
    stash_before = g(proj, "stash", "list")
    (proj / "README.md").write_text("# calc\nA line I haven't committed.\n")
    (proj / "notes.txt").write_text("untracked notes\n")
    (proj / "staged.txt").write_text("staged\n")
    g(proj, "add", "staged.txt")
    skills = tmp / "claude" / "skills" / "killer-tests"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: killer-tests\ndescription: x\n---\n")

    # ---------- init: the branch, the carried work, the marker
    out, _ = goal("init", "--repo", proj, "--goal", "Add CSV export with a test", "--hours", "10")
    ws = run_folder(out)
    G = ledger(ws)
    branch = G["branch"]
    check("init: the run folder sits under KILLER_GOAL_HOME", ws.is_relative_to(tmp / "runs"), ws)
    check("init: the goal branch is made and checked out",
          branch.startswith("killer-goal/") and g(proj, "branch", "--show-current").strip() == branch, out)
    check("init: the user's branch is exactly where it was", sha(proj, "main") == start_sha)
    check("init: uncommitted work becomes the branch's first commit, nothing left behind",
          g(proj, "log", "-1", "--format=%s").strip() == "Work in progress from before the run"
          and set(g(proj, "show", "--name-only", "--format=", "HEAD").split()) == {"README.md", "notes.txt", "staged.txt"}
          and g(proj, "status", "--porcelain").strip() == "" and G["wip"] == sha(proj, "HEAD"), out)
    check("init: the stash is left alone", g(proj, "stash", "list") == stash_before)
    marker = tmp / "runs" / "active" / f"{G['run_id']}.json"
    check("init: the active marker names the project and the run",
          marker.is_file() and json.loads(marker.read_text())["workspace"] == str(ws), out)
    check("init: ends with the next action", "Next: write the done-when checks" in out, out)
    out, code = goal("init", "--repo", proj, "--goal", "again", ok=(2,))
    check("init: a second run in the same project is refused while one is going", "already going" in out, out)

    proj2 = make_repo(tmp / "proj2")
    (proj2 / "calc.py").write_text("# dirty\n")
    out, code = goal("init", "--repo", proj2, "--goal", "x", "--dirty", "stop", ok=(2,))
    check("init --dirty stop: refuses, and changes nothing",
          "uncommitted changes" in out and g(proj2, "branch", "--show-current").strip() == "main"
          and g(proj2, "status", "--porcelain").strip() != "" and "killer-goal" not in g(proj2, "branch"), out)
    plain = tmp / "plain"
    plain.mkdir()
    out, code = goal("init", "--repo", plain, "--goal", "x", ok=(2,))
    check("init: a folder that isn't a git repository is refused", "git repository" in out, out)

    # ---------- done-when checks and the lock
    goal("check", "add", "-w", ws, "The README shows how to export CSV, with an example that works", "--kind", "judge")
    out, code = goal("lock", "-w", ws, ok=(2,))
    check("lock: refuses without a command check", "at least one command check" in out, out)
    out, _ = goal("check", "add", "-w", ws, "export_csv writes one line per row", "--", PY, "tests/test_export.py")
    check("check add: a command check, and a note that its test isn't written yet",
          "Added C2 (command)" in out and "tests/test_export.py isn't there yet" in out, out)
    out, code = goal("check", "add", "-w", ws, "has a command", "--kind", "command", ok=(2,))
    check("check add: a command check with no command is refused", "needs its command" in out, out)
    (proj / "tests" / "test_export.py").write_text(EXPORT_TEST)
    out, code = goal("lock", "-w", ws, ok=(2,))
    check("lock: the acceptance test must be committed on the branch first", "aren't committed" in out, out)
    g(proj, "add", "-A")
    g(proj, "commit", "-q", "-m", "Add an acceptance test for CSV export")
    out, _ = goal("lock", "-w", ws)
    check("lock: locks the checks and the acceptance test's contents",
          "Locked 2 done-when checks (1 command, 1 judge)" in out and "tests/test_export.py" in out, out)
    out, _ = goal("check", "list", "-w", ws)
    check("check list: both checks, locked", "C1" in out and "C2" in out and "locked" in out, out)
    out, code = goal("check", "amend", "-w", ws, "C2", "--statement", "something easier", ok=(2,))
    check("check amend: after the lock it needs a reason", "needs --reason" in out, out)
    goal("check", "amend", "-w", ws, "C2", "--statement", "export_csv writes one line per row, in order",
         "--reason", "the user said order matters")
    am = ledger(ws)["amendments"]
    check("check amend: recorded with its reason, before the run started",
          len(am) == 1 and am[0]["reason"] == "the user said order matters" and am[0]["phase"] == "before the run started", am)
    out, code = goal("checks", "run", "-w", ws, ok=(1,))
    check("checks run: the acceptance test fails before the work, in a sandbox from the goal branch",
          "C2" in out and "FAIL" in out and "0 of 1 command checks pass" in out and "expected" in out, out)
    c2 = [c for c in ledger(ws)["checks"] if c["id"] == "C2"][0]
    check("checks run: the first result is marked as before the work",
          c2["results"][0]["before_work"] is True and c2["results"][0]["sha"] == sha(proj, branch), c2["results"])

    # ---------- the plan
    goal("plan", "add", "-w", ws, "Write the CSV export", "--moves", "C2")
    goal("plan", "add", "-w", ws, "Tests for the branch's changes", "--skill", "killer-tests", "--after", "S1",
         "--answers", "Scope: this branch's changes. Goal: close the gaps. Size: 100. After: the goal branch.", "--agents", "~6")
    goal("plan", "add", "-w", ws, "Bug hunt", "--skill", "killer-bugs", "--after", "S2", "--agents", "10")
    out, _ = goal("plan", "add", "-w", ws, "Docs", "--skill", "killer-imaginary")
    check("plan add: says when a planned skill isn't installed", "isn't installed" in out, out)
    out, code = goal("plan", "add", "-w", ws, "Later", "--after", "S9", ok=(2,))
    check("plan add: a step after one that doesn't exist is refused", "no step S9" in out, out)
    out, code = goal("plan", "edit", "-w", ws, "S1", "--after", "S3", ok=(2,))
    check("plan edit: a dependency loop is refused", "loop" in out, out)
    out, code = goal("next", "-w", ws, ok=(1,))
    check("next: STOP before the run starts, naming what's left", out.startswith("STOP: the run hasn't started")
          and "go" in out, out)

    # ---------- the hook before go, then go
    transcript = tmp / "lead.jsonl"
    transcript.write_text(json.dumps({"type": "user", "text": "start"}) + "\n")
    lead = {"session_id": "lead-session", "cwd": str(proj), "stop_hook_active": False, "transcript_path": str(transcript)}
    out, code = hook(lead)
    check("hook: before go, the turn ends normally (so the user can look at the plan)", out == "" and code == 0, out)
    out, _ = goal("go", "-w", ws)
    token = lead_token(out)
    check("go: starts the clock and announces the run in one line",
          out.startswith("Started: 4 steps") and "nothing pushed" in out, out)
    G = ledger(ws)
    deadline = dt.datetime.fromisoformat(G["deadline"])
    work_until = dt.datetime.fromisoformat(G["work_until"])
    check("go: a 10-hour run keeps the last 45 minutes for the wrap-up",
          abs((deadline - work_until).total_seconds() - 45 * 60) < 2, (G["deadline"], G["work_until"]))
    transcript.write_text(transcript.read_text() + json.dumps({"tool_result": out}) + "\n")
    other_t = tmp / "other.jsonl"
    other_t.write_text(json.dumps({"tool_result": "killer-goal status says ..."}) + "\n")
    out, code = hook({**lead, "session_id": "someone-else", "transcript_path": str(other_t)})
    check("hook: a session whose transcript doesn't have the lead's marker isn't blocked or given the run",
          out == "" and json.loads(marker.read_text())["session"] is None, out)
    out, code = hook(lead)
    blocked = json.loads(out) if out.strip() else {}
    check("hook: the lead's first stop claims the run and is blocked with what to do",
          blocked.get("decision") == "block" and "killer-goal is still running 'Add CSV export with a test'" in blocked.get("reason", "")
          and "4 steps left" in blocked["reason"] and "status" in blocked["reason"]
          and json.loads(marker.read_text())["session"] == "lead-session", out)
    out, code = hook({**lead, "session_id": "someone-else", "transcript_path": str(transcript)})
    check("hook: once claimed, another session is left alone", out == "", out)
    for bad in (b"not json at all", b"", b"[1, 2]", json.dumps({"cwd": str(proj)}).encode()):
        out, code = hook(bad)
        check(f"hook: bad stdin {bad[:12]!r} lets the turn end, prints nothing", out == "" and code == 0, out)
    out, code = hook({"session_id": "stranger", "cwd": str(tmp / "plain"), "transcript_path": ""})
    check("hook: a session with no run is left alone", out == "", out)

    # ---------- the loop: S1, direct work
    out, _ = goal("next", "-w", ws)
    check("next: hands out S1 first, with the instructions for direct work",
          out.startswith("NEXT S1: Write the CSV export") and "Direct work" in out and "step done" in out, out)
    goal("step", "start", "-w", ws, "S1")
    with open(proj / "calc.py", "a") as f:
        f.write(EXPORT_CODE)
    g(proj, "commit", "-qam", "Add export_csv")
    out, _ = goal("step", "done", "-w", ws, "S1", "--headline", "export_csv added; the acceptance test passes")
    check("step done: records the step's commits", "S1 done" in out and "1 commit on" in out, out)
    out, code = goal("next", "-w", ws, ok=(1,))
    check("next: the guard comes first after a step", out.startswith("FIRST: run the guard"), out)
    out, code = goal("step", "start", "-w", ws, "S2", ok=(2,))
    check("step start: refused until the guard has run", "FIRST: run the guard" in out, out)
    out, _ = goal("guard", "-w", ws)
    check("guard: all well after a step", "Guard OK" in out and "main is untouched" in out, out)
    out, code = goal("next", "-w", ws, ok=(1,))
    check("next: then the done-when checks", out.startswith("FIRST: run the done-when checks"), out)
    out, _ = goal("checks", "run", "-w", ws)
    check("checks run: the acceptance test passes now", "1 of 1 command checks pass" in out, out)

    # ---------- the guard catches what moved
    wip = G["wip"]
    g(proj, "update-ref", "refs/heads/main", wip)
    out, code = goal("guard", "-w", ws, ok=(1,))
    check("guard: the user's branch moving fails, with how to put it back",
          "Your branch main moved" in out and f"git branch -f main {start_sha}" in out, out)
    out, code = goal("next", "-w", ws, ok=(1,))
    check("next: refuses a step while the guard has a problem", out.startswith("FIRST: the last guard found a problem"), out)
    g(proj, "update-ref", "refs/heads/main", start_sha)
    g(proj, "update-ref", "refs/remotes/origin/main", wip)
    out, code = goal("guard", "-w", ws, ok=(1,))
    check("guard: a remote-tracking ref changing is reported loudly as a push or fetch",
          "REMOTE-TRACKING REFS CHANGED" in out and "pushed or fetched" in out and "origin/main" in out, out)
    g(proj, "update-ref", "refs/remotes/origin/main", start_sha)
    (Path(proj) / ".git" / "FETCH_HEAD").write_text(f"{start_sha}\t\tbranch 'main' of somewhere\n")
    out, code = goal("guard", "-w", ws, ok=(1,))
    check("guard: a fetch that moved nothing is still noticed (FETCH_HEAD)", "FETCH_HEAD changed" in out, out)
    (Path(proj) / ".git" / "FETCH_HEAD").unlink()
    g(proj, "checkout", "-q", "main")
    out, code = goal("guard", "-w", ws, ok=(1,))
    check("guard: HEAD off the goal branch fails", "HEAD is on main" in out, out)
    g(proj, "checkout", "-q", branch)
    (proj / "calc.py").write_text((proj / "calc.py").read_text() + "# scratch\n")
    g(proj, "stash", "push", "-q", "-m", "a step's stash")
    out, code = goal("guard", "-w", ws, ok=(1,))
    check("guard: a new stash entry fails", "The stash changed" in out, out)
    g(proj, "stash", "pop", "-q")
    g(proj, "checkout", "--", "calc.py")
    g(proj, "tag", "v9")
    g(proj, "branch", "scratch-branch")
    g(proj, "branch", "killer-tests/2026-11-07", branch)
    out, code = goal("guard", "-w", ws, ok=(1,))
    check("guard: a new tag and a new branch fail; a killer-* branch is only listed",
          "Tags changed: v9" in out and "A new branch scratch-branch appeared" in out
          and "note: New branch killer-tests/2026-11-07 from a sub-skill" in out, out)
    g(proj, "tag", "-d", "v9")
    g(proj, "branch", "-D", "scratch-branch")
    (proj / "README.md").write_text((proj / "README.md").read_text() + "Export with export_csv(rows).\n")
    g(proj, "commit", "-qam", "Document export_csv\n\nCo-Authored-By: Someone <noreply@anthropic.com>")
    out, code = goal("guard", "-w", ws, ok=(1,))
    check("guard: AI attribution in a commit on the branch fails", "AI attribution" in out, out)
    g(proj, "commit", "-q", "--amend", "-m", "Document export_csv")
    g(proj, "branch", "-D", "killer-tests/2026-11-07")
    out, _ = goal("guard", "-w", ws)
    check("guard: OK again once everything is put back", "Guard OK" in out, out)

    # ---------- S2: a skill step, recovery from status, blocked
    goal("checks", "run", "-w", ws)   # the amend above changed the branch; the loop wants checks before next
    out, _ = goal("next", "-w", ws)
    check("next: a skill step comes with the skill, its answers and the exact unattended text",
          out.startswith("NEXT S2") and "killer-tests (installed" in out
          and f"Running unattended for killer-goal (run {ws.as_posix()})" in out
          and f"Work on the current branch, {branch}, which killer-goal made for this run" in out
          and "Scope: this branch's changes" in out and "never push, publish, tag" in out, out)
    out, _ = goal("step", "start", "-w", ws, "S2")
    token2 = lead_token(out)
    kitmod = goalmod.kit()
    sub = tmp / "sub-tests" / kitmod.project_key(proj, True) / "2026-11-07-branch"
    sub.mkdir(parents=True)
    (sub / "config.json").write_text("{}")
    (sub / "summary.json").write_text(json.dumps({
        "skill": "killer-tests", "status": "done", "headline": "Mutation score 41% -> 88%: 6 tests added",
        "report": str(sub / "report.md"), "branch": branch, "commits": [],
        "decisions": ["Left two survivors as equivalent mutants"],
        "waiting": [{"text": "Publish the coverage badge", "command": "gh workflow run badge.yml"}],
        "numbers": {"agents": 5}}))
    out, code = goal("status", cwd=proj / "tests")
    check("status with no -w: finds the run from a folder inside the project",
          code == 0 and f"killer-goal:{G['run_id']}" in out and "S2   running" in out, out)
    check("status: names the step's setup answers and its probable run folder, and says it finished",
          "setup answers: Scope: this branch's changes" in out and str(sub) in out and "summary.json is there" in out, out)
    check("status: the last line is the next action, with the exact command",
          out.strip().splitlines()[-1].startswith("Next: finish S2") and "step done" in out.strip().splitlines()[-1], out)
    out, code = goal("status", cwd=tmp, ok=(2,))
    check("status with no -w outside any run says so", "No killer-goal run is going" in out, out)
    goal("step", "blocked", "-w", ws, "S2", "--reason", "needs the user to pick a test framework")
    goal("guard", "-w", ws)
    goal("checks", "run", "-w", ws)
    out, _ = goal("next", "-w", ws)
    check("next: a step after a blocked one waits; an independent one goes ahead, flagged as not installed",
          out.startswith("NEXT S4") and "killer-imaginary is NOT INSTALLED" in out, out)
    goal("step", "skipped", "-w", ws, "S4", "--reason", "killer-imaginary is not installed")
    out, _ = goal("next", "-w", ws)
    check("next: NONE when nothing can run, saying what waits on what",
          out.startswith("NONE: nothing left that can run") and "S3 waits on S2 (blocked)" in out, out)
    out, _ = goal("step", "done", "-w", ws, "S2", "--summary", sub / "summary.json")
    check("step done --summary: takes the sub-run's headline, decisions and waiting items",
          "S2 done: Mutation score 41% -> 88%" in out and "1 decisions, 1 waiting" in out, out)
    check("step done --summary: the sub-run's agent count is added", ledger(ws)["agents_used"] == 5, ledger(ws)["agents_used"])
    out, code = goal("step", "done", "-w", ws, "S3", "--summary", tmp / "nowhere.json", ok=(2,))
    check("step done: a missing summary.json is refused", "no summary" in out, out)
    badsum = tmp / "bad-summary.json"
    badsum.write_text(json.dumps({"status": "finished"}))
    out, code = goal("step", "done", "-w", ws, "S3", "--summary", badsum, ok=(2,))
    check("step done: a malformed summary.json is refused, saying why", "status should be" in out and "no headline" in out, out)
    goal("guard", "-w", ws)
    goal("checks", "run", "-w", ws)

    # ---------- S3: fails, retries with a different approach, fails again
    goal("step", "start", "-w", ws, "S3")
    out, _ = goal("step", "failed", "-w", ws, "S3", "--reason", "the hunt timed out")
    check("step failed: the first failure gets one retry", "one more try" in out, out)
    goal("guard", "-w", ws)
    goal("checks", "run", "-w", ws)
    out, _ = goal("next", "-w", ws)
    check("next: hands the step out again, saying why the last try failed",
          out.startswith("NEXT S3") and "the last try failed: the hunt timed out" in out, out)
    out, code = goal("step", "start", "-w", ws, "S3", ok=(2,))
    check("step start: a retry needs a different approach", "--approach" in out, out)
    goal("step", "start", "-w", ws, "S3", "--approach", "scope: this branch's changes only")
    out, _ = goal("step", "failed", "-w", ws, "S3", "--reason", "timed out again")
    check("step failed: a second failure marks it failed", "marked failed" in out, out)
    goal("guard", "-w", ws)
    goal("checks", "run", "-w", ws)
    out, _ = goal("next", "-w", ws)
    check("next: NONE once every step is done, failed or skipped",
          out.startswith("NONE: all steps done") and "final check" in out, out)

    # ---------- the budget
    late = (deadline + dt.timedelta(minutes=1)).isoformat()
    out, code = goal("budget", "-w", ws, now=late, ok=(1,))
    check("budget: STOP once the deadline has passed", "STOP: time's up" in out, out)
    goal("plan", "add", "-w", ws, "One more thing", "--reason", "found late")
    out, code = goal("next", "-w", ws, now=late, ok=(1,))
    check("next: refuses to hand out a step once time's up, and says how to wrap up",
          out.startswith("STOP: time's up") and "final check" in out, out)
    out, code = goal("budget", "-w", ws, "--cap", "20", ok=(1,))
    check("budget: STOP when the agent cap is reached (one kept for the judge)",
          "STOP: the agent budget is spent" in out and ledger(ws)["agents_used"] >= 19, out)
    out, _ = goal("budget", "-w", ws, "--cap", "0")
    check("budget: OK again with no cap; the change is a decision", out.strip().endswith("OK")
          and any("Agent cap changed" in d["text"] for d in ledger(ws)["decisions"]), out)

    # ---------- the keep-going hook: stall guard, wrap-up, pause, deadline, real process
    out, _ = goal("resume", "-w", ws)
    token = lead_token(out)
    transcript.write_text(transcript.read_text() + json.dumps({"tool_result": out}) + "\n")
    outs = [hook(lead)[0] for _ in range(4)]
    check("hook: blocks three times with nothing changing, then lets the turn end (stall guard)",
          all(json.loads(o)["decision"] == "block" for o in outs[:3]) and outs[3] == "", outs)
    H = json.loads((ws / "hook.json").read_text())
    check("hook: the stall is recorded with the time and why", H["stalled"] and "didn't change" in H["stalled"]["reason"], H)
    out, _ = hook(lead)
    check("hook: stays quiet while still stalled", out == "", out)
    out, _ = goal("status", "-w", ws)
    check("status: says the hook stalled", "STALLED" in out, out)
    goal("decide", "-w", ws, "Kept the CSV header out, as the README example has none")
    out, _ = hook(lead)
    check("hook: blocks again once the ledger moves", out and json.loads(out)["decision"] == "block", out)
    out, _ = goal("resume", "-w", ws)
    transcript.write_text(transcript.read_text() + json.dumps({"tool_result": out}) + "\n")
    results = []
    for i in range(13):
        goal("decide", "-w", ws, f"noise {i}")
        results.append(hook(lead)[0])
    check("hook: twelve blocks in a row with no step starting or finishing lets the turn end",
          all(r for r in results[:12]) and results[12] == ""
          and "without a step starting or finishing" in json.loads((ws / "hook.json").read_text())["stalled"]["reason"],
          [bool(r) for r in results])
    out, _ = goal("resume", "-w", ws)
    token = lead_token(out)
    transcript.write_text(transcript.read_text() + json.dumps({"tool_result": out}) + "\n")
    wrap = (work_until + dt.timedelta(minutes=5)).isoformat()
    out, _ = hook(lead, now=wrap)
    reason = json.loads(out)["reason"] if out else ""
    check("hook: in the wrap-up window it still blocks, pointing at the final check",
          "wrapping up" in reason and "checks run" in reason and "--final" in reason, out)
    out, _ = hook(lead, now=late)
    check("hook: past the deadline it lets the turn end", out == "", out)
    goal("pause", "-w", ws, "--reason", "waiting for the user")
    out, _ = hook(lead)
    check("hook: paused means the turn ends normally", out == "", out)
    out, code = goal("next", "-w", ws, ok=(1,))
    check("next: STOP while paused", out.startswith("STOP: paused"), out)
    out, _ = goal("resume", "-w", ws)
    check("resume: the next stop from the old marker isn't the lead any more",
          json.loads(marker.read_text())["session"] is None and lead_token(out) != token, out)
    token = lead_token(out)
    out, _ = hook(lead)
    check("hook: after resume, a transcript with only the old marker doesn't claim the run", out == "", out)
    transcript.write_text(transcript.read_text() + json.dumps({"tool_result": f"... {token} ..."}) + "\n")
    out, code, secs, err = hook_proc(lead)
    check("hook as a real process: reads stdin, prints the block JSON, exits 0, quickly",
          code == 0 and json.loads(out)["decision"] == "block" and secs < 8 and err == "", (out, code, secs, err))
    out, code, secs, err = hook_proc(b"{broken")
    check("hook as a real process: bad stdin prints nothing and exits 0", code == 0 and out == "" and err == "", (out, err))
    goal("resume", "-w", ws)
    out, _ = hook({"session_id": "cwd-lead", "cwd": str(proj / "tests"), "transcript_path": ""})
    check("hook: with no transcript to read, a session in the project claims the run (the documented fallback)",
          out and json.loads(out)["decision"] == "block" and json.loads(marker.read_text())["session"] == "cwd-lead", out)

    # ---------- the final check, the blind judge, the report
    goal("plan", "drop", "-w", ws, "S5", "--reason", "out of time")
    out, _ = goal("checks", "run", "-w", ws, "--final")
    check("checks run --final: every command check passes", "(final)" in out and "1 of 1 command checks pass" in out, out)
    out, _ = goal("judge-brief", "-w", ws, "--forbid", "install.ps1: it writes the PATH")
    brief = (ws / "judge-brief.md").read_text(encoding="utf-8")
    jbox = Path(re.search(r"\*\*Project copy:\*\* `(.+?)`", brief).group(1)).parent
    check("judge-brief: the goal in the user's words, every check, the sandbox and the forbidden command",
          "> Add CSV export with a test" in brief and "**C1**" in brief and "**C2**" in brief
          and "tests/test_export.py" in brief and "install.ps1: it writes the PATH" in brief
          and (jbox / "repo" / "tests" / "test_export.py").is_file(), brief)
    check("judge-brief: blind: no run folder, plan, steps, headlines or check results",
          str(ws) not in brief and ws.as_posix() not in brief and "Write the CSV export" not in brief
          and "Mutation score" not in brief and "Bug hunt" not in brief and "pass" not in brief.split("## How to work")[0].lower().replace("passes", ""),
          brief)
    check("judge-brief: the judge's copy is the branch's committed work, with history to diff",
          g(jbox / "repo", "cat-file", "-t", G["base"]).strip() == "commit"
          and "export_csv" in (jbox / "repo" / "calc.py").read_text(), jbox)
    out, code = goal("judge", "-w", ws, "--sandbox", "judge", ok=(2,))
    check("judge: no reply.json is refused, with what to do", "no reply" in out and "fresh judge" in out, out)
    (jbox / "scratch" / "reply.json").write_text(json.dumps({"checks": [
        {"id": "C2", "verdict": "met", "evidence": "ran tests/test_export.py: exit 0"}]}))
    out, code = goal("judge", "-w", ws, "--sandbox", "judge", ok=(2,))
    check("judge: a reply that skips a check is refused", "doesn't judge C1" in out, out)
    (jbox / "scratch" / "reply.json").write_text(json.dumps({"checks": [
        {"id": "C1", "verdict": "maybe", "evidence": "x"}, {"id": "C2", "verdict": "met", "evidence": "y"}]}))
    out, code = goal("judge", "-w", ws, "--sandbox", "judge", ok=(2,))
    check("judge: an unknown verdict is refused", "not met, not-met or unclear" in out, out)
    (jbox / "scratch" / "reply.json").write_text(json.dumps({"checks": [
        {"id": "C1", "verdict": "met", "evidence": "README.md line 3 shows export_csv(rows); ran it, got two lines"},
        {"id": "C2", "verdict": "met", "evidence": "ran tests/test_export.py: OBSERVED '1,2\\n3,4\\n', exit 0; it asserts the exact text"}],
        "goal": {"verdict": "met", "missing": []}, "notes": "none"}))
    out, _ = goal("judge", "-w", ws, "--sandbox", "judge")
    check("judge: a good reply is read; the result is met; the sandbox is deleted",
          "2 of 2 checks met" in out and "Result so far: met" in out and not jbox.exists(), out)
    out, _ = goal("status", "-w", ws)
    check("status: in the wrap-up, the next action is the report", "Next: write the report" in out, out)
    out, _ = goal("report", "-w", ws)
    report = (ws / "report.md").read_text(encoding="utf-8")
    heads = ["## Done when", "## What was done", "## On the branch", "## Decisions to check", "## Waiting for your OK",
             "## What didn't work", "## Amendments", "## Budget", "## How to review"]
    check("report: every section, in order", all(h in report for h in heads)
          and [report.index(h) for h in heads] == sorted(report.index(h) for h in heads), report)
    check("report: the result, and the proof for each check",
          "**Result:** met" in report and "failed before the work, passes since S1" in report
          and "README.md line 3" in report, report)
    check("report: the sub-run's decisions and waiting items are gathered in",
          "Left two survivors as equivalent mutants" in report and "gh workflow run badge.yml" in report
          and "Kept the CSV header out" in report, report)
    check("report: the branch's commits, the carried work explained, and how to merge",
          "Add export_csv" in report and "uncommitted work from before the run" in report
          and f"git merge --ff-only {branch}" in report, report)
    check("report: what didn't work, amendments and the budget",
          "S3 Bug hunt (killer-bugs): failed" in report and "scope: this branch's changes only" in report
          and "the user said order matters" in report and "Agents: about" in report, report)
    summary = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
    check("summary.json: the house shape",
          set(summary) == {"skill", "status", "headline", "report", "branch", "commits", "decisions", "waiting", "numbers"}
          and summary["skill"] == "killer-goal" and summary["status"] == "done" and summary["branch"] == branch
          and summary["headline"].startswith("Goal met: 2 of 2 done-when checks met")
          and any("Add export_csv" in c for c in summary["commits"])
          and any("equivalent mutants" in d for d in summary["decisions"])
          and any("gh workflow run badge.yml" in w for w in summary["waiting"]), summary)
    out, code = goal("pr", "-w", ws, ok=(2,))
    check("pr: refused unless a pull request was chosen at setup", "wasn't chosen at setup" in out, out)
    out, _ = goal("status", "-w", ws)
    check("status: after the report, the next action is to stop", "Next: give the user the short summary" in out, out)
    out, _ = goal("stop", "-w", ws)
    check("stop: clears the active marker", not marker.exists() and "dormant" in out, out)
    out, _ = hook(lead)
    check("hook: a stopped run never blocks", out == "", out)
    check("the run never moved the user's branch or stash", sha(proj, "main") == start_sha
          and g(proj, "stash", "list") == stash_before and sha(proj, "feature") == start_sha)

    # ---------- plan only
    proj3 = make_repo(tmp / "proj3")
    (proj3 / "calc.py").write_text((proj3 / "calc.py").read_text() + "# unfinished\n")
    out, _ = goal("init", "--repo", proj3, "--goal", "Is it ready for 1.0?", "--alone", "plan-only")
    ws3 = run_folder(out)
    check("plan-only: no branch, no commit, the tree as it was",
          g(proj3, "branch", "--show-current").strip() == "main" and "killer-goal" not in g(proj3, "branch")
          and g(proj3, "status", "--porcelain").strip() != "", out)
    goal("check", "add", "-w", ws3, "The tests pass", "--", PY, "tests/test_calc.py")
    goal("lock", "-w", ws3)
    goal("plan", "add", "-w", ws3, "Bug hunt", "--skill", "killer-bugs")
    out, code = goal("go", "-w", ws3, ok=(2,))
    check("plan-only: go is refused", "plan-only" in out, out)
    out, _ = goal("checks", "run", "-w", ws3)
    goal("report", "-w", ws3)
    rep3 = (ws3 / "report.md").read_text(encoding="utf-8")
    check("plan-only: the report shows the plan and where things stand",
          "**Result:** plan only" in rep3 and "1 of 1 command checks pass" in rep3 and "planned, not run" in rep3, rep3)
    goal("stop", "-w", ws3)

    # ---------- the one outward step, when chosen: a draft pull request
    origin4 = tmp / "origin4.git"
    origin4.mkdir()
    g(origin4, "init", "-q", "--bare")
    proj4 = make_repo(tmp / "proj4", origin4.as_posix())
    out, _ = goal("init", "--repo", proj4, "--goal", "tidy the calculator", "--alone", "draft-pr", "--hours", "0")
    ws4 = run_folder(out)
    goal("check", "add", "-w", ws4, "The tests pass", "--", PY, "tests/test_calc.py")
    goal("lock", "-w", ws4)
    planfile = tmp / "plan.json"
    planfile.write_text(json.dumps({"steps": [
        {"id": "a", "title": "Tidy calc.py", "kind": "direct"},
        {"id": "b", "title": "Tidy the README", "kind": "direct", "after": "a"}]}))
    out, _ = goal("plan", "load", "-w", ws4, planfile)
    G4 = ledger(ws4)
    check("plan load: steps with dependencies between them", [s["after"] for s in G4["steps"]] == [[], ["S1"]], out)
    goal("go", "-w", ws4)
    check("go with no limit: no deadline", ledger(ws4)["deadline"] is None)
    for sid, path, text in (("S1", "calc.py", "\n# tidy\n"), ("S2", "README.md", "\nTidy.\n")):
        goal("step", "start", "-w", ws4, sid)
        (proj4 / path).write_text((proj4 / path).read_text() + text)
        g(proj4, "commit", "-qam", f"Tidy {path}")
        goal("step", "done", "-w", ws4, sid, "--headline", f"tidied {path}")
        goal("guard", "-w", ws4)
        goal("checks", "run", "-w", ws4)
    goal("checks", "run", "-w", ws4, "--final")
    out, _ = goal("judge-brief", "-w", ws4)
    jb4 = Path(re.search(r"\*\*Project copy:\*\* `(.+?)`", (ws4 / "judge-brief.md").read_text(encoding="utf-8")).group(1)).parent
    (jb4 / "scratch" / "reply.json").write_text(json.dumps({"checks": [{"id": "C1", "verdict": "met", "evidence": "exit 0"}]}))
    goal("judge", "-w", ws4, "--sandbox", "judge")
    goal("report", "-w", ws4)
    fake_gh = tmp / "fake_gh.py"
    fake_gh.write_text("import json, sys, pathlib\npathlib.Path(sys.argv[0]).with_name('gh-args.json').write_text("
                       "json.dumps(sys.argv[1:]))\nprint('https://example.invalid/pull/7')\n")
    os.environ["KILLER_GOAL_GH"] = json.dumps([PY, str(fake_gh)])
    G4 = ledger(ws4)
    G4["wip"] = G4["base"]
    (ws4 / "goal.json").write_text(json.dumps(G4))
    out, code = goal("pr", "-w", ws4, ok=(1,))
    check("pr: never pushes the user's own uncommitted work; it waits instead",
          "Not pushed" in out and not (tmp / "gh-args.json").exists()
          and "killer-goal" not in g(origin4, "branch"), out)
    G4 = ledger(ws4)
    G4["wip"] = None
    G4["waiting"] = []
    (ws4 / "goal.json").write_text(json.dumps(G4))
    out, _ = goal("pr", "-w", ws4)
    gh_args = json.loads((tmp / "gh-args.json").read_text())
    body = Path(gh_args[gh_args.index("--body-file") + 1]).read_text(encoding="utf-8")
    check("pr: pushes the goal branch and opens a draft pull request into the user's branch",
          "https://example.invalid/pull/7" in out and G4["branch"] in g(origin4, "branch")
          and "--draft" in gh_args and gh_args[gh_args.index("--base") + 1] == "main", (out, gh_args))
    check("pr: the description is plain, with no AI attribution",
          "tidy the calculator" in body and not goalmod.ATTRIBUTION.search(body), body)
    out, _ = goal("guard", "-w", ws4)
    check("guard: the push the user chose at setup isn't flagged", "Guard OK" in out, out)
    os.environ.pop("KILLER_GOAL_GH", None)
    goal("stop", "-w", ws4)

    # ---------- hook install, remove, status on a temp settings file
    settings = tmp / "claude-test" / "settings.json"
    settings.parent.mkdir()
    other = {"type": "command", "command": "echo someone else's hook"}
    settings.write_text(json.dumps({"model": "opus", "hooks": {
        "Stop": [{"hooks": [other]}], "PreToolUse": [{"matcher": "Bash", "hooks": [other]}]}}, indent=2))
    out, _ = goal("hook", "install", "--settings", settings)
    data = json.loads(settings.read_text())
    ours = [h for grp in data["hooks"]["Stop"] for h in grp["hooks"] if "hook stop" in h["command"]]
    backups = list(settings.parent.glob("settings.json.bak-*"))
    check("hook install: adds one Stop entry with this Python and goal.py, keeping everything else",
          len(ours) == 1 and ours[0]["timeout"] == 10 and Path(PY).as_posix() in ours[0]["command"]
          and GOAL.as_posix() in ours[0]["command"] and data["model"] == "opus"
          and data["hooks"]["PreToolUse"][0]["hooks"] == [other] and data["hooks"]["Stop"][0]["hooks"] == [other], data)
    check("hook install: backs the file up first, and says a restart or /hooks may be needed",
          len(backups) == 1 and "echo someone else" in backups[0].read_text() and "/hooks" in out, out)
    out, _ = goal("hook", "install", "--settings", settings)
    data2 = json.loads(settings.read_text())
    check("hook install: twice is the same as once",
          "already installed" in out and data2 == data and len(list(settings.parent.glob("settings.json.bak-*"))) == 1, out)
    out, code = goal("hook", "status", "--settings", settings)
    check("hook status: says it's installed, and where", code == 0 and str(settings) in out and "installed" in out, out)
    out, _ = goal("hook", "remove", "--settings", settings)
    data3 = json.loads(settings.read_text())
    check("hook remove: takes out only its own entry",
          data3 == {"model": "opus", "hooks": {"Stop": [{"hooks": [other]}], "PreToolUse": [{"matcher": "Bash", "hooks": [other]}]}}, data3)
    out, code = goal("hook", "status", "--settings", settings, ok=(1,))
    check("hook status: not installed after remove", "not installed" in out, out)
    fresh = tmp / "claude-test" / "new" / "settings.json"
    goal("hook", "install", "--settings", fresh)
    goal("hook", "remove", "--settings", fresh)
    check("hook install/remove on a missing file: creates it, then leaves it empty of hooks",
          json.loads(fresh.read_text()) == {}, fresh.read_text())
    broken = tmp / "claude-test" / "broken.json"
    broken.write_text("{ not json")
    out, code = goal("hook", "install", "--settings", broken, ok=(2,))
    check("hook install: a settings file that isn't JSON is left alone", "isn't valid JSON" in out
          and broken.read_text() == "{ not json", out)
    out, _ = goal("hook", "install", "--project", "--repo", proj2)
    local = proj2 / ".claude" / "settings.local.json"
    check("hook install --project: the project's settings.local.json, kept out of git",
          local.is_file() and ".claude" not in g(proj2, "status", "--porcelain"), (out, g(proj2, "status", "--porcelain")))
    out, code = goal("hook", "status", "--repo", proj2)
    check("hook status: looks in the user's settings (under CLAUDE_CONFIG_DIR) and the project's",
          str(tmp / "claude" / "settings.json") in out and str(local) in out and code == 0, out)
    goal("hook", "remove", "--project", "--repo", proj2)

    # ---------- the real ~/.claude was never touched
    check("the real settings.json is untouched", fingerprint_of(REAL_SETTINGS) == real_before)
    check("no killer-goal folder appeared in the real ~/.claude",
          (REAL_CLAUDE / "killer-goal").exists() == real_goal_home)
finally:
    os.chdir(ROOT)
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"({time.monotonic() - t_start:.0f} s)")
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
