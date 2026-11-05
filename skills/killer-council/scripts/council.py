#!/usr/bin/env python3
"""Bookkeeping for a council run.

Commands:
  init      create the run folder and config, sealing the threshold
  track     add files to the tracked work (run it before editing or creating them)
  snapshot  copy the tracked work into round-N/artifact
  restore   copy a round's snapshot back over the working files
  verify    check members left the work as the snapshot has it
  lint     check briefs for leaks, and the work for traces of the review
  tally     score a round: median, vetoes, decision; rebuilds ledger.md
  holdout   compare the holdout juror's score with the council's
  reveal    print the threshold (for the final report)

The threshold is stored sealed (base64) in config.json so nobody reads it by
accident, the leader included. It guards against slips; it is not security.
Standard library only. Run `council.py <command> --help` for options.
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import re
import secrets
import shutil
import statistics
import sys
from pathlib import Path

SEAL_PREFIX = "council-threshold:"
CONFIDENCE = ("low", "medium", "high")
ARTIFACT_MARKERS = ("=== ARTIFACT START ===", "=== ARTIFACT END ===")
SCALE_MARKERS = ("<!-- SCALE START -->", "<!-- SCALE END -->")
WIDE_SPREAD = 25

# Wording that tells a member about the process instead of the work.
BRIEF_LEAKS = [
    (r"\bthreshold\b", "mentions a threshold"),
    (r"\b(target|passing|minimum|required) score\b", "mentions a target score"),
    (r"\bpass(ing)? mark\b", "mentions a pass mark"),
    (r"\b(must|needs? to|should) score\b|\bscore of at least\b", "tells the member what to score"),
    (r"\bnear[- ]perfect\b|\bhigh bar\b|\bvery high standard\b", "hints at how high the bar is"),
    (r"\bbe (strict|harsh|generous|lenient|kind)\b", "pushes the member's scoring one way"),
    (r"\bround\b|\biteration\b", "mentions rounds or iterations"),
    (r"\brevis(ed|ion|ions)\b|\bupdated version\b|\bnew version\b", "says the work was revised"),
    (r"\bprevious(ly)?\b|\blast time\b|\bearlier (score|review|feedback|version)\b", "points to an earlier review"),
    (r"\bagain\b|\bnow fixed\b|\bhas been fixed\b|\bimproved\b", "implies the work changed"),
    (r"\bother (reviewers|members|seats)\b", "mentions other members"),
]

# Traces of the review process left inside the work itself.
WORK_TRACES = [
    (r"\bcouncil\b", "mentions the council"),
    (r"\breviewers?\b", "addresses reviewers"),
    (r"\b(per|after|addressed?|addressing) (the )?(review|feedback)\b", "refers to review feedback"),
]

NUMBER = re.compile(r"(?<![\w.])(\d{1,3})(?!\w|\.\d)")


# ---------------------------------------------------------------- helpers

def fail(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def seal(n: int) -> str:
    return base64.b64encode(f"{SEAL_PREFIX}{n}".encode()).decode()


def unseal(s: str) -> int:
    raw = base64.b64decode(s.encode()).decode()
    if not raw.startswith(SEAL_PREFIX):
        fail("config.json: the sealed threshold is corrupt")
    return int(raw[len(SEAL_PREFIX):])


def workspace(a) -> Path:
    return Path(a.workspace).expanduser().resolve()


def load_config(ws: Path) -> dict:
    p = ws / "config.json"
    if not p.is_file():
        fail(f"no config.json in {ws}; run init first")
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def threshold_note(cfg: dict) -> str:
    if cfg["mode"] == "range":
        lo, hi = cfg["range"]
        return f"Threshold sealed (somewhere in {lo}-{hi})."
    t = unseal(cfg["sealed"])
    return f"Threshold {t}; a score below {t - cfg['veto_gap']} is a veto."


def expand(paths: list[str], md_only_dirs: bool = False) -> list[str]:
    """Expand globs (PowerShell passes them through literally) and folders."""
    out: list[str] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir() and md_only_dirs:
            out += sorted(str(x) for x in p.glob("*.md"))
        elif any(c in raw for c in "*?[") and not p.exists():
            matches = sorted(glob.glob(str(p)))
            if not matches:
                fail(f"no files match {raw}")
            out += matches
        else:
            out.append(str(p))
    return out


def round_dirs(ws: Path) -> list[tuple[int, Path]]:
    found = []
    for d in ws.glob("round-*"):
        m = re.fullmatch(r"round-(\d+)", d.name)
        if m and d.is_dir():
            found.append((int(m.group(1)), d))
    return sorted(found)


def fmt(x) -> str:
    return f"{x:g}" if isinstance(x, float) else str(x)


def read_score(path: Path) -> tuple[dict | None, str | None]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return None, f"{path.name}: not valid JSON ({e})"
    score = d.get("score")
    if isinstance(score, str) and re.fullmatch(r"\s*\d{1,3}(\.0+)?\s*", score):
        score = float(score)  # members sometimes quote the number
    if isinstance(score, float) and score.is_integer():
        score = int(score)
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 100:
        return None, f"{path.name}: score must be a whole number from 1 to 100, got {score!r}"
    conf = str(d.get("confidence", "medium")).lower()
    if conf not in CONFIDENCE:
        return None, f"{path.name}: confidence must be low, medium or high, got {conf!r}"
    return {
        "seat": d.get("seat") or path.stem,
        "score": score,
        "confidence": conf,
        "source": d.get("source", "agent"),
        "model": d.get("model", ""),
    }, None


# ---------------------------------------------------------------- tracking

def copy_into(art: Path, path: str, index: int) -> dict:
    src = Path(path)
    entry = {"path": path, "exists": src.is_file(), "copy": None}
    if src.is_file():
        name = f"{index:03d}_{src.name}"
        shutil.copy2(src, art / name)
        entry["copy"] = name
    return entry


def folder_listing(files: list[str]) -> dict[str, list[str]]:
    """Names in each tracked file's folder, to spot clutter members leave behind."""
    listing = {}
    for f in files:
        d = Path(f).parent
        if str(d) not in listing and d.is_dir():
            listing[str(d)] = sorted(x.name for x in d.iterdir())
    return listing


def add_tracked(ws: Path, cfg: dict, files: list[str]) -> list[str]:
    added = []
    for f in expand(files):
        p = Path(f).resolve()
        if p.is_dir():
            fail(f"track files, not folders: {p}")
        if str(p) not in cfg["files"]:
            cfg["files"].append(str(p))
            added.append(str(p))
    save_json(ws / "config.json", cfg)
    # A newly tracked file hasn't been touched during the run, so its current
    # state is what every earlier snapshot would have held. Backfill it so a
    # restore to any earlier round also covers it.
    for _, rd in round_dirs(ws):
        man_path = rd / "artifact" / "manifest.json"
        if not man_path.is_file():
            continue
        man = json.loads(man_path.read_text(encoding="utf-8"))
        known = {e["path"] for e in man}
        for p in added:
            if p not in known:
                man.append(copy_into(rd / "artifact", p, len(man)))
        save_json(man_path, man)
    return added


# ---------------------------------------------------------------- commands

def cmd_init(a) -> None:
    ws = workspace(a)
    if (ws / "config.json").exists() and not a.force:
        fail(f"{ws / 'config.json'} already exists; pass --force to start over")
    if a.range is not None:
        nums = [int(x) for x in re.findall(r"\d+", a.range)]
        if len(nums) != 2 or not 1 <= nums[0] < nums[1] <= 100:
            fail("--range must be two numbers in 1-100, low first, e.g. 80-90")
        lo, hi = nums
        t = lo + secrets.randbelow(hi - lo + 1)
        mode, rng = "range", [lo, hi]
    else:
        t, mode, rng = a.threshold, "fixed", None
        if not 1 <= t <= 100:
            fail("--threshold must be from 1 to 100")
    if a.max_rounds < 1:
        fail("--max-rounds must be at least 1")
    ws.mkdir(parents=True, exist_ok=True)
    if round_dirs(ws):
        print("warning: this folder already has round folders; their tallies will mix with the new run")
    cfg = {
        "target": a.target,
        "mode": mode,
        "range": rng,
        "sealed": seal(t),
        "veto_gap": a.veto_gap,
        "max_rounds": a.max_rounds,
        "noise": a.noise,
        "overfit_gap": a.overfit_gap,
        "human": a.human,
        "files": [],
    }
    save_json(ws / "config.json", cfg)
    if a.files:
        add_tracked(ws, cfg, a.files)
    print(f"Council run ready: {ws}")
    print(f"  {threshold_note(cfg)} Up to {a.max_rounds} rounds; noise band {a.noise}; "
          f"human seat: {a.human}.")
    print(f"  Tracking {len(cfg['files'])} file(s).")


def cmd_track(a) -> None:
    ws = workspace(a)
    cfg = load_config(ws)
    added = add_tracked(ws, cfg, a.files)
    if added:
        print(f"Now tracking {len(added)} more file(s):")
        for p in added:
            print(f"  {p}{'' if Path(p).exists() else '  (does not exist yet)'}")
    else:
        print("Those files were already tracked.")


def cmd_snapshot(a) -> None:
    ws = workspace(a)
    cfg = load_config(ws)
    if not cfg["files"]:
        fail("nothing is tracked yet; add files with init --files or track")
    art = ws / f"round-{a.round}" / "artifact"
    if art.exists():
        shutil.rmtree(art)
    art.mkdir(parents=True)
    man = [copy_into(art, p, i) for i, p in enumerate(cfg["files"])]
    save_json(art / "manifest.json", man)
    save_json(art / "folders.json", folder_listing(cfg["files"]))
    present = [e for e in man if e["exists"]]
    print(f"Round {a.round}: saved {len(present)} file(s) to {art}")
    for e in man:
        if not e["exists"]:
            print(f"  not present yet: {e['path']}")


def cmd_restore(a) -> None:
    ws = workspace(a)
    load_config(ws)
    art = ws / f"round-{a.round}" / "artifact"
    man_path = art / "manifest.json"
    if not man_path.is_file():
        fail(f"no snapshot for round {a.round}")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    restored, created_later = 0, []
    for e in man:
        dst = Path(e["path"])
        if e["exists"]:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(art / e["copy"], dst)
            restored += 1
        elif dst.exists():
            created_later.append(e["path"])
    print(f"Restored {restored} file(s) to their round {a.round} state.")
    if created_later:
        print(f"These didn't exist in round {a.round} and were left in place. Delete them if they "
              "only belonged to the fixes you're undoing:")
        for p in created_later:
            print(f"  {p}")


def cmd_verify(a) -> None:
    ws = workspace(a)
    load_config(ws)
    art = ws / f"round-{a.round}" / "artifact"
    man_path = art / "manifest.json"
    if not man_path.is_file():
        fail(f"no snapshot for round {a.round}")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    changed = []
    for e in man:
        dst = Path(e["path"])
        if e["exists"]:
            if not dst.is_file():
                changed.append(f"deleted: {dst}")
            elif dst.read_bytes() != (art / e["copy"]).read_bytes():
                changed.append(f"changed: {dst}")
        elif dst.exists():
            changed.append(f"created: {dst}")
    clutter = []
    fp = art / "folders.json"
    if fp.is_file():
        for folder, names in json.loads(fp.read_text(encoding="utf-8")).items():
            d = Path(folder)
            if d.is_dir():
                tracked = {Path(e["path"]).name for e in man if Path(e["path"]).parent == d}
                clutter += [str(d / n) for n in sorted({x.name for x in d.iterdir()} - set(names) - tracked)]
    if not changed and not clutter:
        print(f"Unchanged: the work matches the round {a.round} snapshot.")
        return
    if changed:
        print(f"The work no longer matches the round {a.round} snapshot. If you didn't make these "
              f"changes, a member did: restore --round {a.round}")
        for c in changed:
            print(f"  {c}")
    if clutter:
        print("New items next to the work since the snapshot (often left by members running "
              "things, e.g. __pycache__). Remove them if they aren't yours:")
        for c in clutter:
            print(f"  {c}")
    raise SystemExit(1)


def scan(path: str, patterns, skip_blocks: bool, numbers: tuple[int, int] | None,
         number_why: str) -> list[tuple[int, str, str]]:
    hits = []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    closing, opened_at = None, 0
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if closing:
            if s == closing:
                closing = None
            continue
        if skip_blocks:
            for start, end in (ARTIFACT_MARKERS, SCALE_MARKERS):
                if s == start:
                    closing, opened_at = end, i
            if closing:
                continue
        for pat, why in patterns:
            if re.search(pat, line, re.IGNORECASE):
                hits.append((i, why, s))
        if numbers:
            lo, hi = numbers
            if any(lo <= int(m.group(1)) <= hi for m in NUMBER.finditer(line)):
                hits.append((i, number_why, s))
    if closing:
        hits.append((opened_at, f"this block never closes with '{closing}', so everything "
                                "after it went unchecked", ""))
    return hits


def cmd_lint(a) -> None:
    ws = workspace(a)
    cfg = load_config(ws)
    t = unseal(cfg["sealed"])
    if cfg["mode"] == "range":
        numbers, number_why = tuple(cfg["range"]), "contains a number inside the sealed range"
    else:
        numbers, number_why = (t, t), "contains the threshold number"
    briefs = expand(a.briefs, md_only_dirs=True) if a.briefs else []
    work = expand(a.work) if a.work else []
    if not briefs and not work:
        fail("give brief files to check, and/or --work <files>")
    # The run folder holds scores and earlier briefs; a brief must never point there.
    # Match it as given as well as resolved: on Windows, resolve() expands 8.3 short
    # names (C:\Users\RUNNER~1), so a brief using the short form would slip past.
    given = Path(a.workspace).expanduser().absolute()
    run_paths = {str(given), given.as_posix(), str(ws), ws.as_posix(), "council-runs"}
    brief_leaks = BRIEF_LEAKS + [
        ("(?:" + "|".join(re.escape(p) for p in run_paths) + ")", "points into the run folder")]
    total = 0
    for f in briefs:
        for line, why, text in scan(f, brief_leaks, True, numbers, number_why):
            total += 1
            print(f"{f}:{line}: {why}")
            if text:
                print(f"    {text[:160]}")
    for f in work:
        if not Path(f).is_file():
            continue
        for line, why, text in scan(f, WORK_TRACES, False, None, ""):
            total += 1
            print(f"{f}:{line}: {why} (trace in the work)")
            print(f"    {text[:160]}")
    checked = len(briefs) + len(work)
    if total:
        print(f"{total} possible leak(s) in {checked} file(s). Rewrite the real ones; false alarms "
              "(such as a line number that happens to match) can stay.")
        raise SystemExit(1)
    print(f"Clean: no leaks found in {checked} file(s).")


def rebuild_ledger(ws: Path, cfg: dict) -> None:
    tallies = []
    for _, rd in round_dirs(ws):
        p = rd / "tally.json"
        if p.is_file():
            tallies.append(json.loads(p.read_text(encoding="utf-8")))
    seats: list[str] = []
    for t in tallies:
        for r in t["scores"]:
            if r["seat"] not in seats:
                seats.append(r["seat"])
    lines = ["# Council ledger", ""]
    if cfg.get("target"):
        lines.append(f"Target: {cfg['target']}  ")
    lines += [threshold_note(cfg), "",
              "| Round | " + " | ".join(seats) + " | Median | Vetoes | Decision |",
              "|" + "---|" * (len(seats) + 4)]
    any_low = False
    for t in tallies:
        by_seat = {r["seat"]: r for r in t["scores"]}
        cells = []
        for s in seats:
            r = by_seat.get(s)
            if r is None:
                cells.append("-")
            else:
                low = r["confidence"] == "low"
                any_low |= low
                cells.append(f"{r['score']}{'*' if low else ''}")
        lines.append(f"| {t['round']} | " + " | ".join(cells)
                     + f" | {fmt(t['median'])} | {', '.join(t['vetoes']) or '-'} | {t['decision']} |")
    if any_low:
        lines += ["", "\\* low confidence"]
    h = ws / "holdout" / "result.json"
    if h.is_file():
        r = json.loads(h.read_text(encoding="utf-8"))
        verdict = "possible overfitting" if r["overfit_warning"] else "consistent"
        lines += ["", f"Holdout: {r['holdout_score']} vs. council median {fmt(r['council_median'])} "
                      f"in round {r['round']} ({verdict})."]
    (ws / "ledger.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_tally(a) -> None:
    ws = workspace(a)
    cfg = load_config(ws)
    n = a.round
    sd = ws / f"round-{n}" / "scores"
    if not sd.is_dir():
        fail(f"no scores folder at {sd}")
    rows, problems = [], []
    for f in sorted(sd.glob("*.json")):
        row, problem = read_score(f)
        if problem:
            problems.append(problem)
        else:
            rows.append(row)
    if problems:
        print("Fix these score files, then tally again:")
        for p in problems:
            print(f"  {p}")
        raise SystemExit(2)
    if not rows:
        fail(f"no score files in {sd}")

    t = unseal(cfg["sealed"])
    floor = t - cfg["veto_gap"]
    noise = cfg["noise"]
    vals = [r["score"] for r in rows]
    med = statistics.median(vals)
    vetoes = [r["seat"] for r in rows if r["score"] < floor and r["confidence"] != "low"]
    soft = [r["seat"] for r in rows if r["score"] < floor and r["confidence"] == "low"]
    passed = med >= t and not vetoes

    meds, nveto = {}, {}
    for r, rd in round_dirs(ws):
        p = rd / "tally.json"
        if r < n and p.is_file():
            h = json.loads(p.read_text(encoding="utf-8"))
            meds[r], nveto[r] = h["median"], len(h["vetoes"])
    meds[n], nveto[n] = med, len(vetoes)

    def rank(r):
        return (meds[r], -nveto[r], r)

    best = max(meds, key=rank)
    earlier = [r for r in meds if r < n]
    regression = None
    if earlier:
        best_before = max(earlier, key=rank)
        if med < meds[best_before] - noise:
            regression = best_before
    plateau = False
    if n >= 3:
        early = [meds[r] for r in meds if r <= n - 2]
        if early and max(meds.values()) - max(early) < noise:
            plateau = True

    if passed:
        decision = "PASS"
    elif n >= cfg["max_rounds"]:
        decision = "STOP_CAP"
    elif plateau:
        decision = "STOP_PLATEAU"
    else:
        decision = "CONTINUE"

    reasons = []
    if med < t:
        reasons.append("median below threshold")
    if vetoes:
        reasons.append(f"veto from {', '.join(vetoes)}")

    save_json(ws / f"round-{n}" / "tally.json", {
        "round": n,
        "median": med,
        "mean": round(statistics.mean(vals), 1),
        "min": min(vals),
        "max": max(vals),
        "spread": max(vals) - min(vals),
        "scores": rows,
        "vetoes": vetoes,
        "low_confidence_vetoes": soft,
        "passed": passed,
        "decision": decision,
        "best_round": best,
        "best_median": meds[best],
        "regression_vs_round": regression,
        "plateau": plateau,
    })
    rebuild_ledger(ws, cfg)

    width = max(len(r["seat"]) for r in rows)
    print(f"Round {n}: {len(rows)} score(s), median {fmt(med)} "
          f"(mean {statistics.mean(vals):.1f}, min {min(vals)}, max {max(vals)})")
    for r in rows:
        flag = "  VETO" if r["seat"] in vetoes else ("  below veto line, low confidence" if r["seat"] in soft else "")
        print(f"  {r['seat']:<{width}}  {r['score']:>3}  {r['confidence']:<6}{flag}")
    print(threshold_note(cfg))
    print(f"Decision: {decision}" + (f" ({'; '.join(reasons)})" if reasons and not passed else ""))
    if decision == "PASS":
        print(f"Keep the round {n} version.")
    elif decision in ("STOP_CAP", "STOP_PLATEAU"):
        why = "round limit reached" if decision == "STOP_CAP" else \
              f"best median hasn't risen more than {noise} in two rounds"
        keep = f"round {best} (median {fmt(meds[best])})"
        print(f"Stop: {why}. Keep {keep}"
              + ("." if best == n else f"; restore it with: restore --round {best}"))
    else:
        print(f"Best version so far: round {best} (median {fmt(meds[best])}).")
    if regression is not None and decision == "CONTINUE":
        print(f"REGRESSION: the median fell more than {noise} below round {regression} "
              f"({fmt(meds[regression])}). Restore round {regression} before fixing: "
              f"restore --round {regression}")
    if max(vals) - min(vals) >= WIDE_SPREAD:
        print(f"Wide spread ({max(vals) - min(vals)} points): members disagree. Read the lowest "
              "and highest seats side by side before fixing.")


def cmd_holdout(a) -> None:
    ws = workspace(a)
    cfg = load_config(ws)
    t = unseal(cfg["sealed"])
    sp = ws / "holdout" / "score.json"
    if not sp.is_file():
        fail(f"no holdout score at {sp}")
    row, problem = read_score(sp)
    if problem:
        fail(problem)
    tp = ws / f"round-{a.round}" / "tally.json"
    if not tp.is_file():
        fail(f"round {a.round} has no tally yet")
    cmed = json.loads(tp.read_text(encoding="utf-8"))["median"]
    hs = row["score"]
    gap = cmed - hs
    overfit = gap > cfg["overfit_gap"]
    save_json(ws / "holdout" / "result.json", {
        "round": a.round,
        "holdout_score": hs,
        "holdout_confidence": row["confidence"],
        "council_median": cmed,
        "gap": gap,
        "holdout_passed": hs >= t,
        "overfit_warning": overfit,
    })
    rebuild_ledger(ws, cfg)
    print(f"Holdout: {hs} ({row['confidence']} confidence) vs. council median {fmt(cmed)} "
          f"in round {a.round}; gap {fmt(gap)}.")
    print("The holdout's score " + ("passes" if hs >= t else "does not pass") + " the threshold.")
    if overfit:
        print(f"OVERFIT WARNING: the holdout scored {fmt(gap)} points under the council (allowed "
              f"gap {cfg['overfit_gap']}). The work may have been tuned to these reviewers rather "
              "than improved.")
    else:
        print("Consistent with the council.")


def cmd_reveal(a) -> None:
    cfg = load_config(workspace(a))
    t = unseal(cfg["sealed"])
    if cfg["mode"] == "range":
        lo, hi = cfg["range"]
        print(f"The sealed threshold was {t} (drawn from {lo}-{hi}).")
    else:
        print(f"Threshold: {t}")


# ---------------------------------------------------------------- CLI

def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="council.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name, func, help_):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.add_argument("--workspace", "-w", required=True, help="the run folder")
        sp.set_defaults(func=func)
        return sp

    s = cmd("init", cmd_init, "Create the run folder and config, sealing the threshold.")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--threshold", type=int, help="the pass score, 1-100")
    g.add_argument("--range", help="pick a secret threshold in this range, e.g. 80-90")
    s.add_argument("--target", default="", help="one-line description of the work")
    s.add_argument("--files", nargs="*", default=[], help="files under review")
    s.add_argument("--max-rounds", type=int, default=5)
    s.add_argument("--veto-gap", type=int, default=20,
                   help="a member this far below the threshold blocks a pass (default 20)")
    s.add_argument("--noise", type=int, default=3,
                   help="median changes this small count as no change (default 3)")
    s.add_argument("--overfit-gap", type=int, default=10,
                   help="holdout this far under the council triggers a warning (default 10)")
    s.add_argument("--human", choices=["none", "open", "blind"], default="none",
                   help="the user's seat: none, open or blind")
    s.add_argument("--force", action="store_true", help="overwrite an existing config")

    s = cmd("track", cmd_track, "Track more files. Run it before editing or creating them.")
    s.add_argument("files", nargs="+")

    s = cmd("snapshot", cmd_snapshot, "Copy the tracked work into round-N/artifact.")
    s.add_argument("--round", "-r", type=int, required=True)

    s = cmd("restore", cmd_restore, "Copy a round's snapshot back over the working files.")
    s.add_argument("--round", "-r", type=int, required=True)

    s = cmd("verify", cmd_verify,
            "Check the work still matches round N's snapshot (run it after members finish).")
    s.add_argument("--round", "-r", type=int, required=True)

    s = cmd("lint", cmd_lint, "Check briefs for leaks, and work files for traces of the review.")
    s.add_argument("briefs", nargs="*", help="brief files or a folder of them")
    s.add_argument("--work", nargs="+", help="work files to check for traces of the review")

    s = cmd("tally", cmd_tally, "Tally round N's score files and decide what happens next.")
    s.add_argument("--round", "-r", type=int, required=True)

    s = cmd("holdout", cmd_holdout, "Compare holdout/score.json with the council's round median.")
    s.add_argument("--round", "-r", type=int, required=True,
                   help="the round whose version the holdout judged")

    cmd("reveal", cmd_reveal, "Print the threshold.")

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
