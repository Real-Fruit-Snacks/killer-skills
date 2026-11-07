# Design: killer-skills page

## Concept
A press proof. Before anything is printed, someone checks a proof sheet, marks every fault in red, and sends it back until it's right. That is what every skill in the set does to its own work, so the page is set like a proof on the makeready table: crop marks, non-photo-blue layout guides, a slug line, a colour bar, and red pen on a real draft. A visitor should leave thinking these skills are exacting, and that they don't hand you a first draft.

## Audience and primary action
Claude Code users who landed from the repo, a link or the README. They need to know what the skills are, that each one downloads on its own, and how to install. Primary action: copy the install line. Second: download one skill's zip.

## Type
Display and text: Archivo, one variable file (width 62–125%, weight 100–900). Display is set ExtraCondensed (width 62%) at weight 800, as large as the column allows, like a press headline. Text is width 100% at 400. One family in two roles keeps it disciplined; the width axis does the contrast.
Mono: DM Mono 400/500, for commands, the proof sheets and the reviewers' notes. It reads like a typed printout.
Scale: 16→18px base, ratio 1.2 (phones) → 1.414 (desktop), from scripts/scale.mjs. The hero headline goes past the scale with its own clamp.

## Color
Light only: it's paper.
- bg `oklch(0.952 0.006 255)`: the cool grey of a makeready table
- sheet `oklch(0.993 0.002 255)`: proof paper, for the sheets and the install ticket
- ink `oklch(0.19 0.012 265)`: process black
- ink-muted `oklch(0.43 0.015 260)`
- line `oklch(0.84 0.01 255)`
- red `oklch(0.54 0.205 29)`: the proofreader's red pencil. Only for marks, faults and focus
- blue `oklch(0.74 0.085 228)`: non-photo blue, for guides and crop marks (decorative); blue-ink `oklch(0.5 0.09 235)` when it carries text
- Buttons are ink with sheet-white text. Red never fills a button: it means "wrong here".
- The colour bar in the footer is the one place cyan, magenta and yellow appear.

## Layout
A 76rem wide column on a 12-column grid. The hero splits 7/5: the headline and install on the left, the proof on the right, breaking the grid with two rotated sheets. The skills with evidence from a real run get full-width plates separated by ink rules, each name set huge. The rest of the set follows as a ruled docket, one row a skill: what it does, the check its script makes before the job counts as done, what it needs, and its download. A skill moves up to a plate when it has a real run worth printing; nothing on a plate is invented. Then a dense block for "Tested on", "Known issues" and the installer settings; then the footer slug line. Sections differ in density on purpose.

## Signature
1. Red pen on real work: the marks draw in over a real draft, and scores are circled by hand.
2. Print furniture: crop marks, faint non-photo-blue column guides, the slug line and the colour bar.

## Wow moment
The hero proof replays a real killer-council run (2026-11-04, a word-count script). Sheet one is the round 1 README, exactly as reviewed. From 0.45 s the reviewers' real findings mark it up: a strike through `pip install wordfreq` ("unrelated PyPI package"), a ring round the documented command ("crashes: ValueError"), a wavy line under "most common" ("prints the least common first"), then a circled 10 at 1.55 s. At 2.15 s the round 2 sheet lands across its foot and a double ring stamps 87, passed. Everything has settled by 3.1 s; the headline is readable from the first frame and nothing waits on the animation. (shoot.mjs calls that "continuous" because its 2.6 s and 4 s frames differ; nothing loops.)
- The animation names live in custom properties on `.proof`, so one set of rules serves both triggers: side by side (wider than 68rem) it plays on load; stacked, script.js adds `.play` when the proof's top scrolls into view.
- No JS: side by side it still plays (CSS only); stacked it stays the finished frame. JS adds only that trigger, the replay button and the copy buttons.
- Reduced motion: the finished frame, with both sheets, every mark and both scores.
- Phone: the proof sits under the install ticket, full width, with the notes under their lines and the second sheet overlapping the first one's foot.

## Imagery and texture
No stock imagery. The proofs are real text in HTML. The killer-repo plate shows real renders of this repo's README from `render.mjs`: the top 1280×996 of the full desktop render, halved to WebP. Re-render them when the README's first screen changes, with `--wait` long enough for the cover's terminal to finish typing. Paper gets a light shadow and a 1px edge; nothing else casts one.

The social card (`assets/og.png`, 1200×630) is `og-card.html`, the same sheets with the animation names switched off, rendered with killer-repo's `card.mjs` (the command is in the file). Re-render it when the proof changes. The README keeps its RFS cover; this page and its card are its own.

## Motion
The hero choreography above, once. Elsewhere only 150ms hover and focus changes, and the copy button's confirmation. Everything sits inside `prefers-reduced-motion: no-preference`.

## Voice
Plain and exact, first person where the maintainer speaks. Real numbers from real runs, and every quote from a reviewer is verbatim or trimmed, never invented. No stock marketing words, no exclamation marks.

## Alternatives considered
- The judges' table: a dark stage where reviewer score cards flip up in giant numerals, 10 / 20 / 8, then 74 / 90 / 87. Strong, but it's about the council alone.
- The contact sheet: a film strip of real screenshots at 390, 768 and 1440, with grease-pencil picks and a loupe that follows the pointer. Suits killer-design best, less so the other two.
