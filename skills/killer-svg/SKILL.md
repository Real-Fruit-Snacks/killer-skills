---
name: killer-svg
description: "Design and hand-write vector graphics people remember: logos and marks, icons and icon sets, diagrams, illustrations, banners, social cards and animated SVG. You lead: ask a few setup questions, set a direction from the subject or the brand, sketch the construction, then write each SVG by hand. A script then proves it: it lints the file, renders it at every size on light, dark and a checkerboard, as an image, in Chromium, Firefox and WebKit, compares the engines pixel by pixel, measures how it stands out on each background, checks it as GitHub shows it and with reduced motion on, and minifies only when the render holds. Last, a blind agent sees only the renders and says what it shows; a second checks that against what it must read as. Use whenever the user wants a logo, icon, diagram, illustration, banner, social card or SVG made, redrawn, cleaned up or checked, even if they never say 'killer-svg'. For a site's favicon set, killer-design fits better; data charts, dataviz; diagrams from text, Mermaid."
---

# Killer SVG

You are the **lead**. You set the direction, draw the construction and write every SVG by hand; a script and fresh agents check it. The bar is a graphic a top studio could have made for *this* subject: one idea, built on a grid, that still reads at the smallest size it will be used and on every background it will sit on. A graphic is done when `svg.mjs check` passes on it as it is, you've looked at every render, and a blind viewer who saw only the images said what it must read as, at its smallest size. You never judge that last part yourself: you know what the shapes mean, so you'd see them in shapes that don't show it.

`scripts/svg.mjs` in this skill's folder does the rest: the run folder, the lint, the renders, the engine comparison, the contrast, GitHub and motion checks, minify, export, the viewer folders and the books. It needs Node 20 or later; if `node` isn't on the PATH, use `~/.cache/killer-skills-node/node.exe` (the Windows installer puts Node there when it's missing). For the renders it uses Playwright from the project, then the killer-skills runtime in `~/.cache/killer-skills`, then killer-design's cache; failing those, the first run installs it into `~/.cache/killer-svg`, which needs the network. Chromium is required. Firefox and WebKit are used when Playwright's own builds of them are installed (the user's Playwright cache often has them); when they aren't, the report says **not checked**, never passed. `svg.mjs <command> --help` lists a command's options.

## The rules that make it work

- **A direction before a path.** Three concepts from the subject, one chosen, the construction written in words (grid, shapes, stroke, palette, how it reduces) before any coordinates.
- **Measured, not guessed.** Well-formed XML, the viewBox, outside references, fonts, contrast on each background, differences between engines and motion with reduced motion on are measured by `check`. A FAIL is a bug, whatever it looks like to you.
- **Look at every size.** `check` can't see a dull mark or a muddy 16 px icon. Read the contact sheets like someone who has never seen the graphic, every round.
- **Not checked is not passed.** An engine that couldn't run is reported as not checked, with the command that would install it. Never write that a graphic works in Safari because it works in Chrome.
- **The viewer is blind.** It sees PNG renders in a folder of their own, named by letter: no SVG, no file names, no brief, nothing from earlier rounds. A second fresh agent compares its words with what the graphic must read as, and must quote them. Start both as `general-purpose`, never `fork`: a fork inherits this conversation.
- **Fix the graphic, never the reads.** When a read misses or the viewer can't make it out at its smallest size, the graphic changes and a new viewer looks again.
- **Self-contained and safe.** No scripts, nothing loaded from outside the file, text outlined or set in a font the file embeds. An SVG shown as an image loads nothing, and that's how most people will see it.
- **No invented brands, no attribution.** Names, taglines and facts the user didn't give are asked for, not made up. Nothing in the files or the commits says who or what made them.

## 1. Setup: ask, don't make the user remember options

Look at the project for a minute first: a README that says what it is, a `DESIGN.md` or brand guide, an existing logo or icons (and their grid and stroke), a killer-design site, where images live (`assets/`, `img/`, `docs/`). Then ask with AskUserQuestion, skipping anything the user already said: the first four questions in one call, and After in a second if it's still open (the tool takes four at a time).

| Header | Question | Options (the first is the default) |
|---|---|---|
| Kind | What are we making? | **A logo or mark (Recommended when none exists)** · An icon or icon set · A diagram · An illustration or banner |
| Look | What should it look like? | **Invent a look from the subject (Recommended)** · Follow our brand (a guide in the repo or one you point to) · Match the project's site. If you found a brand or a site, put it first as the recommended one |
| Used | Where will it be used? | **A website or app (Recommended)** · A GitHub README · An app or favicon icon · Print |
| Check | Have a blind viewer check it reads? | **Yes (Recommended)**: a fresh agent sees only the renders and says what it shows · No |
| After | What happens to the files? | **Commit them on a new local branch (Recommended)** · Leave them uncommitted · Report only: check the graphics that exist, change nothing |

Then, in chat, if they haven't said: what it's for, in their words (the brief), and **what someone who has never seen it must get from it** (the reads). Help turn adjectives into things a viewer could say: "friendly" becomes "a fox's head", "fast" becomes "something moving forward". For an icon set, give each icon its read as `search.svg: a magnifying glass`. If AskUserQuestion isn't available, or the user said "just run it", use the defaults, write the reads yourself from the material, record them with `decide`, and say so in one line.

**Defaults:** a logo · a look invented from the subject · used on a website · the blind check, at most 3 rounds · committed on a new local branch · the files where the project keeps images, else `assets/`.

Start the run:

```
node <skill-dir>/scripts/svg.mjs init --project <folder> --kind <logo|icon|icons|diagram|illustration|banner|card|animation> --brief "<what it is for>" --reads "<what a viewer must get>" [--reads ...] [--files <svg or folder> ...] [--sizes 16,32,...] [--on light,dark] [--target web|github|app|print] [--no-blind] [--branch <name>]
```

Kind becomes `--kind` (`icons` for a set; `banner` or `card` for a wide graphic with its own background; `animation` when the movement is the point), Used becomes `--target`, and No to the blind check becomes `--no-blind`. The files don't need to exist yet. Sizes default by kind: icons 16, 24, 32, 48, 96; logos 16, 32, 64, 256, 1024; banners, cards, diagrams and illustrations their real size and half. A size is the graphic's longer side. `--on` takes `light`, `dark` or `#rrggbb` colours; `logo-dark.svg@dark` limits a file to the backgrounds it's meant for. `init` prints the run folder, `~/.claude/killer-svg/<project>/<date>-<kind>/`, outside the project, so nothing that reaches an agent can lead it to the brief or the reads.

## 2. Direction

Read `references/craft.md`. If killer-design is installed, its `references/color.md` and `typography.md` have more on palettes and type.

- **Find the concept in the subject's world,** past the first association. Write three different concepts in a line each (the idea, the shapes, the colour temperature), pick one, and keep the other two for the handoff.
- **Sketch the construction in words:** the grid (24, 32 or 48 units), the main shapes and how they sit on it, the stroke and corner radius, the palette (two or three colours, and the one-colour version), and what the mark keeps when it shrinks to 16 px.
- **A brand:** if the user picked one, its colours, type and existing marks are the direction. Take the values verbatim and work inside them.

Write it to `<run>/direction.md`, so the next session keeps to it.

## 3. Build

Write each graphic by hand, one file each: a `viewBox`, no fixed size unless the use needs one, `<title>` (and `<desc>` for a diagram), whole numbers on the grid for the main edges. For an icon set, one grid, one stroke width and one corner radius across every icon. If a logo or icon turns to mush at 16 to 32 px, draw a reduced version beside it as `<name>-small.svg`: the viewer is shown it at 32 px and under. Add the files to the run as you write them:

```
node <skill-dir>/scripts/svg.mjs add -w <run> <svg or folder> ...
```

## 4. Look, check and fix

```
node <skill-dir>/scripts/svg.mjs check -w <run>     # writes check.json; exits 1 on any FAIL
node <skill-dir>/scripts/svg.mjs sheet -w <run>     # every file, size and background on one image
```

`check` lints each file, renders it at every size on each background and a checkerboard through `<img>` in every engine that starts, compares the engines pixel by pixel, measures how much of it stands out on each background, checks it as GitHub shows it (`--target github`) and checks motion with reduced motion on (animated files). `references/checks.md` says what each check measures, its thresholds and the usual fixes. Each file gets a contact sheet in `<run>/renders/`, with the small sizes enlarged.

Then look, with the Read tool: the overview sheet first, then each file's sheet. Judge it like someone who has never seen it: what do you see first, is it one idea, does the 16 px version still say it, do the strokes and gaps survive, does it hold on dark? Write down the biggest problems in order of impact, fix shape before detail, and run both commands again. FAIL lines are bugs; WARN lines are judgment calls. Plan on at least two rounds, and stop when `check` passes and another round wouldn't change anything a viewer would notice.

## 5. The blind viewer

Skip this if the user said no. Otherwise, once `check` passes:

```
node <skill-dir>/scripts/svg.mjs audience -w <run>
```

It renders each graphic into a new folder under the temp directory: a board of every size at real size on each background, a large render on each, and frames if it moves, named by letter in a shuffled order. It writes `<run>/audience/round-N/brief.md`. Note the project's `git status`, then send the brief, exactly as written, to one fresh agent: Agent tool, `subagent_type: "general-purpose"`, `run_in_background: false`, `description: "Viewer"`. It writes `reply.json` in its scratch folder. Then:

```
node <skill-dir>/scripts/svg.mjs heard -w <run>      # reads the reply, writes match-brief.md
```

Send `match-brief.md` the same way to one more fresh agent (`description: "Matcher"`). It sees the reads and the viewer's words, never the images. Then:

```
node <skill-dir>/scripts/svg.mjs landed -w <run>     # exits 0 when every read landed, at every size
```

A `landed` or `partly` counts only if its quote is in the viewer's words, about the right graphic. A graphic the viewer couldn't recognise at its smallest size fails the round too. Check `git status` again: neither agent should change anything. If a read missed, find out why on the graphic: is the idea in the shapes, or only in your head? Fix it, run `check` again, and start a new round with `audience`: a new folder and new agents. Stop after 3 rounds and report what still misses. `references/briefs.md` has the brief templates and what the script checks in each reply. These agents only look at images, so they need no sandbox.

## 6. Minify, export and hand off

```
node <skill-dir>/scripts/svg.mjs minify <svg> [--out <file>] -w <run>     # written only if every render matches
node <skill-dir>/scripts/svg.mjs export <svg> --png 16,32,180,512 [--out <folder>] -w <run>
node <skill-dir>/scripts/svg.mjs land -w <run> -m "<what changed and why>"
node <skill-dir>/scripts/svg.mjs report -w <run>
```

With "Report only", skip minify, export and land: they write files. `minify` strips comments, editor metadata and long decimals, renders before and after at the run's sizes and backgrounds, and refuses to write if anything moved, saying where. Run `check` again after it. `export` writes transparent PNGs through Chromium; for a site's full favicon and app icon set, use killer-design's `icons.mjs build` on the SVG. `land` commits the run's files and exports on a new local branch `killer-svg/<date>` (never the branch the user was on), refuses a message or a file that credits an AI, and never pushes. `report` writes `<run>/report.md` and `<run>/summary.json`; its status is `done` only when the last `check` passed on the files as they are and the last viewer round, on these files, landed every read at every size.

Tell the user where the files are and how to use them (an `<img>` tag, a `<picture>` with a dark source for a README, `currentColor` icons inline), the overview sheet (inline if you can show images), the direction in two sentences, what the blind viewer called it in each round, the engines not checked and how to check them, and the two other concepts as an offer.

## Running unattended

killer-goal runs this skill overnight, and a user may start it and walk away. Then:

- **The setup answers come with the request.** Don't ask: use what was given and the defaults for the rest, and say so in one line. Without reads, write them from the material and record them with `decide`.
- **When killer-goal names a branch, commit there** instead of making a new `killer-svg/<date>` branch. `init --branch <name>` or `land --branch <name>` commits on that branch when it's the current one.
- **Questions that come up later don't stop the run.** Take the safe choice, carry on, and record it with `svg.mjs decide -w <run> "<text>"`. They're listed under "Decisions to check" in the report.
- **Nothing outward-facing happens unattended:** no push, pull request, publishing, upload, browser download or settings change. Prepare it, record it with `svg.mjs wait -w <run> "<text>" --command "<cmd>"`, and it's listed under "Waiting for your OK". `render` records the command that installs a missing engine this way by itself.
- **Finish with `svg.mjs report`,** so `summary.json` is there for whoever reads the run in the morning, and end with one line: `killer-svg: <done | partial | blocked> · <headline> · <path to report.md>`.

## When things go wrong

- **No browser:** if Playwright can't be found or installed (offline, no npm), the render commands exit 2 and the report says `blocked`. `lint` still runs with no browser. Say so plainly and never claim a graphic was rendered or seen.
- **Firefox or WebKit not checked:** `render` says why and records the install command under "Waiting for your OK". It needs the network, and installed they take a few hundred MB (on Windows, Firefox about 350 MB and WebKit about 170 MB); run it only with the user's say-so, then `check` again.
- **An engine draws it differently:** the diff image shows where. The usual cause is a feature that engine lacks (the CSS `d` property and `context-stroke` markers in WebKit, `<foreignObject>`); `checks.md` lists them. Draw it another way rather than accept the gap.
- **Text looks different in Firefox or WebKit:** each engine fills a generic or missing font with its own face. For a logo, draw the letters as paths; elsewhere, embed the font or accept it knowingly.
- **It disappears on dark (or light):** give it a colour that holds on both, an outline or a tile, or make a variant for that background and add it as `<file>@dark`. For a README, show the pair through `<picture>` with a `prefers-color-scheme` source.
- **It never stops with reduced motion on:** wrap the animation in `@media (prefers-reduced-motion: no-preference)`, or for SMIL, hide the moving part and show a still one under `prefers-reduced-motion: reduce`.
- **minify refuses:** it rounded something that matters, usually a small viewBox. Try `--decimals 3`, or leave the file as it is.
- **The viewer can't make it out at 16 px:** that's the size it fails at in a tab or a toolbar. Draw `<name>-small.svg` with one idea, heavy strokes and open counters, and run a new round.
- **An agent wrote no usable reply:** `heard` or `landed` says what's wrong. Send the same brief to a fresh agent once and run the command again; if that fails too, record it with `decide` and say so in the report.
- **The files changed while an agent had them, and you didn't change them:** restore them from git or your last copy, and run the round again.
