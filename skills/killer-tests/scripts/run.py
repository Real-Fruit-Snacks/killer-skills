#!/usr/bin/env python3
"""Run a command inside a killer skills sandbox.

A copy of this file sits in every sandbox, next to env.json. It runs the command
in the sandbox's copy of the project (repo/), with the sandbox's own home, temp
and app-data folders, no credentials, and (unless the sandbox allows it) no network:

  python run.py -- <command> [args...]
  python run.py --repeat 20 -- <command> [args...]   how often does it fail?
  python run.py --cwd sub/dir --timeout 120 -- <command> [args...]
  python run.py --stdin answers.txt -- <command> [args...]   feed it input

The command's exit code is the verdict: 0 means it behaved, anything else means it
failed. Lines starting EXPECTED: or OBSERVED: are repeated in the summary. Every
run's full output is saved under logs/. This exits 0 when every run passed and 1
when any failed or timed out.

The isolation is a set of environment variables, not a container. With the network
off, everything is sent through a proxy that doesn't exist: Python, Node.js, git,
curl, npm and pip honour that, but Windows PowerShell 5.1 and programs that open
sockets themselves don't. A program that asks the OS for the user's profile folder
directly ([Environment]::GetFolderPath('UserProfile'), say) finds the real one.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAIL = 30


def build_env(root: Path = HERE) -> dict:
    """The environment for commands in the sandbox at root."""
    spec = json.loads((root / "env.json").read_text(encoding="utf-8"))
    drop = {n.upper() for n in spec.get("drop", [])}
    pattern = re.compile(spec.get("drop_pattern") or r"(?!x)x", re.I)
    env = {k: v for k, v in os.environ.items() if k.upper() not in drop and not pattern.search(k)}
    for k, v in spec.get("set", {}).items():
        if os.name == "nt":   # names are case-insensitive there: keep one spelling
            for name in [n for n in env if n.upper() == k.upper()]:
                del env[name]
        env[k] = v
    first = [str(d) for d in spec.get("path_first", [])]
    if first:   # stand-ins (a gh that refuses, say) ahead of the real tools
        key = next((n for n in env if n.upper() == "PATH"), "PATH")
        env[key] = os.pathsep.join(first + ([env[key]] if env.get(key) else []))
    return env


def resolve(cmd: list[str], env: dict) -> list[str]:
    found = shutil.which(cmd[0], path=env.get("PATH") or env.get("Path"))
    # On Windows, `python` with no Python installed is the Microsoft Store's stand-in, which
    # exits 9009 instead of running anything. The Python running this script is a real one.
    stub = not found or "windowsapps" in found.replace("/", "\\").lower().split("\\")
    if os.name == "nt" and cmd[0].lower() in ("python", "python3", "python.exe", "python3.exe") and stub:
        found = sys.executable
    return [found or cmd[0], *cmd[1:]]


def kill_tree(p: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        p.kill()
    except OSError:
        pass


def run_once(cmd: list[str], cwd: Path, env: dict, timeout: float, stdin: Path | None = None) -> dict:
    extra = {"start_new_session": True} if os.name != "nt" else \
            {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    start = time.monotonic()
    try:
        feed = open(stdin, "rb") if stdin else None
    except OSError as e:
        return {"code": 127, "out": f"could not open the --stdin file {stdin}: {e}\n", "secs": 0.0,
                "timed_out": False}
    try:
        try:
            p = subprocess.Popen(resolve(cmd, env), cwd=str(cwd), env=env,
                                 stdin=feed if feed else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **extra)
        except OSError as e:
            return {"code": 127, "out": f"could not start {cmd[0]}: {e}\n", "secs": 0.0, "timed_out": False}
        try:
            out, _ = p.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            kill_tree(p)
            try:
                out, _ = p.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                out = b""
            timed_out = True
    finally:
        if feed:
            feed.close()
    text =(out or b"").decode("utf-8", errors="replace").replace("\r\n", "\n")
    return {"code": None if timed_out else p.returncode, "out": text,
            "secs": round(time.monotonic() - start, 2), "timed_out": timed_out}


def failed(r: dict) -> bool:
    return r["timed_out"] or r["code"] != 0


def marked(out: str) -> list[str]:
    return [ln.strip() for ln in out.splitlines() if re.match(r"\s*(EXPECTED|OBSERVED):", ln)]


def run_many(root: Path, cmd: list[str], repeat: int = 1, timeout: float = 300,
             cwd: str | None = None, log: bool = True, stdin: Path | None = None) -> dict:
    """Run cmd `repeat` times in the sandbox at root; returns the runs and where their logs are."""
    env = build_env(root)
    where = root / "repo" / cwd if cwd else root / "repo"
    if not where.is_dir():
        raise SystemExit(f"error: no folder {where}")
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    n = 1 + max([int(m.group(1)) for f in logs.glob("*.log")
                 if (m := re.match(r"(\d+)-", f.name))] or [0])
    runs = []
    for k in range(1, repeat + 1):
        r = run_once(cmd, where, env, timeout, stdin)
        if log:
            path = logs / f"{n:03d}-{k}.log"
            path.write_text(f"$ {' '.join(cmd)}\n# cwd {where}\n# exit {r['code']}"
                            f"{' (timed out)' if r['timed_out'] else ''}, {r['secs']} s\n\n{r['out']}",
                            encoding="utf-8")
            r["log"] = str(path)
        runs.append(r)
    return {"cmd": cmd, "cwd": str(where), "runs": runs}


def summary(res: dict) -> str:
    runs = res["runs"]
    bad = [i for i, r in enumerate(runs) if failed(r)]
    lines = [f"$ {' '.join(res['cmd'])}   (in {res['cwd']})"]
    if len(runs) == 1:
        r = runs[0]
        state = f"timed out after {r['secs']} s" if r["timed_out"] else f"exit {r['code']}, {r['secs']} s"
        lines.append(f"{'FAILED' if bad else 'passed'}: {state}" + (f", log {r['log']}" if r.get("log") else ""))
        show = r
    else:
        outs = sum(1 for r in runs if r["timed_out"])
        lines.append(f"{len(bad)} of {len(runs)} runs failed" + (f" ({outs} timed out)" if outs else ""))
        show = runs[bad[0]] if bad else runs[-1]
        if bad:
            lines.append(f"first failure: run {bad[0] + 1}, "
                         + ("timed out" if show["timed_out"] else f"exit {show['code']}")
                         + (f", log {show['log']}" if show.get("log") else ""))
    lines += marked(show["out"])
    tail = show["out"].rstrip("\n").splitlines()[-TAIL:]
    if tail:
        lines.append(f"--- last {len(tail)} lines ---")
        lines += tail
    return "\n".join(lines)


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="run.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repeat", type=int, default=1, help="run it this many times and count failures")
    p.add_argument("--timeout", type=float, default=300, help="seconds per run before it's killed (default 300)")
    p.add_argument("--cwd", help="folder inside the project copy to run from")
    p.add_argument("--stdin", help="a file to feed the command as its input (default: no input)")
    p.add_argument("cmd", nargs=argparse.REMAINDER, help="-- then the command")
    a = p.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        p.error("give the command after --")
    if a.repeat < 1:
        p.error("--repeat must be 1 or more")
    feed = Path(a.stdin).resolve() if a.stdin else None
    if feed and not feed.is_file():
        p.error(f'no file {feed} for --stdin')
    res = run_many(HERE, cmd, a.repeat, a.timeout, a.cwd, stdin=feed)
    print(summary(res))
    sys.exit(1 if any(failed(r) for r in res["runs"]) else 0)


if __name__ == "__main__":
    main()
