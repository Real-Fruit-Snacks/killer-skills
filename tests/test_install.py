"""Tests for install.sh and install.ps1 against a release served from this machine.

Run: python -I tests/test_install.py

Packs the skills with tools/package.py, adds small stand-ins for the packs (the
Windows runtime, Node.js and Python, and the font pack), serves it all over http on
127.0.0.1, and runs each installer this machine has: install.sh with sh, and
install.ps1 with Windows PowerShell and PowerShell 7, the way the README runs them
(piped to iex, and through [scriptblock]::Create with skill names). Then it runs each
one as a file from a folder of release files, with the network address pointing
nowhere, the way an offline install works. Every run gets its own cache folder
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
sys.path.insert(0, str(ROOT / "tools"))
import packs  # noqa: E402

RUNTIME_ZIP = "killer-skills-runtime-windows-x64.zip"
NODE_ZIP = "killer-skills-node-windows-x64.zip"
PYTHON_ZIP = "killer-skills-python-windows-x64.zip"
FONTS = "killer-skills-fonts"
PACK_DIRS = {"killer-skills", "killer-skills-fonts", "killer-skills-node", "killer-skills-python"}
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


def file_runners(folder):
    """The same installers, run as files from a folder: the offline way."""
    out = []
    sh = find_sh()
    if sh:
        out.append(("install.sh (sh)", lambda args, env: subprocess.run([sh, str(folder / "install.sh"), *args],
                                                                      capture_output=True, text=True, env=env)))
    for exe in ["powershell", "pwsh"]:
        path = shutil.which(exe)
        if not path or (exe == "powershell" and os.name != "nt"):
            continue
        out.append((f"install.ps1 ({exe})", lambda args, env, path=path: subprocess.run(
            [path, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(folder / "install.ps1"), *args],
            capture_output=True, text=True, env=env)))
    return out


def stand_in(folder, name, files):
    """A pack zip (and for the font pack a .tar.gz too) holding files, written the way tools/packs.py writes them."""
    src = Path(tempfile.mkdtemp())
    for rel, data in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(data if isinstance(data, bytes) else data.encode())
    packs.zipdir(src, folder / f"{name}.zip")
    if name == FONTS:
        packs.tgzdir(src, folder / f"{name}.tar.gz")
    shutil.rmtree(src)


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

    # Stand-ins for the packs: the same layout and manifests, a few bytes instead of 190 MB.
    stand_in(good, RUNTIME_ZIP[:-4], {
        "runtime.json": json.dumps({"platform": "windows-x64", "playwright": "0.0.0-test", "chromium_headless_shell": "1",
                                    "chrome": "0.0.0.1", "mermaid": "0.0.1"}),
        "node_modules/playwright/package.json": '{"name": "playwright", "version": "0.0.0-test"}',
        "ms-playwright/chromium_headless_shell-1/INSTALLATION_COMPLETE": "",
        "mermaid/mermaid.min.js": "// stand-in"})
    stand_in(good, NODE_ZIP[:-4], {"node.json": '{"platform": "windows-x64", "version": "0.0.1"}', "node.exe": "MZ stand-in"})
    stand_in(good, PYTHON_ZIP[:-4], {"python.json": '{"platform": "windows-x64", "version": "0.0.2"}', "python.exe": "MZ stand-in"})
    stand_in(good, FONTS, {"fonts.json": json.dumps({"count": 2, "families": []}, indent=2),
                           "alpha/alpha-latin-wght-normal.woff2": b"wOF2", "alpha/LICENSE": "OFL"})
    for f in ("install.sh", "install.ps1"):   # a release carries its installers, for installing from a folder
        shutil.copy(ROOT / f, good / f)
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
    dead = "http://127.0.0.1:9/nothing-here"   # where an offline install's network points

    # What a correct install looks like: the bundle unpacked.
    ref = tmp / "ref"
    with tarfile.open(good / "killer-skills.tar.gz") as t:
        t.extractall(ref, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(ref)
    want = tree(ref)
    check("no pack is offered as a skill", not any(s.startswith("killer-skills-") for s in skills), skills)

    def packs_in(c):
        return sorted(p.name for p in c.iterdir()) if c.exists() else []

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
        windows_packs = os.name == "nt" and not is_sh   # install.ps1 on Windows is the one that installs them
        fonts_file = f"{FONTS}.tar.gz" if is_sh else f"{FONTS}.zip"

        r = run([], env)
        out = r.stdout + r.stderr
        check(f"{label}: installs every skill", r.returncode == 0 and tree(dest) == want, out)
        check(f"{label}: says what it installed", all(f"installed {s}" in r.stdout for s in skills) and f"{len(skills)} skills in" in r.stdout, out)
        check(f"{label}: leaves no staging folders", dest.is_dir() and sorted(p.name for p in dest.iterdir()) == skills,
              sorted(p.name for p in dest.iterdir()) if dest.is_dir() else "no install folder")
        check(f"{label}: installs the font pack", (caches / FONTS / "fonts.json").exists() and "installed the font pack: 2 families" in r.stdout, out)
        check(f"{label}: records which font pack it installed",
              (caches / FONTS / "installed-sha256.txt").exists()
              and (caches / FONTS / "installed-sha256.txt").read_text().strip() == hashlib.sha256((good / fonts_file).read_bytes()).hexdigest())
        check(f"{label}: leaves no pack staging folders", set(packs_in(caches)) <= PACK_DIRS, packs_in(caches))
        rt = caches / "killer-skills"
        if windows_packs:
            check(f"{label}: installs the runtime on Windows", (rt / "runtime.json").exists() and "installed the runtime: Playwright 0.0.0-test" in r.stdout, out)
            check(f"{label}: records which runtime it installed",
                  (rt / "installed-sha256.txt").exists() and (rt / "installed-sha256.txt").read_text().strip() == hashlib.sha256((good / RUNTIME_ZIP).read_bytes()).hexdigest())
            if shutil.which("node", path=env.get("PATH")):
                check(f"{label}: skips Node.js when node is on the PATH",
                      "skipped Node.js: v" in r.stdout and not (caches / "killer-skills-node").exists(), out)
            else:
                check(f"{label}: installs Node.js when there's none", "installed Node.js: v0.0.1" in r.stdout, out)
            got_py = "skipped Python: Python 3." in r.stdout and not (caches / "killer-skills-python").exists()
            check(f"{label}: installs Python only when there's no Python 3.10+",
                  got_py or ("installed Python: 0.0.2" in r.stdout and (caches / "killer-skills-python" / "python.json").exists()), out)
        else:
            check(f"{label}: installs no Windows packs off Windows or from sh",
                  not rt.exists() and not (caches / "killer-skills-node").exists() and not (caches / "killer-skills-python").exists(), packs_in(caches))
            if is_sh and os.name == "nt":
                check(f"{label}: points Windows users at install.ps1 for the Windows packs", "install with install.ps1" in r.stdout, out)
        if r.returncode != 0 or tree(dest) != want:
            continue  # the rest builds on a good first install

        target = skills[-1]
        (dest / target / "stale.txt").write_text("left over from an old version")
        (dest / skills[0] / "keep.txt").write_text("another skill's file")
        before_hits = Quiet.hits[f"/good/{RUNTIME_ZIP}"], Quiet.hits[f"/good/{fonts_file}"]
        r = run([target], env)
        out = r.stdout + r.stderr
        check(f"{label}: installs one named skill", r.returncode == 0 and f"updated {target}" in r.stdout and "1 skill in" in r.stdout, out)
        check(f"{label}: replaces the skill whole", not (dest / target / "stale.txt").exists())
        check(f"{label}: leaves other skills alone", (dest / skills[0] / "keep.txt").exists())
        check(f"{label}: leaves no folder from the swap", sorted(p.name for p in dest.iterdir()) == skills, sorted(p.name for p in dest.iterdir()))
        check(f"{label}: doesn't download a font pack it already has",
              "the font pack already in" in r.stdout and Quiet.hits[f"/good/{fonts_file}"] == before_hits[1], out)
        if windows_packs:
            check(f"{label}: doesn't download a runtime it already has",
                  "the runtime already in" in r.stdout and Quiet.hits[f"/good/{RUNTIME_ZIP}"] == before_hits[0], out)

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

        def fresh(name, **extra):
            d, c = tmp / f"dest-{tag}-{name}", tmp / f"cache-{tag}-{name}"
            return d, c, dict(env, KILLER_SKILLS_DIR=p(d), KILLER_SKILLS_CACHE=p(c), **extra)

        d, c, e = fresh("council")
        r = run(["killer-council"], e)
        check(f"{label}: no runtime, Node.js or font pack for a skill that doesn't drive Chromium",
              r.returncode == 0 and not ({"killer-skills", "killer-skills-node", FONTS} & set(packs_in(c)))
              and "runtime" not in r.stdout and "font pack" not in r.stdout, r.stdout + r.stderr)

        d, c, e = fresh("skip", KILLER_SKILLS_RUNTIME="skip")
        r = run(["killer-design", "killer-council"], e)
        check(f"{label}: KILLER_SKILLS_RUNTIME=skip installs no packs", r.returncode == 0 and not packs_in(c), r.stdout + r.stderr)

        if not windows_packs:
            continue
        # ---- the Windows packs' other paths
        d, c, e = fresh("all", KILLER_SKILLS_RUNTIME="all")
        r = run(["killer-design"], e)
        out = r.stdout + r.stderr
        check(f"{label}: KILLER_SKILLS_RUNTIME=all installs Node.js even when it's here",
              r.returncode == 0 and "installed Node.js: v0.0.1" in r.stdout and (c / "killer-skills-node" / "node.exe").exists(), out)
        check(f"{label}: no Python for skills that don't need it", not (c / "killer-skills-python").exists(), packs_in(c))
        r = run(["killer-council"], e)
        check(f"{label}: KILLER_SKILLS_RUNTIME=all installs Python even when it's here",
              r.returncode == 0 and "installed Python: 0.0.2" in r.stdout and (c / "killer-skills-python" / "python.exe").exists(), r.stdout + r.stderr)

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
        check(f"{label}: leaves no runtime staging folder when it fails", set(packs_in(c)) <= PACK_DIRS, packs_in(c))

        d, c, e = fresh("update")
        (c / "killer-skills").mkdir(parents=True)
        (c / "killer-skills" / "installed-sha256.txt").write_text("0" * 64)
        (c / "killer-skills" / "old-file.txt").write_text("from an older runtime")
        r = run(["killer-design"], e)
        check(f"{label}: replaces an older runtime whole",
              r.returncode == 0 and "updated the runtime" in r.stdout and not (c / "killer-skills" / "old-file.txt").exists()
              and (c / "killer-skills" / "runtime.json").exists() and set(packs_in(c)) <= PACK_DIRS, r.stdout + r.stderr)

        d, c, e = fresh("inuse")
        (c / "killer-skills").mkdir(parents=True)
        (c / "killer-skills" / "installed-sha256.txt").write_text("0" * 64)
        held = open(c / "killer-skills" / "busy.txt", "w")   # a session using the runtime
        try:
            r = run(["killer-design"], e)
        finally:
            held.close()
        out = r.stdout + r.stderr
        check(f"{label}: stops with nothing changed when the runtime is in use",
              r.returncode != 0 and "is in use" in out and (c / "killer-skills" / "busy.txt").exists()
              and not (c / "killer-skills" / "runtime.json").exists() and set(packs_in(c)) <= PACK_DIRS, out)

    # ---- offline: each installer run as a file from a folder of release files
    for label, _ in found:
        is_sh = label.startswith("install.sh")
        tag = "local-" + label.split()[0].replace(".", "-") + "-" + label.split("(")[1].rstrip(")")
        p = (lambda x: x.as_posix()) if is_sh else str
        windows_packs = os.name == "nt" and not is_sh
        fonts_file = f"{FONTS}.tar.gz" if is_sh else f"{FONTS}.zip"
        folder = tmp / tag / "release files"   # a space, as in a real Downloads folder
        shutil.copytree(good, folder)
        run = dict(file_runners(folder))[label]
        dest, caches = tmp / tag / "dest", tmp / tag / "cache"
        env = dict(os.environ, KILLER_SKILLS_URL=dead, KILLER_SKILLS_DIR=p(dest), KILLER_SKILLS_CACHE=p(caches),
                   PLAYWRIGHT_BROWSERS_PATH=str(tmp / tag / "browsers"))
        env.pop("KILLER_SKILLS_RUNTIME", None)
        hits = sum(Quiet.hits.values())
        r = run([], env)
        out = r.stdout + r.stderr
        check(f"{label} from a folder: installs every skill with no network", r.returncode == 0 and tree(dest) == want, out)
        check(f"{label} from a folder: says where it installed from", "installing from" in r.stdout and "release files" in r.stdout, out)
        check(f"{label} from a folder: installs the font pack from it", (caches / FONTS / "fonts.json").exists(), packs_in(caches))
        if windows_packs:
            check(f"{label} from a folder: installs the runtime from it", (caches / "killer-skills" / "runtime.json").exists(), packs_in(caches))
        check(f"{label} from a folder: asks the network for nothing", sum(Quiet.hits.values()) == hits)

        r = run(["killer-design"], env)
        check(f"{label} from a folder: a second run copies no pack again", r.returncode == 0 and "the font pack already in" in r.stdout, r.stdout + r.stderr)

        # Only the skills downloaded: the packs are listed in SHA256SUMS.txt but not in the folder.
        (folder / fonts_file).unlink()
        (folder / RUNTIME_ZIP).unlink()
        d2, c2 = tmp / tag / "dest2", tmp / tag / "cache2"
        e2 = dict(env, KILLER_SKILLS_DIR=p(d2), KILLER_SKILLS_CACHE=p(c2))
        r = run(["killer-design"], e2)
        out = r.stdout + r.stderr
        check(f"{label} from a folder: installs the skills when the packs weren't downloaded",
              r.returncode == 0 and (d2 / "killer-design" / "SKILL.md").exists() and f"skipped the font pack: {fonts_file} isn't in" in r.stdout, out)
        if windows_packs:
            check(f"{label} from a folder: says the runtime wasn't in the folder", f"skipped the runtime: {RUNTIME_ZIP} isn't in" in r.stdout, out)

        (folder / f"{skills[0]}.zip").unlink()
        (folder / f"{skills[0]}.tar.gz").unlink()
        r = run([skills[0]], e2)
        out = r.stdout + r.stderr
        check(f"{label} from a folder: fails cleanly when a skill's archive isn't there", r.returncode != 0 and "isn't in" in out, out)

        # Beside some other project's SHA256SUMS.txt, the installer downloads as usual.
        other = tmp / tag / "other"
        other.mkdir()
        shutil.copy(ROOT / ("install.sh" if is_sh else "install.ps1"), other)
        (other / "SHA256SUMS.txt").write_text("0" * 64 + "  something-else.zip\n")
        run_other = dict(file_runners(other))[label]
        d3, c3 = tmp / tag / "dest3", tmp / tag / "cache3"
        hits = sum(Quiet.hits.values())
        r = run_other(["killer-council"], dict(env, KILLER_SKILLS_URL=f"{url}/good", KILLER_SKILLS_DIR=p(d3), KILLER_SKILLS_CACHE=p(c3)))
        out = r.stdout + r.stderr
        check(f"{label}: beside another project's SHA256SUMS.txt, downloads as usual",
              r.returncode == 0 and "installing from" not in r.stdout and (d3 / "killer-council" / "SKILL.md").exists()
              and sum(Quiet.hits.values()) > hits, out)
finally:
    if server:
        server.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
