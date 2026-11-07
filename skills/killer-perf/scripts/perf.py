#!/usr/bin/env python3
"""Measurement and bookkeeping for a killer-perf run.

Commands:
  init           create the run folder: what should get faster, the base commit, the goal
  tests          set the test command and run it once on the unchanged project
  bench add      register a benchmark and try it twice in a fresh copy
  bench noise    measure the unchanged project against itself: the noise band
  profile        run a benchmark once under Python's or Node.js's profiler
  sandbox        make an isolated copy of the project for one agent
  collect        add the hypotheses from a finder's reply
  hypothesis     show one hypothesis, or set its status
  candidate add  save a change from an optimiser's sandbox or a patch file
  candidate show print a candidate's claim and diff, for a challenger's brief
  candidate set  record the user's answer about a candidate
  compare        measure a candidate against the unchanged project, behind the correctness gate
  challenge      record a challenger's reply, rerunning its reproduction with and without the change
  combine        apply several candidates together and measure the combination
  land           commit accepted candidates on a new local branch, then measure the branch
  list           benchmarks, hypotheses and candidates so far
  status         has the real project's working tree changed since init?
  decide         record a call the user should check
  wait           record an outward action prepared but not done
  report         write report.md and summary.json
  clean          delete this run's sandboxes

A speedup counts only when compare says so: base and candidate run in two fresh copies,
interleaved A B B A A B ..., and the candidate is `faster` when the whole 95% bootstrap
confidence interval of the ratio of medians clears the benchmark's threshold
(max(2%, 1.5 x the noise band)) and a Mann-Whitney test gives p < 0.01. Before any timing,
the project's tests must pass with the change, and the benchmark's output must not change.

Runs live in ~/.claude/killer-perf/<project>/<date>-<slug>/; sandboxes in the temp folder.
KILLER_PERF_HOME and KILLER_PERF_SANDBOXES move them. Only one measurement runs on the
machine at a time. Standard library only. Run `perf.py <command> --help` for options.
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import importlib.util
import json
import math
import os
import platform
import random
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("killer_kit", HERE / "kit.py")
kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kit)

SKILL = "perf"
STATE = "perf.json"
SEED = 20240611                 # the bootstrap's seed: the same samples always give the same interval
RESAMPLES = 2000
ALPHA = 0.01                    # the p-value a verdict needs
MIN_THRESHOLD = 0.02            # no change smaller than 2% counts, however quiet the machine
NOISE_FACTOR = 1.5              # threshold = max(2%, 1.5 x the A/A noise band)
NOISE_LIMIT = 0.10              # an A/A band wider than 10% means the numbers can't be trusted
MIN_REPEAT = 6                  # with 5 runs each, even complete separation gives p = 0.012
RIGOR = {"standard": 15, "thorough": 40, "quick": 7}
FAST = 0.05                     # seconds: below this, process start-up dominates
SLOW = 120                      # seconds: above this, cut the runs and warn about the length
LOCK_STALE = 90                 # seconds without a heartbeat before a timing lock counts as left behind
LOCK_WAIT = 4 * 3600
TIME_UNITS = {"ms", "msec", "s", "sec", "secs", "seconds", "us", "µs", "μs", "ns", "min"}

METRIC_RE = re.compile(r"^[ \t]*METRIC:[ \t]*([A-Za-z_][\w.:-]*)[ \t]*=[ \t]*"
                       r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)(?:[ \t]+(\S+))?[ \t]*$", re.M)
METRIC_LINE = re.compile(r"^[ \t]*METRIC:.*(?:\n|$)", re.M)
TIMING_RE = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?[ \t]?"
                       r"(?:ms|msec|seconds|secs|sec|s|µs|μs|us|ns)(?!\w)")
ATTRIBUTION = re.compile(r"(?i)co-authored-by|generated (?:with|by) (?:claude|ai\b|an ai|chatgpt|copilot|a model)"
                         r"|\bclaude\b|\banthropic\b|\bai (?:agent|assistant)\b|\boptimi[sz]er agent\b")
DEFAULT_PROTECT = [
    # tests, benchmarks and their inputs: a candidate that edits them could make itself look good
    "tests/", "test/", "spec/", "__tests__/", "testdata/", "fixtures/", "__snapshots__/",
    "test_*", "*_test.*", "*.test.*", "*.spec.*", "conftest.py",
    "bench/", "benches/", "benchmark/", "benchmarks/",
    # dependencies: no new ones
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock",
    "requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "Pipfile.lock",
    "poetry.lock", "uv.lock", "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "Gemfile", "Gemfile.lock",
    "*.csproj", "pom.xml", "build.gradle*",
    # CI
    ".github/",
]
REJECTED = "rejected"


# ---------------------------------------------------------------- statistics (standard library only)

def median(xs) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        raise ValueError("median of nothing")
    m = n // 2
    return float(s[m]) if n % 2 else (s[m - 1] + s[m]) / 2


def bootstrap_ratio_ci(base, cand, resamples: int = RESAMPLES, seed: int = SEED, conf: float = 0.95):
    """A percentile bootstrap interval for median(cand) / median(base), seeded so it repeats."""
    rng = random.Random(seed)
    n, m = len(base), len(cand)
    ratios = []
    for _ in range(resamples):
        mb = median(rng.choices(base, k=n))
        mc = median(rng.choices(cand, k=m))
        ratios.append(mc / mb if mb else math.inf)
    ratios.sort()
    lo = ratios[round((1 - conf) / 2 * (resamples - 1))]
    hi = ratios[round((1 + conf) / 2 * (resamples - 1))]
    return lo, hi


def mann_whitney(a, b):
    """Mann-Whitney U for a against b, and its two-sided p-value.

    Normal approximation, with the tie correction to the variance and a continuity correction.
    Returns (U of a, p)."""
    n1, n2 = len(a), len(b)
    if not n1 or not n2:
        raise ValueError("Mann-Whitney needs both samples")
    pooled = sorted([(float(v), 0) for v in a] + [(float(v), 1) for v in b], key=lambda t: t[0])
    total = n1 + n2
    r1 = ties = 0.0
    i = 0
    while i < total:
        j = i
        while j + 1 < total and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        rank = (i + j) / 2 + 1          # the average of ranks i+1 .. j+1
        t = j - i + 1
        if t > 1:
            ties += t ** 3 - t
        r1 += rank * sum(1 for k in range(i, j + 1) if pooled[k][1] == 0)
        i = j + 1
    u1 = r1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    var = n1 * n2 / 12 * ((total + 1) - ties / (total * (total - 1)))
    if var <= 0:
        return u1, 1.0
    z = max(0.0, abs(u1 - mu) - 0.5) / math.sqrt(var)
    return u1, min(1.0, math.erfc(z / math.sqrt(2)))


def schedule(rounds: int) -> list[str]:
    """ABBA order: A B, B A, A B, B A, ... so a drift in the machine's speed hits both sides alike."""
    out: list[str] = []
    for i in range(rounds):
        out += ["A", "B"] if i % 2 == 0 else ["B", "A"]
    return out


def decide_verdict(lo: float, hi: float, p: float, threshold: float, higher_better: bool) -> str:
    """faster: the whole interval is past the threshold in the good direction, and p < 0.01."""
    if p >= ALPHA:
        return "no clear change"
    good_low, good_high = hi < 1 - threshold, lo > 1 + threshold
    if (good_low and not higher_better) or (good_high and higher_better):
        return "faster"
    if (good_high and not higher_better) or (good_low and higher_better):
        return "slower"
    return "no clear change"


def plan_runs(secs: float, wall: bool, inner: int | None, repeat: int | None, default_repeat: int) -> tuple[int, int]:
    """(inner, repeat) for a command whose run takes secs: loop the very fast, cut the very slow."""
    if inner is None:
        inner = min(100, max(2, math.ceil(0.25 / max(secs, 0.002)))) if wall and secs < FAST else 1
    if repeat is None:
        repeat = MIN_REPEAT if secs * inner > SLOW else default_repeat
    return inner, repeat


def noise_band(lo: float, hi: float) -> float:
    """How far the A/A ratio's interval reaches from 1, either way."""
    return max(1 - lo, hi - 1, 0.0)


# ---------------------------------------------------------------- output check

def normalise(text: str, extra=()) -> str:
    """Stdout as the output check sees it: no METRIC lines, no timings, masks applied, CRLF folded."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = METRIC_LINE.sub("", text)
    for rx in extra or ():
        text = re.sub(rx, "<masked>", text)
    text = TIMING_RE.sub("<time>", text)
    return "\n".join(ln.rstrip() for ln in text.split("\n")).strip("\n")


def first_difference(a: str, b: str) -> str:
    la, lb = a.split("\n"), b.split("\n")
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else None
        y = lb[i] if i < len(lb) else None
        if x != y:
            show = lambda s: "(no line)" if s is None else repr(s[:160])
            return f"line {i + 1} was {show(x)}, now {show(y)}"
    return "the outputs differ"


def parse_metrics(text: str) -> list[tuple[str, float, str | None]]:
    return [(m.group(1), float(m.group(2)), m.group(3)) for m in METRIC_RE.finditer(text.replace("\r\n", "\n"))]


def metric_value(text: str, name: str) -> float | None:
    found = [v for n, v, _ in parse_metrics(text) if n == name]
    return found[-1] if found else None


# ---------------------------------------------------------------- formatting

def fmt_secs(s: float) -> str:
    if s < 1e-3:
        return f"{s * 1e6:.3g} µs"
    if s < 1:
        return f"{s * 1e3:.3g} ms"
    if s < 600:
        return f"{s:.3g} s"
    return f"{s / 60:.3g} min"


def fmt_value(v: float, b: dict) -> str:
    if b["metric"] == "wall":
        return fmt_secs(v)
    return f"{v:.4g}" + (f" {b['unit']}" if b.get("unit") else "")


def pct_num(x: float) -> str:
    v = abs(x) * 100
    return f"{v:.0f}" if v >= 10 else f"{v:.1f}"


def is_time(b: dict) -> bool:
    return b["metric"] == "wall" or (b.get("unit") or "").lower() in TIME_UNITS


def words(ratio: float, b: dict) -> str:
    if abs(ratio - 1) < 5e-4:
        return "no change"
    up = ratio > 1
    if is_time(b) and not b["higher_better"]:
        word = "slower" if up else "faster"
    else:
        word = "higher" if up else "lower"
    return f"{pct_num(ratio - 1)}% {word}"


def ci_text(lo: float, hi: float, b: dict) -> str:
    if hi < 1 or lo > 1:
        x, y = sorted([abs(lo - 1), abs(hi - 1)])
        return f"95% CI {pct_num(x)}–{pct_num(y)}%"
    return f"95% CI {words(lo, b)} to {words(hi, b)}"


def fmt_p(p: float) -> str:
    if p < 0.001:
        return "p < 0.001"
    return f"p = {p:.3f}" if p < 0.01 else f"p = {p:.2f}"


def result_line(name: str, b: dict, r: dict) -> str:
    """The sentence a lead pastes: medians, the change, its interval, p, runs and noise."""
    v = r["verdict"]
    if v.startswith(REJECTED):
        detail = (r.get("detail") or "").strip().splitlines()
        return f"{name}: {v}" + (f": {detail[0]}" if detail else "") + "."
    line = f"{name}: {fmt_value(r['base']['median'], b)} → {fmt_value(r['cand']['median'], b)}, "
    lo, hi = r["ci"]
    if v in ("faster", "slower"):
        line += f"{words(r['ratio'], b)} ({ci_text(lo, hi, b)})"
    elif v == "same code":
        line += f"ratio {r['ratio']:.3f} ({ci_text(lo, hi, b)})"
    else:
        line += f"no clear change ({words(r['ratio'], b)}; {ci_text(lo, hi, b)})"
    line += f", {fmt_p(r['p'])}, {r['n'][0]} runs each"
    if r.get("inner", 1) > 1:
        line += f" (each the mean of {r['inner']} back-to-back runs)"
    if r.get("noise") is not None:
        line += f"; noise band ±{pct_num(r['noise'])}%"
        if v == "no clear change":
            line += f", so a change had to beat {pct_num(r['threshold'])}%"
    return line + "."


def tail(text: str, n: int = 25) -> str:
    return "\n".join(text.rstrip().splitlines()[-n:])


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


# ---------------------------------------------------------------- run state

def new_state() -> dict:
    return {"benchmarks": {}, "tests": None, "hypotheses": [], "candidates": [], "boxes": {},
            "decisions": [], "waiting": [], "landed": [], "next": {"H": 1, "C": 1, "K": 1}}


def load_state(ws: Path) -> dict:
    return kit.load_json(ws / STATE, None) or new_state()


def mutate(ws: Path, fn):
    """Read perf.json, change it with fn, write it back, under the run's lock."""
    with kit.locked(ws):
        st = load_state(ws)
        out = fn(st)
        kit.save_json(ws / STATE, st)
    return out


def load(a) -> tuple[Path, dict, dict]:
    ws, cfg = kit.load_run(a.workspace)
    return ws, cfg, load_state(ws)


def get_cand(st: dict, cid: str) -> dict:
    for c in st["candidates"]:
        if c["id"].lower() == cid.lower():
            return c
    kit.fail(f"no candidate {cid}")


def get_hyp(st: dict, hid: str) -> dict:
    for h in st["hypotheses"]:
        if h["id"].lower() == hid.lower():
            return h
    kit.fail(f"no hypothesis {hid}")


def get_bench(st: dict, name: str) -> dict:
    b = st["benchmarks"].get(name)
    if b is None:
        have = ", ".join(st["benchmarks"]) or "none yet"
        kit.fail(f"no benchmark {name} (have: {have})")
    return b


def parse_cmd(s: str) -> list[str]:
    """A command given as one string: a JSON list, or split shell-style (use forward slashes)."""
    s = (s or "").strip()
    if s.startswith("["):
        try:
            v = json.loads(s)
        except json.JSONDecodeError as e:
            kit.fail(f"{s!r} isn't a JSON list: {e}")
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            kit.fail(f"{s!r}: a command is a list of strings")
        return v
    v = shlex.split(s)
    if not v:
        kit.fail("an empty command")
    return v


def short(rev: str | None) -> str:
    return rev[:10] if rev else "the folder"


def check_name(name: str, what: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name or ""):
        kit.fail(f"a {what} name is letters, digits, dots, dashes and underscores")


def show_cmd(cmd: list[str]) -> str:
    return " ".join(shlex.quote(x) if re.search(r"[\s\"']", x) else x for x in cmd)


def project_status(cfg: dict) -> str | None:
    return kit.git_status(Path(cfg["repo"])) if cfg["git"] else None


def warn_if_project_changed(cfg: dict) -> None:
    if cfg["git"] and project_status(cfg) != cfg.get("status_at_init"):
        print("warning: the real project's working tree has changed since init. Run perf.py status to see how; "
              "if nobody meant to change it, undo it.")


# ---------------------------------------------------------------- protected files and attribution

def protected_hit(path: str, patterns) -> bool:
    path = path.replace("\\", "/")
    parts = path.split("/")
    for p in patterns or []:
        p = p.replace("\\", "/")
        if p.endswith("/"):
            d = p.rstrip("/")
            if "/" in d:
                if path.startswith(d + "/"):
                    return True
            elif any(fnmatch.fnmatch(x, d) for x in parts[:-1]):
                return True
        elif "/" in p:
            if fnmatch.fnmatch(path, p):
                return True
        elif fnmatch.fnmatch(parts[-1], p):
            return True
    return False


def protected_files(patch: str, patterns) -> list[str]:
    return [f for f in kit.changed_files(patch) if protected_hit(f, patterns)]


def attribution_hits(text: str) -> list[str]:
    return sorted({m.group(0) for m in ATTRIBUTION.finditer(text or "")})


def line_ending_churn(patch: str) -> list[str]:
    """Files whose diff is mostly the same lines with different line endings."""
    out = []
    for chunk in re.split(r"(?m)^diff --git ", patch)[1:]:
        name = chunk.split("\n", 1)[0].rsplit(" b/", 1)[-1].strip()
        lines = chunk.split("\n")
        minus = [ln[1:] for ln in lines if ln.startswith("-") and not ln.startswith("---")]
        plus = [ln[1:] for ln in lines if ln.startswith("+") and not ln.startswith("+++")]
        plus_set = set(plus)
        flips = sum(1 for ln in minus if (ln[:-1] if ln.endswith("\r") else ln + "\r") in plus_set)
        if flips >= 10 and flips * 2 >= len(minus):
            out.append(name)
    return out


def added_lines(patch: str) -> str:
    return "\n".join(ln[1:] for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++"))


# ---------------------------------------------------------------- the machine

def on_battery() -> bool | None:
    """True on battery, False on mains or with no battery, None when it can't be told cheaply."""
    try:
        if os.name == "nt":
            import ctypes

            class SPS(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                            ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                            ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
            s = SPS()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
                return None
            if s.BatteryFlag == 128:
                return False
            return {0: True, 1: False}.get(s.ACLineStatus)
        if sys.platform == "darwin":
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
            return True if "Battery Power" in out else (False if "AC Power" in out else None)
        for p in sorted(Path("/sys/class/power_supply").glob("*")):
            if (p / "type").read_text().strip() == "Mains":
                return (p / "online").read_text().strip() == "0"
    except Exception:
        return None
    return None


def machine_info() -> dict:
    info = {"os": platform.platform(), "cpus": os.cpu_count(), "arch": platform.machine(),
            "python": platform.python_version(), "node": None, "on_battery": on_battery()}
    node = shutil.which("node")
    if node:
        with contextlib.suppress(Exception):
            info["node"] = subprocess.run([node, "--version"], capture_output=True, text=True,
                                          timeout=10).stdout.strip().lstrip("v") or None
    if hasattr(os, "getloadavg"):
        with contextlib.suppress(OSError):
            info["load"] = [round(x, 2) for x in os.getloadavg()]
    return info


def machine_line(m: dict) -> str:
    power = {True: "on battery", False: "on mains power", None: "power source unknown"}[m.get("on_battery")]
    bits = [m["os"], f"{m['cpus']} CPUs", f"Python {m['python']}"]
    if m.get("node"):
        bits.append(f"Node.js {m['node']}")
    bits.append(power)
    if m.get("load"):
        bits.append(f"load {m['load'][0]}")
    return ", ".join(bits)


# ---------------------------------------------------------------- one measurement at a time

@contextlib.contextmanager
def timing_lock(cfg: dict):
    """Only one measurement runs on this machine at a time; two at once would slow each other."""
    folder = Path(cfg["sandboxes_home"])
    folder.mkdir(parents=True, exist_ok=True)
    lock, start, said = folder / "timing.lock", time.monotonic(), False
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {kit.now()}".encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if not said:
                print(f"Waiting: another measurement is running on this machine ({lock}).", flush=True)
                said = True
            if time.monotonic() - start > LOCK_WAIT:
                kit.fail(f"{lock} was held for over {LOCK_WAIT // 3600} hours; delete it if nothing is measuring")
            time.sleep(1)
    stop = threading.Event()

    def beat():
        while not stop.wait(15):
            with contextlib.suppress(OSError):
                os.utime(lock)
    t = threading.Thread(target=beat, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(2)
        lock.unlink(missing_ok=True)


# ---------------------------------------------------------------- timing

def time_run(root: Path, env: dict, cmd: list[str], cwd: str | None, timeout: float) -> dict:
    """Run cmd once in the sandbox, timed with the monotonic high-resolution clock around the process."""
    run = kit.runner()
    where = root / "repo" / cwd if cwd else root / "repo"
    extra = {"start_new_session": True} if os.name != "nt" else \
            {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    argv = run.resolve(cmd, env)
    t0 = time.perf_counter()
    try:
        p = subprocess.Popen(argv, cwd=str(where), env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, **extra)
    except OSError as e:
        return {"code": 127, "out": "", "err": f"could not start {cmd[0]}: {e}", "secs": 0.0, "timed_out": False}
    try:
        out, err = p.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        run.kill_tree(p)
        try:
            out, err = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
        timed_out = True
    secs = time.perf_counter() - t0
    dec = lambda x: (x or b"").decode("utf-8", errors="replace").replace("\r\n", "\n")
    return {"code": None if timed_out else p.returncode, "out": dec(out), "err": dec(err), "secs": secs,
            "timed_out": timed_out}


def describe_failure(r: dict) -> str:
    head = f"timed out after {r['secs']:.0f} s" if r["timed_out"] else f"exit {r['code']}"
    text = (r["out"] + ("\n" + r["err"] if r["err"] else "")).strip()
    return head + (":\n" + tail(text, 15) if text else "")


def take_sample(root: Path, env: dict, b: dict) -> dict:
    """One sample: the command run `inner` times back to back; wall time or the METRIC, averaged."""
    secs, vals, first = 0.0, [], None
    for _ in range(b["inner"]):
        r = time_run(root, env, b["cmd"], b.get("cwd"), b["timeout"])
        if r["timed_out"] or r["code"] != 0:
            return {"failed": True, "detail": describe_failure(r)}
        secs += r["secs"]
        first = r["out"] if first is None else first
        if b["metric"] != "wall":
            v = metric_value(r["out"], b["metric"])
            if v is None:
                return {"failed": True, "detail": f"it printed no METRIC line for {b['metric']}"}
            vals.append(v)
    value = secs / b["inner"] if b["metric"] == "wall" else sum(vals) / len(vals)
    return {"failed": False, "value": value, "secs": secs / b["inner"], "out": first}


def prepare_box(ws: Path, cfg: dict, name: str, rev: str | None, patch: str | None, setup, cwd, timeout):
    """A fresh copy at rev, with the patch applied and the setup run. Returns (root, problem)."""
    root = kit.make_sandbox(cfg, ws, name, rev=rev)
    if patch:
        ok, msg = kit.apply_patch(root / "repo", patch)
        if not ok:
            return root, ("doesn't apply", msg)
    for step in setup or []:
        s = kit.run_in(root, step, 1, timeout, cwd, log=False)
        if kit.failed(s["runs"][0]):
            return root, ("setup failed", kit.summary(s))
    return root, None


def measure_pair(ws: Path, cfg: dict, b: dict, side_a: dict, side_b: dict, label: str,
                 threshold: float | None = None, same_code: bool = False) -> dict:
    """Run one benchmark on two fresh copies, interleaved ABBA, and work out the statistics.

    Side A is always the unchanged project. A failure on side A (or on either side when both are
    the same code) is a setup error and stops the command; a failure on side B rejects the candidate."""
    boxes: dict[str, Path] = {}
    name = b["name"]
    try:
        for key, side in (("A", side_a), ("B", side_b)):
            root, problem = prepare_box(ws, cfg, f"{label}-{key.lower()}-{secrets.token_hex(2)}", side.get("rev"),
                                        side.get("patch"), b.get("setup"), b.get("cwd"), b["timeout_setup"])
            boxes[key] = root
            if problem:
                if key == "B" and not same_code:
                    return {"verdict": f"{REJECTED}: {problem[0]}", "detail": tail(problem[1])}
                kit.fail(f"{name}: on the unchanged project, the {problem[0]}:\n{problem[1]}")
        run = kit.runner()
        envs = {k: run.build_env(r) for k, r in boxes.items()}
        order = schedule(b["warmup"] + b["repeat"])
        samples: dict[str, list[float]] = {"A": [], "B": []}
        ref = None
        est = len(order) * b["inner"] * (b.get("trial_secs") or 0)
        step = max(1, len(order) // 8) if est > 60 else 0
        for i, key in enumerate(order):
            s = take_sample(boxes[key], envs[key], b)
            if s["failed"]:
                if key == "A" or same_code:
                    kit.fail(f"{name}: the unchanged project's benchmark failed in run {i + 1}: {s['detail']}")
                return {"verdict": f"{REJECTED}: bench failed", "detail": s["detail"]}
            if b["output_check"]:
                norm = normalise(s["out"], b.get("normalise"))
                if ref is None:
                    ref = norm
                elif norm != ref:
                    diff = first_difference(ref, norm)
                    if key == "A" or same_code:
                        kit.fail(f"{name}: the unchanged project's output differs between runs ({diff}). Mask the "
                                 "part that varies with --output-normalise '<regex>' (bench add --replace), or turn "
                                 "the check off with --no-output-check and record why with decide.")
                    return {"verdict": f"{REJECTED}: output changed", "detail": diff}
            if i // 2 >= b["warmup"]:
                samples[key].append(s["value"])
            if step and (i + 1) % step == 0 and i + 1 < len(order):
                print(f"  {name}: {i + 1} of {len(order)} runs", flush=True)
    finally:
        for root in boxes.values():
            kit.drop_sandbox(ws, cfg, root.name)
    a_s, b_s = samples["A"], samples["B"]
    ma, mb = median(a_s), median(b_s)
    if ma == 0:
        kit.fail(f"{name}: the metric's median is 0 on the unchanged project, so a ratio means nothing")
    lo, hi = bootstrap_ratio_ci(a_s, b_s)
    u, p = mann_whitney(a_s, b_s)
    r = {"verdict": "same code" if same_code else None, "base": {"median": ma, "samples": a_s},
         "cand": {"median": mb, "samples": b_s}, "ratio": mb / ma, "ci": [lo, hi], "u": u, "p": p,
         "n": [len(a_s), len(b_s)], "order": "".join(order), "warmup": b["warmup"], "inner": b["inner"],
         "output": "same" if b["output_check"] else "not checked", "runs_checked": len(order),
         "seed": SEED, "resamples": RESAMPLES}
    if threshold is not None:
        r["threshold"] = threshold
        r["noise"] = (b.get("noise") or {}).get("band")
        r["verdict"] = decide_verdict(lo, hi, p, threshold, b["higher_better"])
    return r


def overall(results: dict) -> str:
    vs = [r["verdict"] for r in results.values()]
    for v in vs:
        if v.startswith(REJECTED):
            return v
    if "faster" in vs and "slower" in vs:
        return "mixed"
    if "faster" in vs:
        return "faster"
    if "slower" in vs:
        return "slower"
    return "no clear change"


def status_for(verdict: str, challenge: dict | None) -> str:
    if (challenge or {}).get("outcome") == "proven":   # a proven behaviour change outlives any remeasurement
        return f"{REJECTED}: behaviour change"
    if verdict == "faster":
        return "accepted" if (challenge or {}).get("outcome") == "stands" else "faster"
    if verdict == "mixed":
        return "mixed"
    if verdict.startswith(REJECTED):
        return verdict
    return "no gain"


def ensure_ready(st: dict, names: list[str]) -> None:
    """Refuse to measure what can't be trusted: no tests decision, or no clean noise test."""
    if not st["benchmarks"]:
        kit.fail("no benchmarks yet: perf.py bench add -w <run> <name> -- <command>")
    t = st.get("tests")
    if not t:
        kit.fail("set the test command first (perf.py tests -w <run> -- <command>), or say there are none "
                 "with tests --none: the correctness gate needs one or the other")
    for name in names:
        b = get_bench(st, name)
        n = b.get("noise")
        if not n:
            kit.fail(f"{name} has no noise test yet: perf.py bench noise -w <run> {name}")
        if n["state"] != "clean":
            kit.fail(f"{name}'s noise band is ±{pct_num(n['band'])}%, over 10%: its numbers can't be trusted. Fix the "
                     "benchmark (more runs, more warm-up, a larger input, less other work on the machine) and run "
                     "bench noise again.")


def tests_baseline(ws: Path, cfg: dict, rev: str | None) -> dict | None:
    """The test command's result on the unchanged project at rev: run once, then remembered."""
    st = load_state(ws)
    t = st.get("tests")
    if not t or t.get("none"):
        return None
    key = rev or "folder"
    known = (t.get("baselines") or {}).get(key)
    if known:
        return known
    res = kit.run_fresh(cfg, ws, t["cmd"], rev=rev, timeout=t["timeout"], setup=t.get("setup") or None,
                        cwd=t.get("cwd"), hint="tests-base")
    passed = bool(res["runs"]) and not kit.failed(res["runs"][0])
    rec = {"rev": rev, "passed": passed, "secs": res["runs"][0]["secs"] if res["runs"] else None, "at": kit.now(),
           "summary": tail(kit.summary(res), 30)}
    mutate(ws, lambda s: s["tests"].setdefault("baselines", {}).__setitem__(key, rec))
    return rec


def say_baseline(rec: dict, rev: str | None) -> None:
    if rec["passed"]:
        print(f"Tests on the unchanged project ({short(rev)}): passed in {rec['secs']:.1f} s.")
    else:
        print(f"Tests on the unchanged project ({short(rev)}): FAILED.\n{rec['summary']}\n")
        print("The correctness gate needs a test command that passes on the unchanged project, or a candidate's "
              "failure can't be told from one that was already there. Fix the command (leave out tests that "
              "already fail, and record that with decide), then run tests again.")


def evaluate(ws: Path, cfg: dict, names: list[str], base_rev: str | None, patch: str | None, label: str,
             cand_rev: str | None = None, allowed: list[str] | None = None) -> dict:
    """The correctness gate, then the measurement, for one change against the unchanged project."""
    st = load_state(ws)
    gate: dict = {"tests": None}
    if patch is not None:
        hits = [f for f in protected_files(patch, cfg["protect"]) if f not in (allowed or [])]
        if hits:
            return {"verdict": f"{REJECTED}: protected files", "detail": ", ".join(hits), "results": {}, "gate": gate}
    t = st["tests"]
    if not t.get("none"):
        base = tests_baseline(ws, cfg, base_rev)
        if not base["passed"]:
            say_baseline(base, base_rev)
            raise SystemExit(2)
        print("Tests, in a fresh copy with the change ...", flush=True)
        res = kit.run_fresh(cfg, ws, t["cmd"], patch=patch, rev=cand_rev or base_rev, timeout=t["timeout"],
                            setup=t.get("setup") or None, cwd=t.get("cwd"), hint=f"{label}-tests")
        if res.get("patch_error"):
            return {"verdict": f"{REJECTED}: doesn't apply", "detail": res["patch_error"], "results": {}, "gate": gate}
        if res.get("setup_failed") or kit.failed(res["runs"][0]):
            gate["tests"] = {"passed": False, "summary": tail(kit.summary(res), 30)}
            return {"verdict": f"{REJECTED}: tests", "detail": tail(kit.summary(res), 30), "results": {}, "gate": gate}
        gate["tests"] = {"passed": True, "secs": res["runs"][0]["secs"]}
        print(f"Tests: passed in {res['runs'][0]['secs']:.1f} s.", flush=True)
    results = {}
    for name in names:
        b = st["benchmarks"][name]
        r = measure_pair(ws, cfg, b, {"rev": base_rev}, {"rev": cand_rev or base_rev, "patch": patch},
                         f"{label}-{name}"[:40], threshold=b["noise"]["threshold"])
        results[name] = r
        print(result_line(name, b, r), flush=True)
        if r["verdict"].startswith(REJECTED):
            break
    verdict = overall(results)
    detail = next((r.get("detail") for r in results.values() if r["verdict"] == verdict), None)
    return {"verdict": verdict, "detail": detail, "results": results, "gate": gate, "machine": machine_info(),
            "base": base_rev, "at": kit.now()}


def estimate(st: dict, names: list[str], tests: bool = True) -> float:
    secs = 0.0
    for n in names:
        b = st["benchmarks"][n]
        secs += (b["warmup"] + b["repeat"]) * 2 * b["inner"] * (b.get("trial_secs") or 0) + 2 * (b.get("setup_secs") or 0)
    t = st.get("tests") or {}
    if tests and not t.get("none"):
        secs += max([x.get("secs") or 0 for x in (t.get("baselines") or {}).values()] or [0])
    return secs


def fmt_duration(secs: float) -> str:
    if secs < 90:
        return f"about {max(1, round(secs))} s"
    if secs < 5400:
        return f"about {round(secs / 60)} min"
    return f"about {secs / 3600:.1f} hours"


def save_result(ws: Path, label: str, ev: dict) -> str:
    folder = ws / "results"
    folder.mkdir(exist_ok=True)
    n = 1 + len(list(folder.glob(f"{label}-*.json")))
    path = folder / f"{label}-{n}.json"
    kit.save_json(path, ev)
    return f"results/{path.name}"


def compact(results: dict, benches: dict) -> dict:
    out = {}
    for name, r in results.items():
        keep = {k: r.get(k) for k in ("verdict", "ratio", "ci", "p", "n", "detail", "threshold", "noise", "inner",
                                       "output")}
        if r.get("base"):
            keep["before"], keep["after"] = r["base"]["median"], r["cand"]["median"]
        keep["line"] = result_line(name, benches[name], r)
        out[name] = keep
    return out


def improvement(r: dict, b: dict) -> float | None:
    """The fraction better on the deciding metric: 0.28 means 28% faster (or higher, for throughput)."""
    if r.get("ratio") is None:
        return None
    return (r["ratio"] - 1) if b["higher_better"] else (1 - r["ratio"])


# ---------------------------------------------------------------- commands: setup

def parse_goal(s: str) -> dict:
    s = (s or "max").strip().lower()
    if s in ("max", "measure"):
        return {"kind": s}
    m = re.fullmatch(r"(?:target:)?(\d+(?:\.\d+)?)%?", s)
    if not m or not 0 < float(m.group(1)) < 100:
        kit.fail("--goal is max, measure, or a target like 30%")
    return {"kind": "target", "pct": float(m.group(1))}


def goal_text(g: dict) -> str:
    if g["kind"] == "target":
        return f"a target of {g['pct']:g}% better"
    return {"max": "as fast as it'll go", "measure": "find and measure only, no code changes"}[g["kind"]]


def cmd_init(a) -> None:
    path = Path(a.repo).expanduser().resolve()
    if not path.is_dir():
        kit.fail(f"no folder {path}")
    top, is_git = kit.find_top(path)
    base = None
    if is_git and kit.has_head(top):
        base = kit.git(top, "rev-parse", "--verify", (a.rev or "HEAD") + "^{commit}").strip()
    elif a.rev:
        kit.fail("--rev needs a git repository with commits")
    like = None
    if a.like:
        lws, _ = kit.load_run(a.like)
        like = load_state(lws)
    protect = ([] if a.no_default_protect else list(DEFAULT_PROTECT)) + list(a.protect or [])
    extra = {"base_rev": base, "goal": parse_goal(a.goal), "rigor": a.rigor, "repeat": RIGOR[a.rigor],
             "after": a.after, "protect": protect, "status_at_init": kit.git_status(top) if is_git else None}
    ws, cfg, _ = kit.init_run(SKILL, str(path), scope=a.scope, slug=a.slug or "perf", target=a.target,
                              network=a.network, include=a.include or [], exclude=a.exclude or [], env=a.env or [],
                              workspace=a.workspace, force=a.force, extra=extra)
    st = new_state()
    if like:
        for name, b in like["benchmarks"].items():
            st["benchmarks"][name] = {k: v for k, v in b.items() if k != "noise"}
        if like.get("tests"):
            st["tests"] = {k: v for k, v in like["tests"].items() if k != "baselines"}
    if a.tests:
        st["tests"] = {"cmd": parse_cmd(a.tests), "setup": [], "timeout": a.tests_timeout, "cwd": None, "none": False}
    kit.save_json(ws / STATE, st)
    if base:
        dirty = [ln for ln in (cfg["status_at_init"] or "").splitlines() if ln.strip()]
        print(f"Base: {short(base)}" + (f" ({a.rev})" if a.rev else (f" ({cfg['branch']})" if cfg["branch"] else "")))
        if dirty:
            print(f"note: {plural(len(dirty), 'uncommitted change')} and untracked file(s) are left out: every copy "
                  f"is made from {short(base)}. Commit them first if they should count.")
    else:
        print("Base: the folder as it is (not a git repository with commits, so land can't commit anything)")
    print(f"Goal: {goal_text(extra['goal'])}. Rigor: {a.rigor}, {RIGOR[a.rigor]} runs each. After: {a.after}.")
    if like:
        print(f"From {a.like}: {plural(len(st['benchmarks']), 'benchmark')}"
              + (" and the test command" if st.get("tests") else "") + ". Run bench noise for each again.")
    if st.get("tests") and not st["tests"].get("none"):
        with timing_lock(cfg):
            rec = tests_baseline(ws, cfg, base)
        say_baseline(rec, base)


def cmd_tests(a) -> None:
    ws, cfg, st = load(a)
    if a.none:
        if a.argv_cmd:
            kit.fail("give a test command or --none, not both")
        mutate(ws, lambda s: s.__setitem__("tests", {"none": True, "why": a.why or ""}))
        text = "No test command: only the benchmarks' output check guards behaviour" + (f" ({a.why})" if a.why else "")
        mutate(ws, lambda s: s["decisions"].append({"text": text, "at": kit.now()}))
        print(text + ". Recorded under decisions.")
        return
    if not a.argv_cmd:
        t = st.get("tests")
        if not t:
            print("No test command set.")
        elif t.get("none"):
            print("No test command: the project has none" + (f" ({t['why']})" if t.get("why") else "") + ".")
        else:
            print(f"Tests: {show_cmd(t['cmd'])}" + (f", timeout {t['timeout']:g} s" if t.get("timeout") else ""))
            for key, rec in (t.get("baselines") or {}).items():
                print(f"  on {short(rec['rev'])}: {'passed' if rec['passed'] else 'FAILED'}")
        return
    t = {"cmd": a.argv_cmd, "setup": [parse_cmd(s) for s in a.setup or []], "timeout": a.timeout,
         "cwd": a.cwd, "none": False, "baselines": {}}
    mutate(ws, lambda s: s.__setitem__("tests", t))
    print(f"Tests: {show_cmd(t['cmd'])}")
    with timing_lock(cfg):
        rec = tests_baseline(ws, cfg, cfg["base_rev"])
    say_baseline(rec, cfg["base_rev"])
    sys.exit(0 if rec["passed"] else 1)


def cmd_bench_add(a) -> None:
    ws, cfg, st = load(a)
    check_name(a.name, "benchmark")
    cmd = a.argv_cmd
    if not cmd:
        kit.fail("give the benchmark's command after --")
    if a.name in st["benchmarks"] and not a.replace:
        kit.fail(f"there's already a benchmark called {a.name}; pass --replace to change it")
    for rx in a.output_normalise or []:
        try:
            re.compile(rx)
        except re.error as e:
            kit.fail(f"--output-normalise {rx!r}: {e}")
    if a.repeat is not None and a.repeat < MIN_REPEAT:
        kit.fail(f"--repeat must be at least {MIN_REPEAT}: with fewer runs each, the p-value can't get below 0.01")
    if a.inner is not None and a.inner < 1:
        kit.fail("--inner must be 1 or more")
    b = {"name": a.name, "cmd": cmd, "setup": [parse_cmd(s) for s in a.setup or []], "cwd": a.cwd,
         "warmup": a.warmup, "repeat": a.repeat or cfg["repeat"], "inner": a.inner or 1,
         "timeout": a.timeout, "timeout_setup": a.setup_timeout, "metric": a.metric or "wall", "unit": None,
         "higher_better": a.higher_is_better, "output_check": not a.no_output_check,
         "normalise": list(a.output_normalise or []), "trial_secs": None, "setup_secs": None, "added": kit.now()}
    notes = []
    if not a.no_trial:
        print(f"Trying {a.name} twice in a fresh copy of {short(cfg['base_rev'])} ...", flush=True)
        with timing_lock(cfg):
            root = None
            try:
                t0 = time.perf_counter()
                root, problem = prepare_box(ws, cfg, f"trial-{a.name}-{secrets.token_hex(2)}"[:40], cfg["base_rev"],
                                            None, b["setup"], a.cwd, a.setup_timeout)
                if problem:
                    kit.fail(f"the setup step failed on the unchanged project:\n{problem[1]}")
                b["setup_secs"] = round(time.perf_counter() - t0, 3)
                env = kit.runner().build_env(root)
                trial_timeout = a.timeout or 1800
                runs = [time_run(root, env, cmd, a.cwd, trial_timeout) for _ in range(2)]
            finally:
                if root:
                    kit.drop_sandbox(ws, cfg, root.name)
        for r in runs:
            if r["timed_out"] or r["code"] != 0:
                kit.fail(f"the benchmark fails on the unchanged project ({describe_failure(r)}). Fix the command "
                         "before measuring anything with it.")
        names = list(dict.fromkeys(n for n, _, _ in parse_metrics(runs[0]["out"])))
        if a.metric and a.metric != "wall" and a.metric not in names:
            kit.fail(f"it printed no METRIC line for {a.metric}" + (f"; it printed {', '.join(names)}" if names else ""))
        if not a.metric and len(names) > 1:
            kit.fail(f"it prints several metrics ({', '.join(names)}): choose the one that decides with --metric "
                     "(or --metric wall for wall time)")
        if not a.metric and len(names) == 1:
            b["metric"] = names[0]
            notes.append(f"deciding on METRIC {names[0]}, the one it prints; --metric wall decides on wall time")
        if b["metric"] != "wall":
            units = [u for n, _, u in parse_metrics(runs[0]["out"]) if n == b["metric"]]
            b["unit"] = units[-1] if units else None
            if not metric_value(runs[0]["out"], b["metric"]):
                kit.fail(f"METRIC {b['metric']} is 0 on the unchanged project, so a ratio means nothing")
        if b["output_check"]:
            n1, n2 = normalise(runs[0]["out"], b["normalise"]), normalise(runs[1]["out"], b["normalise"])
            if n1 != n2:
                kit.fail(f"the output differs between two runs of the unchanged project ({first_difference(n1, n2)}). "
                         "Mask the part that varies with --output-normalise '<regex>', or turn the check off with "
                         "--no-output-check and record why with decide.")
            if not n1.strip():
                notes.append("it prints nothing, so the output check checks nothing: if it writes files, have it "
                             "print a hash of them")
        t = runs[1]["secs"]
        b["trial_secs"] = round(t, 4)
        b["inner"], b["repeat"] = plan_runs(t, b["metric"] == "wall", a.inner, a.repeat, cfg["repeat"])
        if b["inner"] > 1 and a.inner is None:
            notes.append(f"one run takes {fmt_secs(t)}, so process start-up dominates: each sample runs it "
                         f"{b['inner']} times back to back and divides. A gain inside a few milliseconds of work "
                         "won't show; use a larger input, or time the work itself and print a METRIC line")
        if b["repeat"] == MIN_REPEAT and a.repeat is None and MIN_REPEAT != cfg["repeat"]:
            notes.append(f"one run takes {fmt_secs(t)}, so it's cut to {MIN_REPEAT} runs each, the fewest that can "
                         f"reach p < 0.01; a compare takes "
                         f"{fmt_duration(estimate({'benchmarks': {a.name: b}}, [a.name], tests=False))}")
        if b["timeout"] is None:
            b["timeout"] = max(60.0, math.ceil(t * 10))
    else:
        b["timeout"] = b["timeout"] or 1800.0
        notes.append("not tried (--no-trial): no time estimate, and nothing checked yet")
    for p in a.protect or []:
        if p not in cfg["protect"]:
            kit.update_cfg(ws, lambda c, p=p: c["protect"].append(p))
    if a.no_output_check:
        why = a.why or "no reason given"
        mutate(ws, lambda s: s["decisions"].append(
            {"text": f"Benchmark {a.name} runs without the output check: {why}", "at": kit.now()}))
    mutate(ws, lambda s: s["benchmarks"].__setitem__(a.name, b))
    what = "wall time" if b["metric"] == "wall" else f"METRIC {b['metric']}" + (f" ({b['unit']})" if b["unit"] else "")
    print(f"Benchmark {a.name}: {show_cmd(cmd)}; {what}, {'higher' if b['higher_better'] else 'lower'} is better.")
    if b["trial_secs"] is not None:
        print(f"One run takes about {fmt_secs(b['trial_secs'])}. Each side gets {b['warmup']} warm-up and "
              f"{b['repeat']} timed runs, interleaved; one benchmark's compare takes "
              f"{fmt_duration(estimate({'benchmarks': {a.name: b}}, [a.name], tests=False))}, plus the tests.")
    print(f"Output check: {'on' if b['output_check'] else 'OFF'}" + (f", masking {len(b['normalise'])} pattern(s)"
                                                                       if b["normalise"] else "") + ".")
    for n in notes:
        print(f"note: {n}.")
    print(f"Next: perf.py bench noise -w {ws} {a.name}")


def cmd_bench_noise(a) -> None:
    ws, cfg, st = load(a)
    names = a.names or list(st["benchmarks"])
    if not names:
        kit.fail("no benchmarks yet: perf.py bench add -w <run> <name> -- <command>")
    for name in names:
        get_bench(st, name)
    if a.repeat is not None and a.repeat < MIN_REPEAT:
        kit.fail(f"--repeat must be at least {MIN_REPEAT}")
    for name in names:
        changes = {k: v for k, v in (("repeat", a.repeat), ("warmup", a.warmup)) if v is not None}
        if changes:
            mutate(ws, lambda s, name=name, changes=changes: s["benchmarks"][name].update(changes))
    st = load_state(ws)
    print(f"Noise test: the unchanged project ({short(cfg['base_rev'])}) against itself, in two fresh copies, "
          f"{fmt_duration(estimate(st, names, tests=False))}.", flush=True)
    bad = 0
    with timing_lock(cfg):
        for name in names:
            b = st["benchmarks"][name]
            r = measure_pair(ws, cfg, b, {"rev": cfg["base_rev"]}, {"rev": cfg["base_rev"]}, f"noise-{name}"[:40],
                             same_code=True)
            lo, hi = r["ci"]
            band = noise_band(lo, hi)
            state = "clean" if band <= NOISE_LIMIT else "too noisy"
            biased = r["p"] < ALPHA and (lo > 1 or hi < 1)
            rec = {"band": band, "threshold": max(MIN_THRESHOLD, NOISE_FACTOR * band), "state": state, "ci": [lo, hi],
                   "p": r["p"], "ratio": r["ratio"], "n": r["n"], "before": r["base"]["median"],
                   "after": r["cand"]["median"], "biased": biased, "machine": machine_info(), "at": kit.now(),
                   "order": r["order"]}
            save_result(ws, f"noise-{name}", {"benchmark": name, "noise": rec, "result": r})
            mutate(ws, lambda s, name=name, rec=rec: s["benchmarks"][name].__setitem__("noise", rec))
            print(f"{name}, the unchanged project against itself: {fmt_value(rec['before'], b)} and "
                  f"{fmt_value(rec['after'], b)}, ratio {r['ratio']:.3f} (95% CI {lo:.3f}–{hi:.3f}), {fmt_p(r['p'])}, "
                  f"{r['n'][0]} runs each. Noise band ±{pct_num(band)}%; a change must beat "
                  f"{pct_num(rec['threshold'])}% to count.")
            if biased:
                print(f"warning: two identical copies measured differently ({fmt_p(r['p'])}). Something besides the "
                      "code changes the timing (a cache warming up, another program); the threshold allows for it, "
                      "but more warm-up (--warmup) may help.")
            if state != "clean":
                bad += 1
                print(f"{name}: that's more than 10%, so its numbers can't be trusted yet. Try more runs (--repeat), "
                      "more warm-up (--warmup), closing other work on this machine, or a larger input so each run "
                      "takes longer; then run bench noise again.")
            if rec["machine"]["on_battery"]:
                print("note: this machine is on battery power; laptops slow down on battery, so plug it in.")
    sys.exit(1 if bad else 0)


# ---------------------------------------------------------------- commands: evidence and agents

def profiler_for(cmd: list[str]) -> str | None:
    exe = Path(cmd[0]).name.lower()
    if re.fullmatch(r"(py|pypy[\d.]*|python[\d.]*w?)(\.exe)?", exe):
        return "python"
    if re.fullmatch(r"node(\.exe)?", exe):
        return "node"
    return None


def python_profile_cmd(cmd: list[str], out: Path) -> list[str] | None:
    rest, opts, i = cmd[1:], [], 0
    while i < len(rest) and rest[i].startswith("-") and rest[i] != "-":
        o = rest[i]
        if o == "-c":
            return None
        if o == "-m":
            break
        opts.append(o)
        if o in ("-X", "-W") and i + 1 < len(rest):
            opts.append(rest[i + 1])
            i += 1
        i += 1
    return [cmd[0], *opts, "-m", "cProfile", "-o", str(out), *rest[i:]]


def where_label(path: str, repo: Path) -> str:
    p = path.replace("\\", "/")
    for prefix in {str(repo).replace("\\", "/"), str(repo.resolve()).replace("\\", "/")}:
        if p.lower().startswith(prefix.lower() + "/"):
            return p[len(prefix) + 1:]
    parts = [x for x in p.split("/") if x]
    return "/".join(parts[-2:]) if len(parts) > 1 else p


def summarise_pstats(path: Path, repo: Path, top: int = 25) -> tuple[str, float]:
    import pstats
    stats = pstats.Stats(str(path))
    rows = []
    for (file, line, func), (cc, nc, tt, ct, _callers) in stats.stats.items():
        label = func if file == "~" else f"{where_label(file, repo)}:{line} {func}"
        rows.append((ct, tt, nc, cc, label))
    total = stats.total_tt
    out = []
    for title, key in (("cumulative", 0), ("self", 1)):
        out.append(f"Top {top} by {title} time:")
        out.append(f"  {'cum s':>8} {'self s':>8} {'calls':>9}  function")
        for ct, tt, nc, cc, label in sorted(rows, key=lambda r: -r[key])[:top]:
            calls = f"{nc}/{cc}" if nc != cc else str(nc)
            out.append(f"  {ct:8.3f} {tt:8.3f} {calls:>9}  {label}")
        out.append("")
    return "\n".join(out), total


def summarise_cpuprofile(path: Path, repo: Path, top: int = 25) -> tuple[str, float]:
    prof = json.loads(path.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in prof.get("nodes", [])}
    samples, deltas = prof.get("samples", []), prof.get("timeDeltas", [])
    self_us: dict[int, float] = {}
    for i, sid in enumerate(samples):
        w = deltas[i + 1] if i + 1 < len(deltas) else 0
        self_us[sid] = self_us.get(sid, 0) + max(0, w)
    agg: dict[tuple[str, str], float] = {}
    for nid, us in self_us.items():
        cf = nodes.get(nid, {}).get("callFrame", {})
        url = cf.get("url") or ""
        if url.startswith("file://"):
            url = re.sub(r"^file:///?", "", url)
            if os.name != "nt" and not url.startswith("/"):
                url = "/" + url
        where = where_label(url, repo) if url else "(native)"
        key = (cf.get("functionName") or "(anonymous)", where)
        agg[key] = agg.get(key, 0) + us
    total = sum(agg.values()) / 1e6
    out = [f"Top {top} by self time (Node.js samples, grouped by function and file):",
           f"  {'self s':>8} {'share':>6}  function"]
    for (fn, where), us in sorted(agg.items(), key=lambda kv: -kv[1])[:top]:
        share = us / 1e6 / total * 100 if total else 0
        out.append(f"  {us / 1e6:8.3f} {share:5.1f}%  {fn} ({where})")
    return "\n".join(out) + "\n", total


def cmd_profile(a) -> None:
    ws, cfg, st = load(a)
    b = get_bench(st, a.name)
    kind = profiler_for(b["cmd"])
    if not kind:
        print(f"No built-in profiler applies to {b['cmd'][0]}: profile only wraps a command that starts with python "
              "(or py) or node. For a Python tool with its own launcher, write the benchmark as `python -m <tool> "
              "...`. Otherwise use the language's own profiler (perf, dotnet-trace, go tool pprof, a browser's "
              "performance panel) in a sandbox, and give finders what it shows.")
        return
    patch, rev, tag = None, cfg["base_rev"], a.name
    if a.candidate:
        c = get_cand(st, a.candidate)
        patch, rev, tag = kit.read_patch(ws / c["patch"]), c["base"], f"{a.name}-{c['id']}"
    if kind == "python" and python_profile_cmd(b["cmd"], Path("x")) is None:
        kit.fail("cProfile can't profile `python -c`: put the code in a file and benchmark that")
    folder = ws / "profiles"
    folder.mkdir(exist_ok=True)
    with timing_lock(cfg):
        root = None
        try:
            root, problem = prepare_box(ws, cfg, f"profile-{a.name}-{secrets.token_hex(2)}"[:40], rev, patch,
                                        b.get("setup"), b.get("cwd"), b["timeout_setup"])
            if problem:
                kit.fail(f"the {problem[0]}:\n{problem[1]}")
            raw = root / "scratch" / ("out.prof" if kind == "python" else "cpu")
            if kind == "python":
                argv = python_profile_cmd(b["cmd"], raw)
            else:
                raw.mkdir(parents=True, exist_ok=True)
                argv = [b["cmd"][0], "--cpu-prof", f"--cpu-prof-dir={raw}", *b["cmd"][1:]]
            res = kit.run_in(root, argv, 1, b["timeout"] * 3, b.get("cwd"), log=False)
            if kit.failed(res["runs"][0]):
                kit.fail("it failed under the profiler:\n" + kit.summary(res))
            if kind == "python":
                text, total = summarise_pstats(raw, root / "repo")
                shutil.copy2(raw, folder / f"{tag}.prof")
                raw_name = f"{tag}.prof"
            else:
                found = sorted(raw.glob("*.cpuprofile"), key=lambda p: p.stat().st_mtime)
                if not found:
                    kit.fail("Node.js wrote no .cpuprofile (does the command start Node.js directly?)")
                text, total = summarise_cpuprofile(found[-1], root / "repo")
                shutil.copy2(found[-1], folder / f"{tag}.cpuprofile")
                raw_name = f"{tag}.cpuprofile"
        finally:
            if root:
                kit.drop_sandbox(ws, cfg, root.name)
    head = (f"Profile of {a.name} ({show_cmd(b['cmd'])})" + (f" with {a.candidate}" if a.candidate else "")
            + f", one run, {total:.3f} s profiled. Times under a profiler are inflated: read the shape, not the "
            "numbers. This is evidence of where the time goes, not a conclusion.\n")
    (folder / f"{tag}.txt").write_text(head + "\n" + text, encoding="utf-8")
    print(head)
    print(text)
    print(f"Saved: {folder / (tag + '.txt')} (give finders this file) and {folder / raw_name}")


def cmd_sandbox(a) -> None:
    ws, cfg, st = load(a)
    rev, patch, cid = cfg["base_rev"], None, None
    if a.candidate:
        c = get_cand(st, a.candidate)
        rev, patch, cid = c["base"], kit.read_patch(ws / c["patch"]), c["id"]
    root = kit.make_sandbox(cfg, ws, a.name, rev=rev, network=True if a.net else None)
    if patch:
        ok, msg = kit.apply_patch(root / "repo", patch)
        if not ok:
            kit.drop_sandbox(ws, cfg, a.name)
            kit.fail(f"{cid} doesn't apply: {msg}")
    for f in a.give or []:
        src = Path(f).expanduser()
        if not src.exists():
            kit.drop_sandbox(ws, cfg, a.name)
            kit.fail(f"--give {f}: no such file")
        kit.copy_path(src, root / "scratch" / src.name)
    mutate(ws, lambda s: s["boxes"].__setitem__(a.name, {"rev": rev, "candidate": cid}))
    print(f"Sandbox {a.name} is ready" + (f", with {cid} applied (uncommitted: git diff shows it)" if cid else "") + ".")
    print(kit.sandbox_text(root, cfg["network"] or a.net))
    if a.give:
        print(f"  given:        {', '.join(Path(f).name for f in a.give)} (in the scratch folder)")


def reply_source(a, cfg) -> tuple[Path, Path | None]:
    if bool(getattr(a, "reply", None)) == bool(a.sandbox):
        kit.fail("give the reply file, or --sandbox <name> to read its reply.json")
    if a.sandbox:
        box = kit.find_sandbox(cfg, a.sandbox)
        return box / "scratch" / kit.REPLY, box
    return Path(a.reply), None


def drop_box(ws: Path, cfg: dict, name: str) -> None:
    kit.drop_sandbox(ws, cfg, name)
    mutate(ws, lambda s: s["boxes"].pop(name, None))


def as_pct(v) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(v or ""))
    return float(m.group(1)) if m else None


def cmd_collect(a) -> None:
    ws, cfg, st = load(a)
    path, box = reply_source(a, cfg)
    data = kit.read_reply(path)
    source = a.source or (f"finder:{a.sandbox}" if a.sandbox else "lead")
    if box:
        kit.keep_scratch(box, ws / "scratch" / a.sandbox)
    raw = [h for h in kit.as_list(data.get("hypotheses")) if isinstance(h, dict)]

    def add(s):
        added, empty = [], 0
        for h in raw:
            claim = str(h.get("claim") or "").strip()
            if not claim:
                empty += 1
                continue
            rec = {"id": f"H{s['next']['H']}", "title": str(h.get("title") or claim[:80]).strip(), "claim": claim,
                   "where": [str(w) for w in kit.as_list(h.get("where"))], "why": h.get("why") or "",
                   "expected_effect": h.get("expected_effect") or "", "effect_pct": as_pct(h.get("effect_pct")
                                                                                         or h.get("expected_effect")),
                   "benchmark": h.get("benchmark"), "how_to_check": h.get("how_to_check") or "",
                   "prototype": h.get("prototype"), "risk": h.get("risk") or "",
                   "confidence": h.get("confidence") if h.get("confidence") in ("low", "medium", "high") else "medium",
                   "source": source, "status": "open", "candidates": [], "notes": []}
            s["next"]["H"] += 1
            s["hypotheses"].append(rec)
            added.append(rec["id"])
        s.setdefault("coverage", {})[source] = sorted({str(x) for x in kit.as_list(data.get("covered"))})
        return added, empty
    added, empty = mutate(ws, add)
    if box and not a.keep:
        drop_box(ws, cfg, a.sandbox)
    span = f"{added[0]}-{added[-1]}" if len(added) > 1 else (added[0] if added else "nothing")
    print(f"{source}: added {span}; it read {len(kit.as_list(data.get('covered')))} files"
          + (f"; skipped {empty} with no claim" if empty else "") + ".")
    if not raw and "hypotheses" not in data:
        print("warning: the reply has no \"hypotheses\" list; check it's the finder's answer")


def hyp_rank(h: dict):
    conf = {"high": 0, "medium": 1, "low": 2}.get(h.get("confidence"), 1)
    return (-(h.get("effect_pct") or 0), conf, int(h["id"][1:]))


def cmd_hypothesis(a) -> None:
    ws, cfg, st = load(a)
    h = get_hyp(st, a.id)
    if a.status or a.note:
        if a.status and a.status not in ("open", "tried", "dropped"):
            kit.fail("--status is open, tried or dropped")

        def apply(s):
            x = get_hyp(s, a.id)
            if a.status:
                x["status"] = a.status
            if a.note:
                x["notes"].append(a.note)
            return x
        h = mutate(ws, apply)
        print(f"{h['id']}: {h['status']}. {h['title']}")
        return
    print(f"{h['id']}. {h['title']} ({h['status']}, from {h['source']})")
    print(f"- **Claim:** {h['claim']}")
    print(f"- **Where:** {', '.join(h['where']) or 'Not given.'}")
    print(f"- **Expected effect:** {h['expected_effect'] or 'Not given.'}")
    if h.get("benchmark"):
        print(f"- **Benchmark:** {h['benchmark']}")
    print("\nFor the lead only (keep it out of the optimiser's brief):")
    for k in ("why", "how_to_check", "prototype", "risk"):
        if h.get(k):
            print(f"  {k}: {h[k]}")


def add_candidate(ws: Path, cfg: dict, patch: str, title: str, mechanism: str, base: str | None, source: str,
                  hypothesis: str | None = None, kind: str = "single", members=None, allowed=None, reply=None) -> dict:
    def add(s):
        prefix = "K" if kind == "combination" else "C"
        cid = f"{prefix}{s['next'][prefix]}"
        s["next"][prefix] += 1
        rel = f"candidates/{cid}.patch"
        kit.write_patch(ws / rel, patch)
        c = {"id": cid, "kind": kind, "title": title, "mechanism": mechanism, "hypothesis": hypothesis,
             "members": members or [], "patch": rel, "files": kit.changed_files(patch), "base": base,
             "source": source, "status": "new", "verdict": None, "results": {}, "gate": None, "challenge": None,
             "allowed_protected": allowed or [], "reply": reply or {}, "notes": [], "added": kit.now()}
        s["candidates"].append(c)
        if hypothesis:
            hx = get_hyp(s, hypothesis)
            hx["status"] = "tried"
            hx["candidates"].append(cid)
        return c
    return mutate(ws, add)


def cmd_candidate_add(a) -> None:
    ws, cfg, st = load(a)
    if bool(a.sandbox) == bool(a.patch):
        kit.fail("give --sandbox <name> (an optimiser's copy) or --patch <file>")
    if a.hypothesis:
        get_hyp(st, a.hypothesis)
    reply, base = {}, cfg["base_rev"]
    if a.sandbox:
        box = kit.find_sandbox(cfg, a.sandbox)
        info = st["boxes"].get(a.sandbox) or {}
        if info.get("candidate"):
            kit.fail(f"that copy has {info['candidate']} applied, so its diff isn't one change. Make optimisers' "
                     "copies without --candidate.")
        base = info.get("rev", base)
        patch = kit.box_diff(box)
        rp = box / "scratch" / kit.REPLY
        if rp.is_file():
            try:
                reply = kit.read_reply(rp)
            except SystemExit:
                if not a.title:
                    raise
                print("warning: its reply.json doesn't parse; using --title and --mechanism")
        source = a.source or f"optimiser:{a.sandbox}"
    else:
        if not Path(a.patch).is_file():
            kit.fail(f"no patch file {a.patch}")
        patch = kit.read_patch(Path(a.patch))
        source = a.source or f"patch:{Path(a.patch).name}"
    if reply.get("done") is False and not a.force:
        kit.fail(f"the optimiser says it made no safe change: {reply.get('reason') or 'no reason given'}. "
                 f"Record that with hypothesis {a.hypothesis or '<id>'} --status dropped --note '...', "
                 "or pass --force to keep what it left")
    if not patch.strip():
        kit.fail("no changes in that copy, so there's nothing to measure")
    hits = protected_files(patch, cfg["protect"])
    if hits and not a.allow_protected:
        kit.fail(f"the change touches files candidates must leave alone: {', '.join(hits)}. Tests, benchmarks, their "
                 "inputs and dependency files are protected so a candidate can't make itself look good. If the "
                 "change really needs them, pass --allow-protected (it's recorded under decisions).")
    mention = attribution_hits(added_lines(patch))
    if mention and not a.allow_mention:
        kit.fail(f"the change's added lines mention {', '.join(mention)}. Nothing written into the project may say "
                 "who or what made it: take it out. If the project itself is about these, pass --allow-mention.")
    title = (a.title or reply.get("title") or f"Change from {a.sandbox or Path(a.patch).name}").strip().splitlines()[0]
    mechanism = (a.mechanism or reply.get("mechanism") or
                 (get_hyp(st, a.hypothesis)["claim"] if a.hypothesis else "")).strip()
    c = add_candidate(ws, cfg, patch, title, mechanism, base, source, a.hypothesis, allowed=hits, reply=reply)
    if mention:
        mutate(ws, lambda s: get_cand(s, c["id"]).__setitem__("allow_mention", True))
    if hits:
        mutate(ws, lambda s: s["decisions"].append(
            {"text": f"{c['id']} was allowed to change protected files: {', '.join(hits)}", "at": kit.now()}))
    if a.sandbox and not a.keep:
        drop_box(ws, cfg, a.sandbox)
    files = c["files"]
    print(f"{c['id']}: {title} ({plural(len(files), 'file')}: {', '.join(files[:6])}"
          + (f", and {len(files) - 6} more" if len(files) > 6 else "") + ").")
    churn = line_ending_churn(patch)
    if churn:
        print(f"warning: it rewrites the line endings of {', '.join(churn)}, so it won't combine with other changes "
              "to those files and its commit would touch every line. Ask the optimiser to keep line endings as "
              "they were.")
    print(f"Next: perf.py compare -w {ws} --candidate {c['id']}")


def cmd_candidate_show(a) -> None:
    ws, cfg, st = load(a)
    c = get_cand(st, a.id)
    print(f"{c['id']}. {c['title']} ({c['status']})")
    if c["members"]:
        print(f"Combines: {', '.join(c['members'])}")
    names = list(c["results"]) or list(st["benchmarks"])
    print(f"Claim: this makes {', '.join(names) or 'the benchmarks'} faster without changing behaviour.")
    if a.with_mechanism and c["mechanism"]:
        print(f"Mechanism: {c['mechanism']}")
    print("\n```diff")
    print(kit.read_patch(ws / c["patch"]).rstrip("\n"))
    print("```")


def cmd_candidate_set(a) -> None:
    ws, cfg, st = load(a)
    c = get_cand(st, a.id)
    if a.status not in ("accepted", "rejected"):
        kit.fail("--status is accepted or rejected")
    if a.status == "accepted":
        if c.get("verdict") not in ("faster", "mixed"):
            kit.fail(f"{c['id']} came out '{c.get('verdict') or 'not measured'}', so it can't be accepted: only a "
                     "measured gain can")
        if (c.get("challenge") or {}).get("outcome") == "proven":
            kit.fail(f"{c['id']}'s challenger proved a behaviour change; that can't be accepted")

    def apply(s):
        x = get_cand(s, a.id)
        x["status"] = "accepted" if a.status == "accepted" else f"{REJECTED}: {a.reason or 'by you'}"
        x["notes"].append(f"{a.status} by the user" + (f": {a.note}" if a.note else ""))
        return x
    c = mutate(ws, apply)
    print(f"{c['id']}: {c['status']}. {c['title']}")


# ---------------------------------------------------------------- commands: measuring candidates

def resolve_candidate(ws: Path, cfg: dict, st: dict, ref: str) -> dict:
    if re.fullmatch(r"[CcKk]\d+", ref):
        return get_cand(st, ref)
    if ref in (kit.load_json(ws / "config.json", {}).get("sandboxes") or {}):
        box = kit.find_sandbox(cfg, ref)
        info = st["boxes"].get(ref) or {}
        if info.get("candidate"):
            kit.fail(f"that copy has {info['candidate']} applied; it isn't one change")
        patch = kit.box_diff(box)
        if not patch.strip():
            kit.fail(f"no changes in {ref}")
        reply = {}
        with contextlib.suppress(SystemExit):
            if (box / "scratch" / kit.REPLY).is_file():
                reply = kit.read_reply(box / "scratch" / kit.REPLY)
        c = add_candidate(ws, cfg, patch, reply.get("title") or f"Change from {ref}", reply.get("mechanism") or "",
                          info.get("rev", cfg["base_rev"]), f"optimiser:{ref}", reply=reply)
        print(f"Saved the change in {ref} as {c['id']}.")
        return c
    p = Path(ref)
    if p.is_file():
        c = add_candidate(ws, cfg, kit.read_patch(p), f"Change from {p.name}", "", cfg["base_rev"], f"patch:{p.name}")
        print(f"Saved {p.name} as {c['id']}.")
        return c
    kit.fail(f"--candidate {ref}: not a candidate id, a sandbox of this run, or a patch file")


def record_evaluation(ws: Path, cid: str, ev: dict, file: str) -> dict:
    def apply(s):
        x = get_cand(s, cid)
        x["verdict"] = ev["verdict"]
        x["status"] = status_for(ev["verdict"], x.get("challenge"))
        x["results"] = compact(ev["results"], s["benchmarks"])
        x["gate"] = ev["gate"]
        x["detail"] = ev.get("detail")
        x["measured_on"] = ev.get("base")
        x["result_file"] = file
        x["machine"] = ev.get("machine")
        return x
    return mutate(ws, apply)


def say_outcome(c: dict, ws: Path) -> None:
    v = c["verdict"]
    if v.startswith(REJECTED):
        print(f"{c['id']}: {v}.")
        if c.get("detail") and v == f"{REJECTED}: tests":
            print(c["detail"])
        return
    t = c.get("gate", {}).get("tests")
    gate = ("tests passed" if t and t.get("passed") else "no tests to run") + "; output " + \
           ("unchanged" if all(r.get("output") == "same" for r in c["results"].values()) else "not checked")
    if v == "faster":
        print(f"{c['id']}: faster ({gate}). Next: a challenger, in a copy with it applied: "
              f"perf.py sandbox -w {ws} chal-{c['id']} --candidate {c['id']}")
    elif v == "mixed":
        print(f"{c['id']}: mixed, faster on some benchmarks and slower on others ({gate}). That's the user's call.")
    else:
        print(f"{c['id']}: {v} ({gate}). It isn't a proven gain, so it won't land.")


def cmd_compare(a) -> None:
    ws, cfg, st = load(a)
    names = a.names or list(st["benchmarks"])
    ensure_ready(st, names)
    c = resolve_candidate(ws, cfg, st, a.candidate)
    st = load_state(ws)
    base = cfg["base_rev"]
    if a.base:
        if not cfg["git"]:
            kit.fail("--base needs a git repository")
        base = kit.git(Path(cfg["repo"]), "rev-parse", "--verify", a.base + "^{commit}").strip()
    else:
        base = c.get("base")
    warn_if_project_changed(cfg)
    print(f"Comparing {c['id']} ({c['title']}) with the unchanged project ({short(base)}) on "
          f"{', '.join(names)}: {fmt_duration(estimate(st, names))}.", flush=True)
    with timing_lock(cfg):
        ev = evaluate(ws, cfg, names, base, kit.read_patch(ws / c["patch"]), c["id"],
                      allowed=c.get("allowed_protected"))
    file = save_result(ws, c["id"], ev)
    c = record_evaluation(ws, c["id"], ev, file)
    say_outcome(c, ws)
    warn_if_project_changed(cfg)
    sys.exit(0 if c["verdict"] == "faster" else 1)


def classify_fresh(res: dict) -> str:
    if res.get("patch_error") or res.get("setup_failed") or not res.get("runs"):
        return "inconclusive"
    return kit.classify(res["runs"][0])


def cmd_challenge(a) -> None:
    ws, cfg, st = load(a)
    c = get_cand(st, a.id)
    if c.get("verdict") not in ("faster", "mixed"):
        kit.fail(f"{c['id']} came out '{c.get('verdict') or 'not measured'}': only a measured gain gets challenged")
    path, box = reply_source(a, cfg)
    data = kit.read_reply(path)
    v = data.get("verdict")
    if v not in ("stands", "question", "breaks"):
        kit.fail(f"a challenger's verdict is stands, question or breaks, not {v!r}")
    scratch = None
    if box:
        scratch = ws / "challenges" / c["id"]
        kit.keep_scratch(box, scratch)
    cmd = kit.as_cmd(data.get("command"))
    proof = None
    if cmd:
        patch = kit.read_patch(ws / c["patch"])
        print(f"Rerunning the challenger's reproduction in fresh copies, with {c['id']} and without it ...", flush=True)
        with_c = kit.run_fresh(cfg, ws, cmd, patch=patch, scratch_from=scratch, rev=c["base"], timeout=a.timeout,
                               hint=f"chal-{c['id']}-with")
        without = kit.run_fresh(cfg, ws, cmd, scratch_from=scratch, rev=c["base"], timeout=a.timeout,
                                hint=f"chal-{c['id']}-without")
        kw, ko = classify_fresh(with_c), classify_fresh(without)
        proof = "proven" if kw == "shows" and ko == "behaves" else (
            "already there" if kw == "shows" and ko == "shows" else "not shown")
        print(f"With {c['id']}: the change in behaviour {'showed' if kw == 'shows' else 'did not show'}"
              f" ({kw}). Without it: {ko}.")
        if kw != "behaves":
            print(kit.summary(with_c))
    if proof == "proven" or (c.get("challenge") or {}).get("outcome") == "proven":
        outcome, status = "proven", f"{REJECTED}: behaviour change"
        if proof != "proven":
            print(f"note: an earlier challenger proved {c['id']} changes behaviour; that still stands.")
    elif v == "stands":
        outcome = "stands"
        status = "accepted" if c["verdict"] == "faster" else "mixed"
    else:
        outcome, status = "question", "question"
    concerns = [x for x in kit.as_list(data.get("concerns")) if isinstance(x, (dict, str))]
    question = data.get("question") or ("; ".join(x.get("what", "") if isinstance(x, dict) else x for x in concerns)
                                        if outcome == "question" else None)
    rec = {"verdict": v, "outcome": outcome, "proof": proof, "concerns": concerns, "question": question,
           "command": cmd or None, "expected": data.get("expected"), "observed": data.get("observed"),
           "scratch": f"challenges/{c['id']}" if scratch else None, "at": kit.now()}

    def apply(s):
        x = get_cand(s, c["id"])
        x.setdefault("challenges", []).append(rec)
        if not ((x.get("challenge") or {}).get("outcome") == "proven" and proof != "proven"):
            x["challenge"] = rec   # otherwise the earlier proof stays the one on record
        x["status"] = status
        return x
    c = mutate(ws, apply)
    if box and not a.keep:
        drop_box(ws, cfg, a.sandbox)
    if outcome == "proven":
        print(f"{c['id']}: {c['status']}: its reproduction shows the change, and doesn't show without it.")
    elif outcome == "question":
        why = "a concern without a reproduction that shows it" if v == "breaks" else "the challenger's question"
        print(f"{c['id']}: question ({why}). Ask the user: {question or 'see the concerns'}")
    else:
        print(f"{c['id']}: {c['status']}. The challenger found no behaviour change the tests miss.")


def expand(st: dict, ids: list[str]) -> list[dict]:
    out = []
    for i in ids:
        c = get_cand(st, i)
        for m in (c["members"] if c["kind"] == "combination" else [c["id"]]):
            x = get_cand(st, m)
            if x["id"] not in [y["id"] for y in out]:
                out.append(x)
    return out


def stack(ws: Path, cfg: dict, cands: list[dict], base: str | None) -> tuple[str | None, str]:
    """Apply candidates in order to a fresh copy. Returns (the combined patch or None, what happened)."""
    root = kit.make_sandbox(cfg, ws, f"stack-{secrets.token_hex(3)}", rev=base)
    try:
        for i, c in enumerate(cands):
            ok, msg = kit.apply_patch(root / "repo", kit.read_patch(ws / c["patch"]))
            if not ok:
                after = ", ".join(x["id"] for x in cands[:i]) or "the base"
                return None, f"{c['id']} doesn't apply on top of {after}: {msg}"
        return kit.box_diff(root), "applied"
    finally:
        kit.drop_sandbox(ws, cfg, root.name)


def cmd_combine(a) -> None:
    ws, cfg, st = load(a)
    names = list(st["benchmarks"])
    ensure_ready(st, names)
    cands = expand(st, a.ids)
    if len(cands) < 2:
        kit.fail("combine takes two or more candidates")
    bases = {c["base"] for c in cands}
    if len(bases) > 1:
        kit.fail("those candidates were made on different bases, so they can't be stacked")
    base = bases.pop()
    for c in cands:
        if c["status"] != "accepted":
            print(f"note: {c['id']} is '{c['status']}', not accepted: the combination is measured, but land takes "
                  "accepted candidates only")
    combined, msg = stack(ws, cfg, cands, base)
    if combined is None:
        print(f"They don't combine: {msg}. Drop one, or have an optimiser redo it on top of the others.")
        sys.exit(1)
    ids = [c["id"] for c in cands]
    k = add_candidate(ws, cfg, combined, " + ".join(ids), "", base, "combine", kind="combination", members=ids,
                      allowed=sorted({f for c in cands for f in c.get("allowed_protected") or []}))
    st = load_state(ws)
    print(f"{k['id']}: {' + '.join(ids)} applied in that order. Measuring it against the unchanged project on every "
          f"benchmark: {fmt_duration(estimate(st, names))}.", flush=True)
    with timing_lock(cfg):
        ev = evaluate(ws, cfg, names, base, combined, k["id"], allowed=k["allowed_protected"])
    file = save_result(ws, k["id"], ev)
    k = record_evaluation(ws, k["id"], ev, file)
    st = load_state(ws)
    for name in names:
        rk = k["results"].get(name)
        parts = [(c["id"], c["results"].get(name)) for c in (get_cand(st, i) for i in ids)]
        parts = [(i, r) for i, r in parts if r and r.get("ratio") is not None]
        if not rk or rk.get("ratio") is None or not parts:
            continue
        b = st["benchmarks"][name]
        best_id, best = max(parts, key=lambda p: improvement(p[1], b))
        if improvement(rk, b) < improvement(best, b):
            print(f"note: on {name}, the combination's median gain ({words(rk['ratio'], b)}) is smaller than "
                  f"{best_id}'s alone ({words(best['ratio'], b)}): gains don't always add up. Consider landing "
                  f"{best_id} without the rest.")
    say_outcome(k, ws)
    sys.exit(0 if k["verdict"] == "faster" else 1)


def next_branch(top: Path) -> str:
    base = f"killer-perf/{kit.today()}"
    name, n = base, 2
    while kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{name}"):
        name, n = f"{base}-{n}", n + 1
    return name


def commit_message(c: dict, st: dict, tests_none: bool) -> str:
    lines = [c["title"].rstrip(".")]
    if c["mechanism"]:
        lines += ["", c["mechanism"]]
    measured = [(n, r) for n, r in c["results"].items() if r.get("ratio") is not None]
    if measured:
        n0 = measured[0][1]["n"][0]
        lines += ["", f"Measured against {short(c.get('measured_on') or c['base'])}, {n0} runs each, interleaved:"]
        lines += [f"- {r['line']}" for _, r in measured]
    lines += ["", "Benchmark output unchanged; the project has no tests to run." if tests_none
              else "The tests pass and the benchmark output is unchanged."]
    return "\n".join(lines) + "\n"


def cmd_land(a) -> None:
    ws, cfg, st = load(a)
    if not cfg["git"] or not cfg["base_rev"]:
        kit.fail("land needs a git repository with commits; this run can only report")
    names = list(st["benchmarks"])
    ensure_ready(st, names)
    cands = expand(st, a.ids)
    if not cands:
        kit.fail("name the accepted candidates to land, like land -w <run> C1 C3")
    for c in cands:
        if c["status"] != "accepted":
            kit.fail(f"{c['id']} is '{c['status']}', not accepted. Land takes candidates that came out faster and "
                     "survived a challenger (or that the user accepted with candidate set).")
    top, base = Path(cfg["repo"]), cfg["base_rev"]
    for c in cands:
        if (c.get("measured_on") or c["base"]) != base:
            kit.fail(f"{c['id']} was measured against {short(c.get('measured_on') or c['base'])}, not this run's "
                     f"base {short(base)}")
    tests_none = bool((st.get("tests") or {}).get("none"))
    msgs = {c["id"]: commit_message(c, st, tests_none) for c in cands}
    for c in cands:
        hits = attribution_hits(msgs[c["id"]])
        if not c.get("allow_mention"):
            hits += attribution_hits(added_lines(kit.read_patch(ws / c["patch"])))
        if hits:
            kit.fail(f"{c['id']}'s commit would mention {', '.join(sorted(set(hits)))}. Nothing in the project's "
                     "history may say who or what made it: fix the title or mechanism (candidate add again).")
    current = kit.git(top, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip() or None
    branch = a.branch or next_branch(top)
    if not kit.git_ok(top, "check-ref-format", "--branch", branch):
        kit.fail(f"{branch!r} isn't a valid branch name")
    exists = kit.git_ok(top, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")
    if exists:
        tip = kit.git(top, "rev-parse", f"refs/heads/{branch}").strip()
        if tip != base:
            kit.fail(f"{branch} is at {short(tip)}, not this run's base {short(base)}: these measurements don't "
                     "hold there. Start a run from it (init --rev " + branch + ").")
    combined, msg = stack(ws, cfg, cands, base)
    if combined is None:
        kit.fail(f"they don't apply together: {msg}")
    t = st.get("tests") or {}
    if len(cands) > 1 and not t.get("none"):   # each passed alone; together is another question
        print("Tests on the changes together, in a fresh copy, before anything is committed ...", flush=True)
        with timing_lock(cfg):
            res = kit.run_fresh(cfg, ws, t["cmd"], patch=combined, rev=base, timeout=t["timeout"],
                                setup=t.get("setup") or None, cwd=t.get("cwd"), hint="land-tests")
        if res.get("patch_error") or res.get("setup_failed") or kit.failed(res["runs"][0]):
            print(kit.summary(res))
            kit.fail("together they fail the tests, so nothing was committed. Combine them (perf.py combine) to "
                     "see which ones clash, and land the rest", code=1)
    on_current = exists and branch == current
    if on_current:
        dirty = [ln for ln in kit.git_status(top).splitlines() if ln and not ln.startswith("??")]
        if dirty:
            kit.fail(f"{branch} is checked out with uncommitted changes; commit or stash them first")
        workdir, worktree = top, None
    else:
        worktree = Path(cfg["sandboxes_home"]) / cfg["run_id"] / f"land-{secrets.token_hex(3)}"
        worktree.parent.mkdir(parents=True, exist_ok=True)
        args = ["worktree", "add", "-q"] + ([str(worktree), branch] if exists else ["-b", branch, str(worktree), base])
        r = subprocess.run(["git", "-C", str(top), *args], capture_output=True, text=True)
        if r.returncode:
            kit.fail(f"git worktree add: {r.stderr.strip()}")
        workdir = worktree
    commits = []
    ok = False
    try:
        for c in cands:
            patch = kit.read_patch(ws / c["patch"])
            done, msg = kit.apply_patch(workdir, patch)
            if not done:
                kit.fail(f"{c['id']} doesn't apply in {branch}: {msg}. If only line endings differ, this repository's "
                         "settings check files out differently from a plain clone: apply it by hand with git apply.")
            files = kit.changed_files(patch)
            for i in range(0, len(files), 100):
                kit.git(workdir, "add", "-A", "--", *files[i:i + 100])
            kit.git(workdir, "commit", "-q", "-F", "-", data=msgs[c["id"]])
            sha = kit.git(workdir, "rev-parse", "HEAD").strip()
            commits.append(f"{sha[:10]} {msgs[c['id']].splitlines()[0]}")
            print(f"Committed {c['id']} on {branch}: {commits[-1]}")
        ok = True
    finally:
        if worktree:
            subprocess.run(["git", "-C", str(top), "worktree", "remove", "--force", str(worktree)], capture_output=True)
            subprocess.run(["git", "-C", str(top), "worktree", "prune"], capture_output=True)
            kit.rmtree(worktree)
        if not ok and not exists:
            subprocess.run(["git", "-C", str(top), "branch", "-D", branch], capture_output=True)
    tip = kit.git(top, "rev-parse", f"refs/heads/{branch}").strip()
    rec = {"branch": branch, "base": base, "tip": tip, "commits": commits, "candidates": [c["id"] for c in cands],
           "on_current": on_current, "at": kit.now(), "final": None}
    mutate(ws, lambda s: s["landed"].append(rec))
    against = base
    if a.against:
        against = kit.git(top, "rev-parse", "--verify", a.against + "^{commit}").strip()
    st = load_state(ws)
    print(f"Final check: {branch} ({short(tip)}) against {short(against)} on every benchmark: "
          f"{fmt_duration(estimate(st, names))}.", flush=True)
    with timing_lock(cfg):
        ev = evaluate(ws, cfg, names, against, None, "final", cand_rev=tip)
    file = save_result(ws, "final", ev)
    final = {"verdict": ev["verdict"], "against": against, "results": compact(ev["results"], st["benchmarks"]),
             "gate": ev["gate"], "detail": ev.get("detail"), "machine": ev.get("machine"), "file": file}
    undone = None
    if ev["verdict"].startswith(REJECTED):
        undone = undo_land(top, branch, base, tip, exists, on_current)

    def apply(s):
        s["landed"][-1]["final"] = final
        s["landed"][-1]["undone"] = undone
    mutate(ws, apply)
    if undone:
        print(f"{branch}: the final check said {ev['verdict']}, so the commits were undone ({undone}). Nothing "
              "landed; say so in the report.")
        sys.exit(1)
    print(f"{branch}: {plural(len(commits), 'commit')}, nothing pushed. Final check: {ev['verdict']}.")
    if ev["verdict"] != "faster":
        print("The branch's head didn't confirm the gain against the original. Say so in the report; don't call "
              "it a speedup.")
    sys.exit(0 if ev["verdict"] == "faster" else 1)


def undo_land(top: Path, branch: str, base: str, tip: str, existed: bool, on_current: bool) -> str:
    """Take back the commits land made, and only those: the branch goes back to where it was."""
    if not existed:
        ok = kit.git_ok(top, "branch", "-D", branch)
        return f"deleted {branch}" if ok else f"couldn't delete {branch}: do it with git branch -D {branch}"
    if on_current:
        ok = kit.git_ok(top, "reset", "-q", "--keep", base)
    else:
        ok = kit.git_ok(top, "update-ref", f"refs/heads/{branch}", base, tip)
    return f"moved {branch} back to {short(base)}" if ok else \
        f"couldn't move {branch} back: do it with git reset --keep {short(base)} on that branch"


# ---------------------------------------------------------------- commands: books

def cand_summary(c: dict, benches: dict) -> str:
    bits = []
    for name, r in c["results"].items():
        if r.get("ratio") is None:
            continue
        b = benches[name]
        lo, hi = r["ci"]
        bits.append(f"{name} {words(r['ratio'], b)}, {ci_text(lo, hi, b)}")
    return "; ".join(bits)


def cmd_list(a) -> None:
    ws, cfg, st = load(a)
    t = st.get("tests")
    print("Tests: " + ("none" if t and t.get("none") else (show_cmd(t["cmd"]) if t else "not set")))
    print("\nBenchmarks:")
    for name, b in st["benchmarks"].items():
        n = b.get("noise")
        noise = (f"noise ±{pct_num(n['band'])}%, must beat {pct_num(n['threshold'])}%"
                 + (" (TOO NOISY)" if n["state"] != "clean" else "")) if n else "no noise test yet"
        took = fmt_secs(b["trial_secs"]) if b.get("trial_secs") else "untried"
        print(f"  {name:<12} {took:>9}  {b['repeat']} runs  {noise}  {show_cmd(b['cmd'])}")
    if not st["benchmarks"]:
        print("  none")
    print("\nHypotheses (biggest expected effect first):")
    for h in sorted(st["hypotheses"], key=hyp_rank):
        eff = f"~{h['effect_pct']:g}%" if h.get("effect_pct") else "  ?"
        print(f"  {h['id']:<4} {h['status']:<8} {eff:>6} {h['confidence']:<6} {h['title']}"
              + (f" -> {', '.join(h['candidates'])}" if h["candidates"] else ""))
    if not st["hypotheses"]:
        print("  none")
    print("\nCandidates:")
    for c in st["candidates"]:
        s = cand_summary(c, st["benchmarks"])
        print(f"  {c['id']:<4} {c['status']:<27} {c['title']}" + (f"  [{s}]" if s else ""))
    if not st["candidates"]:
        print("  none")


def cmd_status(a) -> None:
    ws, cfg, st = load(a)
    if not cfg["git"]:
        print("Not a git repository, so there's no status to compare.")
        return
    now, then = project_status(cfg) or "", cfg.get("status_at_init") or ""
    if now == then:
        print("The real project's working tree is as it was at init.")
        return
    a_, b_ = set(then.splitlines()), set(now.splitlines())
    print("The real project's working tree has changed since init:")
    for ln in sorted(b_ - a_):
        print(f"  now:      {ln}")
    for ln in sorted(a_ - b_):
        print(f"  was:      {ln}")
    print("If nobody meant to change it, find which step did, undo it, and say so in the report.")
    sys.exit(1)


def cmd_decide(a) -> None:
    ws, _cfg, _st = load(a)
    mutate(ws, lambda s: s["decisions"].append({"text": a.text, "at": kit.now()}))
    print(f"Recorded under decisions to check: {a.text}")


def cmd_wait(a) -> None:
    ws, _cfg, _st = load(a)
    mutate(ws, lambda s: s["waiting"].append({"text": a.text, "command": a.command, "at": kit.now()}))
    print(f"Recorded as waiting for the user's OK: {a.text}" + (f" ({a.command})" if a.command else ""))


def cmd_clean(a) -> None:
    ws, cfg, _st = load(a)
    n = kit.clean_sandboxes(ws, cfg)
    if cfg["git"]:
        subprocess.run(["git", "-C", cfg["repo"], "worktree", "prune"], capture_output=True)
    mutate(ws, lambda s: s.__setitem__("boxes", {}))
    print(f"Deleted {plural(n, 'sandbox', 'sandboxes')}.")


# ---------------------------------------------------------------- report

def md(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def final_table(results: dict, benches: dict) -> list[str]:
    rows = ["| Benchmark | Before | After | Change | 95% CI | p | Runs each |", "|---|---|---|---|---|---|---|"]
    for name, r in results.items():
        b = benches[name]
        if r.get("ratio") is None:
            rows.append(f"| {name} | | | {md(r['verdict'])} | | | |")
            continue
        lo, hi = r["ci"]
        change = words(r["ratio"], b) if r["verdict"] in ("faster", "slower") else f"no clear change ({words(r['ratio'], b)})"
        rows.append(f"| {name} | {fmt_value(r['before'], b)} | {fmt_value(r['after'], b)} | {change} | "
                    f"{ci_text(lo, hi, b)[7:]} | {fmt_p(r['p'])[2:].strip().lstrip('=').strip()} | {r['n'][0]} |")
    return rows


def cand_block(c: dict, st: dict, ws: Path, landed_in: dict) -> list[str]:
    head = f"### {c['id']}. {c['title']} · {c['status']}"
    out = [head, ""]
    if c["members"]:
        out.append(f"- **Combines:** {', '.join(c['members'])}, applied in that order")
    if c.get("mechanism"):
        out.append(f"- **Why it's faster (the optimiser's account):** {c['mechanism']}")
    if c.get("hypothesis"):
        out.append(f"- **From:** {c['hypothesis']}, {get_hyp(st, c['hypothesis'])['title']}")
    out.append(f"- **Files:** {', '.join(f'`{f}`' for f in c['files'])}; the patch is `{c['patch']}`")
    for name, r in c["results"].items():
        out.append(f"- **Measured:** {r['line']}")
    g = c.get("gate") or {}
    if g.get("tests"):
        out.append("- **Tests:** " + ("passed with the change" if g["tests"].get("passed") else "FAILED with the change"))
    elif c.get("verdict") and not c["verdict"].startswith(REJECTED) and (st.get("tests") or {}).get("none"):
        out.append("- **Tests:** none: the project has no test command, so only the output check guarded behaviour")
    shown_in_line = any(c.get("detail") and c["detail"].strip().splitlines()[0] in r.get("line", "")
                        for r in c["results"].values())
    if c.get("detail") and c["status"].startswith(REJECTED) and not shown_in_line:
        detail = c["detail"].strip().splitlines()
        out += ["- **Why rejected:**", "", "  ```", *("  " + ln for ln in detail[-12:]), "  ```"]
    ch = c.get("challenge")
    if ch:
        what = {"stands": "found no behaviour change the tests miss", "question": "raised a question",
                "proven": "proved a behaviour change"}[ch["outcome"]]
        out.append(f"- **Challenger:** {what}" + (f": {ch['question']}" if ch.get("question") else ""))
        if ch.get("command"):
            out.append(f"  - Reproduction `{show_cmd(ch['command'])}` rerun in fresh copies: {ch['proof']}"
                       + (f"; files in `{ch['scratch']}/`" if ch.get("scratch") else ""))
    if c["id"] in landed_in:
        out.append(f"- **Landed:** {landed_in[c['id']]}")
    out.append("")
    return out


def cmd_report(a) -> None:
    ws, cfg, st = load(a)
    benches, cands = st["benchmarks"], st["candidates"]
    singles = [c for c in cands if c["kind"] == "single"]
    accepted = [c for c in singles if c["status"] == "accepted"]
    unchallenged = [c for c in singles if c["status"] == "faster"]
    questions = [c for c in singles if c["status"] in ("question", "mixed")]
    rejected = [c for c in singles if c["status"].startswith(REJECTED)]
    no_gain = [c for c in singles if c["status"] == "no gain"]
    unmeasured = [c for c in singles if c["status"] == "new"]
    combos = [c for c in cands if c["kind"] == "combination"]
    kept = [x for x in st["landed"] if not x.get("undone")]
    landed = kept[-1] if kept else None
    partial_undo = "; ".join(f"a landing on {x['branch']} failed its final check ({x['final']['verdict']}) and was "
                             f"undone: {x['undone']}" for x in st["landed"] if x.get("undone")) or None
    final = (landed or {}).get("final")
    t = st.get("tests") or {}
    base_fail = [r for r in (t.get("baselines") or {}).values() if not r["passed"]]
    noisy = [n for n, b in benches.items() if (b.get("noise") or {}).get("state") == "too noisy"]
    clean = [n for n, b in benches.items() if (b.get("noise") or {}).get("state") == "clean"]
    first = next(iter(benches), None)
    goal = cfg.get("goal") or {"kind": "max"}

    # headline, status
    blocked = []
    if not benches:
        blocked.append("no benchmark was set up")
    elif not clean:
        blocked.append("no benchmark passed its noise test")
    if base_fail:
        blocked.append("the tests fail on the unchanged project")
    if not t:
        blocked.append("no test command was set")
    partial = []
    if unchallenged:
        partial.append(f"{plural(len(unchallenged), 'gain')} not challenged yet")
    if questions:
        partial.append(f"{plural(len(questions), 'question')} for you")
    if unmeasured:
        partial.append(f"{plural(len(unmeasured), 'candidate')} not measured")
    if final and final["verdict"] != "faster":
        partial.append("the landed branch didn't confirm the gain")
    if partial_undo:
        partial.append(partial_undo)
    if cfg.get("after") == "commit" and accepted and not landed and goal["kind"] != "measure":
        partial.append("proven gains not landed")
    status = "blocked" if blocked else ("partial" if partial else "done")

    def bench_line(results: dict) -> str | None:
        r = results.get(first) if first else None
        return r["line"].rstrip(".") if r else None
    if final:
        head = bench_line(final["results"]) or f"final check {final['verdict']}"
        headline = f"{head}, on {landed['branch']} ({plural(len(landed['commits']), 'commit')})"
        if final["verdict"] != "faster":
            headline = f"Landed {plural(len(landed['commits']), 'commit')} on {landed['branch']}, but the final " \
                       f"check said {final['verdict']}: {head}"
    elif accepted:
        best = max(accepted, key=lambda c: improvement(c["results"].get(first) or {}, benches[first]) or -9)
        headline = f"{plural(len(accepted), 'proven speedup')}, not landed; the best, {best['id']}: " \
                   f"{bench_line(best['results'])}"
    elif blocked:
        headline = "Blocked: " + "; ".join(blocked)
    elif goal["kind"] == "measure":
        nb = benches.get(first, {}).get("noise") if first else None
        headline = (f"Measured {plural(len(benches), 'benchmark')}" + (
            f" ({first}: {fmt_value(nb['before'], benches[first])}, noise ±{pct_num(nb['band'])}%)" if nb else "")
            + f" and found {plural(len(st['hypotheses']), 'lead')}; nothing was changed")
    else:
        headline = f"No proven speedup: {plural(len(cands), 'candidate')} measured, {len(rejected)} rejected"

    m = (final or {}).get("machine") or next((c.get("machine") for c in reversed(cands) if c.get("machine")), None) \
        or next((b["noise"]["machine"] for b in benches.values() if b.get("noise")), None) or machine_info()
    landed_in = {cid: f"`{landed['commits'][i]}` on `{landed['branch']}`" for i, cid in enumerate(landed["candidates"])
                 if i < len(landed["commits"])} if landed else {}

    L = [f"# Performance report: {cfg['target'] or Path(cfg['repo']).name}", "",
         f"**Project:** `{cfg['repo']}`" + (f" at `{short(cfg['base_rev'])}`" if cfg.get("base_rev") else ""),
         f"**Result:** {headline}.",
         f"**Status:** {status}" + (f": {'; '.join(blocked or partial)}" if blocked or partial else ""),
         f"**Goal:** {goal_text(goal)}"]
    if goal["kind"] == "target" and first:
        src = (final or {}).get("results") or (max(accepted, key=lambda c: improvement(
            c["results"].get(first) or {}, benches[first]) or -9)["results"] if accepted else {})
        r = src.get(first)
        if r and r.get("ratio") is not None:
            lo, hi = r["ci"]
            worst = min(improvement({"ratio": lo}, benches[first]), improvement({"ratio": hi}, benches[first]))
            met = r["verdict"] == "faster" and worst * 100 >= goal["pct"]
            L[-1] += (f": {'reached' if met else 'not reached'} on {first} (the whole 95% CI is "
                      f"{'at or past' if met else 'not all past'} {goal['pct']:g}%)")
    L.append("")

    L += ["## Before and after", ""]
    if final:
        L += [f"`{landed['branch']}` (`{short(landed['tip'])}`) against `{short(final['against'])}`, measured after "
              "landing, on every benchmark:", ""] + final_table(final["results"], benches) + [""]
        g = final.get("gate") or {}
        if g.get("tests") is not None:
            L += [f"Tests on the branch: {'passed' if g['tests'].get('passed') else 'FAILED'}.", ""]
    elif [k for k in combos if k.get("results")]:
        k = [k for k in combos if k.get("results")][-1]
        L += [f"Nothing landed. The last combination, {k['id']} ({' + '.join(k['members'])}), against the unchanged "
              "project:", ""] + final_table(k["results"], benches) + [""]
    elif accepted:
        L += ["Nothing landed or combined. Each proven speedup, alone, is under Candidates.", ""]
    else:
        L += ["Nothing to show: no change proved faster.", ""]

    L += ["## Benchmarks", "", "| Benchmark | Command | Decided by | Runs each | One run | Noise band | A change must beat |",
          "|---|---|---|---|---|---|---|"]
    for name, b in benches.items():
        n = b.get("noise")
        what = ("wall time" if b["metric"] == "wall" else f"METRIC {b['metric']}") + \
               (", higher is better" if b["higher_better"] else "")
        L.append(f"| {name} | `{md(show_cmd(b['cmd']))}` | {what} | {b['repeat']} (+{b['warmup']} warm-up)"
                 + (f", each the mean of {b['inner']}" if b["inner"] > 1 else "") + f" | "
                 + (fmt_secs(b["trial_secs"]) if b.get("trial_secs") else "") + " | "
                 + (f"±{pct_num(n['band'])}%" + (" (too noisy)" if n["state"] != "clean" else "") if n else "not measured")
                 + " | " + (f"{pct_num(n['threshold'])}%" if n else "") + " |")
    L.append("")
    for name, b in benches.items():
        notes = []
        if not b["output_check"]:
            notes.append("its output isn't checked")
        if b["normalise"]:
            notes.append(f"its output check masks {', '.join(f'`{x}`' for x in b['normalise'])}")
        if (b.get("noise") or {}).get("biased"):
            notes.append("two identical copies measured differently in its noise test")
        if notes:
            L.append(f"- {name}: " + "; ".join(notes) + ".")
    if t.get("none"):
        L.append("- No test command: only the output check guarded behaviour.")
    elif t:
        L.append(f"- Tests: `{md(show_cmd(t['cmd']))}`" + (", which FAIL on the unchanged project" if base_fail else
                                                            ", passing on the unchanged project") + ".")
    L.append("")

    shown = [c for c in singles if c["status"] in ("accepted", "faster")]
    L += ["## Proven speedups", ""]
    if unchallenged:
        L += [f"{', '.join(c['id'] for c in unchallenged)} measured faster but no challenger has looked for "
              "behaviour changes the tests miss, so they aren't accepted.", ""]
    L += sum((cand_block(c, st, ws, landed_in) for c in shown), []) or ["None.", ""]
    if combos:
        L += ["## Combinations", "", "Accepted changes applied together and measured against the unchanged project. "
              "Gains don't always add up.", ""] + sum((cand_block(k, st, ws, landed_in) for k in combos), [])
    if questions:
        L += ["## Questions for you", "", "These measured faster, but only you can say whether what they change is "
              "acceptable. Nothing landed for them.", ""]
        L += sum((cand_block(c, st, ws, landed_in) for c in questions), [])
    if rejected:
        L += ["## Rejected, and why", ""] + sum((cand_block(c, st, ws, landed_in) for c in rejected), [])
    if no_gain or unmeasured:
        L += ["## No clear gain", ""]
        for c in no_gain + unmeasured:
            s = cand_summary(c, benches)
            L.append(f"- **{c['id']}. {c['title']}**: " + (f"{c['verdict']}: {s}." if c["verdict"] and s
                                                            else c["verdict"] or "not measured"))
        L.append("")
    open_h = sorted([h for h in st["hypotheses"] if h["status"] == "open"], key=hyp_rank)
    if open_h:
        L += ["## Leads not followed", "", "Finders suggested these, but no change was made and measured for them. "
              "Expected effects are the finders' guesses, not measurements.", ""]
        for h in open_h:
            expected = h["expected_effect"] or (f"about {h['effect_pct']:g}%" if h.get("effect_pct") else "")
            L.append(f"- **{h['id']}. {h['title']}** ({h['confidence']} confidence"
                     + (f", expected {expected}" if expected else "") + f"): {h['claim']}"
                     + (f" ({', '.join(h['where'])})" if h["where"] else ""))
        L.append("")
    dropped = [h for h in st["hypotheses"] if h["status"] == "dropped"]
    if dropped:
        L += ["## Leads dropped", ""] + [f"- **{h['id']}. {h['title']}**: " + ("; ".join(h["notes"]) or "dropped")
                                         for h in dropped] + [""]
    if st["decisions"]:
        L += ["## Decisions to check", ""] + [f"- {d['text']}" for d in st["decisions"]] + [""]
    if st["waiting"]:
        L += ["## Waiting for your OK", ""] + [f"- {w['text']}" + (f": `{w['command']}`" if w.get("command") else "")
                                               for w in st["waiting"]] + [""]
    L += ["## How it was measured", "",
          "- Each comparison ran the unchanged project and the candidate in two fresh copies, alternating A B B A A B "
          "... after warm-up runs, so a drift in the machine's speed hits both alike.",
          f"- The change is the ratio of medians, with a bootstrap 95% confidence interval ({RESAMPLES} resamples, "
          "seeded) and a Mann–Whitney U test (normal approximation with tie and continuity corrections).",
          "- A change counts as faster only when the whole interval clears the benchmark's threshold, max(2%, 1.5 × "
          "the noise band), and p < 0.01. The noise band comes from running the unchanged project against itself.",
          "- Before any timing, the tests ran in a fresh copy with the change, and every run's output (timings and "
          "METRIC lines masked) had to match the unchanged project's.",
          f"- Machine: {machine_line(m)}.",
          "- Measured on a desktop machine, not a dedicated benchmark host, so the numbers are relative: they compare "
          "the versions with each other on this machine, not what another machine will see.", "",
          "## Run details", "", f"Run folder: `{ws}`. Patches are in `candidates/`, raw samples in `results/`"
          + (", profiles in `profiles/`" if (ws / "profiles").is_dir() else "") + ".", ""]
    (ws / "report.md").write_text("\n".join(L), encoding="utf-8")

    numbers = {"benchmarks": len(benches), "hypotheses": len(st["hypotheses"]), "candidates": len(singles),
               "accepted": len(accepted), "rejected": len(rejected), "landed": len(landed["commits"]) if landed else 0}
    src = (final or {}).get("results") or {}
    if not src and accepted and first:
        best = max(accepted, key=lambda c: improvement(c["results"].get(first) or {}, benches[first]) or -9)
        src = best["results"]
        numbers["best_candidate"] = best["id"]
    for name, r in src.items():
        if r.get("ratio") is not None:
            b = benches[name]
            numbers[f"{name}_before"] = r["before"]
            numbers[f"{name}_after"] = r["after"]
            numbers[f"{name}_change_pct"] = round(improvement(r, b) * 100, 2)
            numbers[f"{name}_ci_pct"] = sorted(round(improvement({"ratio": x}, b) * 100, 2) for x in r["ci"])
            numbers[f"{name}_p"] = r["p"]
    for name, b in benches.items():
        if b.get("noise"):
            numbers[f"{name}_noise_pct"] = round(b["noise"]["band"] * 100, 2)
    summary = {"skill": "killer-perf", "status": status, "headline": headline, "report": str(ws / "report.md"),
               "branch": landed["branch"] if landed else None, "commits": landed["commits"] if landed else [],
               "decisions": [d["text"] for d in st["decisions"]],
               "waiting": [w["text"] + (f": {w['command']}" if w.get("command") else "") for w in st["waiting"]],
               "numbers": numbers}
    kit.save_json(ws / "summary.json", summary)
    print(f"Wrote {ws / 'report.md'} and summary.json.")
    print(f"killer-perf: {status} · {headline} · {ws / 'report.md'}")


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="perf.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def cmd(parent, name, func, help_, ws=True, takes_cmd=False):
        sp = parent.add_parser(name, help=help_, description=help_)
        if ws:
            sp.add_argument("--workspace", "-w", required=True, help="the run folder")
        sp.set_defaults(func=func, takes_cmd=takes_cmd)
        return sp

    s = cmd(sub, "init", cmd_init, "Create the run folder.", ws=False)
    s.add_argument("--repo", default=".", help="the project folder (default: here)")
    s.add_argument("--target", default="", help="what should get faster, in one line")
    s.add_argument("--rev", help="measure from this commit (default HEAD; uncommitted changes are left out)")
    s.add_argument("--scope", default="repo", help="what finders read: repo, path:<folder>, changes or branch[:<base>]")
    s.add_argument("--goal", default="max", help="max (default), measure (no code changes), or a target like 30%%")
    s.add_argument("--rigor", choices=list(RIGOR), default="standard", help="standard 15, thorough 40, quick 7 runs each")
    s.add_argument("--after", choices=["commit", "ask", "report"], default="commit",
                   help="what happens to proven gains (default: commit on a new local branch)")
    s.add_argument("--tests", help="the test command as one string (or use the tests command)")
    s.add_argument("--tests-timeout", type=float, default=1800)
    s.add_argument("--network", action="store_true", help="let sandboxes and benchmarks use the network")
    s.add_argument("--protect", nargs="*", help="more paths candidates may not change (benchmark inputs, say)")
    s.add_argument("--no-default-protect", action="store_true", help="don't protect tests, benchmarks and dependency files")
    s.add_argument("--include", nargs="*", help="ignored paths every sandbox needs, like node_modules or .venv")
    s.add_argument("--exclude", nargs="*", help="leave these out of scope")
    s.add_argument("--env", nargs="*", help="NAME=VALUE set in every sandbox")
    s.add_argument("--like", help="copy the benchmarks and test command from an earlier run folder")
    s.add_argument("--slug", help="short name for the run folder")
    s.add_argument("--workspace", "-w", help="use this run folder instead of the default")
    s.add_argument("--force", action="store_true", help="start over in an existing run folder")

    s = cmd(sub, "tests", cmd_tests, "Set the test command (after --) and run it once on the unchanged project; "
            "with no command, show it.", takes_cmd=True)
    s.add_argument("--none", action="store_true", help="the project has no tests: only the output check guards behaviour")
    s.add_argument("--why", help="with --none: why")
    s.add_argument("--setup", action="append", help="a command run before the tests, as one string (repeatable)")
    s.add_argument("--timeout", type=float, default=1800, help="seconds before the tests are killed (default 1800)")
    s.add_argument("--cwd", help="folder inside the project to run them from")

    bench = sub.add_parser("bench", help="Register benchmarks and measure their noise.").add_subparsers(
        dest="bench_command", required=True)
    s = cmd(bench, "add", cmd_bench_add, "Register a benchmark (its command after --) and try it twice.", takes_cmd=True)
    s.add_argument("name")
    s.add_argument("--warmup", type=int, default=1, help="warm-up runs per side, not counted (default 1)")
    s.add_argument("--repeat", type=int, help=f"timed runs per side (default from --rigor; at least {MIN_REPEAT})")
    s.add_argument("--inner", type=int, help="run it N times back to back per sample (set for you under 50 ms)")
    s.add_argument("--timeout", type=float, help="seconds per run before it's killed (default 10x a trial run, at least 60)")
    s.add_argument("--metric", help="the METRIC that decides, or wall (default: the one METRIC it prints, else wall)")
    s.add_argument("--lower-is-better", dest="higher_is_better", action="store_false", default=False)
    s.add_argument("--higher-is-better", dest="higher_is_better", action="store_true")
    s.add_argument("--no-output-check", action="store_true", help="don't compare its output (recorded as a decision)")
    s.add_argument("--why", help="with --no-output-check: why")
    s.add_argument("--output-normalise", action="append", help="a regex for output that legitimately varies (repeatable)")
    s.add_argument("--setup", action="append", help="a command run once per copy before timing, as one string")
    s.add_argument("--setup-timeout", type=float, default=1800)
    s.add_argument("--cwd", help="folder inside the project to run it from")
    s.add_argument("--protect", nargs="*", help="its inputs: paths candidates may not change")
    s.add_argument("--replace", action="store_true", help="replace a benchmark of that name")
    s.add_argument("--no-trial", action="store_true", help="don't try it now")
    s = cmd(bench, "noise", cmd_bench_noise, "Measure the unchanged project against itself.")
    s.add_argument("names", nargs="*", help="benchmarks (default: all)")
    s.add_argument("--repeat", type=int, help="change the benchmark's runs per side first")
    s.add_argument("--warmup", type=int, help="change the benchmark's warm-up runs first")

    s = cmd(sub, "profile", cmd_profile, "Run a benchmark once under Python's or Node.js's profiler.")
    s.add_argument("name")
    s.add_argument("--candidate", help="profile with this candidate applied")

    s = cmd(sub, "sandbox", cmd_sandbox, "Make an isolated copy of the project for one agent.")
    s.add_argument("name", help="a name for it, like find-io or opt-H3")
    s.add_argument("--candidate", help="apply this candidate in the copy (for a challenger)")
    s.add_argument("--give", nargs="*", help="files to copy into its scratch folder, like a profile")
    s.add_argument("--net", action="store_true", help="allow the network in this sandbox")

    s = cmd(sub, "collect", cmd_collect, "Add the hypotheses from a finder's reply.")
    s.add_argument("reply", nargs="?", help="a reply saved to a file (or use --sandbox)")
    s.add_argument("--sandbox", help="read reply.json from this sandbox, then delete it")
    s.add_argument("--source", help="who found them, like finder:io")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")

    s = cmd(sub, "hypothesis", cmd_hypothesis, "Show a hypothesis (claim, where, expected effect), or set its status.")
    s.add_argument("id")
    s.add_argument("--status", help="open, tried or dropped")
    s.add_argument("--note")

    cand = sub.add_parser("candidate", help="Save, show and settle candidate changes.").add_subparsers(
        dest="cand_command", required=True)
    s = cmd(cand, "add", cmd_candidate_add, "Save a change from an optimiser's sandbox or a patch file.")
    s.add_argument("--sandbox", help="the optimiser's sandbox: its diff is the change; deleted afterwards")
    s.add_argument("--patch", help="a patch file instead")
    s.add_argument("--hypothesis", help="the hypothesis it acts on, like H3")
    s.add_argument("--title", help="a commit subject (default: the optimiser's)")
    s.add_argument("--mechanism", help="why it's faster, for the commit body (default: the optimiser's)")
    s.add_argument("--source")
    s.add_argument("--allow-protected", action="store_true", help="let it change protected files (recorded)")
    s.add_argument("--allow-mention", action="store_true", help="the project itself mentions AI tools")
    s.add_argument("--force", action="store_true", help="keep it even though the optimiser said it isn't done")
    s.add_argument("--keep", action="store_true", help="keep the sandbox afterwards")
    s = cmd(cand, "show", cmd_candidate_show, "Print a candidate's claim and diff, for a challenger's brief.")
    s.add_argument("id")
    s.add_argument("--with-mechanism", action="store_true", help="include the optimiser's account (not for challengers)")
    s = cmd(cand, "set", cmd_candidate_set, "Record the user's answer about a candidate.")
    s.add_argument("id")
    s.add_argument("--status", required=True, help="accepted or rejected")
    s.add_argument("--reason", help="with rejected: a few words, like 'changes the default'")
    s.add_argument("--note")

    s = cmd(sub, "compare", cmd_compare, "Measure a candidate against the unchanged project, behind the correctness gate.")
    s.add_argument("names", nargs="*", help="benchmarks (default: all)")
    s.add_argument("--candidate", required=True, help="a candidate id, an optimiser's sandbox, or a patch file")
    s.add_argument("--base", help="compare against this commit instead of the candidate's base")

    s = cmd(sub, "challenge", cmd_challenge, "Record a challenger's reply, rerunning its reproduction.")
    s.add_argument("id")
    s.add_argument("reply", nargs="?", help="a reply saved to a file (or use --sandbox)")
    s.add_argument("--sandbox", help="read reply.json from this sandbox, then delete it")
    s.add_argument("--timeout", type=float, default=300)
    s.add_argument("--keep", action="store_true")

    s = cmd(sub, "combine", cmd_combine, "Apply candidates together, in order, and measure the combination.")
    s.add_argument("ids", nargs="+")

    s = cmd(sub, "land", cmd_land, "Commit accepted candidates on a local branch, then measure the branch.")
    s.add_argument("ids", nargs="+")
    s.add_argument("--branch", help="commit here (default: a new killer-perf/<date>); if it's checked out, on it")
    s.add_argument("--against", help="measure the branch against this commit (default: the run's base)")

    cmd(sub, "list", cmd_list, "Benchmarks, hypotheses and candidates so far.")
    cmd(sub, "status", cmd_status, "Has the real project's working tree changed since init?")
    s = cmd(sub, "decide", cmd_decide, "Record a call the user should check.")
    s.add_argument("text")
    s = cmd(sub, "wait", cmd_wait, "Record an outward action prepared but not done.")
    s.add_argument("text")
    s.add_argument("--command", help="the exact command that would do it")
    cmd(sub, "report", cmd_report, "Write report.md and summary.json.")
    cmd(sub, "clean", cmd_clean, "Delete this run's sandboxes.")
    return p


def main(argv=None) -> None:
    kit.utf8_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    after = None
    if "--" in argv:
        i = argv.index("--")
        argv, after = argv[:i], argv[i + 1:]
    p = build_parser()
    a = p.parse_args(argv)
    if after is not None and not a.takes_cmd:
        p.error(f"{a.command} takes no command after --")
    a.argv_cmd = after or []
    a.func(a)


if __name__ == "__main__":
    main()
