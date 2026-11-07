"""Scenario tests for killer-slides' deck.mjs. Run: python -I tests/test_slides.py

Without node, everything is skipped. The PNG decoder, the contrast maths, the skeleton, run
folders, the books (decide, wait, report) and land (in a scratch git repository) run anywhere
node does. The browser checks need Playwright with Chromium, found the way deck.mjs finds it
(this project, ~/.cache/killer-skills, ~/.cache/killer-design, ~/.cache/killer-slides); without
it they print SKIP. Nothing is ever installed: KILLER_SLIDES_NO_INSTALL is set.

The browser checks: a fresh skeleton passes check; a deck with one overflowing, one tiny-text,
one low-contrast, one wordy and one clean slide fails exactly those; text over a pale and a dark
picture, vw sizing, motion with and without a reduced-motion gate, SVG labels, missing notes, a
missing image, a console error and a request to the network; the engine's keys, builds, deep
links, overview and presenter window; shoot, sheet and pdf sizes; the blind audience round from
images to verdicts; fonts from the font pack; and the report's status.
"""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "killer-slides"
DECK = SKILL / "scripts" / "deck.mjs"
NODE = shutil.which("node")
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)
        if detail:
            print("    " + str(detail).replace("\n", "\n    ")[-3000:])


if not NODE:
    print("SKIP node isn't installed: every killer-slides check needs it")
    sys.exit(0)

tmp = Path(tempfile.mkdtemp())
ENV = dict(os.environ, KILLER_SLIDES_HOME=str(tmp / "runs"), KILLER_SLIDES_SANDBOXES=str(tmp / "boxes"),
           KILLER_SLIDES_NO_INSTALL="1", PYTHONIOENCODING="utf-8")
DECK_URL = DECK.resolve().as_uri()


def deck(*args, ok=(0,), cwd=None):
    r = subprocess.run([NODE, str(DECK), *map(str, args)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV, cwd=cwd or tmp, timeout=300)
    out = r.stdout + r.stderr
    if r.returncode not in ok:
        raise AssertionError(f"exit {r.returncode} for deck.mjs {' '.join(map(str, args))}:\n{out}")
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


def run_folder(out):
    return Path(re.search(r"Run folder: (.+)", out).group(1).strip())


def png_bytes(w, h, ctype, rows, filters, palette=None, trns=None):
    """A PNG whose rows are filtered with the given filter types, the way an encoder would."""
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    data, prev = b"", bytes(len(rows[0]))
    for f, row in zip(filters, rows):
        enc = bytearray()
        for i, x in enumerate(row):
            a = row[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 0:
                p = 0
            elif f == 1:
                p = a
            elif f == 2:
                p = b
            elif f == 3:
                p = (a + b) // 2
            else:
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                p = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            enc.append((x - p) & 255)
        data += bytes([f]) + bytes(enc)
        prev = row
    chunks = [(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))]
    if palette:
        chunks.append((b"PLTE", palette))
    if trns:
        chunks.append((b"tRNS", trns))
    chunks += [(b"IDAT", zlib.compress(data)), (b"IEND", b"")]
    return b"\x89PNG\r\n\x1a\n" + b"".join(struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d)) for t, d in chunks)


def png_size(path):
    b = Path(path).read_bytes()
    return struct.unpack(">II", b[16:24])


def set_slides(folder, slides, head=""):
    p = folder / "deck.html"
    s = p.read_text(encoding="utf-8")
    s = re.sub(r"(<main[^>]*>).*?(</main>)", lambda m: m.group(1) + slides + m.group(2), s, flags=re.S)
    if head:
        s = s.replace("</head>", head + "\n</head>")
    p.write_text(s, encoding="utf-8")


def fails_by_slide(ws):
    d = json.loads((ws / "check.json").read_text(encoding="utf-8"))
    return {s["n"]: sorted({f["check"] for f in s["fails"]}) for s in d["slides"]}, sorted({f["check"] for f in d["deckIssues"]["fails"]}), d


NOTE = '<aside class="notes"><p>What I say here.</p></aside>'

try:
    # ---------- the scripts parse, and the shared copy matches its source
    for f in [DECK, SKILL / "scripts" / "fonts.mjs", SKILL / "assets" / "skeleton" / "assets" / "deck.js"]:
        r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
        check(f"node --check {f.name}", r.returncode == 0, r.stderr)
    check("fonts.mjs is killer-design's, byte for byte",
          (SKILL / "scripts" / "fonts.mjs").read_bytes() == (ROOT / "skills" / "killer-design" / "scripts" / "fonts.mjs").read_bytes())
    out, _ = deck("--help")
    check("--help lists every command", all(c in out for c in ("new <folder>", "init <deck>", "shoot -w", "check -w", "sheet -w", "pdf -w",
                                                                  "audience -w", "heard -w", "landed -w", "land -w", "decide -w", "wait -w", "report -w")), out)

    # ---------- PNG decoding and contrast, in Node with no browser
    rgb_rows = [bytes([(x * 37 + y * 11) % 256 for x in range(4 * 3)]) for y in range(5)]
    rgba_rows = [bytes([(x * 53 + y * 29 + 7) % 256 for x in range(3 * 4)]) for y in range(3)]
    (tmp / "rgb.png").write_bytes(png_bytes(4, 5, 2, rgb_rows, [0, 1, 2, 3, 4]))
    (tmp / "rgba.png").write_bytes(png_bytes(3, 3, 6, rgba_rows, [4, 3, 4]))
    (tmp / "pal.png").write_bytes(png_bytes(2, 2, 3, [bytes([0, 1]), bytes([2, 0])], [0, 1],
                                            palette=bytes([255, 0, 0, 0, 255, 0, 0, 0, 255]), trns=bytes([128])))
    (tmp / "grey.png").write_bytes(png_bytes(2, 2, 0, [bytes([0, 255]), bytes([128, 64])], [2, 4]))
    (tmp / "interlaced.png").write_bytes((tmp / "grey.png").read_bytes()[:28] + b"\x01" + (tmp / "grey.png").read_bytes()[29:])
    out, err, code = node_script("png.mjs", f"""
import {{ decodePng, luminance, contrast, over, contrastBehind, sheetLayout, pdfInfo, pngSize }} from '{DECK_URL}';
import fs from 'node:fs';
const res = {{}};
for (const n of ['rgb', 'rgba', 'pal', 'grey']) {{ const p = decodePng(fs.readFileSync(n + '.png')); res[n] = {{ w: p.width, h: p.height, px: [...p.data] }}; }}
try {{ decodePng(fs.readFileSync('interlaced.png')); res.interlaced = 'decoded'; }} catch (e) {{ res.interlaced = e.message; }}
res.size = pngSize(fs.readFileSync('rgb.png'));
const W = {{ r: 255, g: 255, b: 255 }}, K = {{ r: 0, g: 0, b: 0 }};
res.white = luminance(W); res.black = luminance(K); res.max = contrast(W, K); res.g777 = contrast({{ r: 119, g: 119, b: 119 }}, W);
res.over = over({{ r: 255, g: 255, b: 255, a: 0.5 }}, K);
// The left half of the background black, the right half white: white text over all of it is 1:1 at its worst tenth;
// over the left half only (a screenshot clipped at 100,50) it's 21:1.
const img = {{ width: 10, height: 2, data: new Uint8Array(10 * 2 * 4).map((_, i) => (Math.floor(i / 4) % 10 < 5 ? 0 : 255)) }};
res.behind = contrastBehind(img, [[0, 0, 10, 2]], {{ ...W, a: 1 }}).ratio;
res.offset = contrastBehind({{ ...img, x: 100, y: 50 }}, [[100, 50, 5, 2]], {{ ...W, a: 1 }}).ratio;
res.sheet = [sheetLayout(3), sheetLayout(5), sheetLayout(17)];
res.pdf = pdfInfo(Buffer.from('<< /Type /Pages /Count 2 >> << /Type /Page /MediaBox [0 0 1440 810] >> << /Type /Page /MediaBox [0 0 1440 810] >>'));
console.log(JSON.stringify(res));
""")
    res = json.loads(out) if code == 0 and out else {}
    check("png: decodes RGB with every filter type (none, sub, up, average, paeth)",
          res.get("rgb", {}).get("px") == [v for row in rgb_rows for i in range(0, 12, 3) for v in (*row[i:i + 3], 255)], (out, err))
    check("png: decodes RGBA with paeth and average rows", res.get("rgba", {}).get("px") == [v for row in rgba_rows for v in row], res.get("rgba"))
    check("png: decodes a palette with transparency", res.get("pal", {}).get("px") == [255, 0, 0, 128, 0, 255, 0, 255, 0, 0, 255, 255, 255, 0, 0, 128], res.get("pal"))
    check("png: decodes greyscale", res.get("grey", {}).get("px") == [0, 0, 0, 255, 255, 255, 255, 255, 128, 128, 128, 255, 64, 64, 64, 255], res.get("grey"))
    check("png: refuses an interlaced PNG with a reason", "interlaced" in str(res.get("interlaced")), res.get("interlaced"))
    check("png: reads the size from the header", res.get("size") == {"width": 4, "height": 5}, res.get("size"))
    check("contrast: white is luminance 1, black 0, and they differ 21:1",
          res.get("white") == 1 and res.get("black") == 0 and abs(res.get("max", 0) - 21) < 1e-9, res)
    check("contrast: #777 on white is 4.48:1, just under AA", abs(res.get("g777", 0) - 4.478) < 0.01, res.get("g777"))
    check("contrast: half-transparent white over black is mid grey", res.get("over") == {"r": 127.5, "g": 127.5, "b": 127.5}, res.get("over"))
    check("contrast behind text: the worst tenth of the background decides, in a clipped screenshot too",
          abs(res.get("behind", 0) - 1) < 1e-9 and abs(res.get("offset", 0) - 21) < 1e-9, res)
    check("sheet layout: columns and size", [(s["cols"], s["width"], s["height"]) for s in res.get("sheet", [])]
          == [(3, 80 + 3 * 480 + 2 * 24, 80 + 64 + 314), (4, 80 + 4 * 480 + 3 * 24, 80 + 64 + 2 * 314 + 24), (5, 80 + 5 * 480 + 4 * 24, 80 + 64 + 4 * 314 + 3 * 24)], res.get("sheet"))
    check("pdf: counts pages, not the page tree", res.get("pdf", {}).get("pages") == 2 and res["pdf"]["boxes"][0] == {"width": 1440, "height": 810}, res.get("pdf"))

    # ---------- new: the skeleton
    talk = tmp / "talk"
    out, _ = deck("new", talk, "--title", "Why the <night> bus runs late", "--minutes", "10")
    html = (talk / "deck.html").read_text(encoding="utf-8")
    check("new: writes the deck, the engine and a stand-in theme",
          all((talk / p).is_file() for p in ("deck.html", "assets/deck.js", "assets/deck.css", "assets/theme.css")) and (talk / "assets" / "images").is_dir(), out)
    check("new: fills in the title (escaped), the language and the talk length",
          "<title>Why the &lt;night&gt; bus runs late</title>" in html and 'lang="en"' in html and 'data-minutes="10"' in html and "{{" not in html, html[:600])
    every = "".join(p.read_text(encoding="utf-8") for p in talk.rglob("*") if p.is_file())
    check("new: the skeleton requests nothing from the network", not re.search(r"(src|href)=[\"']?(https?:)?//|url\((https?:)?//|@import", every))
    check("new: no attribution in the skeleton", not re.search(r"generated (with|by)|claude|anthropic|co-authored", every, re.I))
    out, code = deck("new", talk, "--title", "Again", ok=(2,))
    check("new: never overwrites a deck", "already exists" in out and "Why the &lt;night&gt;" in (talk / "deck.html").read_text(encoding="utf-8"), out)

    # ---------- init, and bad arguments
    out, _ = deck("init", talk, "--talk", "10", "--message", "Late buses are a timetable problem, not a traffic problem",
                  "--message", "Moving three stops fixes most of it")
    ws = run_folder(out)
    cfg = json.loads((ws / "config.json").read_text(encoding="utf-8"))
    check("init: the run folder sits under KILLER_SLIDES_HOME, keyed by the deck",
          ws.parent.parent == tmp / "runs" and ws.parent.name.startswith("talk-") and re.match(r"\d{4}-\d\d-\d\d-talk$", ws.name), ws)
    check("init: records the talk, notes, key messages and the blind check",
          cfg["talk"] == {"minutes": 10} and cfg["notes"] and cfg["blind"] and cfg["pdf"] and len(cfg["messages"]) == 2 and cfg["deck"] == str(talk / "deck.html"), cfg)
    out, _ = deck("init", talk)
    check("init: a second run the same day gets its own folder", run_folder(out).name.endswith("-talk-2"), out)
    out, _ = deck("init", talk, "--talk", "10", "--read", ok=(2,))
    check("init: --talk and --read together is refused", "pick one" in out, out)
    out, _ = deck("init", talk, "--talk", "ten", ok=(2,))
    check("init: --talk needs a number", "positive number" in out, out)
    out, _ = deck("init", tmp / "later" / "talk", "--message", "A claim")
    wl = run_folder(out)
    check("init: a run can start before the deck exists, and says how to write it", "not written yet" in out and "deck.mjs new" in out, out)
    out, _ = deck("shoot", "-w", wl, ok=(2,))
    check("a render command on a run with no deck yet says how to write one", "no deck at" in out and "deck.mjs new" in out, out)
    out, _ = deck("init", talk, "--message", "Late buses are a timetable problem", "--room", "the people who write timetables", ok=(2,))
    check("init: a room description that gives away a key message is refused before anything is written",
          "shares words" in out and "timetable" in out, out)
    out, _ = deck("init", talk, "--colour", "red", ok=(2,))
    check("init: an unknown option is refused with the usage", "unknown option --colour" in out and "usage:" in out, out)
    out, _ = deck("check", ok=(2,))
    check("a command without -w says it needs the run folder", "needs -w" in out, out)
    out, _ = deck("check", "-w", tmp / "not-a-run", ok=(2,))
    check("a folder that isn't a run is refused", "no killer-slides run" in out, out)

    # ---------- the books, before any check
    deck("decide", "-w", ws, "Kept the stand-in palette: no brand guide in the repo")
    deck("wait", "-w", ws, "Publish the deck to the team site", "--command", "scp -r talk server:/srv/talk")
    out, _ = deck("wait", "-w", ws, "No command", ok=(2,))
    check("wait needs the exact command", "--command" in out, out)
    out, _ = deck("report", "-w", ws)
    summary = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
    check("report before any check: partial, and it says so",
          summary["status"] == "partial" and summary["headline"] == "no check has run yet" and out.strip().endswith(str(ws / "report.md")), (summary, out))
    check("summary.json has every field killer-goal reads",
          set(summary) == {"skill", "status", "headline", "report", "branch", "commits", "decisions", "waiting", "numbers"}
          and summary["decisions"] == ["Kept the stand-in palette: no brand guide in the repo"]
          and summary["waiting"] == ["Publish the deck to the team site: scp -r talk server:/srv/talk"], summary)

    # ---------- land: commit the deck folder on a local branch
    repo = tmp / "repo"
    repo.mkdir()
    g(repo, "init", "-q", "-b", "main")
    for k, v in (("user.name", "Test Person"), ("user.email", "test@example.com"), ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        g(repo, "config", k, v)
    (repo / "README.md").write_text("# project\n")
    g(repo, "add", "-A")
    g(repo, "commit", "-q", "-m", "first")
    (repo / "README.md").write_text("# project\nmy own edit\n")
    deck("new", repo / "talk", "--title", "Repo talk")
    out, _ = deck("init", repo / "talk")
    wr = run_folder(out)
    out, _ = deck("land", "-w", wr, "-m", "Add the deck", "-m", "Co-Authored-By: Claude <noreply@anthropic.com>", ok=(2,))
    check("land: refuses attribution in the message, before touching git",
          "co-author" in out and g(repo, "branch", "--show-current").strip() == "main" and not g(repo, "diff", "--cached", "--name-only").strip(), out)
    g(repo, "add", "README.md")
    out, _ = deck("land", "-w", wr, "-m", "Add the deck", ok=(2,))
    check("land: refuses when other files are staged, and changes nothing",
          "already staged" in out and g(repo, "branch", "--show-current").strip() == "main", out)
    g(repo, "reset", "-q", "README.md")
    out, _ = deck("land", "-w", wr, "-m", "Add the deck for the planning talk")
    branch = g(repo, "branch", "--show-current").strip()
    files = g(repo, "show", "--name-only", "--format=", "HEAD").split()
    check("land: commits only the deck folder on killer-slides/<date>, never on main",
          re.match(r"killer-slides/\d{4}-\d\d-\d\d$", branch) and sorted(files) == ["talk/assets/deck.css", "talk/assets/deck.js", "talk/assets/theme.css", "talk/deck.html"]
          and g(repo, "log", "-1", "--format=%s%n%b").strip() == "Add the deck for the planning talk", (branch, files, out))
    check("land: the user's own edits stay uncommitted, and nothing is pushed",
          g(repo, "status", "--short").strip() == "M README.md" and "Nothing was pushed" in out and g(repo, "log", "main", "--oneline").count("\n") == 1, g(repo, "status", "--short"))
    with open(repo / "talk" / "deck.html", "a", encoding="utf-8") as fh:
        fh.write("<!-- Generated with Claude Code -->\n")
    out, _ = deck("land", "-w", wr, "-m", "Tweak the deck", ok=(2,))
    check("land: refuses a deck that says who generated it, and unstages it",
          "generated it" in out and not g(repo, "diff", "--cached", "--name-only").strip(), out)
    g(repo, "checkout", "-q", "--", "talk/deck.html")
    (repo / "talk" / "assets" / "images" / "map.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    out, _ = deck("land", "-w", wr, "-m", "Add the route map")
    check("land: a second change in the same run goes on the same branch",
          g(repo, "branch", "--show-current").strip() == branch and g(repo, "log", "--oneline", f"main..{branch}").count("\n") == 2, out)
    g(repo, "switch", "-q", "main")
    deck("new", repo / "talk", "--title", "Repo talk")
    out, _ = deck("init", repo / "talk", "--slug", "goal")
    wg = run_folder(out)
    out, _ = deck("land", "-w", wg, "-m", "Deck on the named branch", "--branch", "main")
    check("land --branch: commits on the named branch when it's the current one (killer-goal)",
          g(repo, "branch", "--show-current").strip() == "main" and g(repo, "log", "-1", "--format=%s").strip() == "Deck on the named branch", out)
    out, _ = deck("init", repo / "talk", "--slug", "goal-init", "--branch", "main")
    wgi = run_folder(out)
    (repo / "talk" / "assets" / "images" / "note.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    out, _ = deck("land", "-w", wgi, "-m", "A picture for the named branch")
    check("land: a branch named at init counts as named, even the one the user started on",
          g(repo, "branch", "--show-current").strip() == "main" and g(repo, "log", "-1", "--format=%s").strip() == "A picture for the named branch", out)
    deck("report", "-w", wr)
    s2 = json.loads((wr / "summary.json").read_text(encoding="utf-8"))
    check("report: the branch and commits come from land", s2["branch"] == branch and len(s2["commits"]) == 2 and s2["commits"][0].endswith("Add the deck for the planning talk"), s2)

    # ---------- the browser checks
    out, err, code = node_script("probe.mjs", f"""
import {{ launchChromium }} from '{DECK_URL}';
try {{ const b = await launchChromium({{ install: false }}); await b.close(); console.log('OK'); }}
catch (e) {{ console.log('NO ' + e.message); }}
""")
    if out != "OK":
        print(f"SKIP browser checks: {out or err}")
    else:
        # A fresh skeleton passes.
        out, code = deck("check", "-w", ws, ok=(0, 1))
        by, deckwide, d = fails_by_slide(ws)
        check("check: a fresh skeleton passes, every slide reached through deck.go and #/n",
              code == 0 and d["ok"] and d["api"] and all(not v for v in by.values()) and not deckwide and len(by) == 3, out)
        check("check: the skeleton's stand-ins are listed", sum(1 for s in d["slides"] for w in s["warns"] if w["check"] == "stand-in") == 2, out)
        check("check: writes a 1920x1080 screenshot of each slide", all(png_size(ws / "shots" / "1920x1080" / f"slide-0{n}.png") == (1920, 1080) for n in (1, 2, 3)))
        check("check: contrast is measured against the pixels behind the text",
              all(c["bg"] != "#000000" and c["ratio"] > 4.5 for s in d["slides"] for c in s["contrast"]) and len(d["slides"][1]["contrast"]) == 3, d["slides"][1]["contrast"])

        # One failure per slide, and a clean one.
        fx = tmp / "fx"
        deck("new", fx, "--title", "Fixture")
        wordy = " ".join(f"word{i}" for i in range(90))
        set_slides(fx, f"""
<section class="slide" data-title="Overflow"><h2 style="white-space:nowrap">This headline is far too long to fit on one line of the slide at this size</h2>{NOTE}</section>
<section class="slide" data-title="Tiny"><h2>A clear claim</h2><p style="font-size:14px">Source: a footnote nobody can read</p>{NOTE}</section>
<section class="slide" data-title="Faint"><h2 style="color:#d0d0d0">Grey on near-white</h2>{NOTE}</section>
<section class="slide" data-title="Wordy"><p style="font-size:30px;max-width:none">{wordy}</p>{NOTE}</section>
<section class="slide" data-title="Clean"><h2>One idea, said plainly</h2>{NOTE}</section>""")
        out, _ = deck("init", fx, "--talk", "10")
        wf = run_folder(out)
        out, code = deck("check", "-w", wf, ok=(0, 1))
        by, deckwide, d = fails_by_slide(wf)
        check("check: exactly overflow, small text, contrast and density, and the clean slide passes",
              code == 1 and by == {1: ["overflow"], 2: ["small-text"], 3: ["contrast"], 4: ["density"], 5: []} and not deckwide, (by, deckwide, out))
        check("check: the failures carry their numbers",
              "past the right edge" in out and "14px" in out and re.search(r"1\.\d+:1, needs 3:1", out) and "90 words" in out, out)
        fr = tmp / "fr"
        deck("new", fr, "--title", "Read alone")
        set_slides(fr, f'<section class="slide"><p style="font-size:30px;max-width:none">{wordy}</p></section>')
        out, _ = deck("init", fr, "--read", "--no-notes")
        wfr = run_folder(out)
        out, code = deck("check", "-w", wfr, ok=(0, 1))
        by, _, d = fails_by_slide(wfr)
        check("check: a deck people read on their own allows more words (90 is a warning), and needs no notes when none were asked for",
              code == 0 and by[1] == [] and [w["check"] for w in d["slides"][0]["warns"]] == ["density"], (by, out))

        # Pictures, motion, layout, SVG labels, notes, a missing image, a script error and the network.
        fx2 = tmp / "fx2"
        deck("new", fx2, "--title", "Fixture two")
        (fx2 / "assets" / "images" / "pale.png").write_bytes(png_bytes(8, 4, 2, [bytes([235, 235, 225] * 8)] * 4, [0, 0, 0, 0]))
        (fx2 / "assets" / "images" / "dark.png").write_bytes(png_bytes(8, 4, 2, [bytes([20, 24, 30] * 8)] * 4, [0, 0, 0, 0]))
        cover = "position:absolute;inset:0;width:100%;height:100%;object-fit:cover"
        set_slides(fx2, f"""
<section class="slide" data-title="Gated"><h2 class="pulse">Moves only when motion is welcome</h2>{NOTE}</section>
<section class="slide" data-title="Ungated"><h2 class="spin">Spins for everyone</h2>{NOTE}</section>
<section class="slide" data-title="Viewport"><h2 style="font-size:5vw">Sized in vw</h2>{NOTE}</section>
<section class="slide" data-title="Pale"><img src="assets/images/pale.png" alt="A pale wall" style="{cover}"><h2 style="position:relative;color:#fff">White on a pale photo</h2>{NOTE}</section>
<section class="slide" data-title="Dark"><img src="assets/images/dark.png" alt="" style="{cover}"><h2 style="position:relative;color:#fff">White on a dark photo</h2>{NOTE}</section>
<section class="slide" data-title="Chart"><h2>A chart with small labels</h2><svg viewBox="0 0 400 100" width="800" height="200"><text x="10" y="50" font-size="8">axis label</text></svg>{NOTE}</section>
<section class="slide" data-title="Silent"><h2>A slide with nothing to say</h2></section>
<section class="slide" data-title="Broken"><h2>Things that break</h2><img src="assets/images/missing.png" alt="A chart" width="200" height="100"><img src="https://example.invalid/logo.png" alt="A logo" width="10" height="10"><script>throw new Error("boom on slide eight")</script>{NOTE}</section>""",
                   head="<style>@media (prefers-reduced-motion: no-preference) { .pulse { animation: pulse 2s infinite } } @keyframes pulse { 50% { opacity: .7 } }"
                        " .spin { animation: spin 3s linear infinite } @keyframes spin { to { transform: rotate(360deg) } }</style>")
        out, _ = deck("init", fx2, "--talk", "10")
        wf2 = run_folder(out)
        out, code = deck("check", "-w", wf2, ok=(0, 1))
        by, deckwide, d = fails_by_slide(wf2)
        check("check: motion gated by prefers-reduced-motion passes; ungated motion fails",
              by[1] == [] and by[2] == ["motion"] and "spin" in out, (by, out))
        check("check: text sized in vw lays out differently at 4:3 and fails", by[3] == ["layout"] and "96px at 1920x1080 but 51.2px" in out, (by, out))
        check("check: white text fails on a pale picture and passes on a dark one (sampled from the render)",
              by[4] == ["contrast"] and by[5] == [] and "#ebebe1" in out, (by, out))
        check("check: SVG text is measured at its rendered size", by[6] == ["small-text"] and '16px "axis label"' in out, (by, out))
        check("check: a slide without notes fails when notes were asked for", by[7] == ["notes"], by)
        check("check: a missing image fails on its slide", by[8] == ["assets"] and "missing.png" in out, by)
        check("check: a request to the network and a script error fail the deck, once each",
              deckwide == ["console", "external"] and out.count("example.invalid") == 1 and "boom on slide eight" in out, (deckwide, out))
        check("check: exit 1 and a summary that names the failing slides", code == 1 and d["summary"]["failingSlides"] == [2, 3, 4, 6, 7, 8], d["summary"])

        # The engine, driven by real key presses.
        out, err, code = node_script("drive.mjs", f"""
import {{ launchChromium }} from '{DECK_URL}';
import {{ pathToFileURL }} from 'node:url';
const url = pathToFileURL(process.argv[2]).href, out = {{}};
const browser = await launchChromium({{ install: false }});
try {{
  const ctx = await browser.newContext({{ viewport: {{ width: 1280, height: 720 }} }});
  const page = await ctx.newPage();
  await page.goto(url);
  const at = () => page.evaluate(() => `${{deck.index}}.${{deck.step}} ${{location.hash}}`);
  out.api = [];
  for (let n = 1; n <= 3; n++) {{ await page.evaluate((i) => deck.go(i), n); out.api.push(await page.evaluate((i) => deck.index === i && getComputedStyle(document.querySelectorAll('.slide')[i - 1]).visibility === 'visible' && document.querySelectorAll('.slide.is-current').length === 1, n)); }}
  await page.keyboard.press('Home');
  out.keys = [];
  for (let k = 0; k < 5; k++) {{ await page.keyboard.press('ArrowRight'); out.keys.push(await at()); }}
  await page.keyboard.press('ArrowLeft'); out.back = await at();
  await page.keyboard.press('Home'); out.home = await at();
  await page.keyboard.press('End'); out.end = await at();
  await page.keyboard.press('PageUp'); out.pageup = await at();
  await page.keyboard.press('Home'); await page.mouse.click(1000, 360); out.click = await at();
  await page.mouse.click(200, 360); out.clickBack = await at();
  const p2 = await ctx.newPage(); await p2.goto(url + '#/3'); out.deep = await p2.evaluate(() => deck.index); await p2.close();
  await page.evaluate(() => {{ location.hash = '#/2/1'; }}); await page.waitForTimeout(100); out.hash = await at();
  out.shown = await page.evaluate(() => [...document.querySelectorAll('.slide')[1].querySelectorAll('[data-step]')].map((e) => getComputedStyle(e).visibility));
  await page.keyboard.press('o');
  out.overview = await page.evaluate(() => [deck.mode, [...document.querySelectorAll('.slide')].filter((s) => getComputedStyle(s).visibility === 'visible').length]);
  const r = await page.evaluate(() => {{ const b = document.querySelectorAll('.slide')[2].getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; }});
  await page.mouse.click(r[0], r[1]); out.picked = [await at(), await page.evaluate(() => deck.mode)];
  await page.keyboard.press('Home');
  const [pop] = await Promise.all([ctx.waitForEvent('page'), page.keyboard.press('p')]);
  await pop.waitForLoadState('load'); await pop.waitForTimeout(200);
  out.presenter = await pop.evaluate(() => [document.documentElement.classList.contains('deck-presenter'), document.querySelector('.pv-notes').textContent.trim().slice(0, 17), document.querySelector('.pv-timer').textContent, getComputedStyle(document.querySelectorAll('.slide')[1]).visibility]);
  await pop.keyboard.press('ArrowRight'); await pop.waitForTimeout(150); out.fromPresenter = await at();
  await page.keyboard.press('End'); await pop.waitForTimeout(150); out.toPresenter = await pop.evaluate(() => deck.index);
  await page.keyboard.press('b'); out.black = await page.evaluate(() => document.documentElement.classList.contains('deck-blackout'));
}} finally {{ await browser.close(); }}
console.log(JSON.stringify(out));
""", talk / "deck.html")
        e = json.loads(out) if code == 0 and out.startswith("{") else {}
        check("engine: deck.go reaches every slide, one at a time", e.get("api") == [True, True, True], (out, err))
        check("engine: the right arrow steps through builds, then slides, and stops at the end",
              e.get("keys") == ["2.0 #/2", "2.1 #/2/1", "2.2 #/2/2", "3.0 #/3", "3.0 #/3"], e.get("keys"))
        check("engine: back lands on the previous slide finished; Home, End and Page Up",
              e.get("back") == "2.2 #/2/2" and e.get("home") == "1.0 #/1" and e.get("end") == "3.0 #/3" and e.get("pageup") == "2.2 #/2/2", e)
        check("engine: a click on the right half goes on, on the left half goes back", e.get("click") == "2.0 #/2" and e.get("clickBack") == "1.0 #/1", e)
        check("engine: #/3 opens on slide 3, and #/2/1 shows slide 2 after one build",
              e.get("deep") == 3 and e.get("hash") == "2.1 #/2/1" and e.get("shown") == ["visible", "hidden"], e)
        check("engine: o shows every slide; a click picks one and leaves the overview",
              e.get("overview") == ["overview", 3] and e.get("picked") == ["3.0 #/3", "normal"], e)
        check("engine: p opens the presenter view with the notes, a timer against the talk length and the next slide",
              e.get("presenter") == [True, "The opening line,", "00:00 of 10:00", "visible"], e.get("presenter"))
        check("engine: the presenter window and the deck follow each other", e.get("fromPresenter") == "2.0 #/2" and e.get("toPresenter") == 3, e)
        check("engine: b blacks out the screen", e.get("black") is True, e)

        # shoot, sheet and pdf.
        out, _ = deck("shoot", "-w", ws, "--steps")
        sizes = {(1920, 1080): "1920x1080", (1280, 720): "1280x720", (1024, 768): "1024x768"}
        check("shoot: every slide at projector, laptop and 4:3 sizes",
              all(png_size(ws / "shots" / tag / f"slide-0{n}.png") == wh for wh, tag in sizes.items() for n in (1, 2, 3)), out)
        check("shoot --steps: the slide before each build",
              sorted(p.name for p in (ws / "shots" / "1280x720").glob("*step*")) == ["slide-02-step-0.png", "slide-02-step-1.png"], out)
        out, _ = deck("sheet", "-w", ws)
        m = re.search(r"(\d+)x(\d+) px -> (.+)", out)
        check("sheet: a PNG of the size it reports, three thumbnails wide",
              m and png_size(m.group(3).strip()) == (int(m.group(1)), int(m.group(2))) == (80 + 3 * 480 + 2 * 24, 80 + 64 + 314), out)
        out, code = deck("pdf", "-w", ws)
        check("pdf: one 1920x1080 page per slide", code == 0 and "3 pages for 3 slides, 1440x810 pt" in out and (talk / "deck.pdf").is_file(), out)
        out, code = deck("pdf", "-w", ws, "--notes")
        check("pdf --notes: one page per slide with the notes under it", code == 0 and "3 pages for 3 slides, 1440x1620 pt" in out and (talk / "deck-notes.pdf").is_file(), out)

        # The blind audience: images only, then a matcher, and quotes that must be real.
        out, _ = deck("audience", "-w", ws, "--room", "planners of night bus timetables", ok=(2,))
        check("audience: a room description that gives away the key messages is refused", "shares words" in out and "timetable" in out, out)
        out, _ = deck("audience", "-w", ws, "--room", "people who plan transport for a city council")
        brief = (ws / "audience" / "round-1" / "brief.md").read_text(encoding="utf-8")
        box = Path(re.search(r"in (.+[\\/]slides)$", out, re.M).group(1)).parent
        check("audience: the folder holds only the slide images, at 1280x720, and an empty scratch folder",
              sorted(str(p.relative_to(box)).replace("\\", "/") for p in box.rglob("*")) == ["scratch", "slides", "slides/01.png", "slides/02.png", "slides/03.png"]
              and png_size(box / "slides" / "01.png") == (1280, 720) and str(box).startswith(str(tmp / "boxes")), sorted(box.rglob("*")))
        check("audience: the brief gives no deck, notes, titles or key messages",
              str(talk).replace("\\", "/") not in brief and str(ws).replace("\\", "/") not in brief and "opening line" not in brief.lower()
              and "One claim" not in brief and "timetable" not in brief.lower() and "three stops" not in brief.lower()
              and "people who plan transport" in brief and "01.png" in brief and "03.png" in brief and "<" + "count>" not in brief, brief)
        out, _ = deck("heard", "-w", ws, ok=(2,))
        check("heard: no reply says to send the same brief to a fresh agent", "no reply" in out and "fresh agent" in out, out)
        (box / "scratch" / "reply.json").write_text('{"argument": "", "remember": "the timetable"}', encoding="utf-8")
        out, _ = deck("heard", "-w", ws, ok=(2,))
        check("heard: a malformed reply is refused with what's wrong", '"argument"' in out and '"remember"' in out, out)
        (box / "scratch" / "reply.json").write_text(json.dumps({
            "argument": "Night buses run late because the timetable is wrong, not because of traffic.",
            "remember": ["The timetable is the problem", "One point at a time", "End with what to do next"],
            "unclear": [{"slide": 2, "problem": "the points read like instructions"}], "best": {"slide": 1, "why": "the big title"}, "notes": ""}), encoding="utf-8")
        out, _ = deck("heard", "-w", ws)
        mbrief = (ws / "audience" / "round-1" / "match-brief.md").read_text(encoding="utf-8")
        mbox = Path(re.search(r"writes reply.json in\s+(.+)$", out, re.M).group(1).strip()).parent
        check("heard: the matcher gets the key messages and the audience's words, not the slides",
              "1. Late buses are a timetable problem" in mbrief and "timetable is wrong, not because of traffic" in mbrief
              and "Slide 2: the points read like instructions" in mbrief and ".png" not in mbrief and not box.exists(), mbrief)
        (mbox / "scratch" / "reply.json").write_text('{"messages": [{"id": 1, "verdict": "landed"}]}', encoding="utf-8")
        out, _ = deck("landed", "-w", ws, ok=(2,))
        check("landed: a reply that skips a message is refused", "message 2 must appear exactly once" in out, out)
        (mbox / "scratch" / "reply.json").write_text(json.dumps({"messages": [
            {"id": 1, "verdict": "landed", "quote": "the timetable is wrong, not because of traffic", "why": "said outright"},
            {"id": 2, "verdict": "partly", "quote": "moving stops helps", "why": "close"}], "extra": ["one point at a time"]}), encoding="utf-8")
        out, code = deck("landed", "-w", ws, ok=(0, 1))
        v = json.loads((ws / "audience" / "round-1" / "verdicts.json").read_text(encoding="utf-8"))["verdicts"]
        check("landed: a verdict whose quote the audience never wrote counts as missed",
              code == 1 and [x["verdict"] for x in v] == ["landed", "missed"] and "isn't in the audience's answer" in out and "1 of 2 key messages landed" in out, out)
        check("landed: both agents' folders are deleted afterwards", not mbox.exists(), mbox)
        out, _ = deck("audience", "-w", ws)
        box2 = Path(re.search(r"in (.+[\\/]slides)$", out, re.M).group(1)).parent
        (box2 / "scratch" / "reply.json").write_text(json.dumps({
            "argument": "Late night buses are a timetable problem rather than traffic, and moving three stops would fix most of it.",
            "remember": ["timetable, not traffic", "moving three stops fixes most of it", "a call to action at the end"], "unclear": []}), encoding="utf-8")
        out, _ = deck("heard", "-w", ws)
        mbox2 = Path(re.search(r"writes reply.json in\s+(.+)$", out, re.M).group(1).strip()).parent
        (mbox2 / "scratch" / "reply.json").write_text(json.dumps({"messages": [
            {"id": 1, "verdict": "landed", "quote": "Timetable, not traffic", "why": ""},
            {"id": 2, "verdict": "landed", "quote": "moving  three stops fixes most of it.", "why": ""}]}), encoding="utf-8")
        out, code = deck("landed", "-w", ws, ok=(0, 1))
        check("landed: round 2, every message landed (quotes match ignoring case, spacing and end punctuation)",
              code == 0 and "2 of 2 key messages landed" in out, out)

        out, _ = deck("report", "-w", ws)
        summary = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
        report = (ws / "report.md").read_text(encoding="utf-8")
        check("report: done when check passes on this deck, the PDF is current and every message landed",
              summary["status"] == "done" and summary["numbers"]["slides"] == 3 and summary["numbers"]["messages_landed"] == 2
              and summary["numbers"]["audience_rounds"] == 2 and summary["numbers"]["pdf_pages"] == 3 and "2 of 2 key messages (round 2)" in summary["headline"], summary)
        check("report.md: both rounds, the audience's words, the keys and the decisions",
              "**Round 1**: 1 of 2 landed" in report and "**Round 2**: 2 of 2 landed" in report and "the points read like instructions" in report
              and "| p | Presenter view" in report and "## Decisions to check" in report and "## Waiting for your OK" in report, report)
        with open(talk / "deck.html", "a", encoding="utf-8") as fh:
            fh.write("\n")
        deck("report", "-w", ws)
        summary = json.loads((ws / "summary.json").read_text(encoding="utf-8"))
        check("report: a deck changed after the last check is partial, and says to run check again",
              summary["status"] == "partial" and "run check again" in summary["headline"] and summary["numbers"]["check_current"] is False, summary)

        # Fonts from the killer-skills font pack, self-hosted in the deck.
        pack = Path(os.environ.get("KILLER_SKILLS_CACHE") or Path.home() / ".cache") / "killer-skills-fonts" / "fonts.json"
        if not pack.is_file():
            print("SKIP fonts in a deck: the font pack isn't installed")
        else:
            ft = tmp / "ft"
            deck("new", ft, "--title", "Fonts")
            r = subprocess.run([NODE, str(SKILL / "scripts" / "fonts.mjs"), "add", str(ft), "Newsreader"], capture_output=True, text=True, env=ENV)
            pre = next(x for x in (ft / "assets" / "fonts" / "newsreader").iterdir() if x.suffix == ".woff2")
            set_slides(ft, f"""<section class="slide"><h2 style="font-family:Newsreader">Set in Newsreader</h2><p style="font-family:'Made Up Sans'">And a face the deck doesn't have</p>{NOTE}</section>""",
                       head='<link rel="stylesheet" href="assets/fonts/fonts.css">'
                            f'<link rel="preload" href="assets/fonts/newsreader/{pre.name}" as="font" type="font/woff2" crossorigin>')
            out, _ = deck("init", ft, "--read")
            wft = run_folder(out)
            out, code = deck("check", "-w", wft, ok=(0, 1))
            fonts = {f["family"]: f["status"] for f in json.loads((wft / "check.json").read_text(encoding="utf-8"))["fonts"]}
            check("fonts: a face from the pack loads from the deck's own folder; a face it doesn't ship is a warning",
                  r.returncode == 0 and fonts.get("Newsreader") == "loaded" and fonts.get("Made Up Sans") == "not in the deck"
                  and 'WARN  font "Made Up Sans"' in out, (fonts, out))
            check("fonts: a crossorigin preload, which browsers block for a deck opened from disk, fails with what to do",
                  code == 1 and "remove it" in out, out)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
