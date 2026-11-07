---
name: killer-slides
description: "Make a presentation deck people remember, as one self-contained HTML file that runs in any browser: keys, clicks and swipes, builds, a presenter view with notes and a timer, an overview and print to PDF. You lead: ask a few setup questions, write the talk as claims before any slide, invent a look from the subject, build one idea per slide with speaker notes. Then a script renders every slide at projector, laptop and 4:3 sizes and measures overflow, text too small for the back of the room, contrast against the real pixels, words per slide, motion and network requests. Last, a blind audience agent sees only the slide images and says what the talk argues, and a second blind agent checks that against the key messages. Use whenever the user wants slides, a deck, a talk, a pitch, a keynote, a lecture or a presentation made, redone or improved, even if they never say 'killer-slides'. For a .pptx file, anthropic-skills:pptx fits better; for a website, killer-design."
---

# Killer Slides

You are the **lead**. You write the talk, design the deck and build it; scripts and fresh agents check it. The bar is a deck a top studio could have made for *this* talk: one idea per slide, a visual system that comes from the subject, two or three moments the room will repeat afterwards, and notes the speaker can deliver. The deck is done when `deck.mjs check` passes on the deck as it is, you've looked at every slide, and a blind audience that saw only the slides came away with the key messages. You never judge that last part yourself: you know what each slide means, so you'd see it in slides that don't show it.

`scripts/deck.mjs` in this skill's folder does the rest: the skeleton, the renders, the checks, the PDF, the audience folders, the books and the report. It needs Node 20 or later; if `node` isn't on the PATH, use `~/.cache/killer-skills-node/node.exe` (the Windows installer puts Node there when it's missing). For the renders it uses Playwright from the project, then the killer-skills runtime in `~/.cache/killer-skills`, then killer-design's cache; failing those, the first run installs it into `~/.cache/killer-slides`, which needs the network. `deck.mjs <command> --help` lists a command's options. `scripts/fonts.mjs` is killer-design's font script: it copies faces from the killer-skills font pack into the deck.

## The rules that make it work

- **The story before the slides.** The outline is a list of claims, one sentence per slide. Slides get designed once the claims make the argument on their own.
- **Measured, not guessed.** Overflow, text size, contrast, words, motion and network requests are measured in the real render by `check`. A FAIL is a bug, whatever it looks like on your screen.
- **Look at every slide.** `check` can't see a dull or confusing slide. Read the screenshots and the contact sheet like a critic who didn't make them, every round.
- **The audience is blind.** It sees slide images in a folder of their own: no deck, no notes, no titles, no key messages, nothing from earlier rounds. A second fresh agent compares what it took away with the key messages, and must quote it. Start both as `general-purpose`, never `fork`: a fork inherits this conversation.
- **Fix the deck, never the brief.** When a message misses, the slides change, and a new audience agent looks again.
- **Self-contained.** The deck loads nothing from the network, opens from a file and works in any current browser. `check` blocks and fails every outside request.
- **No invented facts, no attribution.** Numbers, quotes and names the user didn't give are marked `class="todo"` and listed. Nothing in the deck, its notes or its commits says who or what made it.

## 1. Setup: ask, don't make the user remember options

Look at the project for a minute first: a README or docs that say what the talk is about, a `DESIGN.md` or brand guide, a killer-design site, data the talk could chart. Then ask with AskUserQuestion, in one call, skipping anything the user already said.

| Header | Question | Options (the first is the default) |
|---|---|---|
| Talk | How long is the talk? | **10 minutes (Recommended)** · 20 minutes · 45 minutes · Not a talk: a deck people read on their own |
| Look | What should it look like? | **Invent a look from the subject (Recommended)** · Follow our brand (a guide in the repo or one you point to) · Match the project's site. If you found a brand or a site, put it first as the recommended one |
| Notes | Speaker notes? | **Yes, what I'd say on each slide (Recommended)** · No |
| Check | Have a blind reader check that it lands? | **Yes (Recommended)**: a fresh agent sees only the slides and says what the talk argues · No |

Then, in chat, if they haven't said: who the talk is for, what they should do afterwards, and **the three things people should leave with**. Those are the key messages the blind audience is checked against, so help turn topics into claims ("costs" becomes "the night service now costs less than the day service did"). If AskUserQuestion isn't available, or the user said "just run it", use the defaults, write the key messages yourself from the material, record them with `decide`, and say so in one line.

**Defaults:** a 10-minute talk · a look invented from the subject · speaker notes · the blind check, at most 3 rounds · the HTML deck and a PDF · the deck in `slides/<short-name>/` in the current folder, unless the user names a place.

Start the run:

```
node <skill-dir>/scripts/deck.mjs init slides/<name> --talk 10 --message "<key message>" --message "..." --message "..." [--room "<who's in the room>"] [--read] [--no-notes] [--no-blind] [--no-pdf] [--branch <name>]
```

The deck folder doesn't need to exist yet: `new` writes it in step 4. `init` prints the run folder, `~/.claude/killer-slides/<deck>/<date>-<slug>/`. The run lives there, outside the project, so nothing that reaches an agent can lead it to the key messages or earlier rounds. `--room` describes the people, never the subject: it goes into the audience's brief, and `audience` refuses one that shares words with the key messages.

## 2. Story first

Read `references/slides-craft.md`, then write `<run>/outline.md`:

1. The one sentence the talk exists to land, and the key messages.
2. The arc, and where each part sits in it.
3. The slides as claims: one numbered sentence each, with the form each will take (statement, big number, evidence, diagram...). Read the claims alone, in order: they should make the argument.
4. The two or three moments that should land hardest, and why.
5. The notes for each slide: what the speaker says, written out, at about 140 words a minute.

Show the user the claims and the moments in a few lines, and carry on unless they object. Reacting to an outline is cheaper than reacting to a deck.

## 3. Direction

Set the look the way killer-design does. If its guides are installed (`../killer-design/references/`), read `typography.md` and `color.md`; `slides-craft.md` covers what changes on a projected slide.

- **Find the concept in the subject's world,** past the first association. Sketch three different directions in a line each (concept, type, colour temperature, layout attitude), pick one, and keep the other two for the handoff.
- **Type:** a display face for claims and statements at 64–200px and a text face at 36–48px, with real contrast in scale. **Colour:** light or dark for the room, tinted neutrals, one accent that points. **Grid:** one grid for every slide, with claims in consistent positions.
- **The signature:** one visual idea from the concept, carried through the deck, and how the moments use it.
- **A brand or a site:** if the user picked one, its tokens, faces and finished artwork are the direction. Take the values verbatim and find the moments inside it.

Write the direction as the comment at the top of `theme.css` (concept, type, palette, grid, signature), so the next session keeps to it.

## 4. Build

```
node <skill-dir>/scripts/deck.mjs new slides/<name> --title "<title>" --minutes 10
node <skill-dir>/scripts/fonts.mjs add slides/<name> "<display face>" "<text face>"
```

`new` writes `deck.html`, the engine (`assets/deck.js` and `assets/deck.css`, which you leave alone) and `assets/theme.css`, whose values are stand-ins, not a design. Read `references/deck-format.md` before writing slides: the markup, builds, notes, fonts, pictures, and the rules that keep the checks honest. The ones that bite most: size everything in px on the 1920×1080 canvas (never `vw`, `vh` or media queries inside a slide), link `fonts.css` without the preload line `fonts.mjs` prints, and load nothing from the network.

Replace the theme's tokens with the direction, then build the slides from the outline: the claim as each title, the evidence under it, the notes in `<aside class="notes">`. Draw charts and diagrams as inline SVG from real data. Mark every stand-in `class="todo"`.

## 5. Look, check and fix

```
node <skill-dir>/scripts/deck.mjs check -w <run>      # measures every slide, writes check.json, exits 1 on any FAIL
node <skill-dir>/scripts/deck.mjs shoot -w <run> --steps
node <skill-dir>/scripts/deck.mjs sheet -w <run>
```

`check` renders each slide with every build in at 1920×1080, then again at 1024×768 with reduced motion on. It fails text past the slide's edge or cut off by a box, text under 20px, contrast under 4.5:1 (3:1 for large text) against the actual pixels behind it, more than 70 words on a slide (120 for a deck read alone), missing notes, missing files, fonts that didn't load, script errors, any request to the network, motion with reduced motion on, and layout that changes with the window. `deck-format.md` has the full table. `shoot` saves every slide at 1920×1080, 1280×720 and a letterboxed 1024×768, and the slide before each build with `--steps`. `sheet` puts every slide on one numbered contact sheet and outlines the ones that failed the last check.

Then look, with the Read tool. The contact sheet first: rhythm, consistent positions, a visible system, the moments standing out. Then every slide at 1920×1080, then the 1024×768 set and the builds. Review each one like a design lead who didn't make it: what does the eye see first, is the claim the title, could it be read from the back, is anything there that doesn't need to be? Run the test at the end of `slides-craft.md`. Write down the biggest problems in order of impact, fix structure before details, and run all three commands again. FAIL lines are bugs; WARN lines are judgment calls. Plan on at least two rounds, and stop when `check` passes and another round wouldn't change anything the room would notice.

## 6. The blind audience

Skip this if the user said no. Otherwise, once `check` passes:

```
node <skill-dir>/scripts/deck.mjs audience -w <run>
```

It renders every slide at 1280×720 into a new folder under the temp directory and writes `<run>/audience/round-N/brief.md`. Note the project's `git status`, then send the brief, exactly as written, to one fresh agent: Agent tool, `subagent_type: "general-purpose"`, `run_in_background: false`, `description: "Audience"`. It writes `reply.json` in its scratch folder. Then:

```
node <skill-dir>/scripts/deck.mjs heard -w <run>       # reads the reply, writes match-brief.md
```

Send `match-brief.md` the same way to one more fresh agent (`description: "Matcher"`). It sees the key messages and the audience's words, never the slides. Then:

```
node <skill-dir>/scripts/deck.mjs landed -w <run>      # exits 0 when every key message landed
```

A `landed` or `partly` counts only if its quote is in the audience's answer word for word. Check `git status` again: neither agent should change anything, and `heard` warns if the deck folder changed while they had it. If a message missed, or a slide was reported unreadable, find out why on the slides. Is the claim on a slide, as a title, in plain words? Does the evidence show it at a glance? Fix the deck, run `check` again, and start a new round with `audience`: a new folder and new agents. Stop after 3 rounds and report what still misses. `references/briefs.md` has the brief templates and what the script checks in each reply. These agents only look at images and read text; they run no project code, so they need no sandbox.

## 7. Export and hand off

```
node <skill-dir>/scripts/deck.mjs pdf -w <run>              # one 1920x1080 page per slide, checked
node <skill-dir>/scripts/deck.mjs pdf -w <run> --notes      # with notes: each slide over what's said on it
node <skill-dir>/scripts/deck.mjs land -w <run> -m "<what changed and why>"    # only when the deck is in a git repo
node <skill-dir>/scripts/deck.mjs report -w <run>
```

`land` commits only the deck folder, on a new local branch `killer-slides/<date>` (never the branch the user was on). It refuses a message or a file that credits an AI, and never pushes. `report` writes `<run>/report.md` and `<run>/summary.json`. Its status is `done` only when the last `check` passed on the deck as it is now, the PDF matches it, and every key message landed.

Tell the user where the deck is and how to present it: open `deck.html`, → and ← to move, `p` for the presenter view in a second window, `o` for the overview, `f` for full screen. Then the PDFs, the contact sheet (inline if you can show images), the direction in two sentences and the moments, what the blind audience said the talk argues in each round, every stand-in still to fill, the two other directions as an offer, and anything you couldn't check.

## Running unattended

killer-goal runs this skill overnight, and a user may start it and walk away. Then:

- **The setup answers come with the request.** Don't ask: use what was given and the defaults for the rest, and say so in one line. Without key messages, write them from the material and record them with `decide`.
- **When killer-goal names a branch, commit there** instead of making a new `killer-slides/<date>` branch. `land --branch <name>` commits on that branch when it's the current one.
- **Questions that come up later don't stop the run.** Take the safe choice, carry on, and record it with `deck.mjs decide -w <run> "<text>"`. They're listed under "Decisions to check" in the report.
- **Nothing outward-facing happens unattended:** no push, pull request, publishing the deck, upload or settings change. Prepare it, record it with `deck.mjs wait -w <run> "<text>" --command "<cmd>"`, and it's listed under "Waiting for your OK".
- **Finish with `deck.mjs report`,** so `summary.json` is there for whoever reads the run in the morning, and end with one line: `killer-slides: <done | partial | blocked> · <headline> · <path to report.md>`.

## When things go wrong

- **No browser:** if Playwright can't be found or installed (offline, no npm), the render commands exit 2 and the report says `blocked`. Say so plainly, review the HTML by reading it against `deck-format.md` and `slides-craft.md`, and never claim a slide was measured or seen.
- **A FAIL on something deliberate:** a picture meant to bleed takes `data-bleed`; a decorative numeral takes `aria-hidden="true"`. Only when it's true: hiding real content from the checks doesn't make the room able to read it.
- **Layout differs at 1024×768:** something inside a slide uses `vw`, `vh`, `clamp()` with viewport units or a media query, usually a web habit. Use px.
- **Contrast fails on a photo:** the measure is the photo's actual pixels. Add a scrim, crop to a calmer area, or move the text.
- **A console error about CORS, or a file the browser refused:** the deck uses something browsers block for a file opened from disk, usually a `crossorigin` preload, `fetch()` or a module script. `deck-format.md` lists them.
- **The presenter view doesn't open:** the browser blocked the pop-up. Allow pop-ups for the file and press `p` again.
- **The same message misses twice:** the story is the problem, not the styling. Find the slide that should carry it; if none says it plainly as a claim, rewrite or add one, and check the outline still makes the argument.
- **An agent wrote no usable reply:** `heard` or `landed` says what's wrong. Send the same brief to a fresh agent once and run the command again; if that fails too, record it with `decide` and say so in the report.
- **The deck changed while an agent had it, and you didn't change it:** restore it from git or your last copy, and run the round again.
- **The notes run long:** `check` adds them up at 140 words a minute. Cut the notes or the slides, not the speaking pace.
