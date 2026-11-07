#!/usr/bin/env python3
"""Bookkeeping for a killer-bugs run.

Commands:
  init       create the run folder and record the scope; shows what earlier runs left
  sandbox    make an isolated copy of the project for one agent
  collect    add the suspected bugs from a finder's reply, verifying its reproductions
  add        add one suspected bug by hand (the user's, a tool's, your own)
  merge      fold duplicates into one bug
  forward    pick the suspects to prove and challenge: worst first, within the depth's limit
  challenges record a group challenger's verdicts, one per bug
  update     record a prover's, challenger's or fix reviewer's reply, or set fields
  list       the bugs so far
  show       one bug in full
  coverage   the files in scope that no finder read
  check-fix  prove a fix: the test fails without the fix and passes with it
  recheck    rerun the reproductions earlier runs recorded for this project
  decide     record a call the user should check (listed in the report)
  wait       record an outward action left for the user's OK, with its command
  findings   the bugs as JSON for a host's ReportFindings tool
  repro      copy a bug's reproduction into an agent's sandbox
  freshness  when each doc last changed, against the code
  report     write report.md, and summary.json for whoever reads the run next
  close      save this run's outcomes to the project's record
  clean      delete this run's sandboxes

Agents write their reply to reply.json in their sandbox's scratch folder; collect
and update read it from there with --sandbox, and delete the sandbox afterwards.

Runs live in ~/.claude/killer-bugs/<project>/<date>-<slug>/, outside the project,
so an agent working in the project can't come across them. Sandboxes live in the
temp folder. KILLER_BUGS_HOME and KILLER_BUGS_SANDBOXES move them. The sandboxes, run.py
and the run's files come from the shared kit (kit.py, beside this file); bugs.py asks for
sandboxes that keep the project's own git state, since it never diffs one.
Standard library only. Run `bugs.py <command> --help` for options.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import importlib.util
import json
import re
import secrets
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The shared kit (tools/shared/kit.py, copied here by tools/sync.py): run folders, scope, sandboxes,
# run.py and the run's locked JSON files. Loaded by path: python -I leaves this folder off sys.path.
_spec = importlib.util.spec_from_file_location("killer_kit", HERE / "kit.py")
kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit)

SKILL = "bugs"
SCRATCH = "repro"   # reproductions reach it as ../repro/<file>, and the record keeps those commands
(SKIP_DIRS, SECRET_NAMES, DROP_NAMES, DEAD_PROXY, LOOPBACK, OFFLINE_NOTE, TEXT_LIMIT, REPLY) = (
    kit.SKIP_DIRS, kit.SECRET_NAMES, kit.DROP_NAMES, kit.DEAD_PROXY, kit.LOOPBACK, kit.OFFLINE_NOTE,
    kit.TEXT_LIMIT, kit.REPLY)
runner, fail, today, replace, locked, git, git_ok, split0, rmtree = (
    kit.runner, kit.fail, kit.today, kit.replace, kit.locked, kit.git, kit.git_ok, kit.split0, kit.rmtree)
load_json, save_json, read_reply, as_list, as_cmd, parse_env = (
    kit.load_json, kit.save_json, kit.read_reply, kit.as_list, kit.as_cmd, kit.parse_env)
find_top, has_head, project_key, all_files, changed_since, default_base, is_text, excluded, resolve_scope = (
    kit.find_top, kit.has_head, kit.project_key, kit.all_files, kit.changed_since, kit.default_base,
    kit.is_text, kit.excluded, kit.resolve_scope)
copy_path, run_command_line, classify, box_path, drop_sandbox = (
    kit.copy_path, kit.run_command_line, kit.classify, kit.box_path, kit.drop_sandbox)
mutate_cfg = kit.update_cfg


def runs_home() -> Path:
    return kit.runs_home(SKILL)


def sandboxes_home() -> Path:
    return kit.sandboxes_home(SKILL)


def make_sandbox(cfg: dict, ws: Path, name: str, include=(), rev: str | None = None,
                 network: bool | None = None, env: dict | None = None, path_first=()) -> Path:
    """The kit's sandbox, keeping the project's own git state: HEAD, branch and what's staged as they
    are in the project and no base commit, since bugs.py never diffs a sandbox."""
    root = box_path(cfg, name)
    kb = {"KB_SANDBOX": str(root), "KB_REPO": str(root / "repo"), "KB_REPRO": str(root / SCRATCH)}
    root = kit.make_sandbox(cfg, ws, name, include, rev, network, {**kb, **(env or {})}, base=False, scratch=SCRATCH,
                            path_first=[full_path(d) for d in path_first or ()])
    if not name.startswith(NOT_AGENTS):   # one sandbox per agent, so this counts the agents
        stamp = dt.datetime.now().isoformat(timespec="seconds")
        mutate_cfg(ws, lambda c: c.setdefault("agents", []).append({"name": name, "at": stamp}))
    return root


def full_path(d: str) -> str:
    """An absolute path, unless it names each sandbox's own folder ({sandbox}), which is filled in later."""
    return str(d) if "{sandbox}" in str(d) else str(Path(d).resolve())


def script_version() -> str:
    """A short hash of the scripts, so a run records which version of them it ran on."""
    h = hashlib.sha1()
    for f in ("bugs.py", "kit.py", "run.py"):
        if (HERE / f).is_file():
            h.update((HERE / f).read_bytes())
    return h.hexdigest()[:10]


def keep_scratch(box: Path, dst: Path) -> None:
    """Copy an agent's scratch folder (its reproductions) into the run, without its reply."""
    kit.keep_scratch(box, dst, SCRATCH)


STATUSES = ("candidate", "proven", "suspected", "confirmed", "argued", "question", "dismissed",
            "intended", "stale-docs", "fixed", "merged", "known")
SEVERITY = ("high", "medium", "low")
LIKELIHOOD = ("common", "occasional", "rare")
CONFIDENCE = ("low", "medium", "high")
KINDS = ("bug", "test-gap", "mismatch")
LIST_ORDER = ("confirmed", "fixed", "argued", "question", "proven", "candidate", "suspected", "stale-docs",
              "dismissed", "intended", "known", "merged")
# Sources that are agents looking on their own; the lead, the user and tools don't count as "separately".
AGENT_SOURCE = re.compile(r"^(hunter:|promises|copy-of:|read|find|pairs)")
# What goes on to be proven and challenged at each depth, and at most how many.
DEPTHS = {"quick": (("high",), 5), "standard": (("high", "medium"), 15),
          "thorough": (("high", "medium", "low"), 30)}
# Sandboxes are named by the agent's role; these prefixes count as agents in the report.
ROLES = {"promises": "promise checker", "read": "read-through", "find": "finder", "pairs": "finder",
         "prove": "prover", "challenge": "challenger", "code": "code checker", "copy": "copy finder",
         "review": "fix reviewer", "final": "final pass"}
NOT_AGENTS = ("verify-", "check-", "recheck-", "map", "old-")   # the script's own and the lead's


# ---------------------------------------------------------------- helpers

def workspace(a) -> Path:
    return Path(a.workspace).expanduser().resolve()


def load(a) -> tuple[Path, dict]:
    ws = workspace(a)
    cfg = load_json(ws / "config.json", None)
    if cfg is None:
        fail(f"no config.json in {ws}; run init first")
    cfg.setdefault("sandboxes_home", str(sandboxes_home()))   # runs from before the shared kit
    return ws, cfg


def load_bugs(ws: Path) -> dict:
    return load_json(ws / "bugs.json", {"next": 1, "bugs": []})


def mutate(ws: Path, fn):
    """Read bugs.json, change it with fn, write it back, all under the run's lock."""
    with locked(ws):
        db = load_bugs(ws)
        out = fn(db)
        save_json(ws / "bugs.json", db)
    return out


def get_bug(db: dict, bid: str) -> dict:
    for b in db["bugs"]:
        if b["id"].lower() == bid.lower():
            return b
    fail(f"no bug {bid}")


def level(b: dict) -> str | None:
    """The bug's severity, or the triage guess until a challenger sets one."""
    return b.get("severity") or b.get("triage")


def rank_key(b: dict):
    sev = SEVERITY.index(level(b)) if level(b) in SEVERITY else 1
    like = LIKELIHOOD.index(b["likelihood"]) if b.get("likelihood") in LIKELIHOOD else 1
    return (sev, like, 1 if b.get("internal") else 0, int(b["id"][1:]))


def agent_sources(b: dict) -> set[str]:
    """The agents that found this bug on their own: what 'found separately' counts."""
    return {s for s in b["found_by"] if AGENT_SOURCE.match(s)}


# ---------------------------------------------------------------- the project and its scope

# ---------------------------------------------------------------- sandboxes

def runs_total(runs) -> int:
    m = re.match(r"\s*(\d+)\s*(?:/|of)\s*(\d+)", str(runs or ""))
    return max(1, min(10, int(m.group(2)))) if m else 1   # enough to catch a flaky one; hangs make more costly


def verify_repro(ws: Path, cfg: dict, bid: str, repro_dir: str | None, cmd: list[str], repeat: int,
                 timeout: float = 300, quiet: bool = False) -> tuple[str, str]:
    """Rerun a reproduction in a fresh copy of the project; an agent's word isn't enough."""
    name = f"verify-{bid.lower()}-{secrets.token_hex(2)}"
    root = make_sandbox(cfg, ws, name)
    run = runner()
    try:
        if repro_dir and (ws / repro_dir).is_dir():
            shutil.copytree(ws / repro_dir, root / SCRATCH, dirs_exist_ok=True)
        res = run.run_many(root, cmd, repeat, timeout, log=False)
    finally:
        drop_sandbox(ws, cfg, name)
    kinds = [classify(r) for r in res["runs"]]
    verdict = "shows" if "shows" in kinds else ("inconclusive" if "inconclusive" in kinds else "behaves")
    said = {"shows": "showed", "behaves": "did not show", "inconclusive": "was inconclusive"}[verdict]
    print(f"{bid}: rerun in a fresh copy ({len(kinds)} run{'s' if len(kinds) != 1 else ''}), "
          f"the bug {said} in {kinds.count('shows')} of {len(kinds)}.")
    if not quiet or verdict != "shows":
        print(run.summary(res))
    return verdict, f"{kinds.count('shows')}/{len(kinds)}"


# ---------------------------------------------------------------- the record kept between runs

def record_path(cfg: dict) -> Path:
    return Path(cfg["record"])


def load_record(cfg: dict) -> dict:
    return load_json(record_path(cfg), {"entries": []})


# ---------------------------------------------------------------- commands

def cmd_init(a) -> None:
    path = Path(a.repo).expanduser().resolve()
    if not path.is_dir():
        fail(f"no folder {path}")
    top, is_git = find_top(path)
    scope, files = resolve_scope(a.scope, top, is_git)
    files = [f for f in files if not excluded(f, a.exclude)]
    text = [f for f in files if is_text(top / f)]
    key = project_key(top, is_git)
    proj = runs_home() / key
    slug = re.sub(r"[^a-z0-9-]+", "-", (a.slug or scope["kind"]).lower()).strip("-") or "run"
    if a.workspace:
        ws = workspace(a)
        if (ws / "config.json").exists() and not a.force:
            fail(f"{ws} already has a run; pass --force to start over")
    else:
        ws, n = proj / f"{today()}-{slug}", 2
        while (ws / "config.json").exists():
            ws, n = proj / f"{today()}-{slug}-{n}", n + 1
    if a.force and ws.exists():
        for f in ("bugs.json", "coverage.json", "promises.json"):
            (ws / f).unlink(missing_ok=True)
    head = git(top, "rev-parse", "HEAD", check=False).strip() if is_git else ""
    cfg = {
        "version": 2,
        "run_id": secrets.token_hex(3),
        "repo": str(top),
        "git": is_git,
        "head": head or None,
        "branch": (git(top, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip() or None) if is_git else None,
        "scope": scope,
        "target": a.target,
        "network": a.network,
        "after": a.after,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "record": str(proj / "record.json"),
        "include": a.include or [],
        "exclude": a.exclude or [],
        "env": parse_env(a.env),
        "depth": a.depth,
        "path_first": [full_path(d) for d in a.path_first or []],
        "script": script_version(),
        "sandboxes_home": str(sandboxes_home()),
        "sandboxes": {},
        "agents": [],
    }
    save_json(ws / "config.json", cfg)
    (ws / "scope.txt").write_text("".join(f + "\n" for f in text), encoding="utf-8")
    save_json(ws / "bugs.json", {"next": 1, "bugs": []})
    print(f"Run folder: {ws}")
    print(f"Project: {top}" + (f" ({cfg['branch'] or 'detached'} at {head[:10]})" if head else ""))
    skipped = len(files) - len(text)
    where = {"repo": "the whole project", "path": scope.get("path"), "changes": "uncommitted changes",
             "branch": f"changes since {scope.get('ref')}"}[scope["kind"]]
    print(f"Scope: {where}, {len(text)} text files" + (f" ({skipped} binary or over 1 MB left out)" if skipped else ""))
    if not text:
        print("warning: nothing in scope")
    print(f"Network in sandboxes: {'allowed' if a.network else 'off'}")
    if cfg["env"]:
        print(f"Set in every sandbox: {', '.join(cfg['env'])}")
    rec = load_record(cfg)["entries"]
    if rec:
        count = lambda s: sum(1 for e in rec if e.get("status") == s)
        print(f"Record from earlier runs: {count('open')} open, {count('fixed')} fixed, "
              f"{count('intended')} intended. Run recheck to rerun their reproductions.")


def cmd_sandbox(a) -> None:
    ws, cfg = load(a)
    root = make_sandbox(cfg, ws, a.name, a.include or [], a.rev, True if a.net else None, parse_env(a.env),
                        a.path_first or [])
    net = cfg["network"] or a.net
    print(f"Sandbox {a.name} is ready.")
    print(kit.sandbox_text(root, net, a.rev, SCRATCH))


def new_bug(db: dict, c: dict, source: str) -> dict | None:
    claim = str(c.get("claim") or "").strip()
    if not claim:
        return None
    b = {
        "id": f"B{db['next']}",
        "title": str(c.get("title") or claim[:80]).strip(),
        "claim": claim,
        "trigger": c.get("trigger") or "",
        "expected": c.get("expected") or "",
        "observed": c.get("observed") or "",
        "where": [str(w) for w in as_list(c.get("where"))],
        "kind": c.get("kind") if c.get("kind") in KINDS else "bug",
        "confidence": c.get("confidence") if c.get("confidence") in CONFIDENCE else "medium",
        "triage": c.get("severity") if c.get("severity") in SEVERITY else None,
        "found_by": [source],
        "status": "candidate",
        "notes": [],
    }
    # Why it happens is for the lead and whoever fixes it; provers and challengers get only the claim.
    if str(c.get("cause") or "").strip():
        b["cause"] = str(c["cause"]).strip()
    if c.get("source_kind"):
        b["source_kind"] = str(c["source_kind"])
    # A promise read between the lines isn't the owner's promise until they confirm it.
    if c.get("source_kind") == "inferred" or any("inferred" in str(w).lower() for w in b["where"]):
        b["inferred"] = True
    db["next"] += 1
    db["bugs"].append(b)
    return b


def own_repro(ws: Path, bid: str, scratch: str | None, cmd: list[str]) -> tuple[str | None, str | None]:
    """Copy just this bug's reproduction out of the finder's scratch folder into repros/<bid>/.

    Whoever gets it later (a challenger, a fix reviewer) then can't see the finder's other findings.
    The command names its files as ../repro/<folder or file>; those are what's copied."""
    if not scratch or not (ws / scratch).is_dir():
        return None, None
    picked = []
    for arg in cmd:
        a = str(arg).replace("\\", "/")
        i = a.find(f"../{SCRATCH}/")
        if i >= 0:
            first = a[i + len(SCRATCH) + 4:].split("/")[0]
            if first and (ws / scratch / first).exists() and first not in picked:
                picked.append(first)
    if not picked:   # the command doesn't name its files: keep the whole folder, as before
        return scratch, None
    dst = ws / "repros" / bid
    rmtree(dst)
    for first in picked:
        src = ws / scratch / first
        if src.is_dir():
            shutil.copytree(src, dst / first, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            (dst).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst / first)
    loose = [f for f in picked if (ws / scratch / f).is_file()]
    note = (f"kept only {', '.join(loose)} from the finder's scratch folder; a reproduction in a folder of "
            "its own carries its helpers with it") if loose else None
    return f"repros/{bid}", note


def normalize(path: str, top: str) -> str:
    """A file name an agent reported, as a path relative to the project."""
    s = str(path).strip()
    for tok in re.split(r"[\s,;()`'\"]+", s):   # skip labels like "P12" or "(lines 1-40)"
        if "/" in tok or "\\" in tok or "." in tok:
            s = tok
            break
    s = s.replace("\\", "/")
    s = re.sub(r"(:\d+(-\d+)?)+$", "", s)
    m = re.search(r"/[0-9a-f]{6}/[^/]+/repo/(.+)$", s)   # <sandboxes home>/<run id>/<sandbox>/repo/...
    if m:
        s = m.group(1)
    else:
        t = top.replace("\\", "/").rstrip("/") + "/"
        if s.lower().startswith(t.lower()):
            s = s[len(t):]
    return s[2:] if s.startswith("./") else s


def check_where(top: Path, entries) -> list[str]:
    """Locations an agent gave that don't exist: a missing file, or a line past its end."""
    bad = []
    for w in entries:
        m = re.search(r"([^\s(),;`'\"]+?):(\d+)", str(w))
        if not m:
            continue
        p = top / normalize(m.group(1), str(top))
        if not p.is_file():
            bad.append(f"{w}: no such file")
            continue
        try:
            with p.open("rb") as f:
                n = sum(1 for _ in f)
        except OSError:
            continue
        if int(m.group(2)) > n:
            bad.append(f"{w}: line {m.group(2)} is past the end ({n} lines)")
    return bad


def reply_source(a, cfg) -> tuple[Path, Path | None]:
    """The reply file, and the sandbox it came from (if any)."""
    if bool(getattr(a, "reply", None)) == bool(a.sandbox):
        fail("give the reply file, or --sandbox <name> to read its reply.json")
    if a.sandbox:
        box = Path(cfg.get("sandboxes", {}).get(a.sandbox) or box_path(cfg, a.sandbox))
        if not box.is_dir():
            fail(f"no sandbox called {a.sandbox} in this run")
        return box / SCRATCH / REPLY, box
    return Path(a.reply), None


def cmd_collect(a) -> None:
    ws, cfg = load(a)
    path, box = reply_source(a, cfg)
    data = read_reply(path)
    source = a.source or (f"hunter:{a.sandbox}" if a.sandbox else "lead")
    scratch = None
    if box:
        scratch = f"scratch/{a.sandbox}"
        keep_scratch(box, ws / scratch)
    cands = [c for c in as_list(data.get("candidates")) if isinstance(c, dict)]
    badlocs: list[str] = []

    def add(db):
        added, empty = [], 0
        for c in cands:
            b = new_bug(db, c, source)
            if not b:
                empty += 1
                continue
            bad = check_where(Path(cfg["repo"]), b["where"])
            if bad:
                b["notes"].append("locations that don't exist: " + "; ".join(bad))
                badlocs.extend(f"{b['id']} {x}" for x in bad)
            cmd = as_cmd(c.get("command"))
            if cmd:
                b["proof"] = {"verdict": "reproduced", "command": cmd, "expected": c.get("expected"),
                              "observed": c.get("observed"), "runs": c.get("runs"),
                              "entry_point": c.get("entry_point"), "notes": "the finder's own reproduction",
                              "by": source}
                b["repro_dir"], note = own_repro(ws, b["id"], scratch, cmd)
                if note:
                    b["notes"].append(note)
            added.append(b["id"])
        return added, empty
    added, empty = mutate(ws, add)

    with locked(ws):
        cov = load_json(ws / "coverage.json", {})
        covered = {normalize(p, cfg["repo"]) for p in as_list(data.get("covered"))}
        cov[source] = sorted(set(cov.get(source, [])) | covered)
        save_json(ws / "coverage.json", cov)
        if data.get("promises"):
            ps = load_json(ws / "promises.json", [])
            ps += [dict(p, source_agent=source) for p in as_list(data["promises"]) if isinstance(p, dict)]
            save_json(ws / "promises.json", ps)

    tally = {"shows": 0, "behaves": 0, "inconclusive": 0}
    if not a.no_verify:
        for bid in added:
            b = get_bug(load_bugs(ws), bid)
            proof = b.get("proof")
            if not proof:
                continue
            verdict, runs = verify_repro(ws, cfg, bid, b.get("repro_dir"), proof["command"],
                                         runs_total(proof.get("runs")), quiet=True)
            tally[verdict] += 1

            def apply(db, bid=bid, verdict=verdict, runs=runs):
                x = get_bug(db, bid)
                x["verified"] = {"result": verdict, "runs": runs}
                if verdict == "shows":
                    x["status"] = "proven"
                else:
                    x["notes"].append("the finder's reproduction " + ("didn't show the bug" if verdict == "behaves"
                                      else "failed without an OBSERVED: line") + " in a fresh copy")
            mutate(ws, apply)
    if box and not a.keep:
        drop_sandbox(ws, cfg, a.sandbox)
    span = f"{added[0]}-{added[-1]}" if len(added) > 1 else (added[0] if added else "nothing")
    tried = sum(tally.values())
    print(f"{source}: added {span}; it read {len(covered)} files"
          + (f"; {len(as_list(data.get('promises')))} promises" if data.get("promises") else "")
          + (f"; skipped {empty} entries with no claim" if empty else ""))
    if tried:
        print(f"Its reproductions, rerun in fresh copies: {tally['shows']} proven, {tally['behaves']} didn't show, "
              f"{tally['inconclusive']} inconclusive.")
    if badlocs:
        print(f"warning: {len(badlocs)} location{'s' if len(badlocs) != 1 else ''} it gave don't exist "
              f"(noted on the bugs): " + "; ".join(badlocs[:6]) + (" ..." if len(badlocs) > 6 else ""))


def cmd_add(a) -> None:
    ws, _ = load(a)
    b = mutate(ws, lambda db: new_bug(db, {"title": a.title, "claim": a.claim, "trigger": a.trigger,
                                           "expected": a.expected, "where": a.where, "kind": a.kind,
                                           "confidence": a.confidence, "severity": a.severity}, a.source))
    if not b:
        fail("--claim can't be empty")
    print(f"added {b['id']}: {b['title']}")


def cmd_merge(a) -> None:
    ws, _ = load(a)

    def merge(db):
        keep = get_bug(db, a.keep)
        for bid in a.duplicates:
            d = get_bug(db, bid)
            if d is keep:
                continue
            was_proven = d["status"] == "proven"
            d["status"], d["merged_into"] = "merged", keep["id"]
            keep["found_by"] += [s for s in d["found_by"] if s not in keep["found_by"]]
            keep["where"] += [w for w in d["where"] if w not in keep["where"]]
            if CONFIDENCE.index(d["confidence"]) > CONFIDENCE.index(keep["confidence"]):
                keep["confidence"] = d["confidence"]
            if d.get("triage") in SEVERITY and (keep.get("triage") not in SEVERITY or
                                                SEVERITY.index(d["triage"]) < SEVERITY.index(keep["triage"])):
                keep["triage"] = d["triage"]
            if was_proven and keep["status"] == "candidate":   # keep a working reproduction
                for k in ("proof", "repro_dir", "verified"):
                    if k in d:
                        keep[k] = d[k]
                keep["status"] = "proven"
            keep["notes"].append(f"merged {d['id']}: {d['title']}")
        return keep
    keep = mutate(ws, merge)
    print(f"{keep['id']} now stands for {', '.join(a.duplicates)}; found by {len(keep['found_by'])} finders")


def challenge_changes(data: dict) -> dict:
    """What one challenger verdict changes on its bug."""
    v = data.get("verdict")
    if v not in ("stands", "question", "knocked-down", "docs-stale"):
        fail(f"a challenger's verdict is stands, question, knocked-down or docs-stale, not {v!r}")
    changes = {"challenge": {k: data.get(k) for k in ("verdict", "reason", "severity", "likelihood", "question",
                                                       "repro_passes_without_bug", "repro_problem")}}
    # A reproduction that still fails with the bug removed can't tell a fix from no fix. The bug can still
    # stand; its reproduction gets rewritten before anyone fixes against it.
    passes = data.get("repro_passes_without_bug")
    if passes in (False, "false"):
        changes["repro_ok"] = False
    elif passes in (True, "true"):
        changes["repro_ok"] = True
    if data.get("severity") in SEVERITY:
        changes["severity"] = data["severity"]
    if data.get("likelihood") in LIKELIHOOD:
        changes["likelihood"] = data["likelihood"]
    # docs-stale: the code does what it should and the docs describe something older. It's a finding
    # for the docs (killer-docs), and the code is never changed to match them.
    changes["status"] = {"stands": "confirmed", "question": "question", "knocked-down": "dismissed",
                         "docs-stale": "stale-docs"}[v]
    return changes


def cmd_challenges(a) -> None:
    """Record a group challenger's verdicts: one entry per bug in its reply's "verdicts"."""
    ws, cfg = load(a)
    path, box = reply_source(a, cfg)
    entries = [e for e in as_list(read_reply(path).get("verdicts")) if isinstance(e, dict)]
    if not entries:
        fail(f"{path} has no \"verdicts\" list")
    db = load_bugs(ws)
    planned = [(get_bug(db, str(e.get("id") or "")), challenge_changes(e)) for e in entries]   # all valid, or none

    def apply(db):
        out = []
        for b, changes in planned:
            x = get_bug(db, b["id"])
            x.update(changes)
            out.append(x)
        return out
    done = mutate(ws, apply)
    if box and not a.keep:
        drop_sandbox(ws, cfg, a.sandbox)
    for b in done:
        extra = "/".join(x for x in (b.get("severity"), b.get("likelihood")) if x)
        print(f"{b['id']}: {b['status']}" + (f" ({extra})" if extra else "") + f". {b['title']}")


def cmd_update(a) -> None:
    ws, cfg = load(a)
    current = get_bug(load_bugs(ws), a.id)
    changes, notes = {}, []
    box = None
    if a.reply or a.sandbox:
        if not a.role:
            fail("a reply needs --as prover, challenger, code-check or review")
        path, box = reply_source(a, cfg)
        data = read_reply(path)
        v = data.get("verdict")
        if a.role == "prover":
            if v not in ("reproduced", "internal-only", "not-reproduced", "impractical"):
                fail(f"a prover's verdict is reproduced, internal-only, not-reproduced or impractical, not {v!r}")
            changes["proof"] = {k: data.get(k) for k in ("verdict", "command", "expected", "observed",
                                                          "runs", "entry_point", "notes", "would_take")}
            tried_only = v in ("not-reproduced", "impractical")
            changes["status"] = "suspected" if tried_only else "proven"
            changes["internal"] = v == "internal-only"
            changes["impractical"] = v == "impractical"
            if v == "impractical":
                print("It can't be made to happen here. If it's high or medium severity, send a code checker "
                      "(briefs.md); a chain that holds makes it argued.")
            if box:
                keep_scratch(box, ws / "repros" / current["id"])
                changes["repro_dir"] = f"repros/{current['id']}"
            cmd = as_cmd(data.get("command"))
            if not tried_only and not cmd:
                print("warning: the prover gave no command to reproduce it")
            if not tried_only and cmd and not a.no_verify:
                result, runs = verify_repro(ws, cfg, current["id"], changes.get("repro_dir") or current.get("repro_dir"),
                                            cmd, runs_total(data.get("runs")))
                changes["verified"] = {"result": result, "runs": runs}
                if result == "behaves":
                    changes["status"] = "suspected"
                    notes.append("the prover's reproduction didn't show the bug when rerun in a fresh copy")
                elif result == "inconclusive":
                    print("warning: the reproduction failed without an OBSERVED: line (or timed out). Read the "
                          "output above; if it isn't the bug, set --status suspected.")
        elif a.role == "challenger":
            changes.update(challenge_changes(data))
        elif a.role == "code-check":
            # For a bug that can't be run here: a chain from an entry point to the failure, checked by
            # reading. One that holds is "argued": reported apart from the proven bugs, never as one.
            if v not in ("holds", "question", "breaks"):
                fail(f"a code checker's verdict is holds, question or breaks, not {v!r}")
            if v == "holds" and not as_list(data.get("chain")):
                fail("a code checker's 'holds' needs its chain: the steps from an entry point, with file:line")
            changes["challenge"] = {k: data.get(k) for k in ("verdict", "reason", "severity", "likelihood",
                                                              "question", "chain")}
            if data.get("severity") in SEVERITY:
                changes["severity"] = data["severity"]
            if data.get("likelihood") in LIKELIHOOD:
                changes["likelihood"] = data["likelihood"]
            changes["status"] = {"holds": "argued", "question": "question", "breaks": "dismissed"}[v]
        else:
            if v not in ("ok", "problems"):
                fail(f"a fix review's verdict is ok or problems, not {v!r}")
    for field, allowed in (("status", STATUSES), ("severity", SEVERITY), ("likelihood", LIKELIHOOD),
                           ("triage", SEVERITY)):
        val = getattr(a, field)
        if val is not None:
            if val not in allowed:
                fail(f"--{field} is one of {', '.join(allowed)}")
            changes[field] = val
    if a.title:
        changes["title"] = a.title
    if a.record:
        changes["record"] = a.record
    if a.confirm_promise:
        changes["inferred"] = False
        notes.append("the user confirmed the inferred promise")
    if a.note:
        notes.append(a.note)

    def apply(db):
        b = get_bug(db, a.id)
        if a.role == "review" and (a.reply or a.sandbox):
            b.setdefault("reviews", []).append(data)
        b.update(changes)
        b["notes"] += notes
        return b
    b = mutate(ws, apply)
    if box and not a.keep:
        drop_sandbox(ws, cfg, a.sandbox)
    extra = "/".join(x for x in (b.get("severity"), b.get("likelihood")) if x)
    print(f"{b['id']}: {b['status']}" + (f" ({extra})" if extra else "") + f". {b['title']}")


def cmd_list(a) -> None:
    ws, _ = load(a)
    bugs = load_bugs(ws)["bugs"]
    if a.status:
        want = set(a.status.split(","))
        bugs = [b for b in bugs if b["status"] in want]
    if not bugs:
        print("No bugs" + (f" with status {a.status}" if a.status else "") + ".")
        return
    for b in sorted(bugs, key=lambda b: (LIST_ORDER.index(b["status"]), rank_key(b))):
        if b.get("severity"):
            sev = "/".join(x for x in (b.get("severity"), b.get("likelihood")) if x)
        else:
            sev = f"~{b['triage']}" if b.get("triage") else "untriaged"
        finders = len(b["found_by"])
        print(f"{b['id']:<5} {b['status']:<10} {sev:<18} {finders} finder{'s' if finders != 1 else ' '}  {b['title']}")


def cmd_show(a) -> None:
    ws, _ = load(a)
    print(json.dumps(get_bug(load_bugs(ws), a.id), indent=2, ensure_ascii=False))


def cmd_coverage(a) -> None:
    ws, _ = load(a)
    scope = [ln for ln in (ws / "scope.txt").read_text(encoding="utf-8").splitlines() if ln]
    cov = load_json(ws / "coverage.json", {})
    read = set().union(*cov.values()) if cov else set()
    missing = [f for f in scope if f not in read]
    print(f"{len(scope) - len(missing)} of {len(scope)} files in scope were read by at least one finder.")
    for src, files in sorted(cov.items()):
        print(f"  {src}: {len([f for f in files if f in set(scope)])} in scope")
    if missing:
        print("Not read by anyone:")
        groups: dict[str, list[str]] = {}
        for f in missing:
            groups.setdefault(f.split("/")[0] if "/" in f else ".", []).append(f)
        for folder, files in sorted(groups.items()):
            shown = ", ".join(files[:8]) + (f", and {len(files) - 8} more" if len(files) > 8 else "")
            print(f"  {folder} ({len(files)}): {shown}")
    with locked(ws):
        save_json(ws / "coverage-summary.json", {"in_scope": len(scope), "read": len(scope) - len(missing),
                                                 "missing": missing})


def cmd_check_fix(a) -> None:
    ws, cfg = load(a)
    if not cfg["git"]:
        fail("check-fix needs a git repository: it takes the fix out with git")
    top = Path(cfg["repo"])
    base = git(top, "rev-parse", "--verify", (a.base or "HEAD") + "^{commit}").strip()
    changed = changed_since(top, base)
    pats = [p.replace("\\", "/").rstrip("/") for p in (a.tests or [])]
    is_test = lambda f: any(f == p or f.startswith(p + "/") or fnmatch.fnmatch(f, p) for p in pats)
    db = load_bugs(ws)
    b = get_bug(db, a.bug) if a.bug else None
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    repro = None
    if a.repro:
        rb = get_bug(db, a.repro)
        cmd = cmd or as_cmd((rb.get("proof") or {}).get("command"))
        repro = ws / rb["repro_dir"] if rb.get("repro_dir") else None
        b = b or rb
        if rb.get("repro_ok") is False:
            fail(f"{rb['id']}'s reproduction still failed with the bug removed, so it can't check a fix. Rewrite it "
                 "(it has to pass once the bug is gone), save it with update --as prover, then check the fix")
    if not cmd:
        fail("give the test command after --, or --repro <bug> to use a bug's reproduction")
    tests = [f for f in changed if is_test(f)]
    fixes = [f for f in changed if not is_test(f)]
    if not fixes:
        fail(f"nothing changed outside the tests since {a.base or 'HEAD'}, so there's no fix to check")
    name = f"check-{secrets.token_hex(3)}"
    root = make_sandbox(cfg, ws, name)
    run = runner()
    try:
        if repro and repro.is_dir():
            shutil.copytree(repro, root / SCRATCH, dirs_exist_ok=True)
        with_fix = run.run_many(root, cmd, a.repeat, a.timeout, a.cwd)
        for f in fixes:
            if git_ok(top, "cat-file", "-e", f"{base}:{f}"):
                git(root / "repo", "checkout", "-q", base, "--", f)
            else:
                (root / "repo" / f).unlink(missing_ok=True)
        without = run.run_many(root, cmd, a.repeat, a.timeout, a.cwd)
    finally:
        if not a.keep:
            drop_sandbox(ws, cfg, name)
    passes = not any(run.failed(r) for r in with_fix["runs"])
    if a.repro:   # a reproduction has to fail by saying what it saw, not by crashing
        fails_without = any(classify(r) == "shows" for r in without["runs"])
    else:
        fails_without = any(run.failed(r) for r in without["runs"])
    verdict = "PASS" if passes and fails_without else "FAIL"
    print(f"Fix: {', '.join(fixes)}")
    print(f"Tests kept in both runs: {', '.join(tests) or 'none'}")
    print("\n== with the fix ==")
    print(run.summary(with_fix))
    print("\n== without the fix ==")
    print(run.summary(without))
    print()
    if verdict == "PASS":
        print("PASS: the test fails without the fix and passes with it. Read the failure above: it should be "
              "the bug itself, not a missing file or import the fix added.")
    elif not passes:
        print("FAIL: the test fails even with the fix, so the fix doesn't work (or the test is wrong).")
    else:
        print("FAIL: the test passes without the fix too, so it doesn't prove the fix. Make it fail on the bug.")
    record = {"bug": b["id"] if b else None, "base": base, "cmd": cmd, "fix_files": fixes, "test_files": tests,
              "with_fix": [r["code"] for r in with_fix["runs"]], "without_fix": [r["code"] for r in without["runs"]],
              "verdict": verdict, "at": dt.datetime.now().isoformat(timespec="seconds")}
    save_json(ws / "checks" / f"{name}.json", record)
    if b:
        mutate(ws, lambda db: get_bug(db, b["id"]).__setitem__("fix_check", {"verdict": verdict,
                                                                            "check": f"checks/{name}.json"}))
    sys.exit(0 if verdict == "PASS" else 1)


def cmd_recheck(a) -> None:
    ws, cfg = load(a)
    rec = load_record(cfg)
    entries = [e for e in rec["entries"] if e.get("status") in ("open", "fixed") and e.get("command")]
    if not entries:
        print("No recorded reproductions to rerun.")
        return
    proj = record_path(cfg).parent
    name = f"recheck-{secrets.token_hex(2)}"
    root = make_sandbox(cfg, ws, name)
    run, results = runner(), []
    try:
        for e in entries:
            rmtree(root / SCRATCH)
            src = proj / e["repro_dir"] if e.get("repro_dir") else None
            if src and src.is_dir():
                shutil.copytree(src, root / SCRATCH)
            else:
                (root / SCRATCH).mkdir()
            res = run.run_many(root, as_cmd(e["command"]), 1, a.timeout)
            kind = classify(res["runs"][0])
            state = "inconclusive" if kind == "inconclusive" else \
                {("fixed", True): "CAME BACK", ("fixed", False): "still fixed",
                 ("open", True): "still there", ("open", False): "no longer shows"}[(e["status"], kind == "shows")]
            if e.get("repro_ok") is False and kind == "shows":
                state += " (unreliable: this reproduction also fails on correct code)"
            results.append({"id": e["id"], "title": e["title"], "was": e["status"], "now": state})
            print(f"{e['id']:<5} {state:<16} {e['title']}")
    finally:
        drop_sandbox(ws, cfg, name)
    save_json(ws / "recheck.json", results)


def cmd_close(a) -> None:
    ws, cfg = load(a)
    proj = record_path(cfg).parent
    mapping = {"confirmed": "open", "argued": "open", "fixed": "fixed", "intended": "intended", "known": None}

    def close(db):
        rec = load_record(cfg)
        by_id = {e["id"]: e for e in rec["entries"]}
        added = updated = 0
        for b in db["bugs"]:
            if b["status"] not in mapping:
                continue
            status = mapping[b["status"]]
            e = by_id.get(b.get("record") or "")
            if e is None:
                if status is None:
                    continue
                e = {"id": f"R{1 + max([int(x['id'][1:]) for x in rec['entries']] or [0])}",
                     "first_seen": today(), "runs": []}
                rec["entries"].append(e)
                by_id[e["id"]] = e
                b["record"] = e["id"]
                added += 1
            else:
                updated += 1
            e.update({"title": b["title"], "claim": b["claim"], "where": b["where"], "last_seen": today(),
                      "severity": b.get("severity"), "likelihood": b.get("likelihood"), "repro_ok": b.get("repro_ok")})
            if status:
                e["status"] = status
            if ws.name not in e["runs"]:
                e["runs"].append(ws.name)
            cmd = (b.get("proof") or {}).get("command")
            if cmd and b.get("repro_dir"):
                dst = proj / "repros" / e["id"]
                rmtree(dst)
                shutil.copytree(ws / b["repro_dir"], dst)
                e["command"], e["repro_dir"] = cmd, f"repros/{e['id']}"
        save_json(record_path(cfg), rec)
        return added, updated, len(rec["entries"])
    added, updated, total = mutate(ws, close)
    print(f"Record: {added} new, {updated} updated, {total} in all ({record_path(cfg)})")


def cmd_clean(a) -> None:
    ws, cfg = load(a)
    names = list(cfg.get("sandboxes", {}))
    for name in names:
        drop_sandbox(ws, cfg, name)
    base = Path(cfg["sandboxes_home"]) / cfg["run_id"]
    if base.is_dir():
        for leftover in base.iterdir():   # a sandbox whose agent crashed before it was recorded
            rmtree(leftover)
        base.rmdir()
    print(f"Deleted {len(names)} sandbox{'es' if len(names) != 1 else ''}.")


# ---------------------------------------------------------------- report

def finders(b: dict) -> str:
    names = [re.sub(r"^hunter:", "", s).split("#")[0] for s in b["found_by"]]
    distinct = len(agent_sources(b))   # the lead, the user and tools aren't separate finders
    lead = f"{distinct} finders separately" if distinct > 1 else f"{distinct or 'no'} finder{'s' if not distinct else ''}"
    return f"{lead} ({', '.join(dict.fromkeys(names))})"


def short(text, limit: int = 400) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def bug_block(b: dict) -> list[str]:
    p, c = b.get("proof") or {}, b.get("challenge") or {}
    tags = [x for x in (b.get("severity") and f"{b['severity']} severity", b.get("likelihood")) if x]
    head = f"### {b['id']}. {b['title']}" + (f" ({', '.join(tags)})" if tags else "")
    if b["status"] == "fixed":
        head += " · fixed"
    out = [head, "", b["claim"], ""]
    cmd = p.get("command")
    if cmd:
        shown = cmd if isinstance(cmd, str) else " ".join(cmd)
        out.append(f"- **Reproduce:** `{shown}`, run in a copy of the project"
                   + (f"; files in `{b['repro_dir']}/`" if b.get("repro_dir") else ""))
    if p.get("expected") or b.get("expected"):
        out.append(f"- **Expected:** {p.get('expected') or b['expected']}")
    if p.get("observed") or b.get("observed"):
        out.append(f"- **Observed:** {p.get('observed') or b['observed']}")
    if p.get("runs"):
        out.append(f"- **Shows up in:** {p['runs']} runs")
    if b.get("internal"):
        out.append(f"- **Internal only:** reproduced by calling {p.get('entry_point') or 'internal code'} directly; "
                   "no route from a real entry point was found")
    if b["where"]:
        out.append(f"- **Where:** {', '.join(b['where'])}")
    out.append(f"- **Found by:** {finders(b)}")
    if c.get("reason"):   # the full reason is in bugs.json; the report keeps it readable
        out.append(f"- **Why it stands:** {short(c['reason'])}")
    if b.get("repro_ok") is False:
        why = (b.get("challenge") or {}).get("repro_problem")
        out.append("- **Reproduction needs rewriting:** it still fails with the bug removed"
                   + (f" ({short(why, 200)})" if why else "") + ", so it can't check a fix")
    fc = b.get("fix_check")
    if fc:
        out.append(f"- **Fix check:** {fc['verdict']} ({fc['check']})")
    out.append("")
    return out


def cmd_forward(a) -> None:
    """Which suspects go on to be proven and challenged: by severity, within the depth's limit."""
    ws, cfg = load(a)
    depth = a.depth or cfg.get("depth") or "standard"
    levels, limit = DEPTHS[depth]
    limit = a.limit or limit
    bugs = load_bugs(ws)["bugs"]
    # The limit is for the whole run: what's been challenged already (copies included) comes out of it.
    used = sum(1 for b in bugs if b.get("challenge"))
    room = max(0, limit - used)
    waiting = [b for b in bugs if b["status"] in ("candidate", "proven") and not b.get("challenge")]
    unconfirmed = [b["id"] for b in waiting if b.get("inferred")]
    pool = [b for b in waiting if not b.get("inferred")]

    def key(b):   # worst first; then found by more agents separately; then surer; then already proven
        return (SEVERITY.index(level(b)), -len(agent_sources(b)), -CONFIDENCE.index(b["confidence"]),
                0 if b["status"] == "proven" else 1, int(b["id"][1:]))
    untriaged = [b["id"] for b in pool if level(b) not in SEVERITY]
    eligible = sorted([b for b in pool if level(b) in levels], key=key)
    chosen, over = eligible[:room], eligible[room:]
    below = [b["id"] for b in pool if level(b) in SEVERITY and level(b) not in levels]
    code = [b["id"] for b in bugs if b["status"] == "suspected" and b.get("impractical")
            and level(b) in levels and not b.get("challenge")]
    plan = {"depth": depth, "limit": limit, "chosen": [b["id"] for b in chosen],
            "prove": [b["id"] for b in chosen if b["status"] == "candidate"],
            "challenge": [b["id"] for b in chosen if b["status"] == "proven"],
            "code_check": code, "over": [b["id"] for b in over], "below": below, "untriaged": untriaged,
            "unconfirmed": unconfirmed, "used": used}
    with locked(ws):
        save_json(ws / "forward.json", plan)
    names = ", ".join(levels)
    print(f"Depth {depth}: {names} severity, at most {limit} in the run ({used} challenged already). "
          f"Going forward: {len(chosen)}.")
    print(f"  needs a prover:       {' '.join(plan['prove']) or '-'}")
    print(f"  ready for challenge:  {' '.join(plan['challenge']) or '-'}")
    if code:
        print(f"  for a code checker:   {' '.join(code)}  (can't be run here)")
    if over:
        print(f"  over the limit:       {' '.join(plan['over'])}  (listed in the report, not tried)")
    if below:
        print(f"  below this depth:     {len(below)} suspect{'s' if len(below) != 1 else ''}, listed in the report")
    if unconfirmed:
        print(f"  promise to confirm:   {' '.join(unconfirmed)}  (inferred, so they wait for the user: "
              "update --confirm-promise)")
    if untriaged:
        print(f"warning: rate these first with update --triage: {' '.join(untriaged)}")


def first_location(b: dict, top: str) -> tuple[str, int | None]:
    """The first file:line in a bug's locations. A path may contain dashes (Get-RepoList.ps1)."""
    for w in b["where"]:
        for tok in re.split(r"[\s,;()`'\"]+", str(w)):
            m = re.match(r"(.+?)(?::(\d+)(?:-\d+)?)?:?$", tok)
            if m and ("/" in m.group(1) or "." in m.group(1)) and not re.fullmatch(r"[A-Za-z]", m.group(1)):
                return normalize(m.group(1), top), int(m.group(2)) if m.group(2) else None
    return ".", None


def cmd_findings(a) -> None:
    """The bugs in the shape a host's ReportFindings tool takes, worst first."""
    ws, cfg = load(a)
    keep = [b for b in load_bugs(ws)["bugs"] if b["status"] in ("confirmed", "fixed", "argued", "question", "proven")]
    out = []
    for b in sorted(keep, key=lambda b: (0 if b["status"] in ("confirmed", "fixed") else 1, rank_key(b)))[:32]:
        file, line = first_location(b, cfg["repo"])
        trig = str(b.get("trigger") or "").strip()
        seen = str((b.get("proof") or {}).get("observed") or b.get("observed") or "").strip()
        item = {"file": file, "summary": b["claim"],
                "failure_scenario": f"{trig} → {seen}" if trig and seen else (trig or seen or b["claim"]),
                "short_summary": b["title"][:60],
                "category": {"test-gap": "test-coverage", "mismatch": "docs"}.get(b["kind"], "correctness"),
                "verdict": "CONFIRMED" if b["status"] in ("confirmed", "fixed") else "PLAUSIBLE"}
        if line:
            item["line"] = line
        out.append(item)
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_repro(a) -> None:
    """Copy bugs' reproductions into an agent's sandbox, and print how to run each there."""
    ws, cfg = load(a)
    db = load_bugs(ws)
    bugs = [get_bug(db, i) for i in a.ids]
    box = kit.find_sandbox(cfg, a.into)
    dest = box / SCRATCH
    taken: dict[str, str] = {}
    for b in bugs:   # check first: two bugs' files under one name would overwrite each other
        src = ws / b["repro_dir"] if b.get("repro_dir") else None
        for entry in (sorted(p.name for p in src.iterdir()) if src and src.is_dir() else []):
            if entry in taken:
                fail(f"{b['id']} and {taken[entry]} both have a reproduction file or folder called {entry}; "
                     "give them separate challengers")
            taken[entry] = b["id"]
    for b in bugs:
        cmd = as_cmd((b.get("proof") or {}).get("command"))
        src = ws / b["repro_dir"] if b.get("repro_dir") else None
        if src and src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if cmd:
            print(f"{b['id']}: " + kit.run_command_line(box).replace("<command>", " ".join(cmd)))
        else:
            print(f"warning: {b['id']} has no reproduction command")
    print(f"Copied into {dest}.")


def is_doc(f: str) -> bool:
    low = f.lower()
    return low.endswith((".md", ".markdown", ".rst", ".adoc", ".txt")) or low.split("/")[0] in ("docs", "doc", "wiki", "man")


def cmd_freshness(a) -> None:
    """When each doc last changed, against the code. Docs far behind the code may be out of date."""
    ws, cfg = load(a)
    top = Path(cfg["repo"])
    if not cfg["git"] or not kit.has_head(top):
        fail("freshness needs a git repository with commits")
    files = kit.scope_files(ws)
    docs = [f for f in files if is_doc(f)]
    if not docs:
        print("No docs in scope.")
        return
    rows = []
    for d in docs:
        last = git(top, "log", "-1", "--format=%H %cs", "--", d, check=False).strip()
        if not last:
            rows.append({"doc": d, "changed": "not committed", "code_commits_since": 0})
            continue
        sha, date = last.split()
        log = git(top, "log", "--format=@%H", "--name-only", f"{sha}..HEAD", check=False)
        since, touched = 0, False
        for line in log.splitlines() + ["@"]:
            if line.startswith("@"):
                since += touched
                touched = False
            elif line.strip() and not is_doc(line.strip()):
                touched = True
        rows.append({"doc": d, "changed": date, "code_commits_since": since})
    rows.sort(key=lambda r: -r["code_commits_since"])
    with locked(ws):
        save_json(ws / "freshness.json", rows)
    width = max(len(r["doc"]) for r in rows)
    for r in rows:
        print(f"{r['doc']:<{width}}  {r['changed']:<13}  {r['code_commits_since']} code commit"
              f"{'s' if r['code_commits_since'] != 1 else ''} since")
    stale = [r["doc"] for r in rows if r["code_commits_since"] >= a.behind]
    if stale:
        print(f"Behind the code by {a.behind}+ commits: {', '.join(stale)}. Their promises are the likeliest to be "
              "out of date: tell the promise checker to lean on general rules and --help text for them.")


def role_counts(cfg: dict) -> dict:
    counts: dict[str, int] = {}
    for x in cfg.get("agents", []):
        role = ROLES.get(x["name"].split("-")[0], "other")
        counts[role] = counts.get(role, 0) + 1
    return counts


def cmd_decide(a) -> None:
    ws, _ = load(a)
    if bool(a.text) == bool(a.drop):
        fail("give the decision's text, or --drop <number> to take one back")
    with locked(ws):
        items = load_json(ws / "decisions.json", [])
        if a.drop:
            if not 1 <= a.drop <= len(items):
                fail(f"there's no decision {a.drop}; there are {len(items)}")
            gone = items.pop(a.drop - 1)
        else:
            items.append({"text": a.text, "at": dt.datetime.now().isoformat(timespec="seconds")})
        save_json(ws / "decisions.json", items)
    if a.drop:
        print(f"Took back decision {a.drop}: {gone['text']}")
    else:
        print(f"Recorded decision {len(items)}: {a.text}")


def cmd_wait(a) -> None:
    ws, _ = load(a)
    with locked(ws):
        items = load_json(ws / "waiting.json", [])
        items.append({"text": a.text, "command": a.command or "",
                      "at": dt.datetime.now().isoformat(timespec="seconds")})
        save_json(ws / "waiting.json", items)
    print(f"Recorded as waiting for the user's OK: {a.text}" + (f" ({a.command})" if a.command else ""))


def run_commits(cfg: dict) -> tuple[str | None, list[str]]:
    """The project's current branch, and the commits made on it since the run began."""
    top = Path(cfg["repo"])
    if not cfg.get("git") or not cfg.get("head"):
        return None, []
    branch = git(top, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip() or None
    log = git(top, "log", "--format=%h %s", "-n", "100", f"{cfg['head']}..HEAD", check=False)
    return branch, [ln for ln in log.splitlines() if ln.strip()]


def cmd_report(a) -> None:
    ws, cfg = load(a)
    bugs = load_bugs(ws)["bugs"]
    by = lambda *s: sorted([b for b in bugs if b["status"] in s], key=rank_key)
    confirmed, questions, argued = by("confirmed", "fixed"), by("question"), by("argued")
    dismissed, known, unchallenged = by("dismissed", "intended"), by("known"), by("proven")
    stale = by("stale-docs")
    open_ = by("suspected", "candidate")
    plan = load_json(ws / "forward.json", {})
    over = set(plan.get("over", []))
    suspected = [b for b in open_ if b["status"] == "suspected" or level(b) in ("high", "medium")]
    low = [b for b in open_ if b not in suspected]
    scope = cfg["scope"]
    where = {"repo": "the whole project", "path": f"`{scope.get('path')}`", "changes": "uncommitted changes",
             "branch": f"changes since `{scope.get('ref')}`"}[scope["kind"]]
    fixed = sum(1 for b in confirmed if b["status"] == "fixed")
    plural = lambda n, w: f"{n} {w}{'s' if n != 1 else ''}"
    lines = [f"# Bug report: {cfg['target'] or Path(cfg['repo']).name}", "",
             f"**Project:** `{cfg['repo']}`" + (f" at `{(cfg['head'] or '')[:10]}`" if cfg.get("head") else ""),
             f"**Scope:** {where}", "",
             f"**Result:** {plural(len(confirmed), 'proven bug')}" + (f" ({fixed} fixed)" if fixed else "")
             + (f", {len(argued)} argued but not reproduced" if argued else "")
             + f", {plural(len(questions), 'question')} for you, {len(unchallenged)} reproduced but not challenged, "
             f"{len(suspected)} suspected but not proven, {len(low)} low-severity suspicions not checked, "
             f"{len(dismissed)} dismissed" + (f", {len(stale)} places where the docs are out of date" if stale else "")
             + (f", {len(known)} already on record" if known else "") + ".", ""]
    lines += ["## Proven bugs", ""]
    lines += sum((bug_block(b) for b in confirmed), []) or ["None.", ""]
    if argued:
        lines += ["## Argued, not reproduced", "",
                  "These can't be made to happen here: they need a full disk, a narrow race, another system or a "
                  "real service. A code checker traced each one from an entry point to the failure, and the chain "
                  "held. They're probably real, but nobody has seen them happen.", ""]
        for b in argued:
            chain = as_list((b.get("challenge") or {}).get("chain"))
            take = (b.get("proof") or {}).get("would_take")
            lines += bug_block(b)[:-1] + ([f"- **To see it happen:** {take}"] if take else [])
            lines += ["- **The chain:**"] + [f"  {i}. {step}" for i, step in enumerate(chain, 1)] + [""]
    if questions:
        lines += ["## Questions for you", "", "These reproduce, but they may be deliberate. Nothing was changed for them.", ""]
        for b in questions:
            q = (b.get("challenge") or {}).get("question") or "Is this intended?"
            lines += bug_block(b)[:-1] + [f"- **Question:** {q}", ""]
    if unchallenged:
        lines += ["## Reproduced, not challenged", "",
                  "These were made to happen in a fresh copy, but nobody tried to knock them down.", ""]
        lines += sum((bug_block(b) for b in unchallenged), [])
    if suspected:
        lines += ["## Suspected, not proven", "", "Nobody made these happen. They're listed so you can judge them, "
                  "not counted as bugs.", ""]
        for b in suspected:
            tag = f" ({level(b)})" if level(b) else ""
            if b["status"] == "candidate":
                why = "; ".join(b["notes"] + (["over this run's limit, so not tried"] if b["id"] in over else [])) \
                      or "not tried"
                lines.append(f"- **{b['id']}. {b['title']}**{tag}: {b['claim']} *{why[0].upper() + why[1:]}.*")
            else:
                why = (b.get("proof") or {}).get("notes") or "no notes"
                lines.append(f"- **{b['id']}. {b['title']}**{tag}: {b['claim']} *Tried, not reproduced:* {why}")
        lines.append("")
    if low:
        lines += ["## Low severity, not checked", "", "Finders rated these minor, so nobody tried to reproduce them.", ""]
        lines += [f"- **{b['id']}. {b['title']}**: {b['claim']}" for b in low] + [""]
    if stale:
        lines += ["## Docs out of date", "",
                  "Here the code does what it should, and the docs describe something older. These are findings "
                  "for the docs (killer-docs can fix them); the code was left alone.", ""]
        for b in stale:
            why = short((b.get("challenge") or {}).get("reason"))
            lines.append(f"- **{b['id']}. {b['title']}**: {b['claim']}" + (f" *{why}*" if why else ""))
        lines.append("")
    if dismissed:
        lines += ["## Dismissed", ""]
        for b in dismissed:
            why = short((b.get("challenge") or {}).get("reason")) or ("intended, per you" if b["status"] == "intended" else "")
            lines.append(f"- **{b['id']}. {b['title']}**: {why}")
        lines.append("")
    if known:
        lines += ["## Already on record", ""]
        lines += [f"- **{b['id']}. {b['title']}** ({b.get('record', 'record')})" for b in known] + [""]
    promises = load_json(ws / "promises.json", [])
    if promises:
        lines += ["## Promises checked", "", "| | Promise | Source | Result |", "|---|---|---|---|"]
        cell = lambda v: str(v or "").replace("|", "\\|").replace("\n", " ")
        for p in promises:
            lines.append(f"| {cell(p.get('id'))} | {cell(p.get('promise'))} | {cell(p.get('source'))} | {cell(p.get('result'))} |")
        lines.append("")
    cs = load_json(ws / "coverage-summary.json", None)
    if cs:
        lines += ["## Coverage", "", f"{cs['read']} of {cs['in_scope']} files in scope were read by at least one finder."]
        if cs["missing"]:
            lines.append("Not read: " + ", ".join(f"`{m}`" for m in cs["missing"][:40])
                         + (f", and {len(cs['missing']) - 40} more" if len(cs["missing"]) > 40 else "") + ".")
        lines.append("")
    rc = load_json(ws / "recheck.json", None)
    if rc:
        lines += ["## Earlier runs' bugs, rerun", ""] + [f"- {r['id']} {r['title']}: {r['now']}" for r in rc] + [""]
    decisions = load_json(ws / "decisions.json", [])
    waiting = load_json(ws / "waiting.json", [])
    if decisions:
        lines += ["## Decisions to check", ""] + [f"- {d['text']}" for d in decisions] + [""]
    if waiting:
        lines += ["## Waiting for your OK", ""]
        lines += [f"- {w['text']}" + (f": `{w['command']}`" if w.get("command") else "") for w in waiting] + [""]
    roles = role_counts(cfg)
    agents = sum(roles.values())
    started = dt.datetime.fromisoformat(cfg["created"])
    took = dt.datetime.now() - started
    depth = plan.get("depth") or cfg.get("depth") or "standard"
    lines += ["## Run details", "",
              f"- **Depth:** {depth}: {', '.join(DEPTHS[depth][0])} severity went forward, at most "
              f"{plan.get('limit') or DEPTHS[depth][1]}" + (f"; {len(over)} more were over the limit" if over else ""),
              f"- **Agents:** {agents}" + (" (" + ", ".join(f"{n} {r}{'s' if n != 1 else ''}"
                                                          for r, n in sorted(roles.items(), key=lambda x: -x[1])) + ")"
                                           if roles else ""),
              f"- **Time:** started {cfg['created'].replace('T', ' ')}, this report "
              f"{int(took.total_seconds() // 3600)} h {int(took.total_seconds() % 3600 // 60)} min later",
              f"- **Scripts:** version {cfg.get('script') or 'unrecorded'}"
              + (f"; they've changed since the run began (now {script_version()})"
                 if cfg.get("script") and cfg["script"] != script_version() else ""),
              f"- **Run folder:** `{ws}`", ""]
    (ws / "report.md").write_text("\n".join(lines), encoding="utf-8")
    branch, commits = run_commits(cfg)
    headline = (f"{plural(len(confirmed), 'proven bug')}" + (f", {fixed} fixed" if fixed else "")
                + (f", {len(argued)} argued" if argued else "")
                + (f", {len(unchallenged)} reproduced but not challenged" if unchallenged else "")
                + (f", {plural(len(questions), 'question')} for you" if questions else ""))
    # Partial means something this depth meant to settle is unsettled, not that low bugs (which a
    # standard run never challenges) are left unchallenged.
    settles = DEPTHS[depth][0]
    status = "partial" if (questions or [b for b in unchallenged if level(b) in settles]
                           or [b for b in suspected if b["id"] in over]) else "done"
    save_json(ws / "summary.json", {
        "skill": "killer-bugs", "status": status, "headline": headline, "report": str(ws / "report.md"),
        "branch": branch if commits else None, "commits": commits,
        "decisions": [d["text"] for d in decisions],
        "waiting": [w["text"] + (f": {w['command']}" if w.get("command") else "") for w in waiting],
        "numbers": {"proven": len(confirmed), "fixed": fixed, "argued": len(argued), "questions": len(questions),
                    "unchallenged": len(unchallenged), "suspected": len(suspected), "low_unchecked": len(low),
                    "dismissed": len(dismissed), "over_limit": len(over), "stale_docs": len(stale)},
        "script": cfg.get("script"),
        "agents": {"total": agents, "by_role": roles}})
    print(f"Wrote {ws / 'report.md'}: {len(confirmed)} proven, {len(questions)} questions, {len(unchallenged)} "
          f"not challenged, {len(suspected)} suspected, {len(low)} low and unchecked, {len(dismissed)} dismissed.")
    print(f"killer-bugs: {status} · {headline} · {ws / 'report.md'}")


# ---------------------------------------------------------------- CLI

def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="bugs.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name, func, help_, ws=True):
        sp = sub.add_parser(name, help=help_, description=help_)
        if ws:
            sp.add_argument("--workspace", "-w", required=True, help="the run folder")
        sp.set_defaults(func=func)
        return sp

    s = cmd("init", cmd_init, "Create the run folder and record the scope.", ws=False)
    s.add_argument("--repo", default=".", help="the project folder (default: here)")
    s.add_argument("--scope", default="repo", help="repo, path:<folder>, changes, or branch[:<base>]")
    s.add_argument("--target", default="", help="one line on what the project is")
    s.add_argument("--slug", help="short name for the run folder")
    s.add_argument("--network", action="store_true", help="let sandboxes use the network")
    s.add_argument("--after", choices=["ask", "fix", "report"], default="ask",
                   help="what happens once bugs are proven (default ask)")
    s.add_argument("--include", nargs="*", help="ignored paths every sandbox needs, like node_modules")
    s.add_argument("--exclude", nargs="*", help="leave these out of scope, like vendor/ '*.svg' LICENSE")
    s.add_argument("--env", nargs="*", help="NAME=VALUE set in every sandbox; {sandbox} is each one's own folder")
    s.add_argument("--path-first", nargs="*", help="folders put ahead of PATH in every sandbox, for stand-ins "
                   "like a gh that refuses")
    s.add_argument("--depth", choices=list(DEPTHS), default="standard",
                   help="how much gets proven: quick (high, 5), standard (high and medium, 15), thorough (all, 30)")
    s.add_argument("--workspace", "-w", help="use this run folder instead of the default")
    s.add_argument("--force", action="store_true", help="with --workspace, start over in that run folder "
                   "(without it, init always makes a new folder)")

    s = cmd("sandbox", cmd_sandbox, "Make an isolated copy of the project for one agent.")
    s.add_argument("name", help="a name for it, like find-failures or prove-B3")
    s.add_argument("--include", nargs="*", help="ignored paths to copy too, like node_modules")
    s.add_argument("--rev", help="copy this commit or tag instead of the working tree")
    s.add_argument("--net", action="store_true", help="allow the network in this sandbox")
    s.add_argument("--env", nargs="*", help="NAME=VALUE set in this sandbox; {sandbox} is its folder")
    s.add_argument("--path-first", nargs="*", help="folders put ahead of PATH in this sandbox")

    s = cmd("collect", cmd_collect, "Add the suspected bugs from a finder's reply, and verify its reproductions.")
    s.add_argument("reply", nargs="?", help="a reply saved to a file (or use --sandbox)")
    s.add_argument("--sandbox", help="read reply.json from this sandbox's scratch folder, then delete the sandbox")
    s.add_argument("--source", help="who found them, like hunter:failures#1 (default: the sandbox's name)")
    s.add_argument("--no-verify", action="store_true", help="don't rerun the finder's reproductions")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")

    s = cmd("add", cmd_add, "Add one suspected bug by hand.")
    s.add_argument("--claim", required=True, help="what goes wrong, as something you could observe")
    s.add_argument("--title", default="")
    s.add_argument("--trigger", default="", help="how to make it happen")
    s.add_argument("--expected", default="")
    s.add_argument("--where", nargs="*", default=[])
    s.add_argument("--kind", choices=KINDS, default="bug")
    s.add_argument("--confidence", choices=CONFIDENCE, default="medium")
    s.add_argument("--severity", choices=SEVERITY, help="your triage guess")
    s.add_argument("--source", default="lead", help="user, lead, or tool:<name>")

    s = cmd("merge", cmd_merge, "Fold duplicates into one bug.")
    s.add_argument("keep")
    s.add_argument("duplicates", nargs="+")

    s = cmd("forward", cmd_forward, "Pick the suspects to prove and challenge: worst first, within the limit.")
    s.add_argument("--depth", choices=list(DEPTHS), help="override the run's depth")
    s.add_argument("--limit", type=int, help="override the depth's limit")

    cmd("findings", cmd_findings, "Print the bugs as JSON for a host's ReportFindings tool.")

    s = cmd("repro", cmd_repro, "Copy bugs' reproductions into an agent's sandbox and print how to run them.")
    s.add_argument("ids", nargs="+", help="one bug, or a challenger's group")
    s.add_argument("--into", required=True, help="the sandbox, like challenge-B3")

    s = cmd("freshness", cmd_freshness, "When each doc last changed, against the code.")
    s.add_argument("--behind", type=int, default=10, help="flag docs this many code commits behind (default 10)")

    s = cmd("challenges", cmd_challenges, "Record a group challenger's verdicts, one per bug.")
    s.add_argument("reply", nargs="?", help="a reply saved to a file (or use --sandbox)")
    s.add_argument("--sandbox", help="read reply.json from this sandbox, then delete it")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")

    s = cmd("update", cmd_update, "Record a reply about a bug, or set its fields.")
    s.add_argument("id")
    s.add_argument("--from", dest="reply", help="a reply saved to a file")
    s.add_argument("--sandbox", help="read reply.json from this sandbox (a prover's repro files are kept), then delete it")
    s.add_argument("--as", dest="role", choices=["prover", "challenger", "code-check", "review"])
    s.add_argument("--no-verify", action="store_true",
                   help="don't rerun the prover's reproduction in a fresh copy (it's rerun by default)")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")
    s.add_argument("--status")
    s.add_argument("--severity")
    s.add_argument("--likelihood")
    s.add_argument("--triage", help="your severity guess before a challenger has rated it")
    s.add_argument("--title")
    s.add_argument("--record", help="the record entry this bug matches, like R3")
    s.add_argument("--confirm-promise", action="store_true",
                   help="the user confirmed the inferred promise this suspect rests on; it can go forward now")
    s.add_argument("--note")

    s = cmd("list", cmd_list, "List the bugs so far.")
    s.add_argument("--status", help="only these statuses, comma-separated")

    s = cmd("show", cmd_show, "Show one bug in full.")
    s.add_argument("id")

    cmd("coverage", cmd_coverage, "List the files in scope that no finder read.")

    s = cmd("check-fix", cmd_check_fix,
            "Prove a fix: run the test with the fix, then with the fix taken out.")
    s.add_argument("--base", help="the commit before the fix (default HEAD, for an uncommitted fix)")
    s.add_argument("--tests", nargs="*", help="test files or globs, kept in both runs")
    s.add_argument("--repro", help="use this bug's saved reproduction as the test")
    s.add_argument("--bug", help="the bug this fixes")
    s.add_argument("--repeat", type=int, default=1, help="run each side this many times (for flaky bugs)")
    s.add_argument("--timeout", type=float, default=300)
    s.add_argument("--cwd", help="folder inside the project to run from")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")
    s.add_argument("cmd", nargs=argparse.REMAINDER, help="-- then the test command")

    s = cmd("recheck", cmd_recheck, "Rerun the reproductions earlier runs recorded.")
    s.add_argument("--timeout", type=float, default=300)

    s = cmd("decide", cmd_decide, "Record a call the user should check; the report lists it.")
    s.add_argument("text", nargs="?")
    s.add_argument("--drop", type=int, help="take back decision N (as numbered when it was recorded)")

    s = cmd("wait", cmd_wait, "Record an outward action (a push, an issue) left for the user's OK.")
    s.add_argument("text")
    s.add_argument("--command", help="the exact command that would do it")

    cmd("report", cmd_report, "Write report.md, and summary.json for whoever reads the run next.")
    cmd("close", cmd_close, "Save this run's outcomes to the project's record.")
    cmd("clean", cmd_clean, "Delete this run's sandboxes.")

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
