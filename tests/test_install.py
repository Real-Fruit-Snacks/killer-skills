"""Tests for install.sh and install.ps1 against a release served from this machine.

Run: python -I tests/test_install.py

Packs the skills with tools/package.py, adds a small stand-in for the Windows
runtime, serves it all over http on 127.0.0.1, and runs each installer this
machine has: install.sh with sh, and install.ps1 with Windows PowerShell and
PowerShell 7, the way the README runs them (piped to iex, and through
[scriptblock]::Create with skill names). Every run gets its own cache folder
(KILLER_SKILLS_CACHE), so nothing touches the real ~/.cache.
"""
import collections
import functools
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ZIP = "killer-skills-runtime-windows-x64.zip"
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


class Quiet(http.server.SimpleHTTPRequestHandler):
    """Serves the release folders. /flaky-<n>/<file> answers 503 the first time, then serves good/<file>."""
    seen = set()
    hits = collections.Counter()
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def do_GET(self):
        with Quiet.lock:
            Quiet.hits[self.path] += 1
        m = re.match(r"^/flaky-\d+/(.*)$", self.path)
        if m:
            with Quiet.lock:
                first = self.path not in Quiet.seen
                Quiet.seen.add(self.path)
            if first:
                self.send_error(503)
                return
            self.path = "/good/" + m.group(1)
        super().do_GET()


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


def rewrite_sums(folder, change=None):
    """Recompute SHA256SUMS.txt over a release folder; change(name, hash) may return a different hash."""
    lines = []
    for f in sorted(p.name for p in folder.iterdir() if p.name != "SHA256SUMS.txt"):
        h = hashlib.sha256((folder / f).read_bytes()).hexdigest()
        if change:
            h = change(f, h)
        lines.append(f"{h}  {f}")
    (folder / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")


def flip(h):
    return ("0" if h[0] != "0" else "1") + h[1:]


tmp = Path(tempfile.mkdtemp())
server = None
try:
    good = tmp / "release" / "good"
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "package.py"), "--out", str(good)], capture_output=True, text=True)
    check("packs a release to serve", r.returncode == 0, r.stdout + r.stderr)
    skills = sorted(f[:-len(".zip")] for f in os.listdir(good) if f.endswith(".zip") and f != "killer-skills.zip")

    # A stand-in for the Windows runtime: the same layout and runtime.json, a few bytes instead of 125 MB.
    with zipfile.ZipFile(good / RUNTIME_ZIP, "w") as z:
        z.writestr("runtime.json", json.dumps({"platform": "windows-x64", "playwright": "0.0.0-test",
                                               "chromium_headless_shell": "1", "chrome": "0.0.0.1", "mermaid": "0.0.1"}))
        z.writestr("node_modules/playwright/package.json", '{"name": "playwright", "version": "0.0.0-test"}')
        z.writestr("ms-playwright/chromium_headless_shell-1/INSTALLATION_COMPLETE", "")
        z.writestr("mermaid/mermaid.min.js", "// stand-in")
    rewrite_sums(good)

    # The same release with one skill's checksum wrong, and one with the runtime's wrong.
    bad = tmp / "release" / "bad"
    shutil.copytree(good, bad)
    victim = skills[0]
    rewrite_sums(bad, lambda f, h: flip(h) if f in (f"{victim}.zip", f"{victim}.tar.gz") else h)
    bad_rt = tmp / "release" / "bad-runtime"
    shutil.copytree(good, bad_rt)
    rewrite_sums(bad_rt, lambda f, h: flip(h) if f == RUNTIME_ZIP else h)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(tmp / "release")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    # What a correct install looks like: the bundle unpacked.
    ref = tmp / "ref"
    with tarfile.open(good / "killer-skills.tar.gz") as t:
        t.extractall(ref, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(ref)
    want = tree(ref)
    check("the runtime isn't offered as a skill", "killer-skills-runtime-windows-x64" not in skills, skills)

    found = runners()
    check("at least one installer can run here", bool(found), "no sh, powershell or pwsh found")
    for label, run in found:
        tag = label.split()[0].replace(".", "-") + "-" + label.split("(")[1].rstrip(")")
        is_sh = label.startswith("install.sh")
        p = (lambda x: x.as_posix()) if is_sh else str
        dest, caches = tmp / f"dest-{tag}", tmp / f"cache-{tag}"
        env = dict(os.environ, KILLER_SKILLS_URL=f"{url}/good", KILLER_SKILLS_DIR=p(dest), KILLER_SKILLS_CACHE=p(caches),
                   PLAYWRIGHT_BROWSERS_PATH=str(tmp / f"browsers-{tag}"))
        env.pop("KILLER_SKILLS_RUNTIME", None)
        runtime_here = os.name == "nt" and not is_sh   # install.ps1 on Windows is the one that installs it

        r = run([], env)
        out = r.stdout + r.stderr
        check(f"{label}: installs every skill", r.returncode == 0 and tree(dest) == want, out)
        check(f"{label}: says what it installed", all(f"installed {s}" in r.stdout for s in skills) and f"{len(skills)} skills in" in r.stdout, out)
        check(f"{label}: leaves no staging folders", dest.is_dir() and sorted(p.name for p in dest.iterdir()) == skills,
              sorted(p.name for p in dest.iterdir()) if dest.is_dir() else "no install folder")
        rt = caches / "killer-skills"
        if runtime_here:
            check(f"{label}: installs the runtime on Windows", (rt / "runtime.json").exists() and "installed the runtime: Playwright 0.0.0-test" in r.stdout, out)
            check(f"{label}: records which runtime it installed",
                  (rt / "installed-sha256.txt").exists() and (rt / "installed-sha256.txt").read_text().strip() == hashlib.sha256((good / RUNTIME_ZIP).read_bytes()).hexdigest())
            check(f"{label}: leaves no runtime staging folder", sorted(p.name for p in caches.iterdir()) == ["killer-skills"], sorted(p.name for p in caches.iterdir()))
        else:
            check(f"{label}: installs no runtime off Windows or from sh", not rt.exists(), sorted(caches.iterdir()) if caches.exists() else "")
            if is_sh and os.name == "nt":
                check(f"{label}: points Windows users at install.ps1 for the runtime", "installs with install.ps1" in r.stdout, out)
        if r.returncode != 0 or tree(dest) != want:
            continue  # the rest builds on a good first install

        target = skills[-1]
        (dest / target / "stale.txt").write_text("left over from an old version")
        (dest / skills[0] / "keep.txt").write_text("another skill's file")
        before_hits = Quiet.hits[f"/good/{RUNTIME_ZIP}"]
        r = run([target], env)
        out = r.stdout + r.stderr
        check(f"{label}: installs one named skill", r.returncode == 0 and f"updated {target}" in r.stdout and "1 skill in" in r.stdout, out)
        check(f"{label}: replaces the skill whole", not (dest / target / "stale.txt").exists())
        check(f"{label}: leaves other skills alone", (dest / skills[0] / "keep.txt").exists())
        if runtime_here:
            check(f"{label}: doesn't download a runtime it already has",
                  "runtime already in" in r.stdout and Quiet.hits[f"/good/{RUNTIME_ZIP}"] == before_hits, out)

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
        started = time.monotonic()
        r = run([], env_none)
        took = time.monotonic() - started
        check(f"{label}: fails cleanly when the release isn't there", r.returncode != 0 and "could not download" in (r.stdout + r.stderr), r.stdout + r.stderr)
        check(f"{label}: doesn't retry a 404", took < 15, f"took {took:.1f}s")

        flaky_dest, flaky_cache = Path(str(dest) + "-flaky"), Path(str(caches) + "-flaky")
        env_flaky = dict(env, KILLER_SKILLS_URL=f"{url}/flaky-{abs(hash(label)) % 100000}", KILLER_SKILLS_DIR=p(flaky_dest), KILLER_SKILLS_CACHE=p(flaky_cache))
        r = run([], env_flaky)
        check(f"{label}: retries through a 503 on every file", r.returncode == 0 and tree(flaky_dest) == want, r.stdout + r.stderr)

        if not runtime_here:
            continue
        # ---- the runtime's other paths, on Windows
        def fresh(name, **extra):
            d, c = tmp / f"dest-{tag}-{name}", tmp / f"cache-{tag}-{name}"
            return d, c, dict(env, KILLER_SKILLS_DIR=str(d), KILLER_SKILLS_CACHE=str(c), **extra)

        d, c, e = fresh("council")
        r = run(["killer-council"], e)
        check(f"{label}: no runtime for a skill that doesn't drive Chromium",
              r.returncode == 0 and not (c / "killer-skills").exists() and "runtime" not in r.stdout, r.stdout + r.stderr)

        d, c, e = fresh("skip", KILLER_SKILLS_RUNTIME="skip")
        r = run(["killer-design"], e)
        check(f"{label}: KILLER_SKILLS_RUNTIME=skip skips it", r.returncode == 0 and not (c / "killer-skills").exists(), r.stdout + r.stderr)

        d, c, e = fresh("existing")
        core = c / "killer-design" / "node_modules"
        (core / "playwright").mkdir(parents=True)
        (core / "playwright" / "package.json").write_text('{"name": "playwright", "version": "9.9.9"}')
        (core / "playwright-core").mkdir()
        (core / "playwright-core" / "browsers.json").write_text(json.dumps({"browsers": [{"name": "chromium-headless-shell", "revision": "4242"}]}))
        browsers = Path(e["PLAYWRIGHT_BROWSERS_PATH"]) / "chromium_headless_shell-4242"
        browsers.mkdir(parents=True)
        (browsers / "INSTALLATION_COMPLETE").write_text("")
        r = run(["killer-repo"], e)
        check(f"{label}: skips the runtime when killer-design already has Playwright and its Chromium",
              r.returncode == 0 and not (c / "killer-skills").exists() and "skipped the runtime: Playwright 9.9.9" in r.stdout, r.stdout + r.stderr)
        shutil.rmtree(browsers.parent)

        d, c, e = fresh("badrt", KILLER_SKILLS_URL=f"{url}/bad-runtime")
        r = run(["killer-design"], e)
        out = r.stdout + r.stderr
        check(f"{label}: refuses a runtime that fails its checksum",
              r.returncode != 0 and f"{RUNTIME_ZIP} doesn't match SHA256SUMS.txt" in out and not (c / "killer-skills").exists(), out)
        check(f"{label}: leaves no runtime staging folder when it fails", not any(p.name.startswith(".killer-skills-runtime-") for p in c.iterdir()) if c.exists() else True)

        d, c, e = fresh("update")
        (c / "killer-skills").mkdir(parents=True)
        (c / "killer-skills" / "installed-sha256.txt").write_text("0" * 64)
        (c / "killer-skills" / "old-file.txt").write_text("from an older runtime")
        r = run(["killer-design"], e)
        check(f"{label}: replaces an older runtime whole",
              r.returncode == 0 and "updated the runtime" in r.stdout and not (c / "killer-skills" / "old-file.txt").exists()
              and (c / "killer-skills" / "runtime.json").exists(), r.stdout + r.stderr)
finally:
    if server:
        server.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
