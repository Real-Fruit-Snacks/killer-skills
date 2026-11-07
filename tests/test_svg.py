"""Scenario tests for killer-svg's svg.mjs. Run: python -I tests/test_svg.py

Without node, everything is skipped. The XML parser and every lint rule, the PNG codec, the pixel
comparison, the contrast measure, number rounding, minify's text transforms, the motion sample times
and the project key (against the Python kit's) run in Node with no browser. So do run folders, the
books (decide, wait, report) and land, in a scratch git repository reached through the temp path
exactly as tempfile gives it. The browser checks need Playwright with Chromium, found the way
svg.mjs finds it (this project, ~/.cache/killer-skills, ~/.cache/killer-design, ~/.cache/killer-svg);
without it they print SKIP. Nothing is ever installed: KILLER_SVG_NO_INSTALL is set.

The browser checks: a clean icon passes, with Firefox and WebKit reported "not checked" when their
builds can't be found; an outside font, a script, a mark that vanishes on dark and an animation
without reduced-motion handling each give exactly their FAIL; minify keeps the renders identical and
saves bytes, and refuses a change that moves the render; github catches a file that loads an image
beside it and one that loads from the network; motion tells gated from ungated animation; export
writes transparent PNGs of the right size; the blind viewer round from images to verdicts, with a
quote the viewer never wrote, a quote about the wrong graphic and a size the viewer couldn't read;
and the report's status. When this machine has Playwright's Firefox or WebKit, the engines are
compared for real, including a file WebKit draws wrong.
"""
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "killer-svg"
SVG = SKILL / "scripts" / "svg.mjs"
NODE = shutil.which("node")
failures = []
started = time.time()


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    ")[-3000:])


if not NODE:
    print("SKIP node isn't installed: every killer-svg check needs it")
    sys.exit(0)

tmp = Path(tempfile.mkdtemp())   # as tempfile gives it: an 8.3 short path on some Windows machines
(tmp / "no-browsers").mkdir()
ENV = dict(os.environ, KILLER_SVG_HOME=str(tmp / "runs"), KILLER_SVG_SANDBOXES=str(tmp / "boxes"), KILLER_SVG_NO_INSTALL="1",
           KILLER_SVG_BROWSERS=str(tmp / "no-browsers"), PYTHONIOENCODING="utf-8")
SVG_URL = SVG.resolve().as_uri()


def svg(*args, ok=(0,), env=None, cwd=None):
    r = subprocess.run([NODE, str(SVG), *map(str, args)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env or ENV, cwd=cwd or tmp, timeout=300)
    out = r.stdout + r.stderr
    if r.returncode not in ok:
        raise AssertionError(f"exit {r.returncode} for svg.mjs {' '.join(map(str, args))}:\n{out}")
    return out, r.returncode


def node_script(name, body, *args):
    f = tmp / name
    f.write_text(body, encoding="utf-8")
    r = subprocess.run([NODE, str(f), *map(str, args)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV, cwd=tmp, timeout=300)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def g(cwd, *args, ok=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout


def new_repo(path):
    path.mkdir(parents=True)
    g(path, "init", "-q", "-b", "main")
    for k, v in (("user.name", "Test Person"), ("user.email", "test@example.com"), ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        g(path, "config", k, v)
    (path / "README.md").write_text("# project\n")
    g(path, "add", "-A")
    g(path, "commit", "-q", "-m", "first")
    return path


def run_folder(out):
    return Path(re.search(r"Run folder: (.+)", out).group(1).strip())


def same_path(a, b):
    """A path a script printed against one the test made: either spelling, short or resolved."""
    return Path(a) == Path(b) or Path(a).resolve() == Path(b).resolve()


def png_size(path):
    b = Path(path).read_bytes()
    return struct.unpack(">II", b[16:24])


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def fails_of(ws):
    d = json.loads((ws / "check.json").read_text(encoding="utf-8"))
    return {Path(f["file"]).name: sorted({(x["check"], x["rule"]) for x in f["fails"]}, key=str) for f in d["files"]}, d


NS = 'xmlns="http://www.w3.org/2000/svg"'
CLEAN = (f'<svg {NS} viewBox="0 0 24 24" fill="none" stroke="#1d2b3a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" role="img">'
         '<title>Search</title><style>@media (prefers-color-scheme: dark){circle,path{stroke:#e8edf2}}</style>'
         '<circle cx="11" cy="11" r="7"/><path d="m20 20-4.35-4.35"/></svg>\n')
INKSCAPE = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!-- Created with Inkscape (http://www.inkscape.org/) -->
<svg
   xmlns:dc="http://purl.org/dc/elements/1.1/"
   xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
   xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
   xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
   xmlns="http://www.w3.org/2000/svg"
   xmlns:xlink="http://www.w3.org/1999/xlink"
   viewBox="0 0 24 24"
   inkscape:version="1.3"
   sodipodi:docname="mark.svg">
  <title>Mark</title>
  <metadata><rdf:RDF><dc:creator>Someone</dc:creator></rdf:RDF></metadata>
  <sodipodi:namedview id="nv" inkscape:zoom="8.0"/>
  <g inkscape:label="Layer 1" inkscape:groupmode="layer">
    <path d="M 4.000123 4.000456 L 20.0004567 4.0001234 L 12.00012345 20.00034567 Z" fill="#2a6f4f"/>
    <circle cx="12.0000001" cy="10.6666667" r="2.3333333" fill="#ffffff"/>
  </g>
</svg>
"""

try:
    # ---------- the script parses, and says what it does
    r = subprocess.run([NODE, "--check", str(SVG)], capture_output=True, text=True)
    check("node --check svg.mjs", r.returncode == 0, r.stderr)
    out, _ = svg("--help")
    check("--help lists every command", all(c in out for c in ("init --project", "add -w", "lint <svg>", "render -w", "engines -w", "contrast -w", "github <svg>",
                                                                 "motion <svg>", "minify <svg>", "export <svg>", "check -w", "sheet -w", "audience -w",
                                                                 "heard -w", "landed -w", "land -w", "decide -w", "wait -w", "report -w")), out)
    out, code = svg("frobnicate", ok=(2,))
    check("an unknown command exits 2 with the command list", code == 2 and "Commands" in out, out)

    # ---------- the XML parser and every lint rule, in Node with no browser
    fx = {
        "clean": CLEAN,
        "unclosed": f'<svg {NS} viewBox="0 0 1 1"><title>t</title>\n<g>\n</svg>',
        "bare_amp": f'<svg {NS} viewBox="0 0 1 1"><title>Fish & chips</title></svg>',
        "dup_attr": f'<svg {NS} viewBox="0 0 1 1" viewBox="0 0 2 2"><title>t</title></svg>',
        "unquoted": f'<svg {NS} viewBox=0><title>t</title></svg>',
        "prefix": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><use xlink:href="#a"/></svg>',
        "ext_entity": f'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg {NS} viewBox="0 0 1 1"><title>&x;</title></svg>',
        "illustrator": '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" [\n<!ENTITY ns_svg "http://www.w3.org/2000/svg">\n]>\n'
                       '<svg xmlns="&ns_svg;" viewBox="0 0 10 10"><title>t</title><rect width="10" height="10"/></svg>',
        "after_root": f'<svg {NS} viewBox="0 0 1 1"><title>t</title></svg><p/>',
        "no_ns": '<svg viewBox="0 0 10 10"><title>t</title></svg>',
        "not_svg": f'<html {NS}/>',
        "no_viewbox_sized": f'<svg {NS} width="24" height="24"><title>t</title></svg>',
        "no_viewbox": f'<svg {NS}><title>t</title></svg>',
        "bad_viewbox": f'<svg {NS} viewBox="0 0 0 10"><title>t</title></svg>',
        "script": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><script>alert(1)</script></svg>',
        "onload": f'<svg {NS} viewBox="0 0 1 1" onload="go()"><title>t</title></svg>',
        "js_link": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><a href="javascript:go()"><rect width="1" height="1"/></a></svg>',
        "ext_image": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><image href="https://example.com/a.png" width="1" height="1"/></svg>',
        "ext_use": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><use href="other.svg#a"/></svg>',
        "ext_import": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><style>@import url(https://fonts.example.com/x.css);</style></svg>',
        "ext_url": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><rect width="1" height="1" style="fill:url(file:///c/x.svg#g)"/></svg>',
        "link_ok": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><a href="https://example.com"><rect width="1" height="1"/></a></svg>',
        "foreign": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><foreignObject width="1" height="1"><div xmlns="http://www.w3.org/1999/xhtml">hi</div></foreignObject></svg>',
        "font_named": f'<svg {NS} viewBox="0 0 40 10"><title>t</title><text y="8" font-family="Inter, sans-serif">Acme</text></svg>',
        "font_rule": f'<svg {NS} viewBox="0 0 40 10"><title>t</title><style>.label text{{font-family:"Inter"}}</style><g class="label"><text y="8">Acme</text></g></svg>',
        "font_rule_elsewhere": f'<svg {NS} viewBox="0 0 40 10"><title>t</title><style>.other text{{font-family:"Inter"}}</style><g class="label"><text y="8" font-family="serif">Acme</text></g></svg>',
        "font_embedded": f'<svg {NS} viewBox="0 0 40 10"><title>t</title><style>@font-face{{font-family:Mark;src:url(data:font/woff2;base64,AAAA) format("woff2")}}text{{font:700 8px Mark, sans-serif}}</style><text y="8">Acme</text></svg>',
        "font_generic": f'<svg {NS} viewBox="0 0 40 10"><title>t</title><text y="8" style="font-family: sans-serif">Acme</text></svg>',
        "font_none": f'<svg {NS} viewBox="0 0 40 10"><title>t</title><text y="8">Acme</text></svg>',
        "dup_id": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><g id="a"/><g id="a"/></svg>',
        "missing_ref": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><rect width="1" height="1" fill="url(#glow)"/></svg>',
        "css_anim": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><style>@keyframes s{{to{{opacity:0}}}}rect{{animation:s 1s infinite}}</style><rect width="1" height="1"/></svg>',
        "css_anim_gated": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><style>@keyframes s{{to{{opacity:0}}}}@media (prefers-reduced-motion: no-preference){{rect{{animation:s 1s infinite}}}}</style><rect width="1" height="1"/></svg>',
        "smil": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><rect width="1" height="1"><animate attributeName="x" from="0" to="1" dur="1s" repeatCount="indefinite"/></rect></svg>',
        "metadata": INKSCAPE,
        "decimals": f'<svg {NS} viewBox="0 0 24 24"><title>t</title><path d="M1.23456 2.34567L20.123456 4.987654Z"/></svg>',
        "big_icon": f'<svg {NS} viewBox="0 0 24 24"><title>t</title><path d="{"M1 1L2 2 " * 300}"/></svg>',
        "no_title": f'<svg {NS} viewBox="0 0 1 1"><rect width="1" height="1"/></svg>',
        "decorative": f'<svg {NS} viewBox="0 0 1 1" aria-hidden="true"><rect width="1" height="1"/></svg>',
        "raster": f'<svg {NS} viewBox="0 0 1 1"><title>t</title><image href="data:image/png;base64,iVBORw0KGgo=" width="1" height="1"/></svg>',
    }
    (tmp / "lintfx.json").write_text(json.dumps(fx), encoding="utf-8")
    out, err, code = node_script("lint.mjs", f"""
import {{ lintSvg }} from '{SVG_URL}';
import fs from 'node:fs';
const fx = JSON.parse(fs.readFileSync('lintfx.json', 'utf8'));
const kinds = {{ font_generic: 'logo', big_icon: 'icon' }};
const res = {{}};
for (const [k, v] of Object.entries(fx)) {{
  const r = lintSvg(v, {{ kind: kinds[k] || null }});
  res[k] = {{ issues: r.issues.filter((i) => i.level !== 'INFO').map((i) => [i.level, i.rule, i.message, i.line]), animated: r.animated, ok: r.ok }};
}}
res.foreign_github = lintSvg(fx.foreign, {{ target: 'github' }}).issues.filter((i) => i.rule === 'foreign-object').map((i) => i.level);
res.diagram = lintSvg(fx.clean, {{ kind: 'diagram' }}).issues.filter((i) => i.rule === 'title').map((i) => i.message);
console.log(JSON.stringify(res));
""")
    L = json.loads(out) if code == 0 and out.startswith("{") else {}
    rules = lambda k, lvl=None: sorted({(i[0], i[1]) for i in L.get(k, {}).get("issues", []) if lvl is None or i[0] == lvl})
    msg = lambda k: " | ".join(i[2] for i in L.get(k, {}).get("issues", []))
    check("lint: a clean icon has nothing to report", L.get("clean", {}).get("issues") == [] and L["clean"]["ok"], (out, err))
    check("lint: a tag closed out of order fails with where the open one started", rules("unclosed") == [("FAIL", "xml")] and "</svg> closes <g> from line 2" in msg("unclosed"), msg("unclosed"))
    check("lint: a bare &, a repeated or unquoted attribute and an undeclared prefix each fail as XML",
          all(rules(k) == [("FAIL", "xml")] for k in ("bare_amp", "dup_attr", "unquoted", "prefix", "after_root"))
          and "&amp;" in msg("bare_amp") and "appears twice" in msg("dup_attr") and "xmlns:xlink" in msg("prefix"), [msg(k) for k in ("bare_amp", "dup_attr", "unquoted", "prefix")])
    check("lint: an external entity is refused, and an Illustrator DOCTYPE's own entities expand",
          rules("ext_entity") == [("FAIL", "xml")] and "outside file" in msg("ext_entity")
          and ("FAIL", "xml") not in rules("illustrator") and ("FAIL", "root") not in rules("illustrator"), (msg("ext_entity"), msg("illustrator")))
    check("lint: no SVG namespace, or a root that isn't <svg>, fails", rules("no_ns", "FAIL") == [("FAIL", "root")] and rules("not_svg") == [("FAIL", "root")], (msg("no_ns"), msg("not_svg")))
    check("lint: no viewBox fails, suggesting one from the fixed size; a zero-width viewBox fails",
          rules("no_viewbox_sized", "FAIL") == [("FAIL", "viewbox")] and 'viewBox="0 0 24 24"' in msg("no_viewbox_sized")
          and rules("no_viewbox", "FAIL") == [("FAIL", "viewbox")] and rules("bad_viewbox", "FAIL") == [("FAIL", "viewbox")], msg("no_viewbox_sized"))
    check("lint: <script>, an on... handler and a javascript: link each fail",
          all(rules(k, "FAIL") == [("FAIL", "script")] for k in ("script", "onload", "js_link")), [msg(k) for k in ("script", "onload", "js_link")])
    check("lint: an image from the web, a <use> of another file, @import and a file: url() each fail; a plain link doesn't",
          all(rules(k, "FAIL") == [("FAIL", "external")] for k in ("ext_image", "ext_use", "ext_import", "ext_url"))
          and msg("ext_import").count("@import") == 1 and not rules("link_ok"), [msg(k) for k in ("ext_image", "ext_use", "ext_import", "ext_url", "link_ok")])
    check("lint: <foreignObject> warns, and fails for --target github",
          rules("foreign") == [("WARN", "foreign-object")] and L.get("foreign_github") == ["FAIL"], (rules("foreign"), L.get("foreign_github")))
    check("lint: text in a font the file doesn't embed fails, by attribute or by a matching <style> rule",
          rules("font_named", "FAIL") == [("FAIL", "font")] and '"Inter"' in msg("font_named") and rules("font_rule", "FAIL") == [("FAIL", "font")], (msg("font_named"), msg("font_rule")))
    check("lint: a rule that doesn't match the text, an embedded face and a generic family pass",
          not rules("font_rule_elsewhere", "FAIL") and not rules("font_embedded") and not rules("font_generic", "FAIL"),
          (msg("font_rule_elsewhere"), msg("font_embedded"), msg("font_generic")))
    check("lint: a logo's generic family and text with no font warn",
          rules("font_generic") == [("WARN", "font")] and rules("font_none") == [("WARN", "font")], (msg("font_generic"), msg("font_none")))
    check("lint: a duplicate id and a reference to a missing one fail",
          rules("dup_id") == [("FAIL", "ids")] and "used 2 times" in msg("dup_id") and rules("missing_ref") == [("FAIL", "ids")] and "#glow" in msg("missing_ref"), (msg("dup_id"), msg("missing_ref")))
    check("lint: CSS animation and SMIL without a reduced-motion rule fail; gated animation passes",
          rules("css_anim") == [("FAIL", "motion")] and rules("smil") == [("FAIL", "motion")] and not rules("css_anim_gated")
          and L["css_anim_gated"]["animated"] and L["smil"]["animated"] and not L["clean"]["animated"], (msg("css_anim"), msg("smil"), msg("css_anim_gated")))
    check("lint: editor metadata, long decimals, an icon over budget and an embedded raster warn, with bytes saved",
          ("WARN", "metadata") in rules("metadata") and ("WARN", "decimals") in rules("metadata") and re.search(r"\d+ bytes that minify removes", msg("metadata"))
          and rules("decimals") == [("WARN", "decimals")] and rules("big_icon") == [("WARN", "size")] and rules("raster") == [("WARN", "raster")],
          (msg("metadata"), msg("decimals"), msg("big_icon"), msg("raster")))
    check("lint: no <title> warns unless it's decorative; a diagram also wants <desc>",
          rules("no_title") == [("WARN", "title")] and not rules("decorative") and any("<desc>" in m for m in L.get("diagram", [])), (msg("no_title"), L.get("diagram")))

    # ---------- minify's text transforms, the PNG codec, the comparison, contrast and motion times
    (tmp / "inkscape.svg").write_text(INKSCAPE, encoding="utf-8")
    out, err, code = node_script("units.mjs", f"""
import {{ roundNumbers, minifySvg, withoutText, parseXml, encodePng, decodePng, compareImages, enginesVerdict, sameRender, standOut, motionTimes, settle, projectKey, canonical }} from '{SVG_URL}';
import fs from 'node:fs';
const res = {{}};
res.round = [roundNumbers('M1.23456 2.5', 2), roundNumbers('1.004.5', 2), roundNumbers('-0.0001 0.5 10', 2), roundNumbers('1e-7 3.14159', 3), roundNumbers('rotate(12.3456 5.4321 1)', 1)];
res.min = minifySvg(fs.readFileSync('inkscape.svg', 'utf8'));
res.text = minifySvg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9">\\n  <text x="1"><tspan>Hello</tspan> <tspan>world</tspan></text>\\n  <style>\\n   a  {{ fill : red ; }}\\n  </style>\\n  <g></g>\\n</svg>');
res.notext = withoutText('<svg xmlns="http://www.w3.org/2000/svg"><text>Hi</text><rect width="1" height="1"/></svg>');
const img = (w, h, f) => {{ const d = new Uint8Array(w * h * 4); for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) d.set(f(x, y), (y * w + x) * 4); return {{ width: w, height: h, data: d }}; }};
const W = [255, 255, 255, 255], K = [0, 0, 0, 255], T = [0, 0, 0, 0];
const sq = (ox) => img(20, 20, (x, y) => (x >= 5 + ox && x < 15 + ox && y >= 5 && y < 15 ? K : W));
const back = decodePng(encodePng(sq(0)));
res.codec = back.width === 20 && back.height === 20 && Buffer.compare(Buffer.from(back.data), Buffer.from(sq(0).data)) === 0;
const bg = {{ r: 255, g: 255, b: 255 }};
const v = (c) => [c.diff, enginesVerdict(c), sameRender(c)];
res.same = v(compareImages(sq(0), sq(0), bg));
res.shift = v(compareImages(sq(0), sq(1), bg));
res.gone = v(compareImages(sq(0), img(20, 20, () => W), bg));
const grey = img(20, 20, (x, y) => (x >= 5 && x < 15 && y >= 5 && y < 15 ? [50, 50, 50, 255] : W));
const c2 = compareImages(sq(0), grey, bg);
res.tone = [c2.diff, Math.round(c2.mean), enginesVerdict(c2)];
const dark = {{ r: 13, g: 17, b: 23 }};
const blackMark = img(20, 20, (x, y) => (x >= 5 && x < 15 && y >= 5 && y < 15 ? [17, 17, 17, 255] : T));
const whiteMark = img(20, 20, (x, y) => (x >= 5 && x < 15 && y >= 5 && y < 15 ? [240, 240, 240, 255] : T));
const tile = img(20, 20, (x, y) => (x >= 8 && x < 12 && y >= 8 && y < 12 ? [255, 255, 255, 255] : [20, 24, 30, 255]));
const so = (m) => {{ const s = standOut(m, dark); return [+s.share.toFixed(2), +s.coverage.toFixed(2), s.dominant[0].hex]; }};
res.contrast = [so(blackMark), so(whiteMark), so(tile)];
res.times = [motionTimes(1.5, false), motionTimes(0, true)];
res.settle = [settle(['a', 'b', 'c', 'c', 'c', 'c'], [0, 1, 2, 3, 4, 5]), settle(['a', 'a', 'a', 'a'], [0, 1, 2, 3]), settle(['a', 'b', 'a', 'b'], [0, 1, 2, 3])];
res.key = projectKey(canonical(process.argv[2]), false);
console.log(JSON.stringify(res));
""", tmp)
    U = json.loads(out) if code == 0 and out.startswith("{") else {}
    check("round: long decimals round, short ones stay as written, and 1.0 then .5 don't run together",
          U.get("round") == ["M1.23 2.5", "1 .5", "0 0.5 10", "0 3.142", "rotate(12.3 5.4 1)"], (U.get("round"), err))
    m = U.get("min", "")
    check("minify: drops the comment, declaration, metadata, editor namespaces and their attributes, and rounds",
          m.startswith("<svg") and "inkscape" not in m and "sodipodi" not in m and "<!--" not in m and "metadata" not in m and "xmlns:dc" not in m
          and "xmlns:xlink" not in m and 'd="M 4 4 L 20 4 L 12 20 Z"' in m and 'cy="10.67"' in m and "<title>Mark</title>" in m and "<g" not in m, m)
    check("minify: keeps the space between words inside <text>, collapses <style> and drops an empty <g>",
          "<tspan>Hello</tspan> <tspan>world</tspan>" in U.get("text", "") and "<style>a{fill : red;}</style>" in U.get("text", "") and "<g" not in U.get("text", ""), U.get("text"))
    check("the text-free twin keeps the shapes and drops the text", U.get("notext") == '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>', U.get("notext"))
    check("png: what encodePng writes, decodePng reads back exactly", U.get("codec") is True, U.get("codec"))
    check("compare: identical renders match; a one-pixel shift is anti-aliasing, not a difference",
          U.get("same") == [0, "PASS", True] and U.get("shift") == [0, "PASS", True], (U.get("same"), U.get("shift")))
    check("compare: a shape one engine doesn't draw fails; a colour shift no single pixel shows fails on the average",
          U.get("gone", [0])[0] == 100 and U["gone"][1] == "FAIL" and U["gone"][2] is False and U.get("tone", [1])[0] == 0 and U["tone"][1] >= 40 and U["tone"][2] == "FAIL",
          (U.get("gone"), U.get("tone")))
    check("contrast: a near-black mark melts into dark, a light one stands out, and a dark tile covers its box",
          U.get("contrast") == [[1.0, 0.25, "#111111"], [0.0, 0.25, "#f0f0f0"], [0.96, 1.0, "#14181e"]], U.get("contrast"))
    t1, t2 = (U.get("times") or [{}, {}])
    check("motion times: past the last animation, ending on three irregular moments; a loop runs to 10.77 s",
          t1.get("T") == 5.77 and t1["times"][-3:] == [4.54, 5.16, 5.77] and t1["times"][0] == 0 and t2.get("T") == 10.77 and t2["times"][-3:] == [9.54, 10.16, 10.77], U.get("times"))
    check("motion settle: when it last changes, and still only if the last three frames match",
          U.get("settle") == [{"moves": True, "settlesAt": 2, "still": True}, {"moves": False, "settlesAt": 0, "still": True}, {"moves": True, "settlesAt": 3, "still": False}], U.get("settle"))
    spec = importlib.util.spec_from_file_location("killer_kit", ROOT / "tools" / "shared" / "kit.py")
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    check("the project key is the Python kit's, so killer-goal finds the run", U.get("key") == kit.project_key(Path(tmp).resolve(), False), (U.get("key"), err))

    # ---------- init, add and bad arguments
    proj = new_repo(tmp / "proj")
    write(proj / "assets" / "search.svg", CLEAN)
    g(proj, "add", "-A")
    g(proj, "commit", "-q", "-m", "icon")
    out, _ = svg("init", "--project", proj, "--kind", "icon", "--brief", "The search button in the toolbar", "--reads", "a magnifying glass",
                 "--files", proj / "assets" / "search.svg")
    ws = run_folder(out)
    cfg = json.loads((ws / "config.json").read_text(encoding="utf-8"))
    check("init: the run sits in KILLER_SVG_HOME under the kit's project key, named by date and kind",
          same_path(ws.parent.parent, tmp / "runs") and ws.parent.name == kit.project_key(Path(proj).resolve(), True) and re.match(r"\d{4}-\d\d-\d\d-icon$", ws.name), ws)
    check("init: records the kind, brief, reads, backgrounds, engines, git HEAD and branch",
          cfg["kind"] == "icon" and cfg["reads"] == ["a magnifying glass"] and [b["name"] for b in cfg["on"]] == ["light", "dark"]
          and cfg["engines"] == ["chromium", "firefox", "webkit"] and cfg["git"]["startBranch"] == "main" and len(cfg["git"]["head"] or "") == 40
          and "assets/search.svg" in out and "killer-svg/" in out, (cfg, out))
    out, _ = svg("init", "--project", proj, "--kind", "icon", "--brief", "again")
    check("init: a second run the same day gets its own folder", run_folder(out).name.endswith("-icon-2"), out)
    for args, want in [(["--brief", "x"], "--kind"), (["--kind", "sticker", "--brief", "x"], "--kind is one of"), (["--kind", "logo"], "--brief"),
                       (["--kind", "logo", "--brief", "x", "--on", "light,mauve"], "#rrggbb"), (["--kind", "logo", "--brief", "x", "--sizes", "16,4"], "8 to 4096"),
                       (["--kind", "logo", "--brief", "x", "--engines", "firefox"], "must include chromium"), (["--kind", "logo", "--brief", "x", "--colour", "red"], "unknown option --colour"),
                       (["--kind", "logo", "--brief", "x", "--files", "a.svg@sepia"], "#rrggbb"), (["--kind", "logo", "--brief", "x", "--on", "light", "--files", "a.svg@dark"], "isn't one of the run's backgrounds")]:
        out, code = svg("init", "--project", proj, *args, ok=(2,))
        check(f"init refuses {' '.join(args[-2:])}", code == 2 and want in out, out)
    out, _ = svg("check", ok=(2,))
    check("a command without -w says it needs the run folder", "needs -w" in out, out)
    out, _ = svg("check", "-w", tmp / "not-a-run", ok=(2,))
    check("a folder that isn't a run is refused", "no killer-svg run" in out, out)
    out, _ = svg("init", "--project", proj, "--kind", "icons", "--brief", "toolbar set", "--slug", "set")
    wset = run_folder(out)
    write(proj / "icons" / "a.svg", CLEAN)
    write(proj / "icons" / "a-small.svg", CLEAN)
    out, _ = svg("add", "-w", wset, proj / "icons", proj / "assets" / "search.svg@dark", proj / "icons" / "later.svg")
    check("add: a folder brings its .svg files, @dark limits a file's backgrounds, and a -small file is marked as the variant",
          "icons/a.svg" in out and "icons/a-small.svg (the small-size variant)" in out and "assets/search.svg (on dark only)" in out
          and "icons/later.svg (not written yet)" in out, out)

    # ---------- lint from the command line
    lint_dir = tmp / "lint"
    write(lint_dir / "ok.svg", CLEAN)
    write(lint_dir / "bad.svg", fx["script"])
    out, code = svg("lint", lint_dir / "ok.svg")
    check("lint: a clean file exits 0", code == 0 and "No failures" in out, out)
    out, code = svg("lint", lint_dir / "ok.svg", lint_dir / "bad.svg", ok=(1,))
    check("lint: a failing file exits 1 and is named", code == 1 and "FAIL  bad.svg: <script>" in out, out)
    out, code = svg("lint", "-w", wset, ok=(1,))
    check("lint -w: a run's file that isn't written yet fails", "icons/later.svg: isn't written yet" in out, out)

    # ---------- the books, before any check
    svg("decide", "-w", ws, "Kept the existing stroke width of 2")
    svg("wait", "-w", ws, "Publish the icon to the design site", "--command", "scp assets/search.svg site:/icons/")
    out, _ = svg("wait", "-w", ws, "No command", ok=(2,))
    check("wait needs the exact command", "--command" in out, out)
    out, _ = svg("report", "-w", ws)
    summary = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
    check("report before any check: partial, and it says so",
          summary["status"] == "partial" and summary["headline"] == "no check has run yet" and out.strip().endswith("report.md"), (summary, out))
    check("summary.json has every field killer-goal reads, and the numbers",
          set(summary) == {"skill", "status", "headline", "report", "branch", "commits", "decisions", "waiting", "numbers"}
          and summary["decisions"] == ["Kept the existing stroke width of 2"] and summary["waiting"] == ["Publish the icon to the design site: scp assets/search.svg site:/icons/"]
          and {"files", "checks_failing", "engines_checked", "engines_not_checked", "rounds", "reads_landed", "agents"} <= set(summary["numbers"]), summary)

    # ---------- land: commit the graphics on a local branch, from the temp path as given
    repo = new_repo(tmp / "repo")
    write(repo / "art" / "mark.svg", CLEAN)
    (repo / "README.md").write_text("# project\nmy own edit\n")
    out, _ = svg("init", "--project", repo, "--kind", "logo", "--brief", "the project mark", "--files", repo / "art" / "mark.svg")
    wr = run_folder(out)
    out, _ = svg("land", "-w", wr, "-m", "Add the mark", "-m", "Co-Authored-By: Claude <noreply@anthropic.com>", ok=(2,))
    check("land: refuses attribution in the message, before touching git",
          "co-author" in out and g(repo, "branch", "--show-current").strip() == "main" and not g(repo, "diff", "--cached", "--name-only").strip(), out)
    g(repo, "add", "README.md")
    out, _ = svg("land", "-w", wr, "-m", "Add the mark", ok=(2,))
    check("land: refuses when other files are staged, and changes nothing", "already staged" in out and g(repo, "branch", "--show-current").strip() == "main", out)
    g(repo, "reset", "-q", "README.md")
    out, _ = svg("land", "-w", wr, "-m", "Add the project mark")
    branch = g(repo, "branch", "--show-current").strip()
    files = g(repo, "show", "--name-only", "--format=", "HEAD").split()
    check("land: commits only the run's graphic on killer-svg/<date>, never on main",
          re.match(r"killer-svg/\d{4}-\d\d-\d\d$", branch) and files == ["art/mark.svg"] and g(repo, "log", "-1", "--format=%s%n%b").strip() == "Add the project mark", (branch, files, out))
    check("land: the user's own edits stay uncommitted, and nothing is pushed",
          g(repo, "status", "--short").strip() == "M README.md" and "Nothing was pushed" in out and g(repo, "log", "main", "--oneline").count("\n") == 1, g(repo, "status", "--short"))
    with open(repo / "art" / "mark.svg", "a", encoding="utf-8") as fh:
        fh.write("<!-- Generated with Claude Code -->\n")
    out, _ = svg("land", "-w", wr, "-m", "Tweak the mark", ok=(2,))
    check("land: refuses a graphic that says who generated it, and unstages it", "generated it" in out and not g(repo, "diff", "--cached", "--name-only").strip(), out)
    g(repo, "checkout", "-q", "--", "art/mark.svg")
    g(repo, "switch", "-q", "main")
    out, _ = svg("init", "--project", repo, "--kind", "logo", "--brief", "the mark", "--files", repo / "art" / "mark2.svg", "--branch", "main", "--slug", "goal")
    wg = run_folder(out)
    write(repo / "art" / "mark2.svg", CLEAN)
    out, _ = svg("land", "-w", wg, "-m", "A second mark on the named branch")
    check("land: a branch named at init is where it commits, even the one the user started on (killer-goal)",
          g(repo, "branch", "--show-current").strip() == "main" and g(repo, "log", "-1", "--format=%s").strip() == "A second mark on the named branch", out)
    svg("report", "-w", wr)
    s2 = json.loads((wr / "summary.json").read_text(encoding="utf-8"))
    check("report: the branch and commits come from land", s2["branch"] == branch and len(s2["commits"]) == 1 and s2["commits"][0].endswith("Add the project mark"), s2)

    # ---------- the browser checks
    out, err, code = node_script("probe.mjs", f"""
import {{ launchChromium }} from '{SVG_URL}';
try {{ const b = await launchChromium({{ install: false }}); await b.close(); console.log('OK'); }}
catch (e) {{ console.log('NO ' + e.message); }}
""")
    if out != "OK":
        print(f"SKIP browser checks: {out or err}")
    else:
        # A clean icon passes; Firefox and WebKit aren't found, so they're "not checked", never passed.
        out, code = svg("check", "-w", ws, ok=(0, 1))
        by, d = fails_of(ws)
        check("check: a clean icon passes", code == 0 and d["ok"] and by == {"search.svg": []} and not d["summary"]["FAIL"], out)
        check("check: Firefox and WebKit that can't be found are reported not checked, never passed",
              d["summary"]["enginesChecked"] == ["chromium"] and d["summary"]["enginesNotChecked"] == ["firefox", "webkit"]
              and all("isn't installed" in d["engines"][e]["reason"] for e in ("firefox", "webkit"))
              and "Firefox and WebKit not checked" in out and "matches Chromium" not in out, out)
        cfg = json.loads((ws / "config.json").read_text(encoding="utf-8"))
        check("render: the command that installs the missing engines waits for the user's OK",
              any("install firefox webkit" in w["command"] for w in cfg["waiting"]), cfg["waiting"])
        renders = ws / "renders" / "chromium" / "assets-search"
        check("render: every size on light, dark, a checkerboard and clear, at 1x",
              all(png_size(renders / f"{s}-{b}.png") == (s, s) for s in (16, 24, 32, 48, 96) for b in ("light", "dark", "checker", "clear-light", "clear-dark"))
              and (ws / "renders" / "assets-search-sheet.png").is_file(), sorted(p.name for p in renders.glob("*")) if renders.exists() else "no renders")
        out, code = svg("engines", "-w", ws)
        check("engines: with only Chromium, nothing is compared and it says so", code == 0 and "not checked, not passed" in out, out)
        out, code = svg("sheet", "-w", ws)
        mm = re.search(r"sheet: (\d+)x(\d+) px -> (.+)", out)
        check("sheet: one overview PNG of the size it reports", mm and png_size(mm.group(3).strip()) == (int(mm.group(1)), int(mm.group(2))) and "passed the last check" in out, out)

        # One failure per file, and a clean one.
        fxd = tmp / "fixtures"
        write(fxd / "clean.svg", CLEAN)
        write(fxd / "wordmark.svg", f'<svg {NS} viewBox="0 0 120 40"><title>Acme</title><text x="4" y="28" font-family="Inter, sans-serif" font-size="24" fill="#808080">Acme</text></svg>')
        write(fxd / "beacon.svg", f'<svg {NS} viewBox="0 0 24 24"><title>Beacon</title><script>document.title = "x"</script><rect x="2" y="2" width="20" height="20" fill="#cc3333"/></svg>')
        write(fxd / "shadow.svg", f'<svg {NS} viewBox="0 0 24 24"><title>Shadow</title><path d="M4 4h16v16H4z" fill="#111111"/></svg>')
        write(fxd / "spinner.svg", f'<svg {NS} viewBox="0 0 24 24"><title>Spinner</title><style>@keyframes s{{to{{transform:rotate(360deg)}}}} .r{{transform-origin:12px 12px;animation:s 2s linear infinite}}</style><rect class="r" x="6" y="6" width="12" height="12" fill="#cc3333"/></svg>')
        write(fxd / "gated.svg", f'<svg {NS} viewBox="0 0 24 24"><title>Gated</title><style>@keyframes s{{to{{transform:rotate(360deg)}}}} .r{{transform-origin:12px 12px}} @media (prefers-reduced-motion: no-preference){{.r{{animation:s 2s linear infinite}}}}</style><rect class="r" x="6" y="6" width="12" height="12" fill="#cc3333"/></svg>')
        write(fxd / "smil.svg", f'<svg {NS} viewBox="0 0 24 24"><title>Blink</title><style>.still{{display:none}} @media (prefers-reduced-motion: reduce){{.moving{{display:none}} .still{{display:inline}}}}</style><rect class="moving" x="4" y="4" width="16" height="16" fill="#cc3333"><animate attributeName="opacity" values="1;0.2;1" dur="1.3s" repeatCount="indefinite"/></rect><rect class="still" x="4" y="4" width="16" height="16" fill="#cc3333"/></svg>')
        out, _ = svg("init", "--project", fxd, "--kind", "icon", "--brief", "fixtures", "--files", fxd, "--slug", "fixtures")
        wf = run_folder(out)
        t0 = time.time()
        out, code = svg("check", "-w", wf, ok=(0, 1))
        by, d = fails_of(wf)
        check("check: an outside font, a script, a mark that vanishes on dark and ungated animation each fail exactly their check",
              code == 1 and by == {"beacon.svg": [("lint", "script")], "clean.svg": [], "gated.svg": [], "shadow.svg": [("contrast", None)], "smil.svg": [],
                                   "spinner.svg": [("lint", "motion"), ("motion", None)], "wordmark.svg": [("lint", "font")]}, (by, out))
        check("check: the failures carry their numbers and where to look",
              '"Inter"' in out and "disappears on dark (#0d1117)" in out and "#111111 1.0:1" in out and "never stops" in out and re.search(r"Frames: .+spinner\.png", out), out)
        check("check: the motion check passes gated CSS and SMIL hidden under reduced motion, and says how they move",
              re.search(r"gated\.svg: motion: it moves and keeps moving \(it loops\); with reduced motion on, it stays still", out)
              and re.search(r"smil\.svg: motion: it moves and keeps moving \(it loops\); with reduced motion on, it stays still", out), out)
        check("check: check.json has summary FAIL, WARN and INFO lists, and the failing files",
              isinstance(d["summary"]["FAIL"], list) and isinstance(d["summary"]["WARN"], list) and isinstance(d["summary"]["INFO"], list)
              and sorted(d["summary"]["failingFiles"]) == ["beacon.svg", "shadow.svg", "spinner.svg", "wordmark.svg"], d["summary"])
        print(f"     (fixture check took {time.time() - t0:.1f}s)")

        # minify: smaller with identical renders, or nothing written.
        md = tmp / "minify"
        src = write(md / "mark.svg", INKSCAPE)
        out, code = svg("minify", src, "--out", md / "mark.min.svg")
        small = (md / "mark.min.svg").read_text(encoding="utf-8") if (md / "mark.min.svg").exists() else ""
        check("minify: writes a smaller file whose renders match, and says how much it saved",
              code == 0 and re.search(r"\d+ bytes -> \d+ bytes \(\d+ bytes saved", out) and "renders match at 16, 32, 64, 256 and 1024 px" in out
              and len(small.encode()) < len(INKSCAPE.encode()) / 2 and "inkscape" not in small, (out, small))
        tiny = write(md / "tiny.svg", f'<svg {NS} viewBox="0 0 1 1"><title>T</title><rect x="0.1234" y="0.1234" width="0.3456" height="0.3456" fill="#224466"/><rect x="0.55" y="0.55" width="0.4" height="0.4" fill="#cc3333"/></svg>')
        before = tiny.read_bytes()
        out, code = svg("minify", tiny, "--decimals", "1", ok=(1,))
        check("minify: refuses a rounding that moves the render, says where, and writes nothing",
              code == 1 and "refused" in out and "in viewBox units" in out and "--decimals 2" in out and tiny.read_bytes() == before, out)

        # github: an <img> under a strict CSP against the file opened directly.
        gd = tmp / "gh"
        write(gd / "clean.svg", CLEAN)
        write(gd / "badge.svg", f'<svg {NS} viewBox="0 0 64 64"><title>Badge</title><rect width="64" height="64" fill="#2a6f4f"/><image href="dot.png" x="8" y="8" width="48" height="48"/></svg>')
        raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))
        chunk = lambda t, b: struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))
        (gd / "dot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        write(gd / "remote.svg", f'<svg {NS} viewBox="0 0 64 64"><title>R</title><style>@import url("https://fonts.example.invalid/x.css");</style><rect width="64" height="64" fill="#224466"/></svg>')
        out, code = svg("github", gd / "clean.svg", "--sizes", "24,96")
        check("github: a clean icon renders the same as an image and opened directly", code == 0 and "renders the same through <img>" in out, out)
        out, code = svg("github", gd / "badge.svg", "--sizes", "64", ok=(1,))
        check("github: an image beside the file is loaded opened directly but not through <img>: both the request and the difference fail",
              "it loads dot.png" in out and "looks different from the file opened directly" in out and "Diff" in out, out)
        out, code = svg("github", gd / "remote.svg", "--sizes", "64", ok=(1,))
        check("github: a request to the network is caught, offline, by what the file asked for", "it loads https://fonts.example.invalid/x.css from the network" in out, out)

        # motion on its own, and export.
        out, code = svg("motion", fxd / "spinner.svg", ok=(1,))
        check("motion: ungated animation fails on its own too", code == 1 and "never stops" in out, out)
        out, code = svg("export", fxd / "clean.svg", "--png", "16,32,180", "--out", tmp / "png")
        corner = None
        if (tmp / "png" / "clean-180.png").exists():
            raw180 = (tmp / "png" / "clean-180.png").read_bytes()
            corner = zlib.decompress(b"".join(raw180[i + 8:i + 8 + struct.unpack(">I", raw180[i:i + 4])[0]] for i in [raw180.index(b"IDAT") - 4]))[4]
        check("export: transparent PNGs at each size, and a pointer to killer-design's icon set",
              code == 0 and [png_size(tmp / "png" / f"clean-{s}.png") for s in (16, 32, 180)] == [(16, 16), (32, 32), (180, 180)] and corner == 0 and "icons.mjs" in out, (out, corner))

        # The blind viewer: images only, then a matcher, and quotes that must be real.
        vd = new_repo(tmp / "viewer")
        write(vd / "art" / "harbour-mark.svg", f'<svg {NS} viewBox="0 0 32 32" role="img"><title>Harbour</title><path d="M4 20h24l-4 6H8z" fill="#1d4e89"/><path d="M15 6v13H8z" fill="#e0a526"/></svg>')
        write(vd / "art" / "lantern-icon.svg", f'<svg {NS} viewBox="0 0 24 24" role="img"><title>Lantern</title><rect x="7" y="6" width="10" height="14" rx="2" fill="#c0392b"/><circle cx="12" cy="13" r="3" fill="#f6d365"/></svg>')
        out, _ = svg("init", "--project", vd, "--kind", "icons", "--brief", "Marks for the harbour co-op's noticeboard app",
                     "--reads", "harbour-mark.svg: a sailing boat", "--reads", "lantern-icon.svg: a lantern", "--reads", "something nautical", "--files", vd / "art")
        wv = run_folder(out)
        out, code = svg("audience", "-w", wv)
        brief = (wv / "audience" / "round-1" / "brief.md").read_text(encoding="utf-8")
        box = Path(re.search(r"graphics? in (.+[\\/]images)$", out, re.M).group(1)).parent
        held = sorted(str(p.relative_to(box)).replace("\\", "/") for p in box.rglob("*"))
        check("audience: the viewer's folder holds only PNG renders named by letter, and an empty scratch folder",
              held and all(re.fullmatch(r"images/[AB]-(board|large-(light|dark))\.png", h) or h in ("images", "scratch") for h in held)
              and not any(h.endswith(".svg") for h in held) and same_path(box.parent.parent, tmp / "boxes"), held)
        check("audience: the brief names no file, no brief, no reads and no project",
              all(s not in brief.lower() for s in ("harbour", "lantern", "noticeboard", "sailing", "nautical", "art/", str(vd).lower().replace("\\", "/")))
              and "A-board.png" in brief and "B-board.png" in brief and "<" + "listing>" not in brief, brief)
        out, _ = svg("heard", "-w", wv, ok=(2,))
        check("heard: no reply says to send the same brief to a fresh agent", "no reply" in out and "fresh agent" in out, out)
        (box / "scratch" / "reply.json").write_text('{"graphics": [{"id": "A", "what": ""}]}', encoding="utf-8")
        out, _ = svg("heard", "-w", wv, ok=(2,))
        check("heard: a malformed reply is refused with what's wrong", "graphic B must appear exactly once" in out and '"what"' in out, out)
        rnd = json.loads((wv / "config.json").read_text(encoding="utf-8"))["rounds"][-1]
        letter = {Path(x["rel"]).name: x["letter"] for x in rnd["graphics"]}
        boat, lamp = letter["harbour-mark.svg"], letter["lantern-icon.svg"]
        viewer = {"graphics": [{"id": boat, "what": "A small boat with a yellow sail on dark blue water", "suggests": "the sea, sailing", "name": "sailboat", "smallest": 16, "unreadable": ""},
                               {"id": lamp, "what": "A red box with a glowing yellow circle", "suggests": "light", "name": "lamp", "smallest": 24, "unreadable": "the shape at 16 px"}], "notes": ""}
        (box / "scratch" / "reply.json").write_text(json.dumps(viewer), encoding="utf-8")
        out, code = svg("heard", "-w", wv)
        mbrief = (wv / "audience" / "round-1" / "match-brief.md").read_text(encoding="utf-8")
        mbox = Path(re.search(r"writes reply.json in\s+(.+)$", out, re.M).group(1).strip()).parent
        check("heard: the matcher gets the reads, scoped to their graphic, and the viewer's words, not the images",
              f"(about graphic {boat}) a sailing boat" in mbrief and f"(about graphic {lamp}) a lantern" in mbrief and "3. something nautical" in mbrief
              and "glowing yellow circle" in mbrief and ".png" not in mbrief and not box.exists(), mbrief)
        check("heard: a graphic the viewer couldn't recognise at its smallest size is flagged", f"SIZE  {lamp} (art/lantern-icon.svg) needs to read at 16 px" in out, out)
        (mbox / "scratch" / "reply.json").write_text(json.dumps({"reads": [
            {"id": 1, "verdict": "landed", "graphic": boat, "quote": "boat with a yellow sail", "why": ""},
            {"id": 2, "verdict": "landed", "graphic": boat, "quote": "the sea", "why": "close"},
            {"id": 3, "verdict": "landed", "graphic": None, "quote": "a ship on the ocean", "why": ""}]}), encoding="utf-8")
        out, code = svg("landed", "-w", wv, ok=(0, 1))
        v = json.loads((wv / "audience" / "round-1" / "verdicts.json").read_text(encoding="utf-8"))["verdicts"]
        check("landed: a quote the viewer wrote lands; a quote about the wrong graphic and one they never wrote count as missed",
              code == 1 and [x["verdict"] for x in v] == ["landed", "missed", "missed"] and f"quoting graphic {boat}, but this read is about graphic {lamp}" in out
              and "isn't in what the viewer wrote" in out and "1 of 3 reads landed" in out, out)
        check("landed: both agents' folders are deleted afterwards", not mbox.exists(), mbox)
        out, _ = svg("audience", "-w", wv)
        box2 = Path(re.search(r"graphics? in (.+[\\/]images)$", out, re.M).group(1)).parent
        rnd = json.loads((wv / "config.json").read_text(encoding="utf-8"))["rounds"][-1]
        letter = {Path(x["rel"]).name: x["letter"] for x in rnd["graphics"]}
        boat, lamp = letter["harbour-mark.svg"], letter["lantern-icon.svg"]
        viewer["graphics"][0]["id"], viewer["graphics"][1]["id"] = boat, lamp
        viewer["graphics"][1]["smallest"] = 16
        viewer["graphics"][1]["what"] = "A red lantern with a glowing light inside"
        (box2 / "scratch" / "reply.json").write_text(json.dumps(viewer), encoding="utf-8")
        out, _ = svg("heard", "-w", wv)
        mbox2 = Path(re.search(r"writes reply.json in\s+(.+)$", out, re.M).group(1).strip()).parent
        (mbox2 / "scratch" / "reply.json").write_text(json.dumps({"reads": [
            {"id": 1, "verdict": "landed", "graphic": boat, "quote": "A small boat with a yellow  sail.", "why": ""},
            {"id": 2, "verdict": "landed", "graphic": lamp, "quote": "a red lantern", "why": ""},
            {"id": 3, "verdict": "landed", "graphic": boat, "quote": "the sea, sailing", "why": ""}]}), encoding="utf-8")
        out, code = svg("landed", "-w", wv, ok=(0, 1))
        check("landed: round 2, every read landed at every size (quotes match ignoring case, spacing and end punctuation)",
              code == 0 and "3 of 3 reads landed" in out and "Every read landed, at every size" in out, out)

        out, code = svg("check", "-w", wv, ok=(0, 1))
        check("contrast: a main colour under WCAG's 3:1 for graphics warns (the boat's navy hull on dark), without failing",
              code == 0 and re.search(r"WARN  art/harbour-mark\.svg: #1d4e89 \(\d+% of it\) is only 2\.\d:1 against dark \(#0d1117\), under the 3:1 graphics need .+ Lighten it", out), out)
        out, _ = svg("report", "-w", wv)
        summary = json.loads((wv / "summary.json").read_text(encoding="utf-8"))
        report = (wv / "report.md").read_text(encoding="utf-8")
        check("report: done when check passes on these files and the last round landed every read at every size",
              summary["status"] == "done" and summary["numbers"]["files"] == 2 and summary["numbers"]["reads_landed"] == 3 and summary["numbers"]["rounds"] == 2
              and summary["numbers"]["sizes_recognised"] is True and "a blind viewer got 3 of 3 reads (round 2)" in summary["headline"], summary)
        check("report: counts the agents by the distinct replies they wrote, malformed ones included", summary["numbers"]["agents"] == 5, summary["numbers"])
        check("report.md: both rounds, what the viewer called each graphic, and the engines not checked",
              "**Round 1**: 1 of 3 landed" in report and "**Round 2**: 3 of 3 landed" in report and 'they\'d call it "sailboat"' in report
              and "Firefox not checked" in report and "## Waiting for your OK" in report, report)
        with open(vd / "art" / "lantern-icon.svg", "a", encoding="utf-8") as fh:
            fh.write("\n")
        svg("report", "-w", wv)
        summary = json.loads((wv / "summary.json").read_text(encoding="utf-8"))
        check("report: a graphic changed after the last check is partial, and says to run check again",
              summary["status"] == "partial" and "run check again" in summary["headline"] and summary["numbers"]["check_current"] is False, summary)

        # Real engine comparison, when this machine has Playwright's own Firefox or WebKit.
        env2 = {k: v for k, v in ENV.items() if k != "KILLER_SVG_BROWSERS"}
        ed = tmp / "engines"
        write(ed / "clean.svg", CLEAN)
        write(ed / "cssd.svg", f'<svg {NS} viewBox="0 0 64 64"><title>D</title><style>.p{{d:path("M8 8H56V56H8Z")}}</style><path class="p" d="M8 8H20V20H8Z" fill="#cc3333"/></svg>')
        out, _ = svg("init", "--project", ed, "--kind", "icon", "--brief", "engines", "--files", ed, "--on", "light", env=env2)
        we = run_folder(out)
        out, code = svg("check", "-w", we, ok=(0, 1), env=env2)
        by, d = fails_of(we)
        have = d["summary"]["enginesChecked"]
        if have == ["chromium"]:
            print("SKIP engine comparison: Playwright's Firefox and WebKit aren't installed here (CI's runners only have Chromium)")
        else:
            others = [e for e in have if e != "chromium"]
            check(f"engines: a clean icon matches Chromium in {' and '.join(others)}",
                  by["clean.svg"] == [] and all(f"{e.title() if e != 'webkit' else 'WebKit'} matches Chromium at every size" in out for e in others), out)
            if "webkit" in have:
                diffs = list((we / "engines").glob("cssd-webkit-*.png"))
                check("engines: WebKit ignores the CSS d property, so it fails with a diff image",
                      by["cssd.svg"] == [("engines", None)] and "WebKit draws it differently from Chromium" in out and diffs, out)
            else:
                print("SKIP the WebKit difference: WebKit isn't installed here")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"took {time.time() - started:.0f}s")
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
