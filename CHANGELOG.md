# Changelog

Each release publishes every skill on its own (`<skill>.zip`, `<skill>.tar.gz`), all of them together (`killer-skills.zip`, `killer-skills.tar.gz`), the packs (`killer-skills-fonts.zip` and `.tar.gz`, and for Windows `killer-skills-runtime-windows-x64.zip`, `killer-skills-node-windows-x64.zip` and `killer-skills-python-windows-x64.zip`), both installers and `SHA256SUMS.txt`. The asset names don't change, so `https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/<file>` always fetches the newest.

## 1.3.0 (unreleased)

Eight new skills, for eleven in all, each ending with a check a script runs: a bug hunt that reports only the bugs it can make happen, tests kept only if they catch a planted fault, docs that a blind newcomer has to get through, speedups that have to beat the measured noise, upgrades proven by the project's own tests, releases that have to install from scratch, decks measured slide by slide, and killer-goal, which takes a goal in your words and runs the rest overnight.

### killer-goal

- New: killer-goal. It turns a goal into done-when checks, locked before any work starts, plans steps from the other killer skills and direct work, and runs them unattended on `killer-goal/<date>-<slug>`. After every step `goal.py` reruns the checks in a fresh copy of the branch and guards everything else: your branch, other branches, remote-tracking refs, tags and the stash. A blind acceptance judge, who sees the goal, the checks and the branch but never the night's ledger, has to agree before the goal counts as met.
- The ledger is the memory. `goal.py status`, with no arguments, finds the run for the current folder and ends with the next action and its exact command, so a session coming back from a context summary knows what to do.
- An optional Stop hook (`goal.py hook install`: backed up first, removes only its own entry) keeps the session going while steps are left. It does nothing unless a goal is running, and lets the turn end when the run stalls.
- Nothing goes outward. The one exception is a draft pull request at the end, which `goal.py pr` refuses unless you chose it at setup. The morning report lists every decision the skills made and every outward command waiting for your OK.

### killer-bugs

- New: killer-bugs. It asks for the scope, the kinds of bug, the depth and what happens afterwards, then:
  - a promise checker writes down what the project should do from its docs alone, and tests that through its commands and functions;
  - one read-through per slice of the code reads it once, with every chosen kind of bug as its checklist: failures partway, bad input, re-runs and state, concurrency and files in use, platforms and versions, pairs that should agree, tests that prove nothing, and security. Pairs get their own agent when the project has them;
  - finders reproduce what they find, and `bugs.py` reruns each reproduction in a fresh sandboxed copy before it counts;
  - `bugs.py forward` sends the worst suspects on, by severity, up to the depth's limit (5 quick, 15 standard, 30 thorough): to a prover if there's no reproduction yet, then to a challenger that tries to knock each one down;
  - a serious bug that can't be made to happen here (a full disk, a narrow race, another system) goes to a code checker that traces it from an entry point; a chain that holds is reported as argued, apart from the proven bugs;
  - what survives is searched for copies, ranked, and fixed on a local branch if you want, each fix checked by running its test with the fix taken out.
- killer-bugs' `scripts/bugs.py` keeps the books and a record between runs, so the next run reruns earlier reproductions and says if a fixed bug came back. It checks every `file:line` an agent reports, its report gives the agents a run really used by role, and `bugs.py findings` hands the results to the host's findings view. `scripts/run.py` runs commands in a sandbox: its own home, temp and app-data folders, no tokens or keys in the environment, and a dead proxy for the network.

### killer-tests

- New: killer-tests. It measures how many small faults planted in the code the tests catch (mutation testing), has blind writer agents add tests for the ones they miss, and reports the score before and after.
- A new test counts only when `mutants.py check-test` shows it passes 3 runs out of 3 on the real code, fails twice with its fault in, and that the fault still passes with the new test taken out.
- Faults are never planted inside strings or comments: Python is parsed with its own parser, and ten other language families get a tokenizer that masks strings and comments first.
- New tests land on a local `killer-tests/<date>` branch, built in a separate worktree, one commit per test file. Your checkout is never touched.

### killer-docs

- New: killer-docs. `docs.py run-blocks` runs every shell command and code sample in the docs, in order, in a fresh sandboxed copy of the project, the way one reader at one terminal would, and compares the output the docs show with the real output, ignoring dates, durations, versions and ids. `help-drift` compares each command's `--help` with the options the docs use; `links` checks relative links, anchors and images.
- Blind newcomer agents, who never see the code, follow only the docs to install the project and walk each guide. Every stuck step, guess and workaround is recorded, and their reproductions are rerun in fresh copies. Fixes go to the docs only, on a local `killer-docs/<date>` branch; then `recheck` reruns everything and a different fresh newcomer walks again.
- A block that can't run here (a placeholder, another system, the network, a missing tool, a command that would change the system) is listed with its reason and never counted as passing. `pip install`, `npm -g`, `cargo install` and `go install` in the docs land in the sandbox, not your machine.

### killer-perf

- New: killer-perf. Blind finders look for one kind of slowness each, optimisers make one change each in a sandbox, and challengers look for behaviour the tests miss. Only what `perf.py` measured lands, one commit per change on a local branch, with its numbers.
- `perf.py` measures in fresh copies, interleaved A B B A, after a noise test (the unchanged project against itself) sets each benchmark's threshold. It reports medians, a seeded bootstrap 95% interval and a Mann–Whitney test, all in the standard library. A change counts only when the whole interval clears the threshold with p < 0.01, the tests pass and the output is unchanged.
- Tests, benchmarks, their inputs and dependency files are protected from candidates. `land` tests the changes together before committing, and takes its commits back if the branch fails its final check.
- `profile` runs Python benchmarks under cProfile and Node.js ones under `--cpu-prof`, and saves the top functions for the finders.

### killer-upgrade

- New: killer-upgrade. It records a baseline of what installs, builds and passes (three test runs, so flaky tests are told apart), asks each package manager what's out of date, and tries the upgrades in batches in fresh sandboxes. An upgrade lands only when everything that passed at the baseline still passes.
- `upgrade.py bisect` splits a failing batch: it finds the single culprit, or the pair that only breaks together, accepts the rest from the trial that passed, and makes each culprit its own step.
- Majors that break the tests go to migrator agents. `upgrade.py migrate` refuses a change that pins back, adds a dependency or touches a lockfile, checks the rest with a fresh try, and holds the upgrade after two failed attempts.
- It reads npm, pnpm, yarn, pip, uv, poetry, Cargo, Go, Bundler and .NET, plus runtime pins and CI actions on request, and any other tool described with `upgrade.py custom`. Each proven step becomes one commit on `killer-upgrade/<date>`, holding the files exactly as tested, and the branch is checked again in a fresh sandbox.

### killer-release

- New: killer-release. It asks what kind of release and how far to go, bumps every place the version appears in one commit on a local branch, maps the changelog to the commits behind it, and has a blind reviewer check each entry. It builds in a fresh sandbox and installs the result from scratch in an empty one with the network off. The installed tool has to print the new version and run before the release counts as ready.
- `release.py check` is the final gate: the versions agree, the changelog section is dated, the tag is free, the tests pass in a fresh copy, the build and every clean install match the release commit, and there's no AI attribution in the release commits, notes or changelog.
- Publishing is written to `publish.md` as exact commands fitted to the project ("push the tag; CI does the rest" when CI publishes on a tag). Each step runs alone, through `publish --step N --yes`, after your OK. The tests prove that `publish-plan` runs nothing.

### killer-slides

- New: killer-slides. Presentation decks as one HTML file that runs in any browser and loads nothing from the network: keys, builds, a presenter view with notes and a timer, an overview, and a PDF with one slide per page (and one with the notes).
- `deck.mjs check` measures every slide in the real render: text off the slide or cut off, text under 20px, contrast against the actual pixels behind the text, words per slide, motion with reduced motion on, layout that changes with the window, missing files and any network request.
- A blind audience agent sees only the slide images and says what the talk argues. A second blind agent checks that against the key messages and has to quote the audience's words; the script verifies the quotes.

### Across the suite

- Every skill has a "Running unattended" section and the same contract: setup answers come with the request, later questions become decisions listed for the morning, nothing outward-facing happens, and changes land on the branch killer-goal names. Every skill with a bookkeeping script writes `summary.json` beside its report for killer-goal to read, and records its calls with `decide` and its outward actions with `wait`, killer-bugs and killer-council included.
- New: `tools/shared/kit.py`, the sandboxes the Python skills share: copies of the project byte for byte, line endings and all, with a base commit so an agent's changes come back as a patch that applies to the real project. `tools/sync.py` copies it and `run.py` into each skill, and CI fails when a copy drifts from its source.
- Sandboxes no longer pass on the host's `PYTHONPATH`, `PYTHONHOME`, `VIRTUAL_ENV` or `NODE_PATH`, and `cargo install`, `go install` and `npm -g` inside one install into the sandbox. killer-bugs' sandboxes do the same, and keep the project's own `core.autocrlf` and `core.eol`, so a project that keeps LF files gets LF files in its sandboxes on Windows too. On Windows, `run.py` runs a real Python when `python` is only the Microsoft Store's stand-in.
- Fixed: on Windows, two bookkeeping commands at once could lose one's change, because Windows refuses to replace a file another process has open. `bugs.py` and the kit now retry, and a test runs six processes at once against one file.
- On Windows, `install.ps1` installs the Python pack with any of the Python skills when there's no Python 3.10 or later, and the runtime, Node.js and font packs with killer-slides as with killer-design. `install.sh` installs the font pack with killer-slides.
- killer-council's `council.py summary` writes `summary.json` from the tallies and the holdout, not from memory: a pass the holdout disagrees with is partial, and the agent count is the seats it really sent. `decide` and `wait` record an unattended run's calls and outward actions.
- killer-repo's `render.mjs` keeps each project's screenshots in a folder of their own, `<label>-<hash>`, so two projects in folders with the same name, or two projects' "before" runs, no longer overwrite each other's.
- The first skills' descriptions name the newer skill that fits better: killer-design points decks to killer-slides, killer-repo points to killer-docs and killer-release, killer-bugs to killer-tests and killer-council to killer-goal.
- killer-design's `fonts.mjs add` checks every family and weight before copying anything, so a wrong name or weight leaves the site as it was. Before, it copied the families named ahead of the wrong one and never wrote `fonts.css`.
- CI runs every skill's tests on Ubuntu, Windows and macOS, every Python skill's tests on the packed Python, and killer-slides' browser checks on the Windows runtime.

## 1.2.0 (2026-11-05)

Offline, all the way: a folder of release files installs everything with no network, and the skills no longer need Node.js, Python or a font host from outside.

- Both installers are release assets now. Run as a file from a folder holding the release's files (`powershell -ExecutionPolicy Bypass -File .\install.ps1`, or `sh install.sh`), they install from that folder and download nothing. A pack that isn't in the folder is skipped with a note; a skill that isn't there fails cleanly.
- New: the font pack, `killer-skills-fonts.zip` and `.tar.gz` (14 MB). It has 74 faces, every one killer-design's typography guide names plus JetBrains Mono, as latin and latin-ext `.woff2` files with their licences, from Fontsource and pinned in `tools/fonts.json`. Both installers put it in `~/.cache/killer-skills-fonts` with killer-design or killer-repo.
- New: killer-design's `scripts/fonts.mjs`. `list` shows what's in the pack; `add <site> "Family" ...` copies faces into `assets/fonts/` and writes `fonts.css` with `font-display: swap` and each subset's `unicode-range`. `--weights` picks a static family's weights. The typography guide now self-hosts from the pack first and falls back to Google Fonts.
- New, for Windows: `killer-skills-node-windows-x64.zip` (Node.js 24.22.2, 35 MB) and `killer-skills-python-windows-x64.zip` (Python 3.14.10's embeddable package, 12 MB). `install.ps1` installs Node.js with killer-design or killer-repo when there's no Node.js 20 or later on the PATH, and Python with killer-council when there's no Python 3.10 or later. The skills say to use them when `node` or `py` isn't there.
- `KILLER_SKILLS_RUNTIME` covers every pack: `skip` installs none, and the new `all` installs every pack the skills use even when Node.js, Python or killer-design's Playwright is already there. `KILLER_SKILLS_CACHE` is documented.
- Every pack is written with fixed timestamps and order (`tools/packs.py`), so it's the same bytes until its pins change and the installers don't download it again for a new release. The runtime is written this way for the first time, so updating from 1.1.0 downloads it once more.
- The installers rename an installed skill or pack aside before swapping in the new one. If a running session holds it open, the install stops with that folder unchanged, instead of leaving it half replaced.
- killer-repo said Node 18+; Playwright needs Node.js 20 or later, and its SKILL.md says so now.
- Skill names starting `killer-skills-` are kept for the packs; `tools/package.py` refuses them.
- CI builds every pack whenever the scripts, the builders or the pins change. On Windows it runs every Chromium script on the packed Node.js, screenshots a page set in the pack's fonts, and runs killer-council's tests on the packed Python, all with the network cut off. The installer tests now cover installing from a folder with the network pointing nowhere.

## 1.1.0 (2026-11-05)

Offline: nothing the skills used to fetch on first use has to come from the network any more, on Windows.

- New release asset, `killer-skills-runtime-windows-x64.zip` (128 MB): Playwright 1.64.1, the Chrome 156 headless shell it launches, and Mermaid 11.17.2. `install.ps1` installs it into `~/.cache/killer-skills` when killer-design or killer-repo is among the skills, unless it's already up to date there or killer-design already has Playwright and its Chromium. `KILLER_SKILLS_RUNTIME=skip` turns that off. Built by `tools/build-runtime.ps1` from the pins in `tools/runtime.json`.
- killer-design and killer-repo look for Playwright in the project, then the runtime, then their own caches, and point the runtime's Playwright at its own Chromium. Their npm installs, when one is still needed, use `--prefer-offline`, so a seeded npm cache works without the network.
- killer-repo ships `vendor/github-markdown.css` (5.9.0) and `vendor/marked.esm.js` (18.1.3), so it no longer installs github-markdown-css.
- killer-repo's `render.mjs` makes an approximate render when GitHub's Markdown API can't be reached, or with `--offline`: marked, with GitHub's main rules applied (alerts, task lists, linked images, heading anchors, stripped styles and scripts). Every screenshot is labelled, and the report says `"renderer": "approximate"` and why. Mermaid diagrams come from the runtime when it's installed.
- killer-repo's `audit.mjs` says once that it's offline when no outside link answers, instead of warning about each one.
- CI builds the runtime whenever the scripts or the pins change, and runs every Chromium script from it with the network cut off (`tests/test_runtime.py`).

## 1.0.0 (2026-11-05)

First release. killer-council moved here from agent-toolkit, which now takes all three skills from this repo.

- killer-design: designs and builds websites in hand-written HTML and CSS, and screenshots them at phone, tablet and desktop sizes (`scripts/shoot.mjs`). Makes and audits favicon and app icon sets (`scripts/icons.mjs`).
- killer-repo: rebuilds a GitHub repo page around one wow moment. `scripts/render.mjs` renders a README through GitHub's Markdown API in light and dark at desktop and phone widths, `scripts/audit.mjs` checks links, images, anchors and the repo's settings, `scripts/card.mjs` renders a social card and `scripts/frames.mjs` turns stills into an animated SVG.
- killer-council: blind scoring councils with a threshold the members never see. `scripts/council.py` keeps the books: sealed thresholds, snapshots and restore, a leak check on every brief, tallies with vetoes and plateau detection, and a holdout juror.
- `install.ps1` and `install.sh` install every skill, or only the ones you name, from the latest release or a pinned one, and check each archive against `SHA256SUMS.txt` first.
