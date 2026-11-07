"""Tests for killer-design's fonts.mjs against a small stand-in font pack.

Run: python -I tests/test_fonts.py

Builds a pack with one variable family and one static family in a scratch cache
(KILLER_SKILLS_CACHE), then lists it and adds families to a scratch site: the files and
licences copied, fonts.css with one rule per file and URLs relative to it, --weights,
--subsets and --no-italic, adding a family again, and the errors for a family that
isn't there and for a pack that isn't installed. The real pack is checked by
tests/test_runtime.py, which renders a page with it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "skills" / "killer-design" / "scripts" / "fonts.mjs"
LATIN = "U+0000-00FF,U+2000-206F"
EXT = "U+0100-02BA,U+1E00-1EFF"
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    "))


def files_for(fid, variable, weights=(400,)):
    out = []
    for subset, rng in (("latin", LATIN), ("latin-ext", EXT)):
        for style in ("normal", "italic"):
            if variable:
                out.append({"file": f"{fid}-{subset}-wght-{style}.woff2", "subset": subset, "style": style, "unicodeRange": rng})
            else:
                out += [{"file": f"{fid}-{subset}-{w}-{style}.woff2", "subset": subset, "style": style, "weight": w, "unicodeRange": rng}
                        for w in weights]
    return out


tmp = Path(tempfile.mkdtemp())
try:
    pack = tmp / "cache" / "killer-skills-fonts"
    families = [
        {"family": "Alpha Serif", "id": "alpha-serif", "category": "serif", "license": "OFL-1.1", "source": "@fontsource-variable/alpha-serif@1.0.0",
         "axes": {"opsz": [6, 72], "wdth": [75, 100], "wght": [200, 800]}, "styles": ["normal", "italic"], "files": files_for("alpha-serif", True)},
        {"family": "Beta Sans", "id": "beta-sans", "category": "sans-serif", "license": "OFL-1.1", "source": "@fontsource/beta-sans@1.0.0",
         "weights": [400, 700], "styles": ["normal", "italic"], "files": files_for("beta-sans", False, (400, 700))},
    ]
    for f in families:
        (pack / f["id"]).mkdir(parents=True)
        (pack / f["id"] / "LICENSE").write_text(f"licence for {f['family']}")
        for x in f["files"]:
            (pack / f["id"] / x["file"]).write_bytes(b"wOF2" + x["file"].encode())
    (pack / "fonts.json").write_text(json.dumps({"count": 2, "subsets": ["latin", "latin-ext"], "families": families}))
    env = dict(os.environ, KILLER_SKILLS_CACHE=str(tmp / "cache"))

    def fonts(*args, e=env):
        r = subprocess.run(["node", str(FONTS), *map(str, args)], capture_output=True, text=True, env=e)
        return r, r.stdout + r.stderr

    r, out = fonts("list")
    check("list shows every family", r.returncode == 0 and "Alpha Serif" in out and "Beta Sans" in out and "2 of 2 families" in out, out)
    check("list shows axes and weights", "opsz 6-72" in out and "wght 200-800" in out and "weights 400 700" in out, out)
    r, out = fonts("list", "serif")
    check("list filters", "Alpha Serif" in out and "Beta Sans" not in out, out)

    site = tmp / "site"
    site.mkdir()
    r, out = fonts("add", site, "Alpha Serif", "beta-sans", "--weights", "700")
    fdir = site / "assets" / "fonts"
    css = (fdir / "fonts.css").read_text() if (fdir / "fonts.css").exists() else ""
    check("add copies the files and licences", r.returncode == 0
          and sorted(p.name for p in (fdir / "alpha-serif").iterdir()) == sorted([x["file"] for x in families[0]["files"]] + ["LICENSE"])
          and sorted(p.name for p in (fdir / "beta-sans").iterdir()) == sorted([x["file"] for x in families[1]["files"] if x["weight"] == 700] + ["LICENSE"]), out)
    rules = re.findall(r"@font-face \{(.*?)\}", css, re.S)
    check("fonts.css has one rule per file", len(rules) == 4 + 4, len(rules))
    check("fonts.css: variable ranges and the stretch axis",
          'font-family: "Alpha Serif"' in css and "font-weight: 200 800;" in css and "font-stretch: 75% 100%;" in css, css[:800])
    check("fonts.css: static weights, swap, woff2 and unicode-range",
          "font-weight: 700;" in css and "font-weight: 400;" not in css and css.count("font-display: swap;") == 8
          and f"unicode-range: {LATIN};" in css and f"unicode-range: {EXT};" in css and 'format("woff2")' in css, css)
    check("fonts.css: URLs relative to fonts.css", 'url("alpha-serif/alpha-serif-latin-wght-normal.woff2")' in css
          and all((fdir / u).exists() for u in re.findall(r'url\("([^"]+)"\)', css)), re.findall(r'url\("([^"]+)"\)', css))
    check("add prints the link and a preload", '<link rel="stylesheet" href="assets/fonts/fonts.css">' in out
          and 'href="assets/fonts/alpha-serif/alpha-serif-latin-wght-normal.woff2" as="font"' in out, out)
    check("add says --weights doesn't apply to a variable family", "doesn't apply" in out, out)

    r, out = fonts("add", site, "Beta Sans", "--subsets", "latin", "--no-italic")
    css = (fdir / "fonts.css").read_text()
    beta = sorted(p.name for p in (fdir / "beta-sans").iterdir())
    check("adding a family again replaces only it", r.returncode == 0 and css.count('font-family: "Beta Sans"') == 2
          and css.count('font-family: "Alpha Serif"') == 4 and beta == ["LICENSE", "beta-sans-latin-400-normal.woff2", "beta-sans-latin-700-normal.woff2"], (beta, css))
    check("an unasked-for weight says to use --weights", "--weights copies only" in out, out)

    def tree(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}

    before = tree(site)
    # Each run would copy a different file set for the family named first, so a partial add shows.
    r, out = fonts("add", site, "Alpha Serif", "Beta Sans", "--weights", "500", "--no-italic")
    check("refuses a weight the family doesn't have", r.returncode != 0 and "has no weight 500" in out and "400 700" in out, out)
    check("a refused add changes nothing, even for the families before it", tree(site) == before, sorted(set(tree(site)) ^ set(before)))
    r, out = fonts("add", site, "Beta Sans", "Alpha")
    check("an unknown family fails and suggests close ones", r.returncode != 0 and 'no family called "Alpha"' in out and "Alpha Serif" in out, out)
    check("an unknown family changes nothing", tree(site) == before, sorted(set(tree(site)) ^ set(before)))
    fresh = tmp / "fresh"
    fresh.mkdir()
    r, out = fonts("add", fresh, "Alpha Serif", "Beta Sans", "--weights", "500")
    check("a refused add on a new site leaves no assets folder", r.returncode != 0 and not any(fresh.iterdir()), [p.name for p in fresh.iterdir()])

    r, out = fonts("list", e=dict(env, KILLER_SKILLS_CACHE=str(tmp / "empty")))
    check("a missing pack says how to get it", r.returncode != 0 and "isn't installed" in out and "installer" in out and "Google Fonts" in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
