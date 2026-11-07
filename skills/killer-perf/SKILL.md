---
name: killer-perf
description: "Make a project measurably faster (or lighter), and prove every gain. You lead: ask a few setup questions (target, goal, rigor, what happens after), set up benchmarks, measure the noise by running the unchanged project against itself, profile it, and send blind finder agents after one kind of slowness each. Optimisers each make one change in a sandboxed copy. A script times every change against the unchanged code in fresh copies, interleaved, with medians, a bootstrap confidence interval and a Mann-Whitney test, and counts it only when the tests still pass and the output is unchanged. A challenger hunts for behaviour the tests miss; proven gains are combined, remeasured and committed on a new local branch with their numbers. Use whenever the user wants code faster, quicker to start or lighter on memory, asks why it's slow, or wants to optimise, speed up or benchmark it, even if they never say 'killer-perf'. For finding bugs, killer-bugs fits better; for load-testing a live service, a load-testing tool does."
---

# Killer Perf

You are the **lead**. Fresh agents find what's slow, make the changes and look for what the changes break. Each one sees only its own job and its own copy of the project. You set up benchmarks that can be trusted, choose which leads to follow, keep the books, and land what's proven if the user wants. You never call a change faster yourself, and no agent's timing counts. A speedup counts only when `perf.py compare` says `faster`: the tests passed with the change, its output matched the unchanged project's on every run, and the whole 95% confidence interval cleared the benchmark's noise threshold with p < 0.01. Then a challenger has to find no behaviour it changes.

`scripts/perf.py` in this skill's folder does the measuring and keeps the books: run folders, sandboxes, benchmarks, the noise test, the statistics, candidates, landing and the report. Run it with `py` on Windows (or `python` if there's no `py`) and `python3` elsewhere. If none of those works on Windows, use `~/.cache/killer-skills-python/python.exe`; the Windows installer puts Python there when it's missing. `perf.py <command> --help` lists the options. A command that measures prints how long it will take before it starts; when that's over eight minutes, run it in the background and wait for it.

## The rules that make it work

- **Only `compare` counts.** Finders' prototypes, optimisers' rough timings and expected effects are leads, not results. Every number in the report comes from `perf.py`, never typed by you.
- **Noise first.** Each benchmark's noise test runs the unchanged project against itself. Its band sets the threshold a change must beat: the larger of 2% and 1.5 times the band. A benchmark whose band is wider than ±10% isn't compared at all.
- **Correct before fast.** Before any timing, the tests run in a fresh copy with the change, and every timed run's output must match the unchanged project's. A change that's faster but fails either is rejected, never offered as a trade-off.
- **One change per optimiser, the smallest that does it.** No new dependencies, no lowered precision, no skipped work, no changed defaults. Tests, benchmarks, their inputs and dependency files are protected: `candidate add` refuses a change to them.
- **Nothing else runs while measuring.** `perf.py` lets only one measurement run on the machine at a time, but it can't stop agents. Never measure while agents are working: send them, wait for every one, then measure.
- **Every agent is blind** to the run folder, to other agents, to the numbers, and to anyone's reasoning but its own. An optimiser gets the claim, never the finder's argument; a challenger gets the diff, never the optimiser's account. Start agents fresh (`general-purpose`), never with `fork`, which would inherit this conversation.
- **Each agent works in its own sandbox,** a throwaway copy made by `perf.py sandbox`. It runs the project only through the sandbox's `run.py`, with its own home and temp folders, no credentials, and a dead proxy for the network. These are environment variables, not walls: nothing redirects writes to the Windows registry, PATH, services or scheduled tasks. That's why the side-effect check in section 2 comes first.
- **Report what was measured,** not just what won. Rejected changes, changes with no clear gain, and leads nobody followed all go in the report.

## 1. Setup: ask, don't make the user remember options

Skip anything the user already said ("make the tests faster, just report"). Ask the rest with AskUserQuestion, in at most two calls. Before asking, look at the project for a minute: its language, whether it's a command-line tool, a library or a service, whether it has tests and how long they take, and whether anything it does reaches the network. Use that to put the right defaults first.

**Call 1**

| Header | Question | Options (the first is the default) |
|---|---|---|
| Target | What should get faster? | **The main command (Recommended when it's a command-line tool)** · **The test suite** (recommend it instead for a library) · **Startup time** · **Something specific**: a function, a page, a build; type it under Other |
| Goal | How far? | **As fast as it'll go (Recommended)**: up to three rounds, stopping when a round proves nothing · **A target**: like 30% faster; type it under Other · **Find and measure only**: benchmarks, noise and ranked leads, no code changes |
| Rigor | How careful should the measurements be? | **Standard (Recommended)**: 15 runs each, noise checked, every gain challenged · **Thorough**: 40 runs each, and the combined change challenged too; several times as long · **Quick**: 7 runs each |
| After | Once gains are proven, what then? | **Commit each one on a new local branch (Recommended)**: one commit per change, nothing pushed · **Report, then ask** · **Report only** |

**Call 2**

| Header | Question | Options |
|---|---|---|
| Network | Can benchmarks and sandboxes use the network? | **No (Recommended)**: benchmarks need local inputs; this machine's own addresses still work · **Yes**: the numbers then include the network, and the report says so |
| Finders (multiSelect) | Which kinds of slowness should finders look for? | The first four lenses from `references/lenses.md`. Mark at most three that fit "(Recommended)" across both questions |
| More finders (multiSelect) | And these? | The other four |

Skip the After question when the goal is "Find and measure only". If AskUserQuestion isn't available, or the user said "just run it", use the defaults and say so in one line.

**Defaults:** the main command (the test suite for a library) · as fast as it'll go · standard · commit on a new local branch · network off · the recommended finders, at most three.

## 2. Measure the baseline

1. **Init:**
   ```
   py <skill-dir>/scripts/perf.py init --repo <project> --target "<what should get faster>" --goal <max | measure | 30%> --rigor <standard | thorough | quick> --after <commit | ask | report> [--network] [--include .venv node_modules] [--protect <benchmark inputs>] [--env NAME=VALUE]
   ```
   It prints the run folder (`~/.claude/killer-perf/<project>/<date>-perf/`) and the base: the commit every copy is made from. Uncommitted changes are left out, and it says so; if they should count, ask the user to commit them first. `--include` copies ignored folders the code needs (a virtual environment, `node_modules`) into every copy; `--protect` adds paths no candidate may change, on top of the tests, benchmarks and dependency files protected already.
2. **The side-effect check,** before anything runs the project's code. Search the code the tests and benchmarks will run for writes that environment variables don't redirect: the Windows registry, the user's or system PATH, services, scheduled tasks, startup entries, global package installs, absolute paths into the real home folder, and anything that reaches the network. For each, find the project's own test hook (a dry-run flag, a URL override) and use it in the benchmark. List the commands agents must not run in every sandbox block.
3. **The tests.** Find the test command in the README, CONTRIBUTING or the CI config, then `perf.py tests -w <run> -- <command>`. It runs once on the unchanged project. If it fails there, the gate can't tell a broken change from one that was already broken: narrow the command to tests that pass, record that with `perf.py decide`, and set it again. A project with no tests: `perf.py tests -w <run> --none --why "<why>"`; the output check is then the only guard, and the report says so.
4. **Benchmarks.** Read `references/benchmarks.md`, then register one to three:
   ```
   py <skill-dir>/scripts/perf.py bench add -w <run> <name> [--setup "<build command>"] [--protect <its input files>] [--output-normalise '<regex>'] [--metric <name>] [--higher-is-better] -- <command>
   ```
   `--setup` is one quoted string (use forward slashes in paths) or a JSON list, run once in each copy before timing. It tries the command twice in a fresh copy: it must succeed, its output must match between the two runs, and it reports how long a comparison will take. Very fast commands get looped and very slow ones get fewer runs, with a note either way.
5. **Noise:** `perf.py bench noise -w <run>`. Two fresh copies of the unchanged project, interleaved. It prints each benchmark's band and the threshold a change must beat. Over ±10% exits 1: follow its advice and run it again. Don't go on with a benchmark that isn't clean.
6. **Profile:** `perf.py profile -w <run> <name>` runs a Python benchmark under cProfile or a Node.js one under `--cpu-prof`, and saves the top functions to `<run>/profiles/<name>.txt`. For anything else it says which profiler to use; run that in your own sandbox if you can.
7. **Write the facts block** every brief shares (`references/briefs.md`): what the project is, who uses it, and the commands that build, run and test it in a sandbox. Facts only; your guesses about where it's slow stay out.
8. **Announce** in one line, then carry on:
   > killer-perf: the main command (`mytool convert big.csv`, 1.84 s, noise ±2.1%) · finders for algorithms, I/O and start-up · then up to 6 optimisers, a challenger per gain · commit on a new branch · network off.

Check the real project's `git status` with `perf.py status -w <run>` before and after each of sections 3 to 7.

## 3. Find

Make one sandbox per finder, with the profile in its scratch folder: `perf.py sandbox -w <run> find-algo --give <run>/profiles/<name>.txt`. Write each brief from the finder template in `references/briefs.md`, with its lens from `references/lenses.md`, and send them all in one message: one Agent call each, `subagent_type: "general-purpose"`, `run_in_background: false`, a description like "Find slowness: I/O". Then, for each:

```
py <skill-dir>/scripts/perf.py collect -w <run> --sandbox find-algo
```

It adds the hypotheses (H1, H2, ...) and deletes the sandbox. If an agent wrote no `reply.json`, save its reply text to a file and pass that instead of `--sandbox`. `perf.py list -w <run>` ranks them by expected effect. Two finders naming the same cause is the strongest early signal; drop the duplicate with `perf.py hypothesis -w <run> H5 --status dropped --note "same as H2"`.

With "Find and measure only", go to section 8.

## 4. Optimise

Take the hypotheses biggest expected effect first, at most six a round (eight on a thorough run). Leave out any whose `risk` is a behaviour change you can't rule out, and drop it with a note. For each, a sandbox (`perf.py sandbox -w <run> opt-H2`, never with `--candidate`) and a brief from the optimiser template, filled from `perf.py hypothesis -w <run> H2`: its claim, where and expected effect only, not the finder's reasoning. Send them all in one message, then for each:

```
py <skill-dir>/scripts/perf.py candidate add -w <run> --sandbox opt-H2 --hypothesis H2
```

It saves the sandbox's diff as `candidates/C1.patch`, titled from the optimiser's reply, and deletes the sandbox. It refuses an empty change, a change to protected files, a change whose new lines say who or what made it, and a change the optimiser said it isn't done with. For the last, record `hypothesis H2 --status dropped --note "<its reason>"`. A warning about rewritten line endings means the change touches every line of a file: send a fresh optimiser with the same brief.

## 5. Compare

Wait until no agent is running. Then, one candidate at a time:

```
py <skill-dir>/scripts/perf.py compare -w <run> --candidate C1
```

It runs the tests in a fresh copy with the change, then each benchmark in two fresh copies, base and candidate, alternating A B B A A B ... after warm-ups, and prints a line for each:

> main: 412 ms → 297 ms, 28% faster (95% CI 25–31%), p < 0.001, 15 runs each; noise band ±3.0%.

The verdict is `faster`, `slower`, `no clear change` or `mixed` (faster on one benchmark, slower on another), or a rejection: `rejected: tests`, `rejected: output changed` (with the first line that differs), `rejected: bench failed`, `rejected: doesn't apply` or `rejected: protected files`. It exits 0 only for `faster`. `faster` means better on the deciding metric, whatever it measures. Don't argue with a verdict: a candidate that came out `no clear change` didn't prove anything, however good it looked to its optimiser.

## 6. Challenge

For each candidate that came out `faster` or `mixed`: a sandbox with the change applied (`perf.py sandbox -w <run> chal-C1 --candidate C1`) and a brief from the challenger template, with the diff from `perf.py candidate show -w <run> C1`. Send them all in one message, then for each:

```
py <skill-dir>/scripts/perf.py challenge -w <run> C1 --sandbox chal-C1
```

If the challenger wrote a reproduction, the script reruns it in two fresh copies, with the change and without. One that shows with the change and not without makes it `rejected: behaviour change`, whatever the challenger's verdict says, and no later challenge undoes that. A concern with no reproduction that shows becomes a `question`. `stands` makes a `faster` candidate `accepted`. Ask the user every question in one batch at the end, and record each answer with `perf.py candidate set -w <run> C1 --status accepted` (or `rejected --reason "<a few words>"`). Only a measured gain can be accepted.

## 7. Combine and land

- **Combine** when two or more candidates are accepted: `perf.py combine -w <run> C1 C3 C4`. It applies them in order in a fresh copy, then compares the combination with the unchanged project on every benchmark, gate included. If they don't apply together, drop one or have an optimiser redo it on top of the others. If the combination gains less than its best part, it says so: land the best part alone. On a thorough run, challenge the combination too (`sandbox --candidate K1`, then `challenge K1`).
- **Land**, with "Commit each one": `perf.py land -w <run> C1 C3`. It runs the tests on the changes together, then makes `killer-perf/<date>` from the base without touching the user's checkout, commits each accepted candidate with its measured numbers in the commit body, and compares the branch with the base on every benchmark, gate included. If that final check rejects the branch, the commits are taken back; if it says anything but `faster`, the branch stays but the report says so. Never call it a speedup unless the final check says `faster`. With "Report, then ask", ask which to land first (AskUserQuestion, multiSelect, the accepted candidates plus "All of them"). With "Report only", don't land.
- **Another round,** when the goal is "as fast as it'll go" (or a target not yet reached) and this round landed something: a new run on the branch, with the same benchmarks: `perf.py init --repo <project> --rev killer-perf/<date> --like <this run> --slug round-2`. That copies the benchmarks and the test command and reruns the tests; run the noise test again (section 2, step 5), then sections 3 to 7, landing with `--branch killer-perf/<date> --against <the first run's base>` so the final check covers every round. At most three rounds; stop when a round proves nothing.

## 8. Report

`perf.py report -w <run>` writes `<run>/report.md` and `<run>/summary.json`: the before and after table, the benchmarks and their noise, every candidate with its numbers, questions, rejections and why, leads not followed, decisions, what's waiting for the user, and the machine it was measured on. Read it, and add a short summary at the top if the run needs explaining. Then `perf.py clean -w <run>`.

Give the user, in chat: the headline numbers from the report, the changes that landed (or are ready to), the questions that need them, what was rejected and why in a line each, and a link to `report.md`. Don't push anything, and don't open pull requests unless the user asks.

## Running unattended

killer-goal runs this skill overnight, and a user may start it and walk away. Then:

- **The setup answers come with the request.** Don't ask: use what was given and the defaults for the rest, and say so in one line.
- **When killer-goal names a branch, commit there** instead of making a new `killer-perf/<date>` branch: `perf.py land -w <run> C1 --branch <name>`. When that branch is the current one, it commits on it, in place.
- **Questions that come up later don't stop the run.** Take the safe choice, carry on, and record it with `perf.py decide -w <run> "<text>"`. A challenger's question means the candidate isn't landed. They're listed under "Decisions to check" in the report.
- **Nothing outward-facing happens unattended:** no push, pull request, publish, tag, upload or settings change. Prepare it, record it with `perf.py wait -w <run> "<text>" --command "<cmd>"`, and it's listed under "Waiting for your OK".
- **Finish with `perf.py report`,** so `summary.json` is there for whoever reads the run in the morning, and end with one line: `killer-perf: <done | partial | blocked> · <headline> · <path to report.md>`.

## When things go wrong

- **The noise test won't come clean.** Other work on the machine, a laptop on battery, or runs too short to time. Follow `bench noise`'s advice and `references/benchmarks.md`. If it still won't, stop: say the machine is too noisy to measure on, with the band, and report the leads without changes.
- **The base benchmark fails or its output changes partway through a compare.** That stops the command, since nothing about the candidate can be concluded. Usually the benchmark leaves state behind or depends on the time or a random value; fix the benchmark (`bench add --replace`), then run `bench noise` again.
- **A gain disappears when it's measured again.** It was noise. The later result stands; don't pick the run you liked.
- **Tests fail with a change but you think they're flaky.** Run `compare` once more. If they fail again, the rejection stands. If they pass, record the earlier failure with `perf.py decide`: a change can make a race more likely, so the user should know.
- **A compare would take longer than there is time for.** The estimate comes first. Fewer benchmarks, a smaller input that still runs the slow path, or the quick rigor; never fewer than 6 runs each, which can't reach p < 0.01.
- **A patch won't apply when landing** although it applied in a sandbox. This repository's line-ending settings check files out differently from a plain clone. The patches are in `<run>/candidates/`: say so and leave it for the user.
- **The real project changed** (`perf.py status` exits 1). If you didn't do it, find which agent did, undo it, and say so in the report.
- **An agent wrote no usable `reply.json`:** send the same brief to a fresh agent once. If that fails too, note the gap in the report.
- **The code isn't Python or Node.js.** `profile` says so. Finders work from the code and their own probes instead; say in the report that there was no profile.
