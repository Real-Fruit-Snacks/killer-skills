---
name: killer-repo
description: "Make a GitHub repository page people land on and think 'whoa': a README built around one wow moment that shows the project doing its most interesting true thing, plus the description, topics, social card, community files and workflows around it. Invents an identity for each repo instead of applying a template, checks every claim by running it, renders the README exactly as GitHub will in light and dark at desktop and phone widths, and fixes what it sees. Starts with a few setup questions. Use whenever the user wants to write, rewrite, improve, polish or refresh a README or repo page, set up a repo's About panel, topics or social preview, add issue templates or repo workflows, or get a project ready to launch or share, even if they only say 'my repo looks boring' or 'make this README better'. For the project's website itself, killer-design fits better; for docs that must work as written, killer-docs; for cutting a release, killer-release."
---

# Killer repo

The bar: someone lands on the repo (from a search, a shared link, a release) and within ten seconds knows what it is, believes it works and wants to try it. And they think "whoa". Tidy, complete and correct is the floor, not the goal. Most READMEs follow the same template, which is exactly why they're forgettable.

Three habits get you there:

1. **Decide the story before writing.** Who lands here, what they need to believe, and what they should do next. Everything else follows from that.
2. **Design one wow moment.** The top of the README *shows* the project doing its most interesting true thing, in whatever medium fits this project. See `references/wow.md`.
3. **Look at the result.** Render the README exactly as GitHub will, in light and dark, on desktop and phone, look at it like a critic, fix it, and look again.

And one floor under all of it: **everything on the page is true.** A wow moment that leads to a broken install is worse than no wow at all.

There is no template. Every repo gets its own structure, voice and look, invented from what the project is: no fixed section order, no required sections. If the user wants an existing identity kept, see "Working with an existing identity" near the end.

The scripts in `scripts/` need Node 20 or newer; if `node` isn't on the PATH, run them with `~/.cache/killer-skills-node/node.exe`, where the Windows installer puts Node when it's missing. They use Playwright from the project if there is one, then the killer-skills runtime in `~/.cache/killer-skills` (the Windows installer puts Playwright, its Chromium and Mermaid there), then killer-design's copy. Failing all of those, the first run installs Playwright and Chromium into `~/.cache/killer-repo`, which needs the network. GitHub's Markdown stylesheet and an offline Markdown renderer ship in `vendor/`.

- `audit.mjs [repo]`: broken links and anchors, wrong letter case, images that are ignored or not committed, image weight, badges, and the repo as GitHub shows it (About panel, topics, social card, license, releases, community files, failing workflows).
- `render.mjs [repo | README]`: the README through GitHub's own Markdown renderer, screenshotted in light and dark at desktop and phone widths. `--url` screenshots a live GitHub page instead. When GitHub can't be reached it makes an approximate render instead (`--offline` asks for one): close, not exact, labelled on every screenshot. Treat an approximate render as a draft check, say so in the handoff, and render with GitHub before calling the README done.
- `card.mjs <file.html>`: any HTML to an image, such as the 1280×640 social card or a designed still for the README.
- `frames.mjs <frame> <frame> ... --out demo.svg`: a few real still frames, from any source, into one animated SVG that GitHub plays in a README, with no ffmpeg needed.
- `handoff.mjs start | decide | wait | finish`: the books of an unattended run, ending in `summary.json` (see "Running unattended").

They're for looking and checking. What the README says and shows is decided in steps 3 to 5.

## 1. Setup

Work out which repo from the request or the current folder; if it's ambiguous, ask. Skip anything the user already said, and ask the rest with AskUserQuestion in one call:

| Header | Question | Options (the first is the default) |
|---|---|---|
| Scope | How far should this go? | **README, About and card (Recommended)**: the README, the description, topics and website, and a social card · **Full makeover**: also the repo's files (issue forms, security policy, contributing guide and so on) and workflows (CI, releases, regenerating visuals) · **README only** · **Audit only**: a report with screenshots of how it looks now; nothing changes |
| Look | Invent a new look, or build on what's there? | **Invent a new one (Recommended)**: an identity drawn from the project's own subject · **Evolve what's there**: keep what people recognize, raise everything else · **Follow my brand**: use a brand guide in the repo, or one the user points to |
| Changes | How should the changes land? | **Branch and pull request (Recommended)** · **Commit to the default branch** · **Leave them uncommitted** for the user to review |
| Council | Have killer-council score the result before it ships? | **No, the screenshots are enough (Recommended)** · **Yes**: blind reviewers score it and the work iterates until it passes; this takes more time and tokens |

Leave out the Council question if killer-council isn't installed. If AskUserQuestion isn't available, or the user said "just do it", use the defaults and say so in one line.

Whatever the scope, anything public (the description, topics, website, social card, release notes, repo settings) waits for an explicit OK at the end, because it changes what everyone sees the moment it's applied.

## 2. Understand the project

Read before deciding anything: the code, the README and docs, releases and their notes, open issues (they show what people actually ask about), the workflows, and how the thing is installed and run. Then **run it**, or get as close as you can: install it the way the README says, use the main feature, and look at what it produces. A project's most interesting true thing is rarely in its old README; it's in what the tool does.

Take the baseline:

```
node <skill-dir>/scripts/audit.mjs <repo>
node <skill-dir>/scripts/render.mjs <repo> --label before
```

Open the four `-first.png` screenshots. That's what a visitor sees today.

Work out:

- **What it is**, in one sentence a stranger would understand.
- **Who lands here, from where, and what they need to decide.** Someone from a search, a friend from a shared link, a user checking a new release. Name the one action you want: install it, try it in the browser, read the guide, star it for later.
- **The most interesting true thing** about it: not a feature list, but the thing that makes someone lean in.
- **The specifics:** real commands, real output, numbers, names, limits and platforms. Specific detail is what keeps a README from reading as generic.
- **Personality:** three adjectives, and one thing it is not.
- **Documented intent:** decisions the repo already made, such as what it deliberately leaves out or who it isn't for. Keep to them, and put anything beyond them in the handoff as a proposal.

Ask the user only for what you can't find or infer *and* that would change the result.

## 3. Find the story

Visitors meet a repo in three layers:

1. **Before the click:** the social card and the description, wherever the link is shared or the repo shows up in search.
2. **The top of the page:** the name, the About panel (description, website, topics) and the file list. The README starts *below* the file list, so a crowded root folder is part of the first impression.
3. **The README.**

Write three genuinely different angles, a line or two each: what the reader sees first, what they believe ten seconds later, and what they do next. Vary the hook (the problem, the result, what it feels like to use), not just the wording. Pick the one that best fits who lands here. The first idea is usually the most generic, which is why you write three.

## 4. Design the wow moment

Ask what the single most interesting true thing about the project is, and how the top of the README could *show* it instead of saying it.

Write two or three candidates and pick the one that's most true to the project and that you can execute beautifully. The medium follows from the project, never the other way round: a game in motion, a document before and after, a map assembling itself, the one line of output people install it for, a diagram of a system that's otherwise invisible. `references/wow.md` has ideas for different kinds of project, meant as inspiration, not a menu. If the project calls for something it doesn't describe, invent it.

Then ask honestly whether someone seeing it would say "whoa" or "nice". If the answer is "nice", be braver: make it bigger, make it more alive, and clear the space around it.

Making it:

- **Capture the real thing:** the actual app, the real output, a recording of a real run, the real architecture. Staged fakes go stale and get noticed.
- **Use whatever tool fits this project:** Playwright for anything in a browser, the app itself or the OS for a desktop app, a recorded session for a terminal tool, `card.mjs` for a designed still, Mermaid or hand-written SVG for a diagram. For motion made of real moments, capture the stills and join them with `frames.mjs`.
- **Make it reproducible.** Commit the script or recipe that produced the visual so it can be regenerated when the project changes. Put it where this repo keeps its scripts, wired up the way the repo runs things (an npm script, a Make target, a `scripts/` folder), not wherever is convenient.
- **The repo's own checks apply to everything you add.** Run its linter, formatter and other CI checks on your new files and on the README itself, since some repos format Markdown too.
- **Light and dark:** an asset that works on both backgrounds, a `<picture>` with one of each, or an SVG that adapts with its own `prefers-color-scheme` media query.
- **Light enough for a phone:** keep what's on the first screen well under 2 MB. GitHub doesn't play video committed to the repo, so motion means animated SVG, GIF, APNG or WebP. `references/readme-craft.md` lists what GitHub keeps and strips.

## 5. Write the README

The structure comes from the story: whatever order gets this reader from "what is this?" to "I'm trying it" fastest. `references/readme-craft.md` covers what GitHub's Markdown can do and how to use it well.

What holds across projects:

- **The first screen does the work:** the name, one sentence, the wow moment and the way in (an install line, a live link, a first command), before any history or feature list.
- **Show real output.** After the quickstart, show what the reader gets, exactly as the tool produces it.
- **Specifics beat adjectives.** "Converts a 4 GB MKV in 90 seconds on a laptop" beats "fast". If an adjective can't be replaced with a fact, cut it.
- **Be honest early:** what it doesn't do, what it was tested on and known issues, near the top rather than buried. Honesty is what makes the rest believable.
- **Cut what says nothing:** empty sections, boilerplate, a table of contents for a README two screens long.

**Truth pass.** Before looking at screenshots, run every install line and example in a clean place (a temp folder, a fresh shell, or a container or VM when it matters). Check every flag and claim against the code, and list only platforms and versions you actually ran. If something can't be verified here, say so in the handoff rather than guess. Stop anything you started along the way (dev servers, watchers, background jobs), and check that it really stopped.

## 6. The rest of the surface, as far as the scope goes

`references/surface.md` has the details and commands for each piece.

- **About panel:** a description that hooks in one line, topics people actually search for, and the website if there is one.
- **Social card:** design it as HTML around this project's own concept, legible as a small thumbnail, and render it with `card.mjs`. GitHub has no API for the social preview, so the user uploads it in Settings, or you do it in a browser they're logged into if they ask.
- **Repo files** (full makeover): only the ones that fit the project. Issue forms for a tool people report bugs against, a security policy for anything security-relevant, a contributing guide only if contributions are wanted. Tidy the root folder only where that's safe: nothing that scripts, installers, other repos or install one-liners point at.
- **Workflows** (full makeover): CI that tests what the README claims, on the platforms it names, so a status badge tells the truth; a release workflow if releases are currently made by hand; a job that regenerates the visual; a link check. A new workflow isn't done until it has passed on the branch.

## 7. Look at the result

```
node <skill-dir>/scripts/render.mjs <repo> --label after
node <skill-dir>/scripts/audit.mjs <repo>
```

Look at every screenshot, first screens first, as if you've never seen the repo:

- **Ten seconds:** from the first screen alone, do you know what it is, believe it works and know what to do? Is the wow moment visible without scrolling, on both desktop and phone?
- **Dark mode:** does every image read on the dark background? A transparent PNG with dark text disappears there.
- **Phone:** do images scale down legibly, does the wow moment survive at 390px, are code lines and tables usable?
- **Rhythm:** does the page breathe, or is it a wall? Is anything competing with the wow moment?
- **Weight:** `render.mjs` reports the kilobytes of images each first screen loads. Is the wow moment worth what it costs on mobile data?
- **The audit:** fix every FAIL, or confirm it's a false alarm and say why in the handoff. Decide on every WARN.

Fix, render, and look again. Then compare with the `before` shots. If the after isn't clearly better, it isn't done.

Once a branch is pushed (for a public repo), check the real thing: `render.mjs --url https://github.com/<owner>/<repo>/tree/<branch> --label live` shows the actual page, file list and all.

If the user chose the council, run killer-council on the README with seats that fit this project. For example: a newcomer who has to get it running from the README alone, someone choosing between this and an alternative, and an accuracy seat that checks claims against the code.

## 8. Land it and hand off

- Commit in the repo's own style, and follow the user's commit rules (their CLAUDE.md, for example rules about attribution). Land it the way they chose in setup; don't push or open a pull request unless that's what they chose.
- **Public changes need an explicit OK.** Show the proposed description, topics and website, and the card, then ask before running `gh repo edit` or touching releases.
- Hand off with:
  - the before and after first screens (desktop and phone, light and dark), as images
  - the story and the wow moment, a sentence each, and how to regenerate the visual
  - what changed, file by file
  - what you verified by running it, and what you couldn't
  - which public changes were applied and which are waiting; where the card is and where to upload it
  - proposals beyond the scope, such as a site with killer-design, the docs walked by a newcomer with killer-docs, a release cut and test-installed with killer-release, or a demo for the release page

## Running unattended

killer-goal runs this skill overnight, and a user may start it and walk away. Then:

- **Start the books first:** `node <skill-dir>/scripts/handoff.mjs start --project <repo> --target "<one line>"`. It prints the run folder.
- **The setup answers come with the request.** Don't ask: use what was given and the defaults for the rest, and say so in one line. "Branch and pull request" becomes "commit on the branch killer-goal named", with no pull request.
- **Calls only the user could make** (a claim you couldn't verify, a tagline you were unsure of): record each with `handoff.mjs decide -w <run> "<text>"`, and list it in the handoff under "Decisions to check".
- **Nothing public happens unattended:** no `gh repo edit`, topics, website, social card upload, release edit or push. Record each with `handoff.mjs wait -w <run> "<text>" --command "<cmd>"`, and list it under "Waiting for your OK".
- **Finish:** write the handoff to `<run>/report.md`, run `audit.mjs --json <run>/audit.json` one last time, then `handoff.mjs finish -w <run> --headline "<what changed>" --checks <run>/audit.json`. It writes `summary.json` for killer-goal, with the commits made since `start`, and prints the line to end with: `killer-repo: <done | partial> · <headline> · <path to the handoff>`. A FAIL left in the audit makes it partial.

## What makes a repo look AI-made

Check the result against these. Each one says "nobody decided anything here".

- Emoji bullets as a feature list: "🚀 Blazing fast", "✨ Easy to use", "🔒 Secure".
- A wall of badges above the fold, or badges for things that aren't true or don't matter.
- The same skeleton whatever the project: Features, Installation, Usage, Contributing, License, with a table of contents.
- Adjectives instead of facts: powerful, seamless, modern, lightweight, robust, blazing.
- A feature list that restates the description five ways.
- Boilerplate endings: "Contributions are welcome! Feel free to open an issue or PR.", "Made with ❤️".
- Screenshots that are placeholders, mockups, or of something other than the real tool.
- Every section the same length and shape.
- A tagline that could describe any project in the category.

## Working with an existing identity

When the user chose "Evolve what's there" or "Follow my brand", find the identity first: a brand guide (`BRAND.md`, a style guide, a brand repo), existing covers and cards, a docs site, a `DESIGN.md`.

- **Follow my brand:** the guide's rules win wherever they speak, and you invent freely where they don't. Use its tools (a cover generator, templates) rather than recreating their output by hand.
- **Evolve:** keep what people recognize, such as the name treatment, the colors and the cover's composition, and raise the bar on everything else: the story, the wow moment, the truth and the rest of the surface.

## If something can't run

Offline, `render.mjs` still renders (approximately) and `audit.mjs` still checks everything local; it says when no outside link could be reached. If a script fails (no Node, no network for the first install, `gh` not logged in), say so plainly and do that part by hand: review the Markdown against `references/readme-craft.md`, and check links yourself. Never claim to have looked at something you didn't render.
