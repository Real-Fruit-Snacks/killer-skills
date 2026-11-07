# Recipes

Common goals turned into done-when checks and a plan. They're starting points to adapt to the project and the user's words, never a template to fill in. Drop what doesn't apply, and add what the user asked for that isn't here.

These rules hold for every recipe:

- **The checks come from the user's words, not from the plan.** "Add CSV export" is done when CSV export works, not when killer-tests has run. Write each check as something the user would agree means done.
- **Prefer a command check.** A judge check is for what no script can settle. Every goal needs at least one command check.
- **A check uses only the project.** It runs in a fresh sandbox from the goal branch, with the network off and its own home folder, so it can use what's committed and what's installed on the machine, nothing else. Another skill's script (`release.py`, `perf.py`, `deck.mjs`) needs that skill's run folder, which doesn't exist yet when you lock, so it can't be a done-when check. Write a small check script in the project instead (under `tests/`, in the project's language), commit it with the acceptance tests, and lock it. The skill's own report is the detailed proof; the check is the floor.
- **What not to do, whatever the goal:** no major upgrades unless the user asked; no rewrites of code that works; no new dependencies unless the goal needs one; no reformatting of files the goal doesn't touch; nothing outward-facing.

How agents add up: see the counts in `references/skills.md`. A plan that needs more agents or hours than the budget allows should be cut down before `go`, not left to run out halfway; say what was cut in the announcement.

## "Add <feature>"

**Checks.** Acceptance tests written first, from the user's description, in the project's own test style and framework, committed on the goal branch before `lock`:

- `C1` command: the feature's acceptance test passes (`py -I tests/test_export.py`, `npm test -- export`, `go test ./export/...`).
- `C2` command: the project's whole test suite passes (nothing that passed before fails).
- `C3` judge: the docs show how to use the feature, with an example that works.

Run `checks run` before `go`: C1 must fail. A test that passes before the feature exists proves nothing.

**Plan.**

1. Direct work: the feature, test first, small commits. Moves C1.
2. killer-tests on this branch's changes, close the gaps. Moves nothing new; it hardens C1's ground.
3. killer-bugs on this branch's changes, fix every proven bug. After 1 and 2.
4. killer-docs for the feature's docs: scope *README and getting started*, or the guide that covers it; add the *Each guide or how-to* walk when there's a guide. Moves C3.

**Not:** don't widen the feature past what the user described; put ideas for more under "Decisions to check".

## "Ready for release X"

**Checks.**

- `C1` command: the whole test suite passes.
- `C2` command: a check script asserts that every place the version appears says X and the changelog has a dated section for X. It fails now, which is right.
- `C3` command, where the project builds offline: a check script builds it and runs the result's `--version`, which must print X. (killer-release's smoke install, in an empty sandbox, is the stronger proof; its report has it.)
- `C4` judge: the changelog section for X describes what changed, and calls breaking changes breaking.
- If the user named things that must be in X, one check each.

**Plan.**

1. killer-upgrade: patch and minor only, unless the user said majors.
2. killer-bugs: whole project, fix every proven bug.
3. killer-tests: this branch's changes, close the gaps.
4. killer-docs: all the docs; fix them.
5. killer-release: version X, *Prepare everything, I'll publish*; never publish. Last, after all the others: a commit after its build makes the build stale. Its tag, push and publish commands are waiting items in the morning.

**Not:** no majors unless asked; no version number the user didn't choose; no tag, not even a local one.

## "Launch-ready"

**Checks.**

- `C1` command: the whole test suite passes.
- `C2` command: the README's quickstart runs: its commands, or its first example saved as a check script, exit 0 from a fresh copy.
- `C3` judge: someone landing on the repo knows in ten seconds what it is and how to try it.
- `C4` command or judge: the site builds and its pages load without errors, if a site is part of it.

**Plan.**

1. killer-bugs: whole project, fix every proven bug.
2. killer-repo: README, About and card, committed on the goal branch. Its description, topics and card upload wait for the user.
3. killer-docs: all the docs, fix. After killer-repo, so it checks the README killer-repo wrote.
4. killer-design: a site, only if there's none and the user wants one.
5. killer-release: prepare, never publish. Last.

**Not:** nothing public: no `gh repo edit`, no deploy, no release.

## "Faster"

**Checks.**

- `C1` command: a timing check script runs the target (say 15 times) and fails when the median is above a threshold: the user's number, or the starting median less a clear margin. Run it before `go` to see it fail and to know the starting median. Sandboxes are noisy, so leave room; killer-perf's report has the careful numbers.
- `C2` command: the whole test suite passes.
- `C3` judge: the outputs are unchanged for the inputs the user cares about.

**Plan.**

1. killer-perf: the target from the goal, as fast as it'll go within the budget, commit each proven speedup.
2. killer-bugs on this branch's changes, fix: speedups are where caches go stale and errors get swallowed.

**Not:** no lowered precision, skipped work, changed defaults or new dependencies to win time.

## "Make it solid" or "harden"

**Checks.**

- `C1` command: the whole test suite passes.
- `C2` command: the tests the user cares most about exist and pass: a check script for the risky paths they named (bad input, partial failure, re-runs), written first.
- `C3` judge: the code that matters most has tests that would notice it breaking, and no high-severity bug is left open.

**Plan.**

1. killer-bugs: thorough, whole project, fix every proven bug.
2. killer-tests: whole project (or the core folder), close the gaps.
3. killer-upgrade: security fixes only.

**Not:** no refactoring for its own sake; a fix is the smallest change that removes the cause.

## "A talk about X"

**Checks.**

- `C1` command: a check script finds the deck, fails on any `http://` or `https://` it loads (a deck must be self-contained), and fails on a slide with no speaker notes, if notes were asked for.
- `C2` judge: a reader who sees only the slides can say what the talk argues, and it matches the key messages.
- `C3` judge: every slide is readable at the back of a room: no overflow, no tiny text, enough contrast. (killer-slides' `check` measures this in a real render; its report has it.)

**Plan.**

1. killer-slides: the talk length and look from the request; notes on; the blind audience check on. Key messages from the user, or written from the goal and recorded as a decision.
2. Optionally killer-council on the deck, for a talk that matters, asked to commit the final version on the goal branch.

**Not:** no invented numbers, quotes or results on a slide. A stand-in that needs a real figure is listed for the user.
