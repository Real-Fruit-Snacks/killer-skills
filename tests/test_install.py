"""Tests for install.sh and install.ps1 against a release served from this machine.

Run: python -I tests/test_install.py

Packs the skills with tools/package.py, serves them over http on 127.0.0.1,
and runs each installer this machine has: install.sh with sh, and install.ps1
with Windows PowerShell and PowerShell 7, the way the README runs them
(piped to iex, and through [scriptblock]::Create with skill names).
"""
import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def find_sh():
    if os.name == "nt":  # Git for Windows' sh, not WSL's bash
        for p in [r"C:\Program Files\Git\usr\bin\sh.exe", r"C:\Program Files\Git\bin\sh.exe"]:
            if os.path.exists(p):
                return p
        return None
    return shutil.which("sh")


def runners():
    """(label, run(args, env) -> CompletedProcess) for every installer this machine can run."""
    out = []
    sh = find_sh()
    if sh:
        def run_sh(args, env):
            return subprocess.run([sh, str(ROOT / "install.sh"), *args], capture_output=True, text=True, env=env)
        out.append(("install.sh (sh)", run_sh))
    ps1 = str(ROOT / "install.ps1").replace("'", "''")
    for exe in ["powershell", "pwsh"]:
        path = shutil.which(exe)
        if not path or (exe == "powershell" and os.name != "nt"):
            continue

        def run_ps(args, env, path=path):
            if args:
                names = " ".join(f"'{a}'" for a in args)
                cmd = f"& ([scriptblock]::Create((Get-Content -Raw -LiteralPath '{ps1}'))) {names}"
            else:
                cmd = f"Get-Content -Raw -LiteralPath '{ps1}' | iex"
            return subprocess.run([path, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                                  capture_output=True, text=True, env=env)
        out.append((f"install.ps1 ({exe})", run_ps))
    return out


def tree(d):
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in Path(d).rglob("*") if p.is_file()}


tmp = Path(tempfile.mkdtemp())
server = None
try:
    good = tmp / "release" / "good"
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "package.py"), "--out", str(good)], capture_output=True, text=True)
    check("packs a release to serve", r.returncode == 0, r.stdout + r.stderr)
    skills = sorted(f[:-len(".zip")] for f in os.listdir(good) if f.endswith(".zip") and f != "killer-skills.zip")

    # The same release with one wrong checksum, to prove nothing is replaced on a mismatch.
    bad = tmp / "release" / "bad"
    shutil.copytree(good, bad)
    victim = skills[0]
    lines = []
    for line in (bad / "SHA256SUMS.txt").read_text().splitlines():
        h, f = line.split("  ", 1)
        if f in (f"{victim}.zip", f"{victim}.tar.gz"):
            h = ("0" if h[0] != "0" else "1") + h[1:]
        lines.append(f"{h}  {f}")
    (bad / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(tmp / "release")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    # What a correct install looks like: the bundle unpacked.
    import tarfile
    ref = tmp / "ref"
    with tarfile.open(good / "killer-skills.tar.gz") as t:
        t.extractall(ref, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(ref)
    want = tree(ref)

    found = runners()
    check("at least one installer can run here", bool(found), "no sh, powershell or pwsh found")
    for label, run in found:
        dest = tmp / ("dest-" + label.split()[0].replace(".", "-") + "-" + label.split("(")[1].rstrip(")"))
        env = dict(os.environ, KILLER_SKILLS_URL=f"{url}/good", KILLER_SKILLS_DIR=dest.as_posix() if "sh" in label.split()[0] else str(dest))

        r = run([], env)
        out = r.stdout + r.stderr
        check(f"{label}: installs every skill", r.returncode == 0 and tree(dest) == want, out)
        check(f"{label}: says what it installed", all(f"installed {s}" in r.stdout for s in skills) and f"{len(skills)} skills in" in r.stdout, out)
        check(f"{label}: leaves no staging folders", dest.is_dir() and sorted(p.name for p in dest.iterdir()) == skills,
              sorted(p.name for p in dest.iterdir()) if dest.is_dir() else "no install folder")
        if r.returncode != 0 or tree(dest) != want:
            continue  # the rest builds on a good first install

        target = skills[-1]
        (dest / target / "stale.txt").write_text("left over from an old version")
        (dest / skills[0] / "keep.txt").write_text("another skill's file")
        r = run([target], env)
        out = r.stdout + r.stderr
        check(f"{label}: installs one named skill", r.returncode == 0 and f"updated {target}" in r.stdout and "1 skill in" in r.stdout, out)
        check(f"{label}: replaces the skill whole", not (dest / target / "stale.txt").exists())
        check(f"{label}: leaves other skills alone", (dest / skills[0] / "keep.txt").exists())

        r = run(["nope"], env)
        out = r.stdout + r.stderr
        check(f"{label}: refuses an unknown skill", r.returncode != 0 and "no skill called nope" in out and skills[0] in out, out)

        r = run(["../evil"], env)
        out = r.stdout + r.stderr
        check(f"{label}: refuses a path for a name", r.returncode != 0 and "isn't a skill name" in out, out)

        env_bad = dict(env, KILLER_SKILLS_URL=f"{url}/bad")
        before = tree(dest / victim)
        r = run([victim], env_bad)
        out = r.stdout + r.stderr
        check(f"{label}: refuses an archive that fails its checksum", r.returncode != 0 and "doesn't match SHA256SUMS.txt" in out, out)
        check(f"{label}: changes nothing when a checksum fails", tree(dest / victim) == before)

        env_none = dict(env, KILLER_SKILLS_URL=f"{url}/missing")
        r = run([], env_none)
        check(f"{label}: fails cleanly when the release isn't there", r.returncode != 0 and "could not download" in (r.stdout + r.stderr), r.stdout + r.stderr)
finally:
    if server:
        server.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
