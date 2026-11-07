"""The Windows packs, end to end: every Chromium script in the skills, with the network cut off.

Run: python -I tests/test_runtime.py <killer-skills-runtime-windows-x64.zip>
         [--node <killer-skills-node-windows-x64.zip>] [--python <killer-skills-python-windows-x64.zip>]
         [--fonts <killer-skills-fonts.zip>]

With --node, every script runs on the packed Node.js instead of the one on the PATH.
With --python, the tests of every skill whose scripts are Python run on the packed Python. With --fonts, it
checks every family in the font pack, adds two of them to a page with fonts.mjs and
screenshots it, and every face must load from the page's own folder.

Unpacks the runtime into a scratch cache (KILLER_SKILLS_CACHE), then blocks every
way the scripts could download: npm runs offline, Playwright's download host and
the HTTP(S) proxy point at a closed port, and Node's fetch is told to use that
proxy. Then it runs shoot.mjs, icons.mjs, render.mjs, card.mjs and frames.mjs from
a folder with no node_modules. They must all work, from the runtime alone, and the
README render must fall back to the approximate renderer by itself and draw its
Mermaid diagram from the runtime. Windows only: the runtime is built for Windows.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "skills" / "killer-design" / "scripts"
# The tests of every skill whose scripts are Python, run on the packed Python.
PYTHON_TESTS = [("killer-council's", "test_council.py"), ("the shared kit's", "test_kit.py"),
                ("killer-bugs'", "test_bugs.py"), ("killer-tests'", "test_tests.py"), ("killer-docs'", "test_docs.py"),
                ("killer-perf's", "test_perf.py"), ("killer-release's", "test_release.py"),
                ("killer-upgrade's", "test_upgrade.py"), ("killer-goal's", "test_goal.py")]
REPO = ROOT / "skills" / "killer-repo" / "scripts"
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    ")[-3000:])


if os.name != "nt":
    print("SKIP the runtime is built for Windows only")
    sys.exit(0)
ap = argparse.ArgumentParser()
ap.add_argument("runtime")
ap.add_argument("--node")
ap.add_argument("--python")
ap.add_argument("--fonts")
a = ap.parse_args()
for f in (a.runtime, a.node, a.python, a.fonts):
    if f and not Path(f).is_file():
        sys.exit(f"no file {f}")
PINS = json.loads((ROOT / "tools" / "runtime.json").read_text(encoding="utf-8"))

tmp = Path(tempfile.mkdtemp())
try:
    caches = tmp / "cache"

    def unpack(zip_path, folder):
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(caches / folder)
        return caches / folder

    unpack(a.runtime, "killer-skills")
    info = json.loads((caches / "killer-skills" / "runtime.json").read_text())
    print(f"runtime: Playwright {info['playwright']}, Chrome {info['chrome']}, Mermaid {info['mermaid']}")
    NODE = "node"
    if a.node:
        NODE = str(unpack(a.node, "killer-skills-node") / "node.exe")
        v = subprocess.run([NODE, "--version"], capture_output=True, text=True).stdout.strip()
        check("the Node.js pack runs, at the pinned version", v == "v" + PINS["node"]["version"], v)

    dead = "http://127.0.0.1:9"
    env = dict(os.environ, KILLER_SKILLS_CACHE=str(caches), npm_config_offline="true",
               PLAYWRIGHT_DOWNLOAD_HOST=dead, HTTPS_PROXY=dead, HTTP_PROXY=dead, https_proxy=dead, http_proxy=dead,
               NODE_USE_ENV_PROXY="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    env.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    work = tmp / "work"
    work.mkdir()

    def node(script, *args):
        r = subprocess.run([NODE, str(script), *map(str, args)], cwd=work, env=env, capture_output=True, text=True, timeout=600)
        return r, r.stdout + r.stderr

    def no_download(out):
        return not any(s in out for s in ("installing it once", "Downloading Chromium", "downloading Chromium"))

    # killer-design: screenshots of this repo's page, then an icon audit.
    r, out = node(DESIGN / "shoot.mjs", ROOT / "docs", "--widths", "390", "--slices", "1", "--no-full", "--out", tmp / "shoot")
    check("shoot.mjs screenshots a page from the runtime", r.returncode == 0 and (tmp / "shoot" / "docs-390-01.png").exists(), out)
    check("shoot.mjs downloads nothing", no_download(out), out)

    r, out = node(DESIGN / "icons.mjs", "audit", ROOT / "docs", "--out", tmp / "icons")
    check("icons.mjs renders its preview sheet from the runtime", r.returncode == 0 and any((tmp / "icons").rglob("*.png")), out)
    check("icons.mjs downloads nothing", no_download(out), out)

    # killer-repo: a README with a diagram, with GitHub out of reach.
    readme = tmp / "sample" / "README.md"
    readme.parent.mkdir()
    readme.write_text("# Sample\n\n> [!NOTE]\n> Offline.\n\n```mermaid\ngraph LR\n  A --> B\n```\n", encoding="utf-8")
    r, out = node(REPO / "render.mjs", readme, "--out", tmp / "render", "--only", "desktop-light", "--wait", "1500")
    report = json.loads((tmp / "render" / "report.json").read_text(encoding="utf-8")) if (tmp / "render" / "report.json").exists() else {}
    v = report.get("variants", {}).get("desktop-light", {})
    check("render.mjs renders with GitHub out of reach", r.returncode == 0 and (tmp / "render" / "sample-desktop-light-first.png").exists(), out)
    check("render.mjs falls back to the approximate render by itself", report.get("renderer") == "approximate" and "approximate render" in out, out)
    check("render.mjs draws Mermaid from the runtime", report.get("mermaid") == "runtime" and not v.get("mermaidError")
          and any(x.get("name") == "svg" for x in v.get("first", {}).get("visuals", [])), json.dumps(v)[:1500])
    check("render.mjs requests nothing outside this machine", not v.get("failedRequests"), v.get("failedRequests"))
    check("render.mjs downloads nothing", no_download(out), out)

    card = tmp / "card.html"
    card.write_text("<!doctype html><body style='margin:0;background:#111;color:#eee;font:64px system-ui'>card</body>", encoding="utf-8")
    r, out = node(REPO / "card.mjs", card, "--out", tmp / "card.png")
    check("card.mjs renders a card from the runtime", r.returncode == 0 and (tmp / "card.png").exists(), out)

    shots = sorted((tmp / "shoot").glob("*.png"))[:1] + [tmp / "card.png"]
    r, out = node(REPO / "frames.mjs", *shots, "--out", tmp / "frames.svg")
    check("frames.mjs builds an animated SVG from the runtime", r.returncode == 0 and (tmp / "frames.svg").exists(), out)

    if a.fonts:
        fonts = unpack(a.fonts, "killer-skills-fonts")
        index = json.loads((fonts / "fonts.json").read_text(encoding="utf-8"))
        pinned = [f["family"] for f in json.loads((ROOT / "tools" / "fonts.json").read_text(encoding="utf-8"))["families"]]
        check("the font pack has every pinned family", sorted(f["family"] for f in index["families"]) == sorted(pinned) and index["count"] == len(pinned),
              set(pinned) ^ {f["family"] for f in index["families"]})
        bad = [f"{f['id']}/{x['file']}" for f in index["families"] for x in f["files"]
               if not (fonts / f["id"] / x["file"]).is_file() or (fonts / f["id"] / x["file"]).read_bytes()[:4] != b"wOF2"]
        check("every file in the font pack is a woff2 it lists", not bad, bad[:10])
        check("every family has its licence", all((fonts / f["id"] / "LICENSE").is_file() for f in index["families"]))
        check("every family has latin files with a unicode-range",
              all(any(x["subset"] == "latin" and x.get("unicodeRange") for x in f["files"]) for f in index["families"]))

        site = tmp / "fontsite"
        site.mkdir()
        r, out = node(DESIGN / "fonts.mjs", "add", site, "Newsreader", "IBM Plex Mono", "--weights", "400,600")
        check("fonts.mjs adds a variable and a static family from the pack", r.returncode == 0 and (site / "assets" / "fonts" / "fonts.css").exists(), out)
        (site / "index.html").write_text("""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Fonts</title>
<link rel="stylesheet" href="assets/fonts/fonts.css">
<style>body{margin:2rem;font:1.25rem/1.5 "Newsreader",serif}h1{font-size:4rem;font-weight:700}em{font-style:italic}
code{font:600 1rem "IBM Plex Mono",monospace}</style></head>
<body><main><h1>Self-hosted</h1><p>Body text in Newsreader, <em>with its real italic</em>, and
<code>IBM Plex Mono</code> for code.</p></main></body></html>""", encoding="utf-8")
        r, out = node(DESIGN / "shoot.mjs", site, "--widths", "390", "--slices", "1", "--no-full", "--out", tmp / "fontshot")
        rep = json.loads((tmp / "fontshot" / "report.json").read_text(encoding="utf-8")) if (tmp / "fontshot" / "report.json").exists() else {}
        seen = {f["family"]: f["status"] for run in rep.get("runs", []) for f in run.get("checks", {}).get("fonts", [])}
        check("a page using the pack's fonts loads every face", r.returncode == 0 and seen.get("Newsreader") == "web font loaded"
              and seen.get("IBM Plex Mono") == "web font loaded" and not re.search(r"FAIL.*font", out), (seen, out[-1500:]))
        check("the page requests nothing outside its folder", not any(run.get("failedRequests") for run in rep.get("runs", [])),
              [run.get("failedRequests") for run in rep.get("runs", [])])

    # killer-slides: its own tests, browser checks included, on the runtime (and the packed Node.js)
    slides_env = dict(env)
    if a.node:
        slides_env["PATH"] = str(Path(NODE).parent) + os.pathsep + env.get("PATH", "")
    r = subprocess.run([sys.executable, "-I", str(ROOT / "tests" / "test_slides.py")], env=slides_env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    check("killer-slides' tests pass on the runtime, browser checks included",
          r.returncode == 0 and "ALL PASSED" in r.stdout and "SKIP browser checks" not in r.stdout,
          (r.stdout + r.stderr)[-3000:])

    if a.python:
        py = unpack(a.python, "killer-skills-python") / "python.exe"
        v = subprocess.run([str(py), "-I", "-c", "import sys; print(sys.version.split()[0])"], capture_output=True, text=True).stdout.strip()
        check("the Python pack runs, at the pinned version", v == PINS["python"]["version"], v)
        for who, test in PYTHON_TESTS:
            if not (ROOT / "tests" / test).exists():
                continue
            r = subprocess.run([str(py), "-I", str(ROOT / "tests" / test)], capture_output=True, text=True, timeout=900, env=env)
            check(f"{who} tests pass on the Python pack", r.returncode == 0 and "ALL PASSED" in r.stdout, (r.stdout + r.stderr)[-2000:])

    check("nothing was installed into the skills' own caches",
          not (caches / "killer-design").exists() and not (caches / "killer-repo").exists(),
          sorted(p.name for p in caches.iterdir()))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
