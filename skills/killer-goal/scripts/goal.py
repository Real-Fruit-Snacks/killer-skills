#!/usr/bin/env python3
"""Bookkeeping for a killer-goal run: done-when checks, the plan, the ledger, the guard and the keep-going hook.

Commands:
  init         make the run, the goal branch (and switch to it) and the active marker
  check        add | list | amend: the done-when checks; `checks run` runs them
  lock         freeze the checks; after this every change is an amendment the report lists
  plan         add | load | list | edit | drop: the steps
  go           start the clock; the keep-going hook wakes up for this run
  next         the next step, with everything needed to run it; or FIRST, NONE or STOP
  step         start | done | failed | blocked | skipped: record what happened to a step
  checks run   run every command check in a fresh sandbox from the goal branch
  guard        compare the repository with how it was at init
  budget       time and agents used and left
  decide       record a call the user should check
  wait         record an outward action prepared but not done, with its exact command
  sandbox      a fresh copy of the goal branch for an agent
  judge-brief  the acceptance judge's brief, filled in, with its sandbox
  judge        read the acceptance judge's reply.json into the ledger
  pause        the keep-going hook and `next` stop until resume
  resume       carry on after a pause, a stall, a restart or a stop
  stop         end the run and clear its active marker
  status       where the run is and the next action; with no -w, the active run for this folder
  report       report.md and summary.json
  pr           push the branch and open a draft pull request, only when chosen at init
  hook         install | remove | status | stop: the keep-going Stop hook

After a context summary, run `goal.py status` first. Its last line is the next action.

Runs live in ~/.claude/killer-goal/<project>/<date>-<slug>/, and the markers of runs that
are going in ~/.claude/killer-goal/active/. KILLER_GOAL_HOME and KILLER_GOAL_SANDBOXES move
them, KILLER_GOAL_NOW sets the clock (for tests), and CLAUDE_CONFIG_DIR moves ~/.claude for
the hook's settings file and the skills `next` looks for. Standard library only.
Run `goal.py <command> --help` for options.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
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
SCRIPT = Path(__file__).resolve()
SKILL_DIR = HERE.parent
SKILL = "goal"
LEDGER = "goal.json"
HOOK_STATE = "hook.json"
OPEN = ("pending", "running", "retry")
SATISFIES = ("done", "skipped", "dropped")      # a step after one of these may run
STALL_SAME = 3            # blocks in a row with the ledger and branch unchanged
STALL_NO_PROGRESS = 12    # blocks in a row with no step starting or finishing
MAX_STARTS = 4
MAX_FAILS = 2
ALONE = {"branch": "commit to the goal branch; everything else waits for you",
         "draft-pr": "commit to the goal branch, then open a draft pull request at the end",
         "plan-only": "plan and report only, no changes"}
WIP_SUBJECT = "Work in progress from before the run"
HOOK_RE = re.compile(r"killer-goal[\\/]+scripts[\\/]+goal\.py[\"']?\s+hook\s+stop\b")
ATTRIBUTION = re.compile(r"(?im)^\s*co-authored-by:|generated (?:with|by) (?:\[?claude|an? ai\b|ai\b)"
                         r"|\U0001F916|claude\.ai/code|noreply@anthropic\.com")
NL = "\n"
UNATTENDED = ("Running unattended for killer-goal (run {run}). Setup answers: {answers}. Don't ask questions: "
              "use these answers and the defaults for anything else, and say so in one line. Work on the current "
              "branch, {branch}, which killer-goal made for this run: commit there and don't create another branch. "
              "\"This branch's changes\" means the changes since {base}, the commit it started from "
              "(--scope branch:{base}). "
              "Record calls the user should check with the skill's decide command (or list them at the end if it "
              "has none), and never push, publish, tag, open a pull request or change anything public. Finish with "
              "the skill's report (or its handoff), and end with its one-line summary.")

_kit = None


def kit():
    """kit.py from this folder, loaded by path (python -I leaves the script's folder off sys.path)."""
    global _kit
    if _kit is None:
        spec = importlib.util.spec_from_file_location("killer_kit", HERE / "kit.py")
        _kit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_kit)
    return _kit


def fail(msg: str, code: int = 2):
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


# ---------------------------------------------------------------- time, files, paths (no kit: the hook uses these)

def now_dt() -> dt.datetime:
    v = os.environ.get("KILLER_GOAL_NOW", "").strip()
    if v:
        try:
            if re.fullmatch(r"\d+(\.\d+)?", v):
                return dt.datetime.fromtimestamp(float(v)).astimezone()
            d = dt.datetime.fromisoformat(v)
            return d if d.tzinfo else d.astimezone()
        except (ValueError, OverflowError, OSError):
            pass
    return dt.datetime.now().astimezone()


def stamp(d: dt.datetime | None = None) -> str:
    return (d or now_dt()).isoformat(timespec="seconds")


def parse_time(s) -> dt.datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.astimezone()


def hm(seconds: float) -> str:
    m = int(max(0, seconds)) // 60
    return f"{m // 60}:{m % 60:02d}"


def clock(d: dt.datetime | None) -> str:
    if not d:
        return "-"
    return d.strftime("%H:%M") if d.date() == now_dt().date() else d.strftime("%Y-%m-%d %H:%M")


def short(text: str, n: int = 80) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def read_json(path, default=None, tries: int = 4):
    """A JSON file, retried briefly: on Windows a file being replaced can't be read for a moment."""
    for i in range(tries):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError):
            if i == tries - 1:
                raise
            time.sleep(0.05)
    return default


def write_json(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(2)}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for i in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)


def goal_home() -> Path:
    return Path(os.environ.get("KILLER_GOAL_HOME") or Path.home() / ".claude" / "killer-goal").expanduser()


def active_dir() -> Path:
    return goal_home() / "active"


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()


def inside(child: str, parent: str) -> bool:
    try:
        c = os.path.normcase(os.path.realpath(child))
        p = os.path.normcase(os.path.realpath(parent)).rstrip("\\/")
        return c == p or c.startswith(p + os.sep)
    except (OSError, ValueError, TypeError):
        return False


def gp() -> str:
    return f'"{Path(sys.executable).as_posix()}" "{SCRIPT.as_posix()}"'


def cmdline(sub: str, ws=None, rest: str = "") -> str:
    s = f"{gp()} {sub}"
    if ws:
        s += f' -w "{Path(ws).as_posix()}"'
    return s + (f" {rest}" if rest else "")


def ref_tip(repo: str, branch: str) -> str:
    """A branch's commit read from .git directly, without running git (the hook must be fast)."""
    try:
        gd = Path(repo) / ".git"
        if gd.is_file():
            t = gd.read_text(encoding="utf-8").strip()
            if t.startswith("gitdir:"):
                gd = (Path(repo) / t[7:].strip()).resolve()
        common = gd
        cf = gd / "commondir"
        if cf.is_file():
            common = (gd / cf.read_text(encoding="utf-8").strip()).resolve()
        loose = common / "refs" / "heads" / branch
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()
        packed = common / "packed-refs"
        if packed.is_file():
            want = f" refs/heads/{branch}"
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(want):
                    return line.split(" ", 1)[0]
    except (OSError, ValueError):
        pass
    return ""


# ---------------------------------------------------------------- the ledger: pure helpers (the hook uses these too)

def live_checks(G) -> list:
    return [c for c in G.get("checks", []) if not c.get("dropped")]


def get_step(G, sid: str) -> dict:
    for s in G.get("steps", []):
        if s["id"].lower() == str(sid).lower():
            return s
    fail(f"no step {sid}; `plan list` shows them")


def get_check(G, cid: str) -> dict:
    for c in G.get("checks", []):
        if c["id"].lower() == str(cid).lower():
            return c
    fail(f"no check {cid}; `check list` shows them")


def step_by_id(G, sid):
    return next((s for s in G.get("steps", []) if s["id"] == sid), None)


def unmet_deps(G, s) -> list:
    out = []
    for d in s.get("after", []):
        dep = step_by_id(G, d)
        if dep is None or dep["status"] not in SATISFIES:
            out.append(d)
    return out


def runnable_steps(G) -> list:
    return [s for s in G.get("steps", []) if s["status"] in ("pending", "retry") and not unmet_deps(G, s)]


def running_step(G):
    return next((s for s in G.get("steps", []) if s["status"] == "running"), None)


def fails(s) -> int:
    return sum(1 for t in s.get("attempts", []) if t.get("outcome") == "failed")


def step_label(s) -> str:
    return s.get("skill") or "direct work"


def marks(G) -> dict:
    return G.setdefault("marks", {})


def bump(G) -> int:
    G["seq"] = G.get("seq", 0) + 1
    return G["seq"]


def budget_stop(G, now) -> str | None:
    wu, dl = parse_time(G.get("work_until")), parse_time(G.get("deadline"))
    if wu and now >= wu:
        return (f"time's up for new steps (the wrap-up began at {clock(wu)}; the deadline is {clock(dl)})")
    cap = G.get("agents_cap")
    if cap is not None and G.get("agents_used", 0) >= max(cap - 1, 0):
        return f"the agent budget is spent (about {G.get('agents_used', 0)} of {cap}; one is kept for the acceptance judge)"
    return None


def time_left(G, now) -> str:
    dl = parse_time(G.get("deadline"))
    if not dl:
        return "no time limit"
    return f"{hm((dl - now).total_seconds())} left"


def final_results(G) -> dict:
    f = G.get("final")
    if not f:
        return {}
    out = {}
    for c in live_checks(G):
        for r in reversed(c.get("results", [])):
            if r.get("final") and r.get("seq") == f.get("seq"):
                out[c["id"]] = r
                break
    return out


def judge_fresh(G) -> bool:
    j, f = G.get("judge"), G.get("final")
    return bool(j and f and j.get("seq", 0) > f.get("seq", 0))


def outcome(G) -> tuple[str, str]:
    """(met | partly met | not met | plan only, one line why)."""
    checks = live_checks(G)
    if G.get("plan_only"):
        latest = [last_result(c) for c in checks if c["kind"] == "command"]
        ok = sum(1 for r in latest if r and r.get("pass"))
        return "plan only", f"nothing was changed; today {ok} of {len(latest)} command checks pass"
    if not G.get("final"):
        return "not met", "the final check didn't run"
    fr = final_results(G)
    cmd = [c for c in checks if c["kind"] == "command"]
    passing = [c["id"] for c in cmd if fr.get(c["id"], {}).get("pass")]
    failing = [c["id"] for c in cmd if c["id"] not in passing]
    if not judge_fresh(G):
        why = "the acceptance judge didn't run after the final check"
        if failing:
            why = f"{', '.join(failing)} {'fails' if len(failing) == 1 else 'fail'}, and " + why
        return ("partly met" if passing else "not met"), why
    verdicts = G["judge"].get("verdicts", {})
    met = [c["id"] for c in checks if (c["kind"] == "judge" or c["id"] in passing)
           and verdicts.get(c["id"], {}).get("verdict") == "met"]
    disagree = [c["id"] for c in checks if verdicts.get(c["id"], {}).get("verdict") != "met"]
    if len(met) == len(checks):
        return "met", f"all {len(checks)} done-when checks pass and the acceptance judge agrees on each"
    parts = []
    if failing:
        parts.append(f"{', '.join(failing)} {'fails' if len(failing) == 1 else 'fail'} in the final check")
    if disagree:
        parts.append(f"the judge doesn't agree on {', '.join(disagree)}")
    return ("partly met" if met or passing else "not met"), "; ".join(parts) or "not every check holds"


def last_result(c, final: bool = False):
    for r in reversed(c.get("results", [])):
        if not final or r.get("final"):
            return r
    return None


def wrapup(G, ws) -> tuple[str, str]:
    m = marks(G)
    last_change = m.get("code", 0)
    f, j, rep = G.get("final"), G.get("judge"), G.get("report")
    if not f or f.get("seq", 0) < last_change:
        return "run the final check", cmdline("checks run", ws, "--final")
    if not judge_fresh(G):
        brief = G.get("judge_brief")
        if brief and brief.get("seq", 0) > f.get("seq", 0):
            return (f"send the brief in judge-brief.md (in the run folder) to a fresh general-purpose agent, "
                    f"then read its reply", cmdline("judge", ws, f"--sandbox {brief['sandbox']}"))
        return "make the blind acceptance judge's brief and its sandbox", cmdline("judge-brief", ws)
    if not rep or rep.get("seq", 0) < j.get("seq", 0):
        return "write the report", cmdline("report", ws)
    if (G.get("alone") == "draft-pr" and rep.get("result") in ("met", "partly met")
            and not G.get("pr")):
        return "open the draft pull request the user chose at setup", cmdline("pr", ws)
    return "give the user the short summary from report.md, then end the run", cmdline("stop", ws)


def next_action(G, ws, now) -> tuple[str, str]:
    """The one next thing to do, from the ledger alone. status, next and the hook all use it."""
    st = G.get("status")
    if st == "stopped":
        if G.get("report"):
            return f"the run is stopped; the report is {Path(ws) / 'report.md'}", ""
        return "the run is stopped and there's no report yet", cmdline("report", ws)
    checks = live_checks(G)
    if st == "setup":
        if not checks:
            return "write the done-when checks", cmdline("check add", ws, '"<statement>" -- <command>')
        if not any(c["kind"] == "command" for c in checks):
            return "add at least one command check", cmdline("check add", ws, '"<statement>" -- <command>')
        if not G.get("locked"):
            return "lock the done-when checks", cmdline("lock", ws)
        if not G.get("steps"):
            return "plan the steps", cmdline("plan add", ws, '"<title>" --skill <killer-x> --answers "<...>"')
        if G.get("plan_only"):
            if not marks(G).get("checks"):
                return "run the checks once, to show where things stand", cmdline("checks run", ws)
            if not G.get("report"):
                return "write the report (plan only)", cmdline("report", ws)
            return "give the user the plan and the report, then end the run", cmdline("stop", ws)
        return "start the run (after the user has seen the plan, if they asked to)", cmdline("go", ws)
    if st == "paused":
        return f"the run is paused ({G.get('pause_reason') or 'no reason given'}); when it can carry on", cmdline("resume", ws)
    r = running_step(G)
    if r:
        return f"finish {r['id']} ({step_label(r)}: {short(r['title'], 50)}) and record how it went", cmdline("step done", ws, done_args(r))
    m = marks(G)
    if m.get("guard_ok") is False:
        return "the last guard found a problem: fix it (see When things go wrong), then run the guard again", cmdline("guard", ws)
    if m.get("changed", 0) > m.get("guard", 0):
        return "run the guard after the last step", cmdline("guard", ws)
    if m.get("code", 0) > m.get("checks", 0):
        return "run the done-when checks after the last step", cmdline("checks run", ws)
    if runnable_steps(G) and not budget_stop(G, now):
        return "get the next step", cmdline("next", ws)
    return wrapup(G, ws)


def progress_marker(G) -> str:
    parts = [f"{s['id']}={s['status']}/{len(s.get('attempts', []))}" for s in G.get("steps", [])]
    for k in ("final", "judge", "report", "pr"):
        parts.append(f"{k}={(G.get(k) or {}).get('seq', 0)}")
    return "|".join(parts)


def fingerprint(ws: Path, G) -> str:
    h = hashlib.sha256()
    for i in range(4):
        try:
            h.update((Path(ws) / LEDGER).read_bytes())
            break
        except PermissionError:
            if i == 3:
                raise
            time.sleep(0.05)
    h.update(ref_tip(G.get("repo", ""), G.get("branch") or "").encode())
    return h.hexdigest()


# ---------------------------------------------------------------- the keep-going hook

def transcript_has(path: str, token: bytes, cap: int = 1 << 30) -> bool:
    """Search a transcript for a token, from the end backwards, in chunks."""
    chunk = 8 << 20
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        pos, read, carry = end, 0, b""
        while pos > 0 and read < cap:
            start = max(0, pos - chunk)
            f.seek(start)
            data = f.read(pos - start) + carry
            if token in data:
                return True
            carry = data[:len(token)]
            read += pos - start
            pos = start
    return False


def is_lead(m: dict, cwd: str, transcript: str) -> bool:
    token = m.get("lead_token")
    if not token:
        return False
    if transcript and os.path.isfile(transcript):
        return transcript_has(transcript, token.encode())
    return bool(cwd) and inside(cwd, m.get("project", ""))


def hook_log(H: dict, sid: str, decision: str, note: str) -> None:
    log = H.setdefault("log", [])
    log.append([stamp(), (sid or "")[:8], decision, note])
    del log[:-40]


def hook_stop_decision(raw: bytes) -> dict | None:
    """What the Stop hook prints: a block with the next instruction, or None to let the turn end."""
    data = json.loads(raw.decode("utf-8-sig") or "{}") if raw else {}
    if not isinstance(data, dict):
        return None
    sid = data.get("session_id")
    if not isinstance(sid, str) or not sid:
        return None
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else ""
    transcript = data.get("transcript_path") if isinstance(data.get("transcript_path"), str) else ""
    adir = active_dir()
    if not adir.is_dir():
        return None
    markers = []
    for f in sorted(adir.glob("*.json")):
        try:
            m = read_json(f)
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and m.get("workspace"):
            markers.append((f, m))
    if not markers:
        return None
    chosen = next(((f, m) for f, m in markers if m.get("session") == sid), None)
    if chosen is None:
        for f, m in markers:
            if m.get("session") or not is_lead(m, cwd, transcript):
                continue
            fresh = read_json(f)
            if not isinstance(fresh, dict) or fresh.get("session"):
                continue
            fresh.update(session=sid, claimed=stamp())
            write_json(f, fresh)
            chosen = (f, fresh)
            break
    if chosen is None:
        return None
    ws = Path(chosen[1]["workspace"])
    G = read_json(ws / LEDGER)
    if not isinstance(G, dict) or G.get("status") != "running":
        return None
    now = now_dt()
    H = read_json(ws / HOOK_STATE, {}) or {}
    dl = parse_time(G.get("deadline"))
    if dl and now >= dl:
        hook_log(H, sid, "allow", "past the deadline")
        write_json(ws / HOOK_STATE, H)
        return None
    fp, pm = fingerprint(ws, G), progress_marker(G)
    stalled = H.get("stalled")
    if stalled and stalled.get("fp") == fp:
        hook_log(H, sid, "allow", "still stalled")
        write_json(ws / HOOK_STATE, H)
        return None
    same = H.get("same", 0) + 1 if fp == H.get("fp") else 0
    since = H.get("since", 0) + 1 if pm == H.get("pm") else 0
    why = None
    if same >= STALL_SAME:
        why = f"the ledger and the branch didn't change across {STALL_SAME} blocks in a row"
    elif since >= STALL_NO_PROGRESS:
        why = f"{STALL_NO_PROGRESS} blocks in a row without a step starting or finishing"
    if why:
        H.update(stalled={"time": stamp(now), "reason": why, "fp": fp}, fp=fp, pm=pm, same=same, since=since)
        H["stalls"] = H.get("stalls", 0) + 1
        hook_log(H, sid, "allow", f"stalled: {why}")
        write_json(ws / HOOK_STATE, H)
        return None
    text, cmd = next_action(G, ws, now)
    goal = short(G.get("goal", ""), 90)
    left = [s for s in G.get("steps", []) if s["status"] in OPEN]
    status = cmdline("status", ws)
    if running_step(G) or (runnable_steps(G) and not budget_stop(G, now)):
        reason = (f"killer-goal is still running '{goal}': {len(left)} step{'' if len(left) == 1 else 's'} left, "
                  f"{time_left(G, now)}. Run `{status}` and carry on with the next step. If you're blocked on "
                  f"something only the user can answer, record it with `{cmdline('step blocked', ws, '<id> --reason ...')}` "
                  f"and move to the next step; if nothing can move, run `{cmdline('pause', ws, '--reason ...')}`.")
    else:
        why_wrap = budget_stop(G, now) or "no step is left that can run"
        reason = (f"killer-goal is wrapping up '{goal}': {why_wrap}, {time_left(G, now)}. The run isn't finished "
                  f"until the report is written and the run is stopped. Run `{status}` if you need the whole picture.")
    reason += f" Next: {text}" + (f": `{cmd}`" if cmd else ".")
    H.update(fp=fp, pm=pm, same=same, since=since, stalled=None, blocks=H.get("blocks", 0) + 1, last=stamp(now))
    hook_log(H, sid, "block", short(text, 60))
    write_json(ws / HOOK_STATE, H)
    return {"decision": "block", "reason": reason}


def hook_stop_main() -> None:
    """Fail-safe: any error, bad JSON or missing file lets the turn end, and nothing is printed."""
    try:
        raw = sys.stdin.buffer.read() if getattr(sys.stdin, "buffer", None) else b""
        out = hook_stop_decision(raw)
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=True))
            sys.stdout.flush()
    except BaseException:   # noqa: BLE001 - a hook that errors must never hold a session
        pass
    raise SystemExit(0)


# ---------------------------------------------------------------- git and run helpers (these use the kit)

def git(repo, *args, check=True) -> str:
    return kit().git(Path(repo), *args, check=check)


def load(a):
    ws, cfg = kit().load_run(a.workspace)
    G = read_json(ws / LEDGER)
    if not isinstance(G, dict):
        fail(f"{ws} isn't a killer-goal run (no {LEDGER})")
    return ws, cfg, G


def mutate(ws: Path, fn):
    """Change goal.json under the run's lock; returns what fn returns. Never call the kit's sandbox code inside."""
    with kit().locked(ws):
        G = read_json(Path(ws) / LEDGER)
        out = fn(G)
        write_json(Path(ws) / LEDGER, G)
    return out


def marker_path(G) -> Path:
    return active_dir() / f"{G['run_id']}.json"


def write_marker(G, ws: Path, session=None) -> None:
    write_json(marker_path(G), {"run_id": G["run_id"], "workspace": str(ws), "project": G["repo"],
                                "goal": short(G["goal"], 120), "branch": G["branch"], "created": G["created"],
                                "lead_token": G.get("lead_token"), "session": session, "claimed": None})


def new_lead_token(G) -> str:
    G["lead_token"] = f"kg-lead-{G['run_id']}-{secrets.token_hex(3)}"
    return G["lead_token"]


def lead_line(G) -> str:
    return (f"Lead session marker: {G.get('lead_token')} (the keep-going hook looks for this line "
            f"to know which session is the lead)")


def snapshot(repo: Path) -> dict:
    heads, remotes, tags = {}, {}, {}
    for line in git(repo, "for-each-ref", "--format=%(objectname) %(refname)").splitlines():
        sha, _, ref = line.partition(" ")
        if ref.startswith("refs/heads/"):
            heads[ref[11:]] = sha
        elif ref.startswith("refs/remotes/"):
            remotes[ref[13:]] = sha
        elif ref.startswith("refs/tags/"):
            tags[ref[10:]] = sha
    stash = git(repo, "stash", "list", "--format=%H", check=False).split()
    fh = git(repo, "rev-parse", "--git-path", "FETCH_HEAD", check=False).strip()
    fpath = (Path(repo) / fh) if fh and not Path(fh).is_absolute() else Path(fh) if fh else None
    fetch = hashlib.sha1(fpath.read_bytes()).hexdigest() if fpath and fpath.is_file() else ""
    upstream = sorted(git(repo, "config", "--get-regexp", r"^branch\..*\.(remote|merge|pushremote)$",
                          check=False).splitlines())
    return {"heads": heads, "remotes": remotes, "tags": tags, "stash": stash, "fetch_head": fetch,
            "upstream": upstream,
            "head_ref": git(repo, "symbolic-ref", "-q", "HEAD", check=False).strip(),
            "head": git(repo, "rev-parse", "HEAD", check=False).strip()}


def tip(G) -> str:
    return git(G["repo"], "rev-parse", "--verify", "-q", G["branch"] + "^{commit}", check=False).strip()


def log_lines(repo, a: str, b: str) -> list[str]:
    if not a or not b or a == b:
        return []
    out = git(repo, "log", "--no-decorate", "--format=%h %s", f"{a}..{b}", check=False)
    return [ln for ln in out.splitlines() if ln.strip()]


def show_cmd(c) -> str:
    if c.get("shell"):
        return c["shell"]
    return " ".join(x if x and not re.search(r"[\s\"']", x) else '"' + x.replace('"', '\\"') + '"'
                    for x in c.get("argv", []))


def find_sh() -> str | None:
    sh = shutil.which("sh")
    if sh:
        return sh
    g = shutil.which("git")
    if g and os.name == "nt":
        root = Path(g).resolve().parents[1]
        for cand in (root / "usr" / "bin" / "sh.exe", root / "bin" / "sh.exe"):
            if cand.is_file():
                return str(cand)
    return None


def check_argv(c) -> list[str]:
    if c.get("shell"):
        sh = find_sh()
        if sh:
            return [sh, "-c", c["shell"]]
        if os.name == "nt":
            return ["cmd", "/d", "/s", "/c", c["shell"]]
        fail("no sh to run a --shell check with")
    return list(c["argv"])


def tracked_files(repo: Path, argv, shell, explicit) -> list[str]:
    """Files in the project a check runs: their contents are locked with the check."""
    tokens = list(argv or [])
    if shell:
        try:
            tokens += shlex.split(shell)
        except ValueError:
            tokens += shell.split()
    out = []
    for t in tokens:
        t = t.strip("'\"").split("::")[0].replace("\\", "/")
        if not t or t.startswith("-"):
            continue
        p = Path(t)
        full = p if p.is_absolute() else Path(repo) / p
        try:
            rel = full.resolve().relative_to(Path(repo).resolve()).as_posix()
        except (ValueError, OSError):
            continue
        if full.is_file() and rel not in out and not rel.startswith(".git/"):
            out.append(rel)
    for e in explicit or []:
        full = Path(repo) / e
        if not full.is_file():
            fail(f"--files {e}: no such file in {repo}")
        rel = full.resolve().relative_to(Path(repo).resolve()).as_posix()
        if rel not in out:
            out.append(rel)
    return out


def path_like(argv, shell) -> list[str]:
    tokens = list(argv or []) + ((shell or "").split())
    out = []
    for t in tokens:
        t = t.strip("'\"").split("::")[0].replace("\\", "/")
        if t and not t.startswith("-") and not Path(t).is_absolute() and re.search(r"/|\.(py|js|mjs|ts|sh|ps1|rb|go|rs)$", t):
            out.append(t[2:] if t.startswith("./") else t)
    return out


def check_rev(G) -> str:
    return G["base"] if G.get("plan_only") else G["branch"]


def blobs(G, files) -> tuple[dict, list]:
    found, missing = {}, []
    for f in files:
        sha = git(G["repo"], "rev-parse", "--verify", "-q", f"{check_rev(G)}:{f}", check=False).strip()
        if sha:
            found[f] = sha
        else:
            missing.append(f)
    return found, missing


def lock_hash(G) -> str:
    rows = [{k: c.get(k) for k in ("id", "statement", "kind", "argv", "shell", "files", "blobs", "dropped", "timeout")}
            for c in G.get("checks", [])]
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def parse_agents(v) -> int | None:
    if v is None or v == "":
        return None
    m = re.fullmatch(r"\s*[~+]?\s*(\d+)\s*", str(v))
    if not m:
        fail(f"agents is a number, like 12 or ~12, not {v!r}")
    return int(m.group(1))


def split_ids(v) -> list[str]:
    if isinstance(v, list):
        items = v
    else:
        items = re.split(r"[,\s]+", v or "")
    return [str(x).strip().upper() for x in items if str(x).strip()]


def skill_location(name: str, repo: str) -> Path | None:
    for base in (claude_dir() / "skills", Path(repo) / ".claude" / "skills", SKILL_DIR.parent):
        p = base / name / "SKILL.md"
        if p.is_file():
            return p
    return None


def skill_places(repo: str) -> str:
    return ", ".join(str(b / "<name>") for b in (claude_dir() / "skills", Path(repo) / ".claude" / "skills", SKILL_DIR.parent))


# ---------------------------------------------------------------- init

def cmd_init(a) -> int:
    K = kit()
    path = Path(a.repo).expanduser().resolve()
    if not path.is_dir():
        fail(f"no folder {path}")
    top, is_git = K.find_top(path)
    if not is_git or not K.has_head(top):
        fail("killer-goal needs a git repository with at least one commit, so the work stays on its own branch. "
             "Commit the project first.")
    hours = None
    if str(a.hours).strip().lower() not in ("0", "none", "no-limit", "nolimit", "unlimited"):
        try:
            hours = float(a.hours)
        except ValueError:
            fail(f"--hours takes a number, or 0 for no limit, not {a.hours!r}")
        if hours <= 0:
            hours = None
    if a.agents is not None and a.agents < 1:
        fail("--agents is a cap of 1 or more")
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply"):
        rel = git(top, "rev-parse", "--git-path", marker, check=False).strip()
        if rel and ((top / rel) if not Path(rel).is_absolute() else Path(rel)).exists():
            fail(f"git has an operation in progress in {top} ({marker}); finish or abort it first")
    if not git(top, "config", "user.name", check=False).strip() or not git(top, "config", "user.email", check=False).strip():
        fail("git doesn't know who you are in this repository (user.name and user.email), so nothing could be "
             "committed. Set them first.")
    adir = active_dir()
    for f in sorted(adir.glob("*.json")) if adir.is_dir() else []:
        m = read_json(f, {}) or {}
        if m.get("project") and inside(str(top), m["project"]) and inside(m["project"], str(top)):
            fail(f"a killer-goal run is already going in this project: {m.get('workspace')}. "
                 f"Finish it, or end it with: {cmdline('stop', m.get('workspace'))}")
    plan_only = a.alone == "plan-only"
    dirty = git(top, "status", "--porcelain=v1", "--untracked-files=all").splitlines()
    if dirty and a.dirty == "stop" and not plan_only:
        fail(f"there are {len(dirty)} uncommitted changes in {top}. Commit or stash them yourself, or start with "
             f"--dirty carry: they become the goal branch's first commit and your branch stays exactly as it is.")
    start = snapshot(top)
    orig = start["head_ref"][11:] if start["head_ref"].startswith("refs/heads/") else None
    start.update(branch=orig, sha=start["head"])
    words = re.findall(r"[A-Za-z0-9]+", a.goal.lower())
    slug = K.slugify(a.slug or "-".join(words[:6]), "goal")[:32].strip("-") or "goal"
    ws, cfg, _ = K.init_run(SKILL, str(top), slug=slug, target=short(a.goal, 200), network=a.network,
                            include=a.include or [], env=a.env or [], quiet=True,
                            extra={"goal_version": 1})
    date_slug = f"{K.today()}-{slug}"
    branch, wip, base = orig, None, start["sha"]
    if not plan_only:
        branch, n = f"killer-goal/{date_slug}", 2
        while K.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{branch}"):
            branch, n = f"killer-goal/{date_slug}-{n}", n + 1
        index = Path(top / git(top, "rev-parse", "--git-path", "index").strip())
        saved_index = index.read_bytes() if index.is_file() else None
        git(top, "branch", "--no-track", branch, start["sha"])
        try:
            git(top, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
            if dirty:
                git(top, "add", "-A")
                # A snapshot of the user's own unfinished work: their hooks (formatters, linters) mustn't change or refuse it.
                git(top, "commit", "-q", "--no-verify", "-m", WIP_SUBJECT)
                wip = git(top, "rev-parse", "HEAD").strip()
                base = wip
        except BaseException:
            if orig:
                git(top, "symbolic-ref", "HEAD", f"refs/heads/{orig}", check=False)
            else:
                git(top, "update-ref", "--no-deref", "HEAD", start["sha"], check=False)
            if saved_index is not None:
                index.write_bytes(saved_index)
            git(top, "branch", "-D", branch, check=False)
            K.rmtree(ws)
            raise
    K.update_cfg(ws, lambda c: c.update(branch=branch, head=base))
    G = {"version": 1, "skill": "killer-goal", "run_id": cfg["run_id"], "repo": str(top), "goal": a.goal,
         "status": "setup", "created": stamp(), "hours": hours, "depth": a.depth, "alone": a.alone,
         "plan_only": plan_only, "agents_cap": a.agents, "agents_used": 0, "branch": branch, "base": base,
         "wip": wip, "start": start, "checks": [], "next_check": 1, "locked": None, "amendments": [],
         "steps": [], "next_step": 1, "plan_changes": [], "decisions": [], "waiting": [], "guards": [],
         "marks": {}, "seq": 0, "lead_token": None, "started": None, "deadline": None, "work_until": None,
         "final": None, "judge": None, "judge_brief": None, "report": None, "pr": None}
    write_json(ws / LEDGER, G)
    write_marker(G, ws)
    print(f"Run folder: {ws}")
    print(f"Run: killer-goal:{G['run_id']}")
    print(f"Goal: {a.goal}")
    print(f"Project: {top}")
    if orig:
        print(f"Your branch: {orig} at {start['sha'][:10]}. It stays exactly as it is.")
    else:
        print(f"HEAD was detached at {start['sha'][:10]}.")
    if plan_only:
        print("Plan only: no branch, no commits; the checks run from the commit you're on.")
    else:
        print(f"Goal branch: {branch}, made from {start['sha'][:10]} and checked out.")
        if wip:
            print(f"Your {len(dirty)} uncommitted change{'' if len(dirty) == 1 else 's'} became its first commit, "
                  f"{wip[:10]} \"{WIP_SUBJECT}\".")
    print(f"Time: {'up to ' + str(hours).rstrip('0').rstrip('.') + ' hours' if hours else 'no limit'}, counted from `go` · "
          f"Agents: {'up to ' + str(a.agents) if a.agents else 'no cap'} · Depth: {a.depth} · Alone: {ALONE[a.alone]}")
    print(f"Network in check sandboxes: {'allowed' if a.network else 'off'}")
    text, cmd = next_action(G, ws, now_dt())
    print(f"Next: {text}: {cmd}")
    return 0


# ---------------------------------------------------------------- done-when checks

def cmd_check_add(a) -> int:
    ws, cfg, G = load(a)
    argv = a.command or []
    kind = a.kind or ("command" if argv or a.shell else "judge")
    if kind == "command" and not (argv or a.shell):
        fail('a command check needs its command after --, like: check add -w <run> "the export test passes" '
             "-- py -I tests/test_export.py")
    if argv and a.shell:
        fail("give the command after -- or with --shell, not both")
    if kind == "judge" and (argv or a.shell):
        fail("a judge check has no command; leave out --kind judge to make it a command check")
    if not a.statement.strip():
        fail("say what must be true, in a sentence")
    if G.get("locked") and not a.reason:
        fail("the checks are locked: adding one now is an amendment, so give --reason")
    files = tracked_files(Path(G["repo"]), argv, a.shell, a.files) if kind == "command" else []
    absent = [t for t in path_like(argv, a.shell) if t not in files and not (Path(G["repo"]) / t).exists()]
    found, missing = blobs(G, files) if G.get("locked") else ({}, [])
    if missing:
        fail(f"commit {', '.join(missing)} on {G['branch']} first: the checks run from the branch's commits")

    def fn(G):
        cid = f"C{G['next_check']}"
        G["next_check"] += 1
        c = {"id": cid, "statement": a.statement.strip(), "kind": kind, "argv": argv or None, "shell": a.shell,
             "files": files, "explicit_files": list(a.files or []), "blobs": found if G.get("locked") else {},
             "timeout": a.timeout,
             "created": stamp(), "results": []}
        G["checks"].append(c)
        if G.get("locked"):
            G["amendments"].append({"check": cid, "time": stamp(), "reason": a.reason, "what": "added after the lock",
                                    "before": None, "after": describe_check(c), "phase": phase(G)})
            G["locked"]["hash"] = lock_hash(G)
        bump(G)
        return c

    c = mutate(ws, fn)
    print(f"Added {c['id']} ({kind}): {c['statement']}")
    if kind == "command":
        print(f"  runs: {show_cmd(c)}   (from the goal branch, in a fresh sandbox; passes on exit 0)")
        if files:
            print(f"  locks the contents of: {', '.join(files)} (commit them on {G['branch']} before `lock`)")
        if absent:
            print(f"  note: {', '.join(absent)} isn't there yet. Write it and commit it on {G['branch']}; `lock` "
                  f"finds it and locks its contents too.")
    else:
        print("  the blind acceptance judge checks this one, with evidence")
    return 0


def describe_check(c) -> str:
    s = f"{c['statement']} [{c['kind']}"
    if c["kind"] == "command":
        s += f": {show_cmd(c)}"
    return s + "]"


def phase(G) -> str:
    return "during the run" if G.get("started") else "before the run started"


def cmd_check_list(a) -> int:
    ws, cfg, G = load(a)
    checks = G.get("checks", [])
    if not checks:
        print("No done-when checks yet.")
        return 0
    lk = G.get("locked")
    print(f"Done-when checks ({'locked ' + clock(parse_time(lk['time'])) if lk else 'not locked yet'}):")
    for c in checks:
        r = last_result(c)
        state = "dropped" if c.get("dropped") else check_state(G, c)
        print(f"  {c['id']:<4} {state:<14} {c['kind']:<8} {c['statement']}")
        if c["kind"] == "command" and not c.get("dropped"):
            print(f"       runs: {show_cmd(c)}" + (f"   locks: {', '.join(c['files'])}" if c.get("files") else ""))
        if r and r.get("note"):
            print(f"       {r['note']}")
    if G.get("amendments"):
        print(f"{len(G['amendments'])} amendment{'' if len(G['amendments']) == 1 else 's'}; the report lists them.")
    return 0


def check_state(G, c) -> str:
    if c["kind"] == "judge":
        j = (G.get("judge") or {}).get("verdicts", {}).get(c["id"])
        return f"judge: {j['verdict']}" if j else "judge: pending"
    r = last_result(c)
    if not r:
        return "not run"
    return ("pass" if r.get("pass") else "FAIL") + f" {clock(parse_time(r.get('time')))}"


def cmd_check_amend(a) -> int:
    ws, cfg, G = load(a)
    c = get_check(G, a.id)
    locked = bool(G.get("locked"))
    if locked and not a.reason:
        fail("the checks are locked: an amendment needs --reason, and the report lists it")
    argv = a.command
    if argv and a.shell:
        fail("give the command after -- or with --shell, not both")
    new = dict(c)
    if a.statement:
        new["statement"] = a.statement.strip()
    if argv:
        new["argv"], new["shell"] = argv, None
    if a.shell:
        new["shell"], new["argv"] = a.shell, None
    if a.timeout:
        new["timeout"] = a.timeout
    if a.drop:
        new["dropped"] = True
    if new["kind"] == "command" and not new.get("dropped"):
        if a.files is not None:
            new["explicit_files"] = list(a.files)
        new["files"] = tracked_files(Path(G["repo"]), new.get("argv"), new.get("shell"), new.get("explicit_files"))
        if locked:
            found, missing = blobs(G, new["files"])
            if missing:
                fail(f"commit {', '.join(missing)} on {G['branch']} first")
            new["blobs"] = found
    before, after = describe_check(c), ("dropped: " if new.get("dropped") else "") + describe_check(new)
    if before == after and new.get("blobs") == c.get("blobs") and not a.drop:
        print(f"{c['id']} is unchanged.")
        return 0

    def fn(G):
        cur = get_check(G, c["id"])
        cur.update(new)
        if G.get("locked"):
            G["amendments"].append({"check": c["id"], "time": stamp(), "reason": a.reason, "what":
                                    "dropped" if a.drop else "changed", "before": before, "after": after,
                                    "phase": phase(G)})
            G["locked"]["hash"] = lock_hash(G)
        bump(G)

    mutate(ws, fn)
    print(f"{'Dropped' if a.drop else 'Amended'} {c['id']}: {after}")
    if locked:
        print("  Recorded as an amendment; the report lists it with your reason.")
    return 0


def cmd_lock(a) -> int:
    ws, cfg, G = load(a)
    checks = live_checks(G)
    if G.get("locked"):
        print(f"The checks were locked at {G['locked']['time']}. Change one with check amend --reason.")
        return 0
    if not checks:
        fail("there are no done-when checks to lock")
    if not any(c["kind"] == "command" for c in checks):
        fail("every goal needs at least one command check: something a script runs, not only the judge's word")
    scanned = {c["id"]: tracked_files(Path(G["repo"]), c.get("argv"), c.get("shell"), c.get("explicit_files"))
               for c in checks if c["kind"] == "command"}
    files = sorted({f for fs in scanned.values() for f in fs})
    found, missing = blobs(G, files)
    if missing:
        fail(f"these files aren't committed on {G['branch']}, and the checks run from its commits: "
             f"{', '.join(missing)}. Commit them first.")

    def fn(G):
        for c in G["checks"]:
            if c["id"] in scanned:
                c["files"] = scanned[c["id"]]
            c["blobs"] = {f: found[f] for f in c.get("files", []) if f in found}
        G["locked"] = {"time": stamp(), "hash": None}
        G["locked"]["hash"] = lock_hash(G)
        bump(G)
        return G["locked"]["hash"]

    h = mutate(ws, fn)
    n_cmd = sum(1 for c in checks if c["kind"] == "command")
    print(f"Locked {len(checks)} done-when checks ({n_cmd} command, {len(checks) - n_cmd} judge); hash {h[:12]}.")
    if files:
        print(f"Their files are locked too: {', '.join(files)}. If one changes, its check fails until it's amended.")
    print("From now on a check changes only through `check amend --reason`, and the report lists every amendment.")
    return 0


def cmd_checks_run(a) -> int:
    K = kit()
    ws, cfg, G = load(a)
    only = set(split_ids(a.only)) if a.only else None
    checks = [c for c in live_checks(G) if not only or c["id"] in only]
    if only and len(checks) != len(only):
        fail(f"no such check: {', '.join(sorted(only - {c['id'] for c in checks}))}")
    if not any(c["kind"] == "command" for c in checks):
        fail("no command checks to run")
    if a.final and only:
        fail("the final check runs every check; leave out --only")
    tampered = bool(G.get("locked") and G["locked"].get("hash") != lock_hash(G))
    rev = check_rev(G)
    sha = git(G["repo"], "rev-parse", "--verify", rev + "^{commit}").strip()
    results = {}
    for c in checks:
        if c["kind"] != "command":
            continue
        note = ""
        if G.get("locked") and c.get("files"):
            now_blobs, missing = blobs(G, c["files"])
            moved = [f for f in c["files"] if f in missing or now_blobs.get(f) != c.get("blobs", {}).get(f)]
            if moved:
                note = (f"{', '.join(moved)} changed since the lock, which moves the goalposts: put it back, "
                        f"or amend {c['id']} with a reason")
        t0 = time.monotonic()
        res = K.run_fresh(cfg, ws, check_argv(c), rev=rev, timeout=c.get("timeout") or 900, hint=f"check-{c['id']}")
        runs = res.get("runs") or []
        r0 = runs[0] if runs else {"code": None, "out": res.get("patch_error", ""), "timed_out": False}
        ok = bool(runs) and not K.failed(r0) and not note
        tail = r0.get("out", "").rstrip("\n").splitlines()[-12:]
        results[c["id"]] = {"time": stamp(), "pass": ok, "code": r0.get("code"), "timed_out": r0.get("timed_out"),
                            "secs": round(time.monotonic() - t0, 1), "tail": tail, "sha": sha,
                            "final": bool(a.final), "note": note}

    def fn(G):
        seq = bump(G)
        for c in G["checks"]:
            r = results.get(c["id"])
            if r:
                r["seq"] = seq
                if not c.get("results"):
                    r["before_work"] = not G.get("started")
                c.setdefault("results", []).append(r)
                del c["results"][:-30]
        if not only:
            marks(G)["checks"] = seq
        if a.final:
            G["final"] = {"seq": seq, "time": stamp(), "sha": sha}
        if tampered:
            G["tampered"] = stamp()

    mutate(ws, fn)
    G = read_json(ws / LEDGER)
    print(f"Done-when checks{' (final)' if a.final else ''}, from {rev} at {sha[:10]}:")
    for c in live_checks(G):
        if only and c["id"] not in only:
            continue
        r = results.get(c["id"])
        if c["kind"] == "judge":
            print(f"  {c['id']:<4} {check_state(G, c):<15} judge    {short(c['statement'], 70)}")
            continue
        state = "pass" if r["pass"] else ("TIMED OUT" if r["timed_out"] else "FAIL")
        print(f"  {c['id']:<4} {state:<15} command  {short(show_cmd(c), 60)}   ({r['secs']} s)")
        if r["note"]:
            print(f"       {r['note']}")
        elif not r["pass"]:
            for ln in r["tail"][-6:]:
                print(f"       | {ln}")
    cmd = [r for r in results.values()]
    ok = sum(1 for r in cmd if r["pass"])
    judges = sum(1 for c in checks if c["kind"] == "judge")
    print(f"{ok} of {len(cmd)} command checks pass" + (f"; {judges} judge check{'' if judges == 1 else 's'} "
                                                       f"wait{'s' if judges == 1 else ''} for the acceptance judge" if judges else "") + ".")
    if tampered:
        print("WARNING: the checks don't match the lock: goal.json was changed outside goal.py. The report says so.")
    if not G.get("started") and ok < len(cmd):
        print("Before the work starts, failing checks are expected: they show the goal isn't met yet.")
    return 0 if ok == len(cmd) else 1


# ---------------------------------------------------------------- the plan

def check_cycles(steps) -> None:
    graph = {s["id"]: s.get("after", []) for s in steps}
    state = {}

    def visit(n, path):
        if state.get(n) == 1:
            fail(f"that makes a loop: {' -> '.join(path + [n])}")
        if state.get(n) == 2:
            return
        state[n] = 1
        for d in graph.get(n, []):
            visit(d, path + [n])
        state[n] = 2

    for n in graph:
        visit(n, [])


def new_step(G, spec: dict, known: set) -> dict:
    title = str(spec.get("title") or "").strip()
    if not title:
        fail("every step needs a title")
    skill = (spec.get("skill") or "").strip() or None
    kind = spec.get("kind") or ("skill" if skill else "direct")
    if kind not in ("skill", "direct"):
        fail(f"a step's kind is skill or direct, not {kind!r}")
    if kind == "skill" and not skill:
        fail(f"step '{title}': a skill step needs --skill <killer-x>")
    if skill == "killer-goal":
        fail("killer-goal can't run itself as a step")
    after, moves = split_ids(spec.get("after")), split_ids(spec.get("moves"))
    for d in after:
        if d not in known:
            fail(f"step '{title}' comes after {d}, and there's no step {d}")
    ids = {c["id"] for c in G.get("checks", [])}
    for m in moves:
        if m not in ids:
            fail(f"step '{title}' moves {m}, and there's no check {m}")
    return {"id": None, "title": title, "kind": kind, "skill": skill if kind == "skill" else None,
            "answers": (spec.get("answers") or "").strip(), "after": after, "moves": moves,
            "agents_est": parse_agents(spec.get("agents")) or 0, "status": "pending", "attempts": [],
            "created": stamp()}


def cmd_plan_add(a) -> int:
    ws, cfg, G = load(a)
    if G.get("status") == "stopped":
        fail("the run is stopped")

    def fn(G):
        s = new_step(G, {"title": a.title, "kind": a.kind, "skill": a.skill, "answers": a.answers, "after": a.after,
                         "moves": a.moves, "agents": a.agents}, {x["id"] for x in G["steps"]})
        s["id"] = f"S{G['next_step']}"
        G["next_step"] += 1
        G["steps"].append(s)
        if G.get("started"):
            G["plan_changes"].append({"time": stamp(), "step": s["id"], "what": "added", "reason": a.reason or ""})
        bump(G)
        return s

    if G.get("started") and not a.reason:
        fail("the run has started: adding a step is a plan change, so give --reason")
    s = mutate(ws, fn)
    print_step_line(G, s)
    warn_skill(s, G)
    return 0


def warn_skill(s, G) -> None:
    if s.get("kind") != "skill":
        return
    if not skill_location(s["skill"], G["repo"]):
        print(f"  note: {s['skill']} isn't installed (looked in {skill_places(G['repo'])}). "
              f"`next` will say so; skip the step or do the work directly.")


def cmd_plan_load(a) -> int:
    ws, cfg, G = load(a)
    data = read_json(Path(a.file).expanduser(), None) if Path(a.file).expanduser().is_file() else fail(f"no file {a.file}")
    items = data.get("steps") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        fail(f"{a.file} should be a list of steps, or {{\"steps\": [...]}}")
    if G.get("started") and not a.reason:
        fail("the run has started: loading steps is a plan change, so give --reason")

    def fn(G):
        known = {x["id"] for x in G["steps"]}
        n = G["next_step"]
        mapping, made = {}, []
        for i, spec in enumerate(items):
            if not isinstance(spec, dict):
                fail(f"step {i + 1} in {a.file} isn't an object")
            fid = str(spec.get("id") or f"#{i + 1}").upper()
            sid = f"S{n}"
            n += 1
            mapping[fid] = sid
            known_here = known | set(mapping.values())
            spec = dict(spec)
            spec["after"] = [mapping.get(d, d) for d in split_ids(spec.get("after"))]
            s = new_step(G, spec, known_here - {sid})
            s["id"] = sid
            made.append(s)
        G["steps"].extend(made)
        G["next_step"] = n
        check_cycles(G["steps"])
        if G.get("started"):
            for s in made:
                G["plan_changes"].append({"time": stamp(), "step": s["id"], "what": "added", "reason": a.reason})
        bump(G)
        return made

    made = mutate(ws, fn)
    print(f"Loaded {len(made)} step{'' if len(made) == 1 else 's'}:")
    for s in made:
        print_step_line(G, s)
        warn_skill(s, G)
    return 0


def print_step_line(G, s, extra: str = "") -> None:
    deps = f" (after {', '.join(s['after'])})" if s.get("after") else ""
    mv = f" moves {', '.join(s['moves'])}" if s.get("moves") else ""
    est = f" ~{s['agents_est']} agents" if s.get("agents_est") else ""
    print(f"  {s['id']:<4} {s['status']:<8} {step_label(s):<15} {short(s['title'], 60)}{deps}{mv}{est}{extra}")


def cmd_plan_list(a) -> int:
    ws, cfg, G = load(a)
    if not G.get("steps"):
        print("No steps planned yet.")
        return 0
    print(f"Plan for '{short(G['goal'], 70)}':")
    for s in G["steps"]:
        tail = f": {short(s['headline'], 70)}" if s.get("headline") else ""
        if s["status"] in ("failed", "blocked", "skipped", "dropped") and s.get("reason"):
            tail = f": {short(s['reason'], 70)}"
        print_step_line(G, s, tail)
    est = sum(s.get("agents_est", 0) for s in G["steps"] if s["status"] in OPEN)
    print(f"About {est} agents planned for the steps left" + (f"; cap {G['agents_cap']}" if G.get("agents_cap") else "") + ".")
    return 0


def cmd_plan_edit(a) -> int:
    ws, cfg, G = load(a)
    if G.get("started") and not a.reason:
        fail("the run has started: a plan change needs --reason, and the report lists it")

    def fn(G):
        s = get_step(G, a.id)
        if s["status"] in ("done", "dropped"):
            fail(f"{s['id']} is {s['status']}; it can't change now")
        changed = []
        for field, val in (("title", a.title), ("answers", a.answers), ("skill", a.skill)):
            if val is not None:
                s[field] = val.strip()
                changed.append(field)
        if a.skill:
            s["kind"] = "skill"
        if a.kind:
            s["kind"] = a.kind
            if a.kind == "direct":
                s["skill"] = None
            changed.append("kind")
        if s["kind"] == "skill" and not s.get("skill"):
            fail("a skill step needs --skill")
        if a.after is not None:
            after = split_ids(a.after)
            ids = {x["id"] for x in G["steps"]}
            for d in after:
                if d not in ids or d == s["id"]:
                    fail(f"{s['id']} can't come after {d}")
            s["after"] = after
            check_cycles(G["steps"])
            changed.append("after")
        if a.moves is not None:
            ids = {c["id"] for c in G["checks"]}
            mv = split_ids(a.moves)
            for m in mv:
                if m not in ids:
                    fail(f"no check {m}")
            s["moves"] = mv
            changed.append("moves")
        if a.agents is not None:
            s["agents_est"] = parse_agents(a.agents) or 0
            changed.append("agents")
        if not changed:
            fail("nothing to change: give --title, --answers, --skill, --kind, --after, --moves or --agents")
        if G.get("started"):
            G["plan_changes"].append({"time": stamp(), "step": s["id"], "what": "changed " + ", ".join(changed),
                                      "reason": a.reason})
        bump(G)
        return s

    s = mutate(ws, fn)
    print(f"Changed {s['id']}:")
    print_step_line(G, s)
    return 0


def cmd_plan_drop(a) -> int:
    ws, cfg, G = load(a)
    if not a.reason:
        fail("dropping a step needs --reason")

    def fn(G):
        s = get_step(G, a.id)
        if s["status"] in ("done", "running"):
            fail(f"{s['id']} is {s['status']}; record its outcome with `step` instead")
        s["status"], s["reason"] = "dropped", a.reason
        G["plan_changes"].append({"time": stamp(), "step": s["id"], "what": "dropped", "reason": a.reason})
        bump(G)
        return s

    s = mutate(ws, fn)
    print(f"Dropped {s['id']}: {s['title']}. Steps after it may now run.")
    return 0


# ---------------------------------------------------------------- go, next, steps

def cmd_go(a) -> int:
    ws, cfg, G = load(a)
    if G.get("plan_only"):
        fail("this is a plan-only run: nothing runs. Run `checks run`, then `report`.")
    if G["status"] == "running":
        print(f"The run is already going (since {G['started']}).")
        print(lead_line(G))
        return 0
    if G["status"] != "setup":
        fail(f"the run is {G['status']}; use resume")
    if not G.get("locked"):
        fail("lock the done-when checks first (`lock`): they're written before any work starts")
    if not G.get("steps"):
        fail("plan at least one step first")
    now = now_dt()

    def fn(G):
        G["status"] = "running"
        G["started"] = stamp(now)
        if G.get("hours"):
            minutes = G["hours"] * 60
            reserve = min(45.0, max(10.0, minutes * 0.1))
            G["deadline"] = stamp(now + dt.timedelta(minutes=minutes))
            G["work_until"] = stamp(now + dt.timedelta(minutes=max(minutes - reserve, minutes * 0.5)))
        new_lead_token(G)
        bump(G)
        return G

    G = mutate(ws, fn)
    write_marker(G, ws)
    (ws / HOOK_STATE).unlink(missing_ok=True)
    steps = G["steps"]
    skills = ", ".join(step_label(s) for s in steps)
    when = f"until {clock(parse_time(G['deadline']))} (new steps until {clock(parse_time(G['work_until']))})" if G.get("deadline") else "with no time limit"
    print(f"Started: {len(steps)} steps ({skills}) · {len(live_checks(G))} done-when checks · {when} · "
          f"branch {G['branch']} · nothing pushed.")
    print(lead_line(G))
    print(f"Next: {cmdline('next', ws)}")
    return 0


def sub_short(skill: str) -> str:
    return skill[len("killer-"):] if skill and skill.startswith("killer-") else (skill or "")


def done_args(s) -> str:
    """What `step done` needs for this step: the skill's summary.json, or a headline and its report."""
    if s.get("kind") != "skill":
        return f'{s["id"]} --headline "<what changed, and the proof>"'
    return f'{s["id"]} --summary <its run folder>/summary.json'


def print_step_block(ws, G, s, head: str) -> None:
    print(f"{head} {s['id']}: {s['title']}")
    if s["kind"] == "skill":
        loc = skill_location(s["skill"], G["repo"])
        if loc:
            print(f"  Skill: {s['skill']} (installed: {loc.parent})")
        else:
            skip = cmdline("step skipped", ws, f'{s["id"]} --reason "{s["skill"]} is not installed"')
            print(f"  Skill: {s['skill']} is NOT INSTALLED (looked in {skill_places(G['repo'])}). Skip it with "
                  f"`{skip}`, or do the work directly.")
        print(f"  Setup answers: {s.get('answers') or '(none given: the skill defaults, and say so)'}")
        print(f"  Depth for this run: {G.get('depth')}")
    else:
        print("  Direct work: you do it, test first, committing on the goal branch.")
    if s.get("moves"):
        states = ", ".join(f"{m} ({check_state(G, c)})" for m in s["moves"] for c in [step_check(G, m)] if c)
        print(f"  Should move: {states}")
    if s.get("agents_est"):
        cap = f" of {G['agents_cap']}" if G.get("agents_cap") else ""
        print(f"  About {s['agents_est']} agents (used so far: {G.get('agents_used', 0)}{cap})")
    print(f"  Branch: {G['branch']}; it started from {G['base'][:10]}. For a skill's \"this branch's changes\", "
          f"that means the changes since {G['base'][:10]}.")
    n = len(s.get("attempts", []))
    if fails(s):
        last = next((t for t in reversed(s["attempts"]) if t.get("outcome") == "failed"), {})
        print(f"  Attempt {n + 1} (the last try failed: {last.get('reason')}). Use a different approach, and say what "
              f"changed with --approach.")
    print("Do it:")
    approach = ' --approach "<what is different this time>"' if fails(s) else ""
    if head != "IN PROGRESS":
        print(f"  1. {cmdline('step start', ws, s['id'] + approach)}")
    if s["kind"] == "skill":
        print(f"  2. Invoke the Skill tool with skill \"{s['skill']}\" and these args:")
        answers = (s.get("answers") or "").strip().rstrip(".") or "the skill's defaults"
        print("     " + UNATTENDED.format(run=Path(ws).as_posix(), answers=answers,
                                         branch=G["branch"], base=G["base"][:10]))
        print(f"  3. When it finishes: {cmdline('step done', ws, done_args(s))}")
        print("     No summary.json? Use --headline \"<its one-line summary>\" --report <its report.md> instead.")
    else:
        print(f"  2. Do the work on {G['branch']}: a test that fails without the change first, then the change. "
              f"Commit each logical change with a plain message that says what changed and why; no AI attribution, "
              f"no push.")
        done = cmdline("step done", ws, f'{s["id"]} --headline "<what changed, and the proof>"')
        print(f"  3. Then: {done}")
    print(f"     It failed: step failed {s['id']} --reason \"...\". Blocked on the user: step blocked {s['id']} --reason \"...\".")
    print("  4. Then guard, checks run, and next again.")
    print(lead_line(G))


def dep_text(G, deps) -> str:
    return ", ".join(f"{d} ({(step_by_id(G, d) or {}).get('status', 'missing')})" for d in deps)


def step_check(G, cid):
    return next((c for c in G.get("checks", []) if c["id"] == cid), None)


def cmd_next(a) -> int:
    ws, cfg, G = load(a)
    now = now_dt()
    st = G["status"]
    if st == "setup":
        if G.get("plan_only"):
            print("STOP: plan only: nothing runs. Run `checks run` to show where things stand, then `report`.")
        else:
            text, cmd = next_action(G, ws, now)
            print(f"STOP: the run hasn't started. Next: {text}: {cmd}")
        return 1
    if st == "paused":
        print(f"STOP: paused ({G.get('pause_reason') or 'no reason given'}). To carry on: {cmdline('resume', ws)}")
        return 1
    if st == "stopped":
        print("STOP: the run is stopped.")
        return 1
    r = running_step(G)
    if r:
        started = r["attempts"][-1]["started"] if r.get("attempts") else "?"
        print_step_block(ws, G, r, "IN PROGRESS")
        print(f"(started {clock(parse_time(started))}; finish it and record the outcome before the next step)")
        return 0
    m = marks(G)
    if m.get("guard_ok") is False or m.get("changed", 0) > m.get("guard", 0) or m.get("code", 0) > m.get("checks", 0):
        text, cmd = next_action(G, ws, now)
        print(f"FIRST: {text}: {cmd}")
        return 1
    stop = budget_stop(G, now)
    if stop:
        text, cmd = wrapup(G, ws)
        print(f"STOP: {stop}. Wrap up: {text}: {cmd}")
        return 1
    run = runnable_steps(G)
    if run:
        print_step_block(ws, G, run[0], "NEXT")
        return 0
    waiting = [(s, unmet_deps(G, s)) for s in G["steps"] if s["status"] in ("pending", "retry")]
    text, cmd = wrapup(G, ws)
    if waiting:
        why = "; ".join(f"{s['id']} waits on {dep_text(G, deps)}" for s, deps in waiting)
        print(f"NONE: nothing left that can run ({why}); go to the final check. Next: {text}: {cmd}")
    else:
        print(f"NONE: all steps done; go to the final check. Next: {text}: {cmd}")
    return 0


def read_summary(path: str) -> dict:
    p = Path(path).expanduser()
    if p.is_dir():
        p = p / "summary.json"
    if not p.is_file():
        fail(f"no summary at {p}")
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        fail(f"{p} isn't valid JSON: {e}")
    if not isinstance(d, dict):
        fail(f"{p} isn't a JSON object")
    problems = []
    if d.get("status") not in ("done", "partial", "blocked"):
        problems.append("its status should be done, partial or blocked")
    if not isinstance(d.get("headline"), str) or not d["headline"].strip():
        problems.append("it has no headline")
    for k in ("commits", "decisions", "waiting"):
        if k in d and d[k] is not None and not isinstance(d[k], list):
            problems.append(f"{k} should be a list")
    if problems:
        fail(f"{p} isn't a killer skill's summary.json: {'; '.join(problems)}")

    def text(x):
        if isinstance(x, dict):
            t = x.get("text") or x.get("what") or ""
            c = x.get("command")
            return f"{t} (`{c}`)" if c else str(t)
        return str(x)

    return {"path": str(p.resolve()), "skill": d.get("skill"), "status": d["status"], "headline": d["headline"].strip(),
            "report": d.get("report"), "branch": d.get("branch"),
            "commits": [str(c) for c in d.get("commits") or []],
            "decisions": [text(x) for x in d.get("decisions") or [] if text(x)],
            "waiting": [text(x) for x in d.get("waiting") or [] if text(x)],
            "numbers": d.get("numbers") if isinstance(d.get("numbers"), dict) else {}}


def cmd_step(a) -> int:
    ws, cfg, G = load(a)
    action = a.action
    s0 = get_step(G, a.id)
    now = now_dt()
    if action == "start":
        return step_start(a, ws, G, s0, now)
    if action in ("failed", "blocked", "skipped") and not a.reason:
        fail(f"step {action} needs --reason: it goes in the report")
    summary = read_summary(a.summary) if getattr(a, "summary", None) else None
    if action == "done" and not (summary or a.headline):
        fail("step done needs --summary <the sub-run's summary.json> or --headline \"<what changed, and the proof>\"")
    if s0["status"] in ("done", "dropped", "failed", "skipped"):
        fail(f"{s0['id']} is already {s0['status']}")
    if action == "skipped" and s0["status"] == "running" and not a.reason:
        fail("give --reason")
    t = tip(G) if not G.get("plan_only") else ""
    warnings = []
    sub_branch = (summary or {}).get("branch")
    if summary and sub_branch and sub_branch != G["branch"]:
        warnings.append(f"WARNING: {s0['skill'] or 'the skill'} says it committed to {sub_branch}, not {G['branch']}. "
                        f"Bring its commits onto {G['branch']} (git merge --ff-only {sub_branch}, or cherry-pick), "
                        f"and the guard lists the extra branch.")
    if summary and summary["status"] == "blocked" and action == "done":
        warnings.append("WARNING: the sub-run says it was blocked; if it didn't do its job, record step failed or blocked instead.")
    if summary and summary["commits"] and not G.get("plan_only"):
        off = [c for c in summary["commits"] if c.split()[0] and not kit().git_ok(G["repo"], "merge-base", "--is-ancestor", c.split()[0], G["branch"])]
        if off:
            warnings.append(f"WARNING: {len(off)} of its commits aren't on {G['branch']}: {', '.join(off[:3])}")

    def fn(G):
        s = get_step(G, a.id)
        was_started = s["status"] == "running"
        if s["status"] in ("pending", "retry", "blocked") and action in ("done", "failed", "blocked"):
            s["attempts"].append({"n": len(s["attempts"]) + 1, "started": None, "tip": t, "approach": None,
                                  "note": "recorded without step start"})
            was_started = True
        att = s["attempts"][-1] if s["attempts"] else None
        seq = bump(G)
        if att is not None and was_started:
            att.update(ended=stamp(now), outcome=action, reason=a.reason, seq=seq)
            att["commits"] = log_lines(G["repo"], att.get("tip") or G["base"], t) if t else []
        agents = parse_agents(a.agents) if a.agents is not None else None
        if agents is None and was_started and action in ("done", "failed"):
            counted = ((summary or {}).get("numbers") or {}).get("agents")
            agents = counted if isinstance(counted, int) else s.get("agents_est", 0)
        if agents:
            G["agents_used"] = G.get("agents_used", 0) + agents
            s["agents"] = s.get("agents", 0) + agents
        if action == "done":
            s["status"] = "done"
            s["headline"] = a.headline or summary["headline"]
            s["report"] = a.report or (summary or {}).get("report")
            if summary:
                s["summary"] = summary
        elif action == "failed":
            s["reason"] = a.reason
            s["status"] = "failed" if (fails(s) >= MAX_FAILS or a.final) else "retry"
            if summary:
                s["summary"] = summary
        elif action == "blocked":
            s["status"], s["reason"] = "blocked", a.reason
            if summary:
                s["summary"] = summary
        elif action == "skipped":
            s["status"], s["reason"] = "skipped", a.reason
        if was_started:
            marks(G)["changed"] = seq
            if action in ("done", "failed", "blocked"):
                marks(G)["code"] = seq
        for w in warnings:
            G["decisions"].append({"time": stamp(now), "text": f"{s['id']}: {w.replace('WARNING: ', '')}", "source": "killer-goal"})
        return s

    s = mutate(ws, fn)
    for w in warnings:
        print(w)
    if action == "done":
        print(f"{s['id']} done: {s['headline']}")
        commits = s["attempts"][-1].get("commits", []) if s.get("attempts") else []
        if commits:
            print(f"  {len(commits)} commit{'' if len(commits) == 1 else 's'} on {G['branch']} in this step")
        if summary:
            print(f"  from {summary['path']}: {len(summary['decisions'])} decisions, {len(summary['waiting'])} waiting")
    elif action == "failed":
        if s["status"] == "retry":
            print(f"{s['id']} failed (attempt {len(s['attempts'])}). It gets one more try with a different approach: "
                  f"`next` hands it out again.")
        else:
            needs = [x["id"] for x in G["steps"] if s["id"] in x.get("after", [])]
            print(f"{s['id']} failed {fails(s)} time{'' if fails(s) == 1 else 's'}; it's marked failed."
                  + (f" Steps that need it won't run: {', '.join(needs)}." if needs else ""))
    elif action == "blocked":
        print(f"{s['id']} is blocked: {a.reason}. It waits for the user; move on to steps that don't need it.")
    else:
        print(f"{s['id']} skipped: {a.reason}")
    G = read_json(ws / LEDGER)
    text, cmd = next_action(G, ws, now)
    print(f"Next: {text}: {cmd}")
    return 0


def step_start(a, ws, G, s0, now) -> int:
    if G["status"] != "running":
        fail(f"the run is {G['status']}, not running" + ("; start it with `go`" if G["status"] == "setup" else "; `resume` first"))
    if s0["status"] == "running":
        print(f"{s0['id']} is already in progress (attempt {len(s0['attempts'])}).")
        print(lead_line(G))
        return 0
    if s0["status"] in ("done", "dropped", "failed", "skipped"):
        fail(f"{s0['id']} is {s0['status']}")
    other = running_step(G)
    if other:
        fail(f"{other['id']} is still in progress: record its outcome first (one step at a time)")
    deps = unmet_deps(G, s0)
    if deps and not a.force:
        fail(f"{s0['id']} comes after {dep_text(G, deps)}")
    if len(s0.get("attempts", [])) >= MAX_STARTS:
        fail(f"{s0['id']} has been started {MAX_STARTS} times; record it as failed or skipped")
    if fails(s0) and not a.approach:
        last = next((t for t in reversed(s0["attempts"]) if t.get("outcome") == "failed"), {})
        fail(f"{s0['id']} failed before ({last.get('reason')}). A retry needs a different approach: "
             f"add --approach \"<what's different this time>\"")
    m = marks(G)
    if not a.force and (m.get("guard_ok") is False or m.get("changed", 0) > m.get("guard", 0)
                        or m.get("code", 0) > m.get("checks", 0)):
        text, cmd = next_action(G, ws, now)
        fail(f"FIRST: {text}: {cmd}")
    stop = budget_stop(G, now)
    if stop and not a.force:
        fail(f"STOP: {stop}")
    t = tip(G)

    def fn(G):
        s = get_step(G, a.id)
        s["attempts"].append({"n": len(s["attempts"]) + 1, "started": stamp(now), "tip": t, "approach": a.approach})
        s["status"] = "running"
        bump(G)
        return s

    s = mutate(ws, fn)
    print(f"Started {s['id']} (attempt {len(s['attempts'])}): {s['title']}")
    print(lead_line(G))
    return 0


# ---------------------------------------------------------------- the guard

def guard_compare(G, now_snap) -> tuple[list, list]:
    st = G["start"]
    repo, goal, orig = G["repo"], G["branch"], st.get("branch")
    problems, notes = [], []
    if orig:
        cur = now_snap["heads"].get(orig)
        if cur is None:
            problems.append(f"Your branch {orig} is gone. It was at {st['sha'][:10]}. Put it back: "
                            f"git branch {orig} {st['sha']}")
        elif cur != st["sha"]:
            extra = log_lines(repo, st["sha"], cur)
            problems.append(f"Your branch {orig} moved: it was at {st['sha'][:10]}, it's at {cur[:10]} now"
                            + (f" ({len(extra)} new commit{'' if len(extra) == 1 else 's'}: {'; '.join(extra[:3])})" if extra else "")
                            + f". If the run did it: copy anything worth keeping onto {goal} "
                            f"(git cherry-pick {st['sha'][:10]}..{cur[:10]}), then put it back: git branch -f {orig} {st['sha']}")
    allowed_new = set(G.get("guard_known_branches", []))
    for name, sha in sorted(st["heads"].items()):
        if name in (orig, goal):
            continue
        cur = now_snap["heads"].get(name)
        if cur is None:
            problems.append(f"The branch {name} was deleted (it was at {sha[:10]}). Put it back: git branch {name} {sha}")
        elif cur != sha:
            problems.append(f"The branch {name} moved from {sha[:10]} to {cur[:10]}. If the run did it, put it back: "
                            f"git branch -f {name} {sha}")
    for name in sorted(set(now_snap["heads"]) - set(st["heads"]) - {goal}):
        ahead = log_lines(repo, G["base"], now_snap["heads"][name])
        if name.startswith("killer-"):
            notes.append(f"New branch {name} from a sub-skill ({len(ahead)} commit{'' if len(ahead) == 1 else 's'} "
                         f"past the base). Its work belongs on {goal}.")
        elif name not in allowed_new:
            problems.append(f"A new branch {name} appeared. If the run made it, bring its commits onto {goal} and "
                            f"delete it: git branch -D {name}")
    if not G.get("plan_only"):
        tipsha = now_snap["heads"].get(goal)
        if not tipsha:
            problems.append(f"The goal branch {goal} is gone. The run's work was on it; look in `git reflog`.")
        elif not kit().git_ok(repo, "merge-base", "--is-ancestor", G["base"], tipsha):
            problems.append(f"The goal branch {goal} no longer contains its starting commit {G['base'][:10]}: "
                            f"something reset or rewrote it. `git reflog {goal}` shows where it was.")
    want = f"refs/heads/{goal}" if goal else st["head_ref"]
    if want and now_snap["head_ref"] != want:
        where = now_snap["head_ref"][11:] if now_snap["head_ref"].startswith("refs/heads/") else f"a detached {now_snap['head'][:10]}"
        problems.append(f"HEAD is on {where}, not {goal}. Switch back: git switch {goal} (uncommitted changes come along)")
    rem_old, rem_new = st["remotes"], now_snap["remotes"]
    changes = []
    for name in sorted(set(rem_old) | set(rem_new)):
        a, b = rem_old.get(name), rem_new.get(name)
        if a != b:
            changes.append(f"{name}: {'new' if not a else a[:10]} -> {'gone' if not b else b[:10]}")
    if changes:
        problems.append("REMOTE-TRACKING REFS CHANGED: something pushed or fetched during the run. "
                        + "; ".join(changes[:8]) + ". A push can't be undone from here: find what did it, tell the user "
                        "in the report, and once it's understood run guard --accept \"<what happened>\".")
    if now_snap["fetch_head"] != st["fetch_head"]:
        problems.append("FETCH_HEAD changed: something fetched from a remote during the run. Find what did it; "
                        "then guard --accept \"<what happened>\".")
    if now_snap["upstream"] != st["upstream"]:
        added = sorted(set(now_snap["upstream"]) - set(st["upstream"]))
        removed = sorted(set(st["upstream"]) - set(now_snap["upstream"]))
        problems.append("Branch upstream settings changed (a sign of `git push -u`): "
                        + "; ".join([f"+ {x}" for x in added] + [f"- {x}" for x in removed]))
    tag_changes = [n for n in sorted(set(st["tags"]) | set(now_snap["tags"])) if st["tags"].get(n) != now_snap["tags"].get(n)]
    if tag_changes:
        problems.append(f"Tags changed: {', '.join(tag_changes[:8])}. Tagging waits for the user. If the run made "
                        f"them, delete them: git tag -d {' '.join(tag_changes[:8])}")
    if now_snap["stash"] != st["stash"]:
        problems.append(f"The stash changed ({len(st['stash'])} entries at the start, {len(now_snap['stash'])} now). "
                        f"The user's stash must stay as it was: `git stash list` and the reflog show what moved.")
    if not G.get("plan_only") and now_snap["heads"].get(goal):
        out = git(repo, "log", "--format=%h%x00%B%x1e", f"{G['base']}..{goal}", check=False)
        for rec in out.split("\x1e"):
            h, _, body = rec.strip().partition("\x00")
            if h and ATTRIBUTION.search(body):
                problems.append(f"Commit {h} on {goal} has AI attribution in its message. Reword it so the history "
                                f"reads as the user's own work.")
    dirty = git(repo, "status", "--porcelain=v1", "--untracked-files=all", check=False).splitlines()
    if dirty:
        notes.append(f"{len(dirty)} uncommitted change{'' if len(dirty) == 1 else 's'} in the project "
                     f"({', '.join(x[3:] for x in dirty[:4])}{'...' if len(dirty) > 4 else ''}). Checks run from the "
                     f"branch's commits, so commit what belongs to the work and remove what doesn't.")
    return problems, notes


def cmd_guard(a) -> int:
    ws, cfg, G = load(a)
    snap = snapshot(Path(G["repo"]))
    problems, notes = guard_compare(G, snap)
    if a.accept:
        hard = [p for p in problems if p.startswith(("Your branch", "HEAD is on", "The goal branch"))]
        if hard:
            for p in hard:
                print(f"PROBLEM: {p}")
            fail("--accept can't accept these: put them right first")

        def acc(G):
            st = G["start"]
            st.update(remotes=snap["remotes"], tags=snap["tags"], stash=snap["stash"], fetch_head=snap["fetch_head"],
                      upstream=snap["upstream"])
            for name, sha in snap["heads"].items():
                if name in (st.get("branch"), G["branch"]):
                    continue
                if name in st["heads"] or not name.startswith("killer-"):
                    st["heads"][name] = sha
            for name in list(st["heads"]):
                if name not in snap["heads"] and name != st.get("branch"):
                    del st["heads"][name]
            G["decisions"].append({"time": stamp(), "text": f"Guard: accepted changes to the repository ({'; '.join(short(p, 120) for p in problems)}) because: {a.accept}",
                                   "source": "killer-goal"})

        mutate(ws, acc)
        G = read_json(ws / LEDGER)
        problems, notes = guard_compare(G, snap)
        print(f"Accepted, and recorded as a decision: {a.accept}")

    def rec(G):
        seq = bump(G)
        G.setdefault("guards", []).append({"time": stamp(), "seq": seq, "ok": not problems, "problems": problems, "notes": notes})
        del G["guards"][:-30]
        if problems:
            G.setdefault("guard_problems", [])
            for p in problems:
                if p not in G["guard_problems"]:
                    G["guard_problems"].append(p)
        marks(G)["guard"] = seq
        marks(G)["guard_ok"] = not problems

    mutate(ws, rec)
    orig = G["start"].get("branch")
    for p in problems:
        print(f"PROBLEM: {p}")
    for n in notes:
        print(f"note: {n}")
    if problems:
        print(f"GUARD FAILED: {len(problems)} problem{'' if len(problems) == 1 else 's'}. Fix {'it' if len(problems) == 1 else 'them'}, "
              f"then run the guard again: {cmdline('guard', ws)}")
        return 1
    print(f"Guard OK: {('your branch ' + orig + ' is untouched at ' + G['start']['sha'][:10] + '; ') if orig else ''}"
          f"no other branch moved; no push, fetch, tag or stash change; HEAD is on {G['branch']}.")
    return 0


# ---------------------------------------------------------------- budget, decisions, waiting

def cmd_budget(a) -> int:
    ws, cfg, G = load(a)
    add = parse_agents(a.agents) if a.agents else 0
    if add or a.cap is not None:
        def fn(G):
            if add:
                G["agents_used"] = G.get("agents_used", 0) + add
            if a.cap is not None:
                old = G.get("agents_cap")
                G["agents_cap"] = a.cap if a.cap > 0 else None
                G["decisions"].append({"time": stamp(), "text": f"Agent cap changed from {old or 'none'} to {G['agents_cap'] or 'none'}",
                                       "source": "killer-goal"})
            bump(G)
        mutate(ws, fn)
        G = read_json(ws / LEDGER)
    now = now_dt()
    started = parse_time(G.get("started"))
    dl, wu = parse_time(G.get("deadline")), parse_time(G.get("work_until"))
    if started:
        used = (now - started).total_seconds()
        if dl:
            print(f"Time: {hm(used)} used of {hm((dl - started).total_seconds())}; {hm((dl - now).total_seconds())} left "
                  f"(new steps until {clock(wu)}, then the wrap-up until {clock(dl)})")
        else:
            print(f"Time: {hm(used)} used; no limit")
    else:
        print("Time: the clock starts at `go`" + (f"; {G['hours']} hours" if G.get("hours") else "; no limit"))
    cap = G.get("agents_cap")
    print(f"Agents: about {G.get('agents_used', 0)} used" + (f" of {cap} (one kept for the acceptance judge)" if cap else "; no cap"))
    steps = G.get("steps", [])
    count = lambda *st: sum(1 for s in steps if s["status"] in st)
    print(f"Steps: {count('done')} done, {count('running')} running, {count('pending', 'retry')} left, "
          f"{count('blocked')} blocked, {count('failed')} failed, {count('skipped', 'dropped')} skipped or dropped")
    stop = budget_stop(G, now) if G.get("status") == "running" else None
    if stop:
        print(f"STOP: {stop}")
        return 1
    print("OK")
    return 0


def cmd_decide(a) -> int:
    ws, cfg, G = load(a)
    if not a.text.strip():
        fail("say what you decided")
    mutate(ws, lambda G: (G["decisions"].append({"time": stamp(), "text": a.text.strip(), "source": "killer-goal"}), bump(G)))
    print(f"Recorded under \"Decisions to check\": {a.text.strip()}")
    return 0


def cmd_wait(a) -> int:
    ws, cfg, G = load(a)
    if not a.text.strip() or not a.command.strip():
        fail("say what's waiting and give its exact --command")
    mutate(ws, lambda G: (G["waiting"].append({"time": stamp(), "text": a.text.strip(), "command": a.command.strip(),
                                                 "source": "killer-goal"}), bump(G)))
    print(f"Recorded under \"Waiting for your OK\": {a.text.strip()} (`{a.command.strip()}`)")
    return 0


# ---------------------------------------------------------------- sandboxes and the acceptance judge

def make_box(ws, cfg, G, name, net) -> Path:
    return kit().make_sandbox(cfg, ws, name, rev=check_rev(G), network=True if net else None)


def cmd_sandbox(a) -> int:
    ws, cfg, G = load(a)
    root = make_box(ws, cfg, G, a.name, a.net)
    print(f"Sandbox {a.name} is ready, from {check_rev(G)}.")
    print(kit().sandbox_text(root, bool(cfg["network"] or a.net), rev=check_rev(G)))
    return 0


def template(heading: str) -> str:
    text = (SKILL_DIR / "references" / "briefs.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(heading)}\s*$(.*?)^(`{{3,4}})markdown\s*\n(.*?)^\2\s*$", text, re.M | re.S)
    if not m:
        fail(f"references/briefs.md has no '{heading}' template")
    return m.group(3)


def cmd_judge_brief(a) -> int:
    K = kit()
    ws, cfg, G = load(a)
    checks = live_checks(G)
    if not checks:
        fail("there are no done-when checks to judge")
    taken = set(read_json(ws / "config.json", {}).get("sandboxes", {}))
    name, n = "judge", 2
    while name in taken or K.box_path(cfg, name).exists():
        name, n = f"judge-{n}", n + 1
    root = make_box(ws, cfg, G, name, a.net)
    net = bool(cfg["network"] or a.net)
    box_t, body_t = template("The sandbox block"), template("Acceptance judge")
    need = {"box": ("<path>", "<run command>", "<scratch>", "<network>", "<forbidden>"),
            "body": ("<goal>", "<checks>", "<base>", "<sandbox block>")}
    gone = [x for x in need["box"] if x not in box_t] + [x for x in need["body"] if x not in body_t]
    if gone:
        K.drop_sandbox(ws, cfg, name)
        fail(f"references/briefs.md is out of step with goal.py: its templates have no {', '.join(gone)}")
    lines = []
    for c in checks:
        lines.append(f"- **{c['id']}**: {c['statement']}")
        if c["kind"] == "command":
            lines.append(f"  Command, run from the project copy: `{show_cmd(c)}`")
    box_v = {"<path>": str(root / "repo"), "<run command>": K.run_command_line(root), "<scratch>": str(root / "scratch"),
             "<network>": "the network on" if net else "the network off (this machine's own addresses still work)",
             "<forbidden>": "; ".join(a.forbid) if a.forbid else "nothing beyond the above."}
    box = re.sub("|".join(re.escape(k) for k in box_v), lambda m: box_v[m.group(0)], box_t)
    body_v = {"<goal>": G["goal"].replace(NL, NL + "> "), "<checks>": NL.join(lines), "<base>": G["base"],
              "<sandbox block>": box.rstrip(NL)}
    body = re.sub("|".join(re.escape(k) for k in body_v), lambda m: body_v[m.group(0)], body_t)
    leaks = [x for x in (str(ws), Path(ws).as_posix()) if x in body]
    if leaks:
        K.drop_sandbox(ws, cfg, name)
        fail(f"the judge's brief would show it {leaks[0]}; it must not see the run")
    (ws / "judge-brief.md").write_text(body, encoding="utf-8")

    def fn(G):
        seq = bump(G)
        G["judge_brief"] = {"seq": seq, "time": stamp(), "sandbox": name}
        G["agents_used"] = G.get("agents_used", 0) + 1

    mutate(ws, fn)
    print(body.rstrip("\n"))
    print()
    print("-" * 60)
    print(f"Send the brief above, exactly as written, to one fresh agent: subagent_type \"general-purpose\", "
          f"run_in_background false, description \"Acceptance check\". It's saved as {ws / 'judge-brief.md'}.")
    print(f"Then: {cmdline('judge', ws, '--sandbox ' + name)}")
    return 0


def cmd_judge(a) -> int:
    K = kit()
    ws, cfg, G = load(a)
    if not (a.sandbox or a.reply):
        fail("give --sandbox <name> (the judge's sandbox) or --from <a saved reply>")
    box = K.find_sandbox(cfg, a.sandbox) if a.sandbox else None
    path = (box / "scratch" / K.REPLY) if box else Path(a.reply)
    if not path.is_file():
        fail(f"no reply at {path}. If the judge wrote none, send the same brief to a fresh judge once "
             f"(judge-brief makes a new sandbox for it).")
    data = K.read_reply(path)
    ids = {c["id"]: c for c in live_checks(G)}
    rows = data.get("checks")
    problems, verdicts = [], {}
    if not isinstance(rows, list):
        problems.append("it has no \"checks\" list")
        rows = []
    for r in rows:
        if not isinstance(r, dict):
            problems.append("an entry in \"checks\" isn't an object")
            continue
        cid = str(r.get("id", "")).upper()
        v = str(r.get("verdict", "")).lower().strip()
        ev = r.get("evidence")
        if cid not in ids:
            problems.append(f"it judges {cid or 'a check with no id'}, which isn't one of the checks")
        elif v not in ("met", "not-met", "unclear"):
            problems.append(f"{cid}'s verdict is {v!r}, not met, not-met or unclear")
        elif not isinstance(ev, str) or not ev.strip():
            problems.append(f"{cid} has no evidence")
        else:
            verdicts[cid] = {"verdict": v, "evidence": ev.strip()}
    missing = [c for c in ids if c not in verdicts]
    if missing and not problems:
        problems.append(f"it doesn't judge {', '.join(missing)}")
    if problems:
        fail(f"the judge's reply can't be used: {'; '.join(problems)}. Send the same brief to a fresh judge "
             f"once (judge-brief makes a new sandbox for it).")
    overall = data.get("goal") if isinstance(data.get("goal"), dict) else {}
    keep = Path(ws) / "judge" / (a.sandbox or "reply")
    if box:
        K.keep_scratch(box, keep)
    shutil.copy2(path, Path(ws) / f"judge-reply-{a.sandbox or 'file'}.json")

    def fn(G):
        seq = bump(G)
        G["judge"] = {"seq": seq, "time": stamp(), "verdicts": verdicts, "goal": overall.get("verdict"),
                      "missing": [str(x) for x in overall.get("missing") or []], "notes": str(data.get("notes") or ""),
                      "after_final": bool(G.get("final"))}

    mutate(ws, fn)
    if box and not a.keep:
        K.drop_sandbox(ws, cfg, a.sandbox)
    met = sum(1 for v in verdicts.values() if v["verdict"] == "met")
    print(f"Acceptance judge: {met} of {len(verdicts)} checks met.")
    for cid, v in verdicts.items():
        print(f"  {cid:<4} {v['verdict']:<8} {short(v['evidence'], 100)}")
    if not G.get("final"):
        print("note: there's no final check yet, so this verdict doesn't count until the judge runs after `checks run --final`.")
    G = read_json(ws / LEDGER)
    res, why = outcome(G)
    print(f"Result so far: {res}: {why}")
    return 0


# ---------------------------------------------------------------- pause, resume, stop

def cmd_pause(a) -> int:
    ws, cfg, G = load(a)
    if G["status"] != "running":
        fail(f"the run is {G['status']}, not running")
    mutate(ws, lambda G: (G.update(status="paused", pause_reason=a.reason or "", paused=stamp()), bump(G)))
    print(f"Paused{': ' + a.reason if a.reason else ''}. The keep-going hook and `next` wait until: {cmdline('resume', ws)}")
    return 0


def cmd_resume(a) -> int:
    ws, cfg, G = load(a)
    if G.get("plan_only"):
        fail("a plan-only run doesn't run")
    if G["status"] == "setup":
        fail("the run hasn't started; use go")
    now = now_dt()
    dl = parse_time(G.get("deadline"))
    if dl and now >= dl and not a.hours:
        fail(f"the deadline ({clock(dl)}) has passed; give --hours N to carry on for N more hours")

    def fn(G):
        was = G["status"]
        G["status"] = "running"
        if a.hours:
            minutes = a.hours * 60
            G["deadline"] = stamp(now + dt.timedelta(minutes=minutes))
            G["work_until"] = stamp(now + dt.timedelta(minutes=max(minutes - min(45.0, max(10.0, minutes * 0.1)), minutes * 0.5)))
            G["decisions"].append({"time": stamp(now), "text": f"Time extended: {a.hours} more hours from {clock(now)}",
                                   "source": "killer-goal"})
        G.pop("pause_reason", None)
        new_lead_token(G)
        bump(G)
        return was

    was = mutate(ws, fn)
    G = read_json(ws / LEDGER)
    write_marker(G, ws)
    (ws / HOOK_STATE).unlink(missing_ok=True)
    print(f"Resumed (it was {was}). The keep-going hook will treat the next session to stop with the line below "
          f"in its transcript as the lead.")
    print(lead_line(G))
    text, cmd = next_action(G, ws, now)
    print(f"Next: {text}: {cmd}")
    return 0


def cmd_stop(a) -> int:
    ws, cfg, G = load(a)
    mutate(ws, lambda G: (G.update(status="stopped", stopped=stamp(), stop_reason=a.reason or ""), bump(G)))
    marker_path(G).unlink(missing_ok=True)
    n = kit().clean_sandboxes(ws, cfg)
    print(f"Stopped. The active marker is cleared, so the keep-going hook is dormant again"
          + (f"; deleted {n} sandbox{'' if n == 1 else 'es'}" if n else "") + ".")
    if G.get("report"):
        print(f"Report: {ws / 'report.md'}")
    else:
        print(f"There's no report yet: {cmdline('report', ws)}")
    if G.get("branch") and not G.get("plan_only"):
        print(f"The work is on {G['branch']}; you're still on it.")
    return 0


# ---------------------------------------------------------------- status

def find_active(cwd: Path) -> list[tuple[Path, dict]]:
    out = []
    adir = active_dir()
    for f in sorted(adir.glob("*.json")) if adir.is_dir() else []:
        try:
            m = read_json(f)
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and m.get("project") and inside(str(cwd), m["project"]):
            out.append((f, m))
    return out


def sub_runs(G, s) -> list[Path]:
    """Run folders a skill step's skill probably made: newer than the step's start."""
    started = parse_time((s.get("attempts") or [{}])[-1].get("started"))
    if not started or not s.get("skill"):
        return []
    K = kit()
    short_name = sub_short(s["skill"])
    roots = []
    env = os.environ.get(K.env_name(short_name, "HOME"))
    home = Path(env).expanduser() if env else Path.home() / ".claude" / f"killer-{short_name}"
    top = Path(G["repo"])
    roots.append(home / K.project_key(top, True))
    if s["skill"] == "killer-slides" and home.is_dir():     # its runs are kept per deck, not per project
        roots += [d for d in home.iterdir() if d.is_dir()]
    if s["skill"] == "killer-council":                      # ~/.claude/council-runs/<date>-<slug>/, one for all projects
        roots += list(dict.fromkeys([Path.home() / ".claude" / "council-runs", claude_dir() / "council-runs"]))
    found = []
    for r in roots:
        if not r.is_dir():
            continue
        for d in r.iterdir():
            try:
                if d.is_dir() and dt.datetime.fromtimestamp(d.stat().st_mtime).astimezone() >= started - dt.timedelta(seconds=5):
                    found.append(d)
            except OSError:
                continue
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[:2]


def cmd_status(a) -> int:
    if a.workspace:
        ws, cfg, G = load(a)
    else:
        here = Path.cwd()
        found = find_active(here)
        if not found:
            print(f"No killer-goal run is going for {here}.")
            K = kit()
            top, is_git = K.find_top(here)
            if is_git:
                proj = goal_home() / K.project_key(top, True)
                past = sorted((p for p in proj.glob("*/goal.json")), key=lambda p: p.stat().st_mtime, reverse=True) if proj.is_dir() else []
                if past:
                    G0 = read_json(past[0], {}) or {}
                    print(f"The last one: {past[0].parent} ({G0.get('status')}): '{short(G0.get('goal', ''), 70)}'")
            return 2
        if len(found) > 1:
            found.sort(key=lambda fm: len(fm[1]["project"]), reverse=True)
            if len(found[0][1]["project"]) == len(found[1][1]["project"]):
                print("More than one killer-goal run is going here; pick one with -w:")
                for _, m in found:
                    print(f"  {m['workspace']}  '{short(m.get('goal', ''), 60)}'")
                return 2
        a.workspace = found[0][1]["workspace"]
        ws, cfg, G = load(a)
    now = now_dt()
    print(f"killer-goal:{G['run_id']} · {G['status']} · '{short(G['goal'], 100)}'")
    print(f"Run folder: {ws}")
    if G.get("plan_only"):
        print(f"Plan only: nothing changes; checks run from {G['base'][:10]}.")
    else:
        snap_head = git(G["repo"], "symbolic-ref", "-q", "--short", "HEAD", check=False).strip()
        on = "HEAD is on it" if snap_head == G["branch"] else f"HEAD is on {snap_head or 'a detached commit'}, NOT on it"
        lastg = (G.get("guards") or [None])[-1]
        gtxt = f"last guard {clock(parse_time(lastg['time']))}: {'OK' if lastg['ok'] else 'FAILED'}" if lastg else "no guard run yet"
        orig = G["start"].get("branch")
        print(f"Branch: {G['branch']} ({on}) · started from {G['base'][:10]}"
              + (f" · your branch {orig} at {G['start']['sha'][:10]}" if orig else "") + f" · {gtxt}")
    started, dl = parse_time(G.get("started")), parse_time(G.get("deadline"))
    tline = "not started" if not started else (f"{hm((now - started).total_seconds())} used, {time_left(G, now)}"
                                                + (f" (deadline {clock(dl)}, new steps until {clock(parse_time(G['work_until']))})" if dl else ""))
    cap = G.get("agents_cap")
    steps = G.get("steps", [])
    cnt = lambda *st: sum(1 for s in steps if s["status"] in st)
    print(f"Time: {tline} · Agents: about {G.get('agents_used', 0)}{' of ' + str(cap) if cap else ''} · "
          f"Steps: {cnt('done')} done, {cnt('running')} running, {cnt('pending', 'retry')} left, {cnt('blocked')} blocked, {cnt('failed')} failed")
    print("Done when" + (" (locked)" if G.get("locked") else " (NOT locked)") + ":")
    for c in G.get("checks", []):
        if c.get("dropped"):
            continue
        what = show_cmd(c) if c["kind"] == "command" else c["statement"]
        print(f"  {c['id']:<4} {check_state(G, c):<15} {c['kind']:<8} {short(what, 70)}")
    print("Plan:")
    for s in steps:
        tail = ""
        if s.get("headline"):
            tail = f": {short(s['headline'], 60)}"
        elif s.get("reason") and s["status"] in ("failed", "blocked", "skipped", "dropped", "retry"):
            tail = f": {short(s['reason'], 60)}"
        if s["status"] == "running":
            att = s["attempts"][-1]
            tail = f"  [attempt {att['n']}, since {clock(parse_time(att.get('started')))}]"
        print_step_line(G, s, tail)
        if s["status"] == "running":
            if s.get("answers"):
                print(f"       setup answers: {s['answers']}")
            for d in sub_runs(G, s):
                has = (d / "summary.json").is_file()
                print(f"       its run folder, probably: {d}" + (" (summary.json is there: it finished)" if has else " (no summary.json yet)"))
    print(f"Decisions to check: {len(G.get('decisions', [])) + sum(len((s.get('summary') or {}).get('decisions', [])) for s in steps)} · "
          f"Waiting for the user: {len(G.get('waiting', [])) + sum(len((s.get('summary') or {}).get('waiting', [])) for s in steps)} · "
          f"Amendments: {len(G.get('amendments', []))}")
    H = read_json(ws / HOOK_STATE, {}) or {}
    if H:
        line = f"Keep-going hook: blocked {H.get('blocks', 0)} stop{'' if H.get('blocks', 0) == 1 else 's'}"
        if H.get("stalled"):
            line += f"; STALLED at {clock(parse_time(H['stalled']['time']))}: {H['stalled']['reason']} (record progress, or resume, to wake it)"
        print(line)
    text, cmd = next_action(G, ws, now)
    print(f"Next: {text}" + (f": {cmd}" if cmd else ""))
    return 0


# ---------------------------------------------------------------- report

def md_cell(s) -> str:
    return " ".join(str(s or "").split()).replace("|", "\\|")


def cmd_report(a) -> int:
    ws, cfg, G = load(a)
    repo, now = G["repo"], now_dt()
    res, why = outcome(G)
    steps, checks = G.get("steps", []), live_checks(G)
    fr = final_results(G)
    verdicts = (G.get("judge") or {}).get("verdicts", {}) if judge_fresh(G) else {}
    t = tip(G) if not G.get("plan_only") else G["base"]
    start_sha = G["start"]["sha"]
    commits = log_lines(repo, G["base"], t) if not G.get("plan_only") else []
    all_log = log_lines(repo, start_sha, t) if not G.get("plan_only") else []
    stat = git(repo, "diff", "--stat", f"{G['base']}..{t}", check=False).rstrip() if commits else ""
    orig = G["start"].get("branch")
    L = [f"# killer-goal: {G['goal']}", ""]
    L.append(f"**Result:** {res}: {why}.")
    L.append("")
    L.append(f"Run folder: `{ws}` · Branch: `{G['branch']}` · Started {G.get('started') or 'never'} · Report written {stamp(now)}")
    if G.get("tampered"):
        L.append("")
        L.append(f"**Warning:** at {G['tampered']} the done-when checks didn't match their lock: goal.json was changed outside goal.py.")
    L += ["", "## Done when", "", "| Check | Kind | Final state | Evidence | Judge |", "|---|---|---|---|---|"]
    first_pass = {}
    for c in checks:
        res_list = c.get("results", [])
        before = next((r for r in res_list if r.get("before_work")), None)
        fp = next((r for r in res_list if r.get("pass")), None)
        if fp:
            prior = sorted((t2["seq"], s["id"]) for s in steps for t2 in s.get("attempts", [])
                           if t2.get("seq") and t2["seq"] < fp["seq"] and t2.get("outcome") == "done")
            first_pass[c["id"]] = prior[-1][1] if prior else None
        if c["kind"] == "command":
            r = fr.get(c["id"])
            state = ("pass" if r["pass"] else ("timed out" if r.get("timed_out") else "FAIL")) if r else "not run in a final check"
            ev = f"`{show_cmd(c)}`"
            if r and r.get("note"):
                ev += f"; {r['note']}"
            elif r and not r["pass"] and r.get("tail"):
                ev += f"; last line: {r['tail'][-1]}"
            if before is not None and not before.get("pass") and r and r.get("pass"):
                ev += "; failed before the work" + (f", passes since {first_pass[c['id']]}" if first_pass.get(c["id"]) else "")
        else:
            state = "(judge only)"
            ev = "the acceptance judge's evidence"
        v = verdicts.get(c["id"])
        jtxt = f"{v['verdict']}: {v['evidence']}" if v else ("no judge" if not judge_fresh(G) else "not judged")
        L.append(f"| {c['id']}: {md_cell(c['statement'])} | {c['kind']} | {state} | {md_cell(ev)} | {md_cell(short(jtxt, 400))} |")
    if judge_fresh(G) and (G["judge"].get("missing") or G["judge"].get("notes")):
        L.append("")
        if G["judge"].get("missing"):
            L.append("The judge says the goal is still missing:")
            L += [f"- {x}" for x in G["judge"]["missing"]]
        if G["judge"].get("notes"):
            L.append(f"\nThe judge's notes: {G['judge']['notes']}")
    L += ["", "## What was done", ""]
    if steps:
        L += ["| Step | Skill | Outcome | Headline | Report |", "|---|---|---|---|---|"]
        for s in steps:
            rep = s.get("report")
            outc = s["status"] + (f" (attempt {len(s['attempts'])})" if len(s.get("attempts", [])) > 1 else "")
            head = s.get("headline") or s.get("reason") or ""
            if G.get("plan_only"):
                outc, head = "planned, not run", s.get("answers") or ""
            L.append(f"| {s['id']}: {md_cell(s['title'])} | {step_label(s)} | {outc} | {md_cell(head)} | "
                     f"{('[' + Path(rep).name + '](' + Path(rep).as_posix() + ')') if rep else ''} |")
    else:
        L.append("No steps were planned.")
    L += ["", "## On the branch", ""]
    if G.get("plan_only"):
        L.append("Plan only: nothing was changed.")
    elif all_log:
        L += ["```text", f"$ git log --oneline {start_sha[:10]}..{G['branch']}"] + all_log + ["```"]
        if G.get("wip"):
            L.append(f"\nThe first commit, {G['wip'][:10]}, is your uncommitted work from before the run, carried here "
                     f"so {orig or 'your branch'} stayed exactly as it was.")
        if stat:
            L += ["", f"What the run changed (`git diff --stat {G['base'][:10]}..{G['branch']}`):", "", "```text", stat, "```"]
    else:
        L.append("No commits.")
    decisions = [(d.get("source", "killer-goal"), d["text"]) for d in G.get("decisions", [])]
    waiting = [(w.get("source", "killer-goal"), f"{w['text']}: `{w['command']}`") for w in G.get("waiting", [])]
    for s in steps:
        sm = s.get("summary") or {}
        decisions += [(f"{s['id']} {s.get('skill') or ''}".strip(), d) for d in sm.get("decisions", [])]
        waiting += [(f"{s['id']} {s.get('skill') or ''}".strip(), w) for w in sm.get("waiting", [])]
    if not G.get("plan_only") and commits and orig:
        waiting.append(("killer-goal", f"Merge the work into {orig} when you're happy with it: "
                                       f"`git switch {orig} && git merge --ff-only {G['branch']}`"))
    if G.get("pr") and G["pr"].get("url"):
        waiting.insert(0, ("killer-goal", f"A draft pull request is open, because you chose that at setup: {G['pr']['url']}"))
    L += ["", "## Decisions to check", ""]
    L += [f"- {d} ({src})" for src, d in decisions] or ["None."]
    L += ["", "## Waiting for your OK", ""]
    L += [f"- {w} ({src})" for src, w in waiting] or ["Nothing."]
    L += ["", "## What didn't work", ""]
    bad = []
    for s in steps:
        if s["status"] in ("failed", "blocked", "retry"):
            tries = "; ".join(f"attempt {t2['n']}" + (f" ({t2['approach']})" if t2.get("approach") else "") + f": {t2.get('reason') or t2.get('outcome')}"
                              for t2 in s.get("attempts", []) if t2.get("outcome"))
            label = "blocked, waiting for you" if s["status"] == "blocked" else s["status"]
            bad.append(f"- {s['id']} {s['title']} ({step_label(s)}): {label}: {s.get('reason')}" + (f". Tried: {tries}" if tries else ""))
        elif s["status"] == "pending" and G.get("started"):
            deps = unmet_deps(G, s)
            bad.append(f"- {s['id']} {s['title']}: not run" + (f" (it needs {', '.join(deps)})" if deps else ""))
        elif s["status"] in ("skipped", "dropped"):
            bad.append(f"- {s['id']} {s['title']}: {s['status']}: {s.get('reason')}")
    for p in G.get("guard_problems", []):
        bad.append(f"- Guard: {p}")
    H = read_json(ws / HOOK_STATE, {}) or {}
    if H.get("stalls"):
        bad.append(f"- The keep-going hook stopped blocking {H['stalls']} time{'' if H['stalls'] == 1 else 's'}"
                   + (f", last at {H['stalled']['time']}: {H['stalled']['reason']}" if H.get("stalled") else ""))
    L += bad or ["Nothing: every step that ran finished."]
    L += ["", "## Amendments", ""]
    if G.get("amendments"):
        L += [f"- {x['check']} {x['what']} {x['phase']}, {x['time']}: {x['reason'] or 'no reason needed before the lock'}. "
              f"Before: {x['before'] or '(new)'}. After: {x['after']}." for x in G["amendments"]]
    else:
        L.append("None: the done-when checks are the ones locked before the work started.")
    if G.get("plan_changes"):
        L += ["", "Plan changes during the run:"] + [f"- {x['step']} {x['what']}: {x['reason']}" for x in G["plan_changes"]]
    started, dl = parse_time(G.get("started")), parse_time(G.get("deadline"))
    used = hm((now - started).total_seconds()) if started else "0:00"
    cnt = lambda *st: sum(1 for s in steps if s["status"] in st)
    L += ["", "## Budget", ""]
    L.append(f"- Time: {used} used" + (f" of {hm((dl - started).total_seconds())}" if dl and started else "; no limit" if started else ""))
    L.append(f"- Agents: about {G.get('agents_used', 0)}" + (f" of {G['agents_cap']}" if G.get("agents_cap") else "")
             + " (approximate: each step's count as recorded, plus the acceptance judge)")
    L.append(f"- Steps: {cnt('done')} done, {cnt('failed')} failed, {cnt('blocked')} blocked, "
             f"{cnt('skipped', 'dropped')} skipped or dropped, {cnt('pending', 'retry', 'running')} not run, of {len(steps)}")
    if H:
        L.append(f"- Keep-going hook: kept the run going {H.get('blocks', 0)} time{'' if H.get('blocks', 0) == 1 else 's'}")
    L += ["", "## How to review", ""]
    if not G.get("plan_only") and commits:
        L.append(f"- Everything the run changed: `git diff {G['base'][:10]}..{G['branch']}`")
        L.append(f"- Commit by commit: `git log -p {G['base'][:10]}..{G['branch']}`")
    for s in steps:
        if s.get("report"):
            L.append(f"- {s['id']} ({step_label(s)}): [{s['report']}]({Path(s['report']).as_posix()})")
    L.append(f"- The run's ledger: `{ws / LEDGER}`; `goal.py status -w {ws}` shows it.")
    report_md = "\n".join(L).rstrip() + "\n"
    (ws / "report.md").write_text(report_md, encoding="utf-8")
    met_n = sum(1 for c in checks if verdicts.get(c["id"], {}).get("verdict") == "met"
                and (c["kind"] == "judge" or fr.get(c["id"], {}).get("pass")))
    if G.get("plan_only"):
        headline = f"Plan only: {len(steps)} steps planned; {why}"
    else:
        headline = (f"Goal {res}: {met_n} of {len(checks)} done-when checks met, {cnt('done')} of {len(steps)} steps done, "
                    f"{len(commits)} commit{'' if len(commits) == 1 else 's'} on {G['branch']}")
    status = {"met": "done", "partly met": "partial", "plan only": "done"}.get(res, "blocked")
    summary = {"skill": "killer-goal", "status": status, "headline": headline, "report": str(ws / "report.md"),
               "branch": None if G.get("plan_only") else G["branch"], "commits": commits,
               "decisions": [f"{d} ({src})" for src, d in decisions],
               "waiting": [f"{w} ({src})" for src, w in waiting],
               "numbers": {"checks": len(checks), "checks_met": met_n,
                           "command_checks_passing": sum(1 for r in fr.values() if r.get("pass")),
                           "steps": len(steps), "steps_done": cnt("done"), "steps_failed": cnt("failed"),
                           "steps_blocked": cnt("blocked"), "agents": G.get("agents_used", 0),
                           "hours": round((now - started).total_seconds() / 3600, 2) if started else 0,
                           "commits": len(commits), "amendments": len(G.get("amendments", [])),
                           "hook_blocks": H.get("blocks", 0)}}
    write_json(ws / "summary.json", summary)

    def fn(G):
        G["report"] = {"seq": bump(G), "time": stamp(now), "result": res}

    mutate(ws, fn)
    print(f"Wrote {ws / 'report.md'} and summary.json.")
    print(f"killer-goal: {status} · {headline} · {ws / 'report.md'}")
    return 0


# ---------------------------------------------------------------- the one outward step, when chosen

def gh_command() -> list[str]:
    v = os.environ.get("KILLER_GOAL_GH")
    if v:
        try:
            cmd = json.loads(v)
        except ValueError:
            cmd = None
        if not isinstance(cmd, list) or not cmd:
            fail("KILLER_GOAL_GH is a JSON list, like [\"gh\"]")
        return [str(x) for x in cmd]
    return ["gh"]


def cmd_pr(a) -> int:
    K = kit()
    ws, cfg, G = load(a)
    if G.get("alone") != "draft-pr":
        fail(f"a pull request wasn't chosen at setup (the run may: {ALONE[G.get('alone', 'branch')]}). It waits for "
             f"the user: record it with `wait` instead.")
    if not G.get("report"):
        fail("write the report first: the pull request comes after it")
    if G["report"].get("result") not in ("met", "partly met"):
        fail(f"the result is {G['report'].get('result')}; a draft pull request is only for met or partly met")
    if G.get("pr"):
        print(f"Already done: {G['pr'].get('url') or G['pr']}")
        return 0
    repo, branch, orig = G["repo"], G["branch"], G["start"].get("branch")
    if G.get("wip"):
        cmd_line = f"git push -u origin {branch}"
        mutate(ws, lambda G: (G["waiting"].append({"time": stamp(), "source": "killer-goal", "command": cmd_line,
               "text": f"Push {branch} and open the draft pull request: its first commit is your uncommitted work from "
                       f"before the run, which you haven't seen go public. Review it first"}), bump(G)))
        print(f"Not pushed: the branch's first commit, {G['wip'][:10]}, is your uncommitted work from before the run, "
              f"and it would go public. Recorded under \"Waiting for your OK\" instead.")
        return 1
    if not orig:
        fail("you started on a detached HEAD, so there's no branch for the pull request to go into")
    snap = snapshot(Path(repo))
    problems, _ = guard_compare(G, snap)
    if problems:
        for p in problems:
            print(f"PROBLEM: {p}")
        fail("the guard has problems; fix them before anything goes out")
    remote = git(repo, "config", "--get", f"branch.{orig}.remote", check=False).strip() or "origin"
    if not K.git_ok(repo, "remote", "get-url", remote):
        fail(f"no remote {remote} to push to")
    body = pr_body(ws, G)
    hit = ATTRIBUTION.search(body)
    if hit:
        fail(f"the pull request text has AI attribution in it ({hit.group(0)!r}); it must read as the user's own")
    bodyf = ws / "pr-body.md"
    bodyf.write_text(body, encoding="utf-8")
    title = short(G["goal"][0].upper() + G["goal"][1:], 72) if G["goal"] else branch
    push = ["git", "-C", repo, "push", "-u", remote, branch]
    create = gh_command() + ["pr", "create", "--draft", "--base", orig, "--head", branch, "--title", title,
                             "--body-file", str(bodyf)]
    if a.dry_run:
        print("Would run:")
        print("  " + " ".join(push))
        print("  " + " ".join(create))
        return 0
    quiet = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", GH_PROMPT_DISABLED="1")
    r = subprocess.run(push, capture_output=True, text=True, encoding="utf-8", errors="replace", env=quiet,
                       stdin=subprocess.DEVNULL)
    if r.returncode:
        fail(f"git push failed: {(r.stderr or r.stdout).strip()}")
    after = snapshot(Path(repo))

    def rebase(G):
        G["start"].update(remotes=after["remotes"], upstream=after["upstream"], fetch_head=after["fetch_head"])
        G["pr"] = {"seq": bump(G), "time": stamp(), "pushed": branch, "remote": remote, "url": None}

    mutate(ws, rebase)
    cwd = repo
    try:
        r2 = subprocess.run(create, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd,
                            env=quiet, stdin=subprocess.DEVNULL)
        ok, out = r2.returncode == 0, (r2.stdout or "") + (r2.stderr or "")
    except OSError as e:
        ok, out = False, str(e)
    url = next((w for w in out.split() if w.startswith("http")), None) if ok else None
    if ok:
        mutate(ws, lambda G: (G["pr"].update(url=url or out.strip()), bump(G)))
        print(f"Pushed {branch} to {remote} and opened a draft pull request: {url or out.strip()}")
        return 0
    cmd_text = " ".join(create[:-2] + ["--body-file", f"\"{bodyf}\""])
    mutate(ws, lambda G: (G["waiting"].append({"time": stamp(), "source": "killer-goal", "command": cmd_text,
           "text": f"{branch} is pushed; opening the draft pull request failed ({short(out, 120)})"}), bump(G)))
    print(f"Pushed {branch}, but gh couldn't open the pull request: {short(out, 200)}. Recorded under \"Waiting for your OK\".")
    return 1


def pr_body(ws, G) -> str:
    res, why = outcome(G)
    L = [G["goal"], "", f"Result: {res}: {why}.", "", "Done when:"]
    fr = final_results(G)
    for c in live_checks(G):
        r = fr.get(c["id"])
        state = ("passes" if r and r.get("pass") else "fails") if c["kind"] == "command" else "checked by review"
        L.append(f"- {c['statement']} ({state})")
    L += ["", "What changed:"]
    for s in G.get("steps", []):
        if s["status"] == "done":
            L.append(f"- {s['title']}: {s.get('headline')}")
    left = [s for s in G.get("steps", []) if s["status"] in ("failed", "blocked", "pending", "retry")]
    if left:
        L += ["", "Not done:"] + [f"- {s['title']} ({s['status']})" for s in left]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- hook install, remove, status

def settings_file(a) -> Path:
    if getattr(a, "settings", None):
        return Path(a.settings).expanduser().resolve()
    if os.environ.get("KILLER_GOAL_SETTINGS"):
        return Path(os.environ["KILLER_GOAL_SETTINGS"]).expanduser().resolve()
    if getattr(a, "project", False):
        top, is_git = kit().find_top(Path(a.repo or ".").resolve())
        if not is_git:
            fail("--project needs a git repository; run it from the project or give --repo")
        return top / ".claude" / "settings.local.json"
    return claude_dir() / "settings.json"


def hook_cmd() -> str:
    return f'"{Path(sys.executable).as_posix()}" -I "{SCRIPT.as_posix()}" hook stop'


def load_settings(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except ValueError as e:
        fail(f"{path} isn't valid JSON ({e}); fix it first, nothing was changed")
    if not isinstance(data, dict):
        fail(f"{path} isn't a JSON object; nothing was changed")
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict) or not isinstance(hooks.get("Stop", []), list):
        fail(f"{path} has a hooks section in a shape this doesn't know; add the hook by hand: {hook_cmd()}")
    return data


def our_entries(data: dict) -> list[tuple[int, int]]:
    out = []
    for gi, group in enumerate(data.get("hooks", {}).get("Stop", []) or []):
        if isinstance(group, dict) and isinstance(group.get("hooks"), list):
            for hi, h in enumerate(group["hooks"]):
                if isinstance(h, dict) and isinstance(h.get("command"), str) and HOOK_RE.search(h["command"]):
                    out.append((gi, hi))
    return out


def backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    base = path.with_name(f"{path.name}.bak-{dt.date.today().isoformat()}")
    b, n = base, 2
    while b.exists():
        b, n = base.with_name(f"{base.name}-{n}"), n + 1
    shutil.copy2(path, b)
    return b


def cmd_hook_install(a) -> int:
    path = settings_file(a)
    data = load_settings(path)
    want = {"type": "command", "command": hook_cmd(), "timeout": 10}
    found = our_entries(data)
    groups = data.get("hooks", {}).get("Stop", [])
    if len(found) == 1 and groups[found[0][0]]["hooks"][found[0][1]] == want:
        print(f"The keep-going hook is already installed in {path}.")
        return 0
    bak = backup(path)
    data.setdefault("hooks", {}).setdefault("Stop", [])
    if found:
        stop = data["hooks"]["Stop"]
        gi, hi = found[0]
        stop[gi]["hooks"][hi] = want
        emptied = set()
        for gi2, hi2 in sorted(found[1:], reverse=True):
            del stop[gi2]["hooks"][hi2]
            emptied.add(gi2)
        data["hooks"]["Stop"] = [g for i, g in enumerate(stop) if not (i in emptied and g.get("hooks") == [])]
    else:
        data["hooks"]["Stop"].append({"hooks": [want]})
    write_json(path, data)
    if getattr(a, "project", False) and not a.settings:
        exclude_local(path)
    print(f"Installed the keep-going hook in {path}" + (f" (backup: {bak})" if bak else "") + ".")
    print("It does nothing unless a killer-goal run is going. Claude Code may only pick it up after a restart or a "
          "visit to /hooks, so do that before leaving it overnight.")
    return 0


def exclude_local(path: Path) -> None:
    """Keep a project's settings.local.json out of git, so the run's commits can't sweep it up."""
    try:
        top = path.parent.parent
        rel = git(top, "rev-parse", "--git-path", "info/exclude", check=False).strip()
        if not rel:
            return
        ex = (top / rel) if not Path(rel).is_absolute() else Path(rel)
        line = "/.claude/settings.local.json"
        have = ex.read_text(encoding="utf-8").splitlines() if ex.is_file() else []
        if line not in have:
            ex.parent.mkdir(parents=True, exist_ok=True)
            with ex.open("a", encoding="utf-8", newline="\n") as f:
                f.write(("\n" if have and have[-1] != "" else "") + line + "\n")
    except OSError:
        pass


def cmd_hook_remove(a) -> int:
    path = settings_file(a)
    data = load_settings(path)
    found = our_entries(data)
    if not found:
        print(f"The keep-going hook isn't in {path}; nothing to remove.")
        return 0
    bak = backup(path)
    stop = data["hooks"]["Stop"]
    touched = set()
    for gi, hi in sorted(found, reverse=True):
        del stop[gi]["hooks"][hi]
        touched.add(gi)
    data["hooks"]["Stop"] = [g for i, g in enumerate(stop) if not (i in touched and g.get("hooks") == [])]
    if not data["hooks"]["Stop"]:
        del data["hooks"]["Stop"]
    if not data["hooks"]:
        del data["hooks"]
    write_json(path, data)
    print(f"Removed the keep-going hook from {path}" + (f" (backup: {bak})" if bak else "") + ". Other hooks are as they were.")
    return 0


def cmd_hook_status(a) -> int:
    places = []
    if getattr(a, "settings", None) or os.environ.get("KILLER_GOAL_SETTINGS"):
        places.append(settings_file(a))
    else:
        places.append(claude_dir() / "settings.json")
        top, is_git = kit().find_top(Path(a.repo or ".").resolve())
        if is_git:
            places += [top / ".claude" / "settings.json", top / ".claude" / "settings.local.json"]
    installed = False
    for p in places:
        if not p.is_file():
            print(f"  {p}: no file")
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig") or "{}")
        except ValueError:
            print(f"  {p}: not valid JSON")
            continue
        found = our_entries(data) if isinstance(data, dict) and isinstance(data.get("hooks", {}), dict) else []
        if not found:
            print(f"  {p}: not installed")
            continue
        gi, hi = found[0]
        cmd = data["hooks"]["Stop"][gi]["hooks"][hi]["command"]
        paths = re.findall(r'"([^"]+)"', cmd)
        gone = [x for x in paths if not Path(x).exists()]
        if gone:
            print(f"  {p}: installed, but it points at {', '.join(gone)}, which isn't there: run hook install again")
        else:
            installed = True
            print(f"  {p}: installed")
    print("The keep-going hook is " + ("installed." if installed else "not installed."))
    return 0 if installed else 1


# ---------------------------------------------------------------- the command line

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="goal.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def leaf(parent, name, func, help_, ws=True, takes_command=False, aliases=()):
        sp = parent.add_parser(name, help=help_, description=help_, aliases=list(aliases))
        if ws:
            sp.add_argument("--workspace", "-w", required=True, help="the run folder")
        sp.set_defaults(func=func, takes_command=takes_command)
        return sp

    s = leaf(sub, "init", cmd_init, "Make the run, the goal branch and the active marker.", ws=False)
    s.add_argument("--repo", default=".", help="the project folder (default: here)")
    s.add_argument("--goal", required=True, help="the goal, in the user's own words")
    s.add_argument("--hours", default="10", help="how long it may work, counted from go; 0 for no limit (default 10)")
    s.add_argument("--agents", type=int, help="a cap on agents, roughly counted (default: none)")
    s.add_argument("--depth", choices=["quick", "standard", "thorough"], default="standard")
    s.add_argument("--alone", choices=list(ALONE), default="branch", help="what it may do without the user")
    s.add_argument("--dirty", choices=["carry", "stop"], default="carry",
                   help="uncommitted work: carry it as the branch's first commit (default), or stop")
    s.add_argument("--slug", help="short name for the run folder and branch")
    s.add_argument("--network", action="store_true", help="let check sandboxes use the network")
    s.add_argument("--include", nargs="*", help="ignored paths every sandbox needs, like node_modules")
    s.add_argument("--env", nargs="*", help="NAME=VALUE set in every sandbox")

    chk = sub.add_parser("check", aliases=["checks"], help="the done-when checks: add, list, amend, run")
    csub = chk.add_subparsers(dest="sub", required=True)
    s = leaf(csub, "add", cmd_check_add, "Add a done-when check. A command check's command goes after --.", takes_command=True)
    s.add_argument("statement", help="what must be true, in a sentence")
    s.add_argument("--kind", choices=["command", "judge"], help="command (passes on exit 0) or judge (default: command when one is given)")
    s.add_argument("--shell", help="run this command line through sh -c instead (for pipes)")
    s.add_argument("--files", nargs="*", help="files whose contents are locked with the check (the command's own files are found)")
    s.add_argument("--timeout", type=float, default=900, help="seconds before the check is killed (default 900)")
    s.add_argument("--reason", help="why, when adding after the lock (it's an amendment)")
    leaf(csub, "list", cmd_check_list, "List the done-when checks and their latest state.")
    s = leaf(csub, "amend", cmd_check_amend, "Change a check; after the lock, with --reason, and the report lists it.", takes_command=True)
    s.add_argument("id")
    s.add_argument("--reason")
    s.add_argument("--statement")
    s.add_argument("--shell")
    s.add_argument("--files", nargs="*")
    s.add_argument("--timeout", type=float)
    s.add_argument("--drop", action="store_true", help="drop the check (the report lists it)")
    s = leaf(csub, "run", cmd_checks_run, "Run every command check in a fresh sandbox from the goal branch.")
    s.add_argument("--final", action="store_true", help="the final check, after the last step")
    s.add_argument("--only", help="just these checks, like C1,C3")

    leaf(sub, "lock", cmd_lock, "Freeze the done-when checks.")

    pl = sub.add_parser("plan", help="the steps: add, load, list, edit, drop")
    psub = pl.add_subparsers(dest="sub", required=True)
    s = leaf(psub, "add", cmd_plan_add, "Add a step.")
    s.add_argument("title")
    s.add_argument("--kind", choices=["skill", "direct"])
    s.add_argument("--skill", help="the killer skill to run, like killer-bugs")
    s.add_argument("--answers", help="its setup answers, in plain words")
    s.add_argument("--after", help="steps it needs first, like S1,S2")
    s.add_argument("--moves", help="checks it should move, like C1,C3")
    s.add_argument("--agents", help="roughly how many agents it uses, like ~12")
    s.add_argument("--reason", help="why, once the run has started")
    s = leaf(psub, "load", cmd_plan_load, "Add the steps in a JSON file.")
    s.add_argument("file")
    s.add_argument("--reason")
    leaf(psub, "list", cmd_plan_list, "List the steps.")
    s = leaf(psub, "edit", cmd_plan_edit, "Change a step (with --reason once the run has started).")
    s.add_argument("id")
    s.add_argument("--reason")
    s.add_argument("--title")
    s.add_argument("--kind", choices=["skill", "direct"])
    s.add_argument("--skill")
    s.add_argument("--answers")
    s.add_argument("--after")
    s.add_argument("--moves")
    s.add_argument("--agents")
    s = leaf(psub, "drop", cmd_plan_drop, "Drop a step, with a reason.")
    s.add_argument("id")
    s.add_argument("--reason")

    leaf(sub, "go", cmd_go, "Start the clock: the run is live.")
    leaf(sub, "next", cmd_next, "The next step to run, with everything needed to run it.")

    st = sub.add_parser("step", help="record a step: start, done, failed, blocked, skipped")
    ssub = st.add_subparsers(dest="action", required=True)
    for action in ("start", "done", "failed", "blocked", "skipped"):
        s = leaf(ssub, action, cmd_step, f"Record that a step is {action if action != 'start' else 'starting'}.")
        s.add_argument("id")
        s.set_defaults(action=action)
        s.add_argument("--reason", help="why (failed, blocked and skipped need one)")
        s.add_argument("--agents", help="agents this step used (default: its summary's count or the plan's estimate)")
        if action == "start":
            s.add_argument("--approach", help="for a retry: what's different this time")
            s.add_argument("--force", action="store_true", help="start even though a step it needs isn't done, or the budget's spent")
        else:
            s.add_argument("--summary", help="the sub-run's summary.json (or its run folder)")
            s.add_argument("--headline", help="one line: what changed, and the proof")
            s.add_argument("--report", help="the sub-run's report")
        if action == "failed":
            s.add_argument("--final", action="store_true", help="no retry: mark it failed now")

    s = leaf(sub, "guard", cmd_guard, "Compare the repository with how it was at init.")
    s.add_argument("--accept", help="accept changes to remotes, tags, stash or other branches, with what happened")
    s = leaf(sub, "budget", cmd_budget, "Time and agents used and left.")
    s.add_argument("--agents", help="add agents used, like +3")
    s.add_argument("--cap", type=int, help="set the agent cap (0 for none); recorded as a decision")
    s = leaf(sub, "decide", cmd_decide, "Record a call the user should check.")
    s.add_argument("text")
    s = leaf(sub, "wait", cmd_wait, "Record an outward action prepared but not done.")
    s.add_argument("text")
    s.add_argument("--command", required=True, help="the exact command the user would run")
    s = leaf(sub, "sandbox", cmd_sandbox, "A fresh copy of the goal branch for an agent.")
    s.add_argument("name")
    s.add_argument("--net", action="store_true", help="allow the network in this sandbox")
    s = leaf(sub, "judge-brief", cmd_judge_brief, "Write the acceptance judge's brief and make its sandbox.")
    s.add_argument("--forbid", action="append", help="a command the judge mustn't run, and why (repeat it)")
    s.add_argument("--net", action="store_true", help="allow the network in the judge's sandbox")
    s = leaf(sub, "judge", cmd_judge, "Read the acceptance judge's reply into the ledger.")
    s.add_argument("--sandbox", help="the judge's sandbox (its scratch/reply.json is read, then it's deleted)")
    s.add_argument("--from", dest="reply", help="a reply saved to a file instead")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")
    s = leaf(sub, "pause", cmd_pause, "Pause: the keep-going hook and next wait.")
    s.add_argument("--reason")
    s = leaf(sub, "resume", cmd_resume, "Carry on after a pause, stall, restart or stop.")
    s.add_argument("--hours", type=float, help="carry on for this many more hours (after the deadline)")
    s = leaf(sub, "stop", cmd_stop, "End the run and clear its active marker.")
    s.add_argument("--reason")
    s = leaf(sub, "status", cmd_status, "Where the run is, and the next action.", ws=False)
    s.add_argument("--workspace", "-w", help="the run folder (default: the active run for this folder)")
    leaf(sub, "report", cmd_report, "Write report.md and summary.json.")
    s = leaf(sub, "pr", cmd_pr, "Push the branch and open a draft pull request (only if chosen at init).")
    s.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")

    hk = sub.add_parser("hook", help="the keep-going Stop hook: install, remove, status, stop")
    hsub = hk.add_subparsers(dest="sub", required=True)
    for name, func, help_ in (("install", cmd_hook_install, "Add the keep-going Stop hook to Claude Code's settings."),
                              ("remove", cmd_hook_remove, "Take the keep-going hook out again, and nothing else."),
                              ("status", cmd_hook_status, "Say whether the hook is installed, and where.")):
        s = leaf(hsub, name, func, help_, ws=False)
        s.add_argument("--settings", help="this settings file instead of ~/.claude/settings.json")
        s.add_argument("--repo", help="the project, for --project and status (default: here)")
        if name != "status":
            s.add_argument("--project", action="store_true", help="the project's .claude/settings.local.json instead")
    leaf(hsub, "stop", lambda a: hook_stop_main(), "What Claude Code runs when a turn ends (reads its JSON on stdin).", ws=False)
    return p


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:2] == ["hook", "stop"]:
        hook_stop_main()
    kit().utf8_stdout()
    tail = None
    if "--" in argv:
        i = argv.index("--")
        argv, tail = argv[:i], argv[i + 1:]
    a = build_parser().parse_args(argv)
    if tail is not None and not getattr(a, "takes_command", False):
        fail("nothing goes after -- for this command")
    a.command = tail or None
    raise SystemExit(a.func(a) or 0)


if __name__ == "__main__":
    main()
