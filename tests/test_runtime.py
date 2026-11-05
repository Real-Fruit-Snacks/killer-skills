"""The Windows runtime, end to end: every Chromium script in the skills, with the network cut off.

Run: python -I tests/test_runtime.py <killer-skills-runtime-windows-x64.zip>

Unpacks the runtime into a scratch cache (KILLER_SKILLS_CACHE), then blocks every
way the scripts could download: npm runs offline, Playwright's download host and
the HTTP(S) proxy point at a closed port, and Node's fetch is told to use that
proxy. Then it runs shoot.mjs, icons.mjs, render.mjs, card.mjs and frames.mjs from
a folder with no node_modules. They must all work, from the runtime alone, and the
README render must fall back to the approximate renderer by itself and draw its
Mermaid diagram from the runtime. Windows only: the runtime is built for Windows.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "skills" / "killer-design" / "scripts"
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
if len(sys.argv) < 2 or not Path(sys.argv[1]).is_file():
    sys.exit("usage: python -I tests/test_runtime.py <killer-skills-runtime-windows-x64.zip>")

tmp = Path(tempfile.mkdtemp())
try:
    caches = tmp / "cache"
    with zipfile.ZipFile(sys.argv[1]) as z:
        z.extractall(caches / "killer-skills")
    info = json.loads((caches / "killer-skills" / "runtime.json").read_text())
    print(f"runtime: Playwright {info['playwright']}, Chrome {info['chrome']}, Mermaid {info['mermaid']}")

    dead = "http://127.0.0.1:9"
    env = dict(os.environ, KILLER_SKILLS_CACHE=str(caches), npm_config_offline="true",
               PLAYWRIGHT_DOWNLOAD_HOST=dead, HTTPS_PROXY=dead, HTTP_PROXY=dead, https_proxy=dead, http_proxy=dead,
               NODE_USE_ENV_PROXY="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    env.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    work = tmp / "work"
    work.mkdir()

    def node(script, *args):
        r = subprocess.run(["node", str(script), *map(str, args)], cwd=work, env=env, capture_output=True, text=True, timeout=600)
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

    check("nothing was installed into the skills' own caches",
          not (caches / "killer-design").exists() and not (caches / "killer-repo").exists(),
          sorted(p.name for p in caches.iterdir()))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
