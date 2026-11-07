# The skills a goal can run

One entry per killer skill: what it's for, when it's the wrong tool, every setup question with the **safe unattended answer** for each (always one the skill offers), its depth, roughly how many agents a standard run sends, and what it leaves behind. Put the answers in `plan add --answers` as plain words, one per header, with the run's depth in them, like `Scope: this branch's changes. Depth: standard. After: commit on the branch killer-goal named.`

Some skills are separate installs. `goal.py next` looks for `~/.claude/skills/<name>/SKILL.md`, the project's `.claude/skills/<name>`, and the folder killer-goal itself sits in, and says so when a planned skill isn't there. Then skip the step (`step skipped --reason`) or do the work directly.

Every skill here has a "Running unattended" section: given killer-goal's unattended text, it doesn't ask, commits on the goal branch instead of making its own, records its calls, takes nothing outward, and ends with one line. If one stops to ask anyway, answer with the safe choice below and record it with `goal.py decide`.

"This branch's changes" always means the changes since the commit the goal branch started from, never against `main`. killer-bugs, killer-tests, killer-docs and killer-perf take it as `--scope branch:<base>`. `next` prints the base, and the unattended text says it too.

Agent counts are rough, for the budget. `step done` takes the count from a summary.json's `numbers.agents` when there is one. No skill writes that yet, so the plan's `--agents` estimate is what gets counted: set it from the numbers below, and add what a step really used with `goal.py budget --agents +N`.

## Where each one lands

| Skill | Run folder | summary.json | How it takes the goal branch |
|---|---|---|---|
| killer-bugs | `~/.claude/killer-bugs/<project>/<date>-<slug>/` | yes, from `bugs.py report` | in prose: fixes are committed on it |
| killer-tests | `~/.claude/killer-tests/<project>/<date>-<slug>/` | yes | `mutants.py init --branch` or `land --branch` |
| killer-docs | `~/.claude/killer-docs/<project>/<date>-<slug>/` | yes | `docs.py branch --branch` |
| killer-perf | `~/.claude/killer-perf/<project>/<date>-perf/` | yes | `perf.py land --branch` |
| killer-release | `~/.claude/killer-release/<project>/<date>-<slug>/` | yes | `release.py init --branch` or `bump --apply --branch` |
| killer-upgrade | `~/.claude/killer-upgrade/<project>/<date>-upgrade/` | yes | `upgrade.py land --branch` |
| killer-slides | `~/.claude/killer-slides/<deck>/<date>-<slug>/` | yes | `deck.mjs land --branch` (or `init --branch`) |
| killer-repo | `~/.claude/killer-repo/<project>/<date>-repo-page/` | yes, from `handoff.mjs finish` | in prose |
| killer-design | `~/.claude/killer-design/<project>/<date>-site/` | yes, from `handoff.mjs finish` | in prose |
| killer-council | `~/.claude/council-runs/<date>-<slug>/` | yes, from `council.py summary` | in prose, and only when the answers ask it to commit |

`<project>` is the project folder's name and a short hash. Record the step with `step done --summary <its run folder>`. If a run ended without its summary.json, use `--headline "<its one-line summary>" --report <its report.md>`, and record its "Decisions to check" and "Waiting for your OK" yourself with `goal.py decide` and `goal.py wait`, since nothing else copies them into the ledger. `goal.py status` looks in these places for a running step's folder.

## killer-bugs: proven bugs, fixed if you want

- **For:** finding the bugs a project really has. A promise checker and one read-through per slice of the code, checking every chosen kind of bug; reproductions rerun in fresh copies; the worst suspects, up to the depth's limit, proven and challenged; fixes checked by taking them out.
- **Wrong tool:** one bug the user already knows about (debug, or a direct step); style review; scoring work against a bar (killer-council).
- **Setup, unattended:**
  - Scope: *This branch's changes* after feature work; *Whole project* for "ready for release" or "harden".
  - Kinds and More kinds: every kind that fits the project. Kinds are checklists for the read-throughs, so they don't add agents.
  - Depth: the run's depth: *Standard*, *Thorough*, or *Quick* for light.
  - After: *Fix every proven bug* when the goal is to fix or ship; otherwise *Report only*. Never *Report, then ask*: unattended, it turns into *Report only*.
  - Network: *No*. Your seat: *No, just run it*. Copies: *This project*.
- **Agents, standard:** about 10 to 25: a promise checker and one read-through per slice (plus a Pairs agent when the project has pairs), then provers and challengers for at most 15 high- and medium-severity suspects, a code checker for any that can't be made to happen here, copy finders for serious bugs, a fix reviewer per fix and one finder over the fixes. Quick: about 5 to 10. Thorough: about 30 to 60. Its `summary.json` gives the real count under `agents`.
- **Leaves:** one commit per fixed bug on the goal branch, each with a test that fails without the fix. `report.md` and `summary.json` in its run folder from `bugs.py report` (run it again after the fixes, so the summary counts them): `step done --summary <its run folder>`. Its state: `bugs.py list -w <its run>`. `close` adds the outcomes to the project's record, so the next run rechecks them.

## killer-tests: tests that catch real bugs

- **For:** writing tests that would notice a wrong line, measured by mutation testing: faults planted one at a time; a new test counts when the script shows it kills one.
- **Wrong tool:** finding bugs (killer-bugs); a red or flaky suite (the baseline stops the run: fix that first); planning a test strategy without writing tests (engineering:testing-strategy).
- **Setup, unattended:**
  - Scope: *This branch's changes* after feature work; *Whole project* otherwise.
  - Goal: *Close the gaps it finds*, or *A target score* when the user named one.
  - Size: *300, sampled* (light: *100, quick*). Never *Every mutant* unattended on a big project.
  - After: *A new local branch, one commit per test file*, which unattended goes on the goal branch (`init --branch <goal branch>` or `land --branch <goal branch>`).
  - Network, asked only when the tests seem to need it: *No*.
  - Depth: *It stays a survivor, tagged with the reason*; *Thorough* on a thorough run.
- **Agents, standard:** about 5 to 20: a writer per slice of 5 to 15 survivors, a second writer for a slice that fails, and one test reviewer. Thorough adds a judge per survivor a writer called equivalent. Past 16 slices it takes the biggest files first.
- **Leaves:** test files as commits on the goal branch; it never commits anything else. `report.md` and `summary.json`, with the mutation score before and after. A writer's note about a bug in the code is a lead for killer-bugs.

## killer-docs: docs a newcomer can follow

- **For:** making the docs true: every runnable block runs in a fresh sandbox, `--help` and links are checked, and a blind newcomer gets through each journey the docs promise, then a different one after the fixes.
- **Wrong tool:** the README as a landing page (killer-repo); writing docs from nothing (engineering:documentation); a program that's wrong where the docs are right (that's a finding for killer-bugs).
- **Setup, unattended:**
  - Scope: *All the docs*; *README and getting started* for a small change; *This branch's changes* when the goal's work changed the docs.
  - Walks: *Install and first run*. Add *Each guide or how-to* when the goal is about docs or a new feature has a guide, and *Contributing: build and run the tests* when the goal changes how it's built.
  - After: *Fix the docs on a new local branch*, which unattended goes on the goal branch (`docs.py branch -w <its run> --branch <goal branch>`). *Report, then ask* turns into *Report only*.
  - Network: *No*. Install steps that fetch are skipped and listed.
  - Depth: *Standard*, also for light; *Thorough* adds a fact checker.
- **Agents, standard:** 2 per journey: a newcomer before the fixes and a fresh one after, plus up to two more for a journey still stuck. About 2 to 6. Thorough adds a fact checker.
- **Leaves:** doc fixes as commits on the goal branch (`docs.py commit` refuses code files). `report.md` and `summary.json`, with code bugs listed for killer-bugs. With killer-repo in the plan, run killer-docs after it.

## killer-perf: speedups with proof

- **For:** making something measurably faster: interleaved benchmarks against the unchanged code, a noise band, the tests and the outputs unchanged, and a challenger for every gain.
- **Wrong tool:** load testing a live service; "faster" with no command to measure; finding bugs (killer-bugs).
- **Setup, unattended:**
  - Target: what the goal names: *The main command* for a CLI, *The test suite* for a library, *Startup time*, or *Something specific*.
  - Goal: *As fast as it'll go* (up to three rounds), or *A target* when the user gave one ("30% faster").
  - Rigor: the run's depth: *Standard* (15 runs each), *Thorough* (40), *Quick* (7) for light.
  - After: *Commit each one on a new local branch*, which unattended goes on the goal branch (`perf.py land -w <its run> <ids> --branch <goal branch>`).
  - Network: *No*.
  - Finders and More finders: at most three lenses across both, the ones that fit.
- **Agents, standard:** about 10 to 15 a round: three finders, up to six optimisers (eight on thorough), a challenger per gain. Up to three rounds while each round lands something.
- **Leaves:** one commit per accepted speedup, its measured numbers in the body. `report.md` and `summary.json`. A second round is a new run folder (`perf.py init --repo <project> --like <first run> --slug round-2`, then `land --branch <goal branch> --against <the first run's base>`): give `step done` the last one, and `goal.py decide` anything in the earlier ones the user should check. Its state: `perf.py list -w <its run>`. It measures one thing at a time on the machine, so nothing heavy runs beside it.

## killer-release: a release that installs

- **For:** getting a release ready: every version site bumped in one commit, a changelog checked against what changed, artifacts built in a clean copy and installed from scratch in an empty sandbox with the network off.
- **Wrong tool:** a deploy to production (engineering:deploy-checklist); publishing tonight.
- **Setup, unattended:**
  - Version: the version the user named. Otherwise the kind `changes` suggests, recorded with `decide`: *Patch* for fixes only, *Minor* for new features. If it suggests *Major* and the user never said so, `step blocked` with the question.
  - Landing: *Prepare everything, I'll publish*. Always: unattended, it never runs `publish`.
  - Smoke: *This machine, in a clean sandbox*.
  - Notes: *Draft them from the changelog*.
  - Newcomer, asked when there are install steps: *Yes*.
  - Network, asked when dependencies aren't on the machine: *From a folder I'll name* if the user named one; otherwise *From the network*, and say so at setup. With neither, the clean install can't pass.
- **Agents, standard:** 1 or 2: a release reviewer, and a first-time installer when there are install steps.
- **Leaves:** the bump and changelog commits on the goal branch: `init --branch <goal branch>` (or `bump --apply --branch`), and every later `release.py commit` goes on the same branch. The artifacts with `SHA256SUMS.txt`, `notes.md` and `publish.md` in its run folder, every tag, push and upload in `publish.md` recorded as waiting. `report.md` and `summary.json`. Run it last: a commit after its build makes the build stale.

## killer-upgrade: dependency upgrades, each one proven

- **For:** bringing dependencies up to date: every upgrade tried in a fresh sandbox against a baseline of the project's own build and tests, failing batches bisected, breaking majors migrated by an agent if allowed.
- **Wrong tool:** a security audit without upgrading (the ecosystem's audit command); a bug an upgrade exposed (killer-bugs).
- **Setup, unattended:**
  - Scope: *Patch and minor only, no majors*, unless the user asked for majors (then *Everything that's out of date*); *Security fixes only* for "harden"; *One package* when the goal names one.
  - Majors: *List them only*, unless the user asked for majors (then *Try them, and migrate the code if tests break*).
  - Also: *Neither*.
  - After: *Commit them on a new local branch*, which unattended goes on the goal branch (`upgrade.py land -w <its run> --branch <goal branch>`).
- **Network:** on in its sandboxes, because installs need a registry. It isn't a setup question (`init --no-network` cuts it, and then installs fail). Package install scripts run code from the internet, so say so at setup: this is the step to think about before a night alone.
- **Agents, standard:** none with no majors. With majors, up to two migrators per major that breaks, and a changelog reader per major that passes, if wanted.
- **Leaves:** one commit per accepted step, in order: the patch and minor upgrades together, dev dependencies together, each major alone, manifests and lockfiles together. `report.md` and `summary.json`. Its state: `upgrade.py status -w <its run>`.

## killer-repo: the repo page

- **For:** the README around one true wow moment, the About panel, topics, a social card and community files, rendered the way GitHub shows them in light and dark, on desktop and phone.
- **Wrong tool:** the project's own website (killer-design); whether the docs beyond the README can be followed (killer-docs).
- **Setup, unattended:**
  - Scope: *README, About and card*. Not *Full makeover*: its new workflows only count once they've passed on a pushed branch.
  - Look: *Invent a new one*; *Evolve what's there* when the repo has a look people know; *Follow my brand* when there's a brand guide.
  - Changes: *Branch and pull request*, which unattended means commit on the goal branch, no pull request. Never *Commit to the default branch*.
  - Council: *No, the screenshots are enough*.
- **Agents:** none of its own.
- **Leaves:** README, asset and visual-script commits on the goal branch. The description, topics, website and social card upload recorded as waiting with their exact commands. Its handoff (`report.md`) and `summary.json` in its run folder, from `handoff.mjs`; a failing `audit.mjs` check makes it partial. Needs Node 20+ and Playwright. The exact render needs GitHub (through `gh` or its API); without it the render is approximate and labelled.

## killer-design: a website

- **For:** designing and building a site in hand-written HTML, CSS and a little JS, with a full icon set, screenshotted at phone, tablet and desktop widths and fixed until it looks right.
- **Wrong tool:** a critique with no changes (design-critique); the repo page (killer-repo).
- **Setup, unattended:** it has no setup questions. Put the job in the answers: what the site is for, where it goes (`site/` unless the project has a place), and the brand or `DESIGN.md` to follow if there is one. It lists its assumptions and stand-ins as decisions.
- **Agents:** none.
- **Leaves:** the site, `DESIGN.md` and the icons, committed on the goal branch. Deploys recorded as waiting. Its handoff (`report.md`) and `summary.json` in its run folder, from `handoff.mjs`; a failing check in the final `shoot.mjs` report makes it partial. Needs Node 20+ and Playwright, from the project or the killer-skills runtime; otherwise the first run installs it and needs the network.

## killer-slides: a talk

- **For:** a deck in one self-contained HTML file, every slide measured in a real render, and a blind audience agent that has to say what the talk argues.
- **Wrong tool:** a .pptx (anthropic-skills:pptx); a website (killer-design).
- **Setup, unattended:**
  - Talk: the length the user gave, else *10 minutes*; *Not a talk* for a deck people read on their own.
  - Look: *Invent a look from the subject*; *Match the project's site* or *Follow our brand* when there is one.
  - Notes: *Yes*. Check: *Yes*.
  - Key messages, asked in chat: from the goal. If the user gave none, write the three things people should leave with and record them with `decide`.
  - Where: `slides/<short-name>/`, unless the user named a place.
- **Agents, standard:** 2 to 6: an audience agent and a matcher a round, at most three rounds.
- **Leaves:** the deck folder (`deck.html`, `assets/`, the PDFs), committed on the goal branch with `deck.mjs land -w <its run> -m "<message>" --branch <goal branch>`. The screenshots and contact sheet in its run folder. `report.md` and `summary.json`. Needs Node 20+ and Playwright.

## killer-council: a blind score, and fixes until it passes

- **For:** improving a piece of work (a deck, a README, a design doc, a plan) until fresh reviewers who never see the threshold score it past it.
- **Wrong tool:** code correctness (killer-bugs, killer-tests); a single review with no fixes (code-review, design-critique).
- **Setup, unattended:** Threshold *85* · Members *4* (light: *3*; thorough: *5*) · Models *Same model for all* · Your seat *No, just run it* · Rounds *5* (light: *3*) · Extras *Holdout juror* only: the approval step and pauses need someone there. Name the files under review in the answers, and add "commit the final version on the goal branch": it commits only when asked. If it didn't, commit the work as direct work.
- **Agents, standard:** 5 to 21: four seats a round for up to five rounds, plus the holdout. A pass in round 2 is 9.
- **Leaves:** the improved work. `report.md` and `summary.json` in its run folder; the summary counts the seats it really sent, and a pass the holdout disagrees with is partial.
