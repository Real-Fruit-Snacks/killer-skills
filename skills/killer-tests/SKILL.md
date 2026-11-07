---
name: killer-tests
description: "Write tests that catch real bugs, and prove they do. You lead: ask a few setup questions (scope, goal, how many mutants, where the tests go), check the suite passes every time, then have a script plant small faults in sandboxed copies of the code, one at a time (mutation testing), to find the ones the tests miss. Fresh agents write tests for those survivors in the project's own style. A new test counts only when the script shows it passes 3 times on the real code, fails twice on its fault, and that fault passes without it. A fresh reviewer checks the tests for brittleness, they land on a new local branch, and the report gives the mutation score before and after. Use whenever the user wants better tests, more coverage, tests that would catch bugs, to find the gaps in a test suite, to know how good their tests are, or mutation testing, even if they never say 'killer-tests'. For hunting bugs, killer-bugs fits better. For planning a test strategy without writing tests, engineering:testing-strategy does."
---

# Killer Tests

You are the **lead**. Fresh agents write the tests, each one blind to the others and to the run. You plan the run, keep the books, slice the work and land what passes. You never decide that a test works yourself. Coverage only says a line ran; a test here counts once `mutants.py check-test` has shown it passes on the real code three times out of three, fails twice with the fault it targets in place, and that the fault survives without it.

`scripts/mutants.py` in this skill's folder keeps the books: the run folder, sandboxes, the mutants, their results, the checks on new tests, landing and the report. Run it with `py` on Windows (or `python` if there's no `py`) and `python3` elsewhere. If none of those works on Windows, use `~/.cache/killer-skills-python/python.exe`; the Windows installer puts Python there when it's missing. `mutants.py <command> --help` lists the options.

## The rules that make it work

- **The measure is faults caught, not lines run.** The script makes mutants: one small change to one line (`<` to `<=`, `and` to `or`, a condition turned around, a return value dropped, a call removed, `0` to `1`). Strings and comments are never touched. A mutant the suite still passes with is a survivor: a gap.
- **A clean suite first.** `baseline` runs the tests three times in a fresh copy. A failing or flaky suite stops the run, because then a mutant's result means nothing.
- **A test counts when `check-test` passes.** The new tests pass 3 of 3 runs on the real code; each fault they're credited with fails the suite twice; and that fault passes with the new tests taken out. Flaky passes, flaky kills and faults that fail anyway don't count.
- **Tests only.** Writers change nothing but tests. `check-test` refuses a patch that touches any other file, and `land` only ever commits test files.
- **Every agent is blind** to the run folder, the score, the goal and the other agents. A judge gets the change, never the writer's reason for calling it equivalent. Start agents fresh (`general-purpose`), never with `fork`, which would inherit this conversation.
- **Each agent works in its own sandbox,** a throwaway copy of the project made by `mutants.py sandbox`. It runs the project's code only through the sandbox's `run.py`, with its own home and temp folders, no credentials, and a dead proxy for the network. These are environment variables, not walls, so the side-effect check in section 2 comes first.
- **Equivalence is a claim, not a result.** A writer may say a fault can't change anything observable. It stays a survivor, tagged with the reason, unless a judge agrees on a thorough run; even then the report says it's the judge's word.
- **Report what wasn't mutated:** files left out and why, survivors with no test, and every number from the script.

## 1. Setup: ask, don't make the user remember options

Skip anything the user already said ("measure my tests, don't write any"). Before asking, look at the project for a minute: its languages, its test framework and command (README, CONTRIBUTING, CI config, `package.json` scripts, `pyproject.toml`, Makefile), how long the suite takes, whether the tests need a service or the network, and whether the current branch is ahead of `main`. Use that to put the right defaults first. Ask the rest with AskUserQuestion, in at most two calls.

**Call 1**

| Header | Question | Options (the first is the default) |
|---|---|---|
| Scope | Which code should the tests cover? | **Whole project (Recommended)** · **This branch's changes**: only lines changed since main (recommend it instead when the branch is ahead) · **One folder or file**: type it under Other |
| Goal | How far should it go? | **Close the gaps it finds (Recommended)**: write tests for survivors until none is left untried · **A target score**: stop at, say, 80% (type a number under Other) · **Measure only**: the score and the survivors, no tests written |
| Size | How many mutants? | **300, sampled (Recommended)** · **100, quick** · **Every mutant**: can take hours on a big project |
| After | Where do the new tests go? | **A new local branch, one commit per test file (Recommended)** · **Leave them uncommitted** · **Report only**: the tests stay in the run folder as patches |

**Call 2**, only when it matters:

| Header | Question | Options |
|---|---|---|
| Network | The tests seem to need the network. Can the sandboxes use it? | **No (Recommended)**: the tests that run offline only, with a test command that leaves the others out · **Yes** |
| Depth | When a writer says a fault can't be caught, who decides? | **It stays a survivor, tagged with the reason (Recommended)** · **Thorough**: a fresh judge looks at each one; the ones it calls equivalent leave the score |

If AskUserQuestion isn't available, or the user said "just run it", use the defaults and say so in one line.

**Defaults:** the whole project (or this branch's changes, when it's ahead of main) · close the gaps · 300 mutants, sampled · a new local branch, one commit per test file · network off · equivalents stay tagged survivors · the test command from the README, CI or package files (ask in chat only if none can be found).

## 2. Map the project

1. **Init**, with the test command after `--`:
   ```
   py <skill-dir>/scripts/mutants.py init --repo <project> --scope <repo | branch | branch:<base> | changes | path:<folder>> --target "<one line on what it is>" [--goal close|score:80|measure] [--after branch|uncommitted|report] [--branch <name>] [--include .venv node_modules] [--exclude 'gen/' '*.pb.go'] [--tests '<globs the usual patterns miss>'] [--cwd <subfolder>] [--network] -- <test command>
   ```
   It prints the run folder (`~/.claude/killer-tests/<project>/<date>-<slug>/`) and how many source and test files it found. The test command must run the whole offline suite without asking anything: `python -m pytest -q`, `npm test --silent`, `go test ./...`. If the tests run built files (a `dist/` folder), the command must build first; it's run without a shell, so wrap a pipeline in one (`sh -c "npm run build && npm test"`, `cmd /c ...`). If the project needs installed dependencies (`node_modules`, a virtual environment), pass them with `--include`; never install anything into the user's machine.
2. **No tests at all:** say so. Offer to start a test file only when the ecosystem has an obvious framework (pytest if `pyproject.toml` lists it, `node --test` for Node 18 or later with no framework, `go test`). Otherwise ask which framework to use.
3. **The side-effect check.** The suite will run hundreds of times, unattended. Search the code the tests reach for writes that environment variables don't redirect: the Windows registry and PATH, services, scheduled tasks, startup entries, global package installs, absolute paths into the real home folder, and anything that reaches the network. For each, find the project's test hook (an override, a dry-run flag, a variable its tests use), and list the commands agents must not run in every sandbox block. If the suite itself does one of these things, use a test command that leaves those tests out, and say so in the report.
4. **Write the facts block** every brief shares: what the project is, how its tests are organised, the test command, and the run command in a sandbox. Facts only.
5. **Check the project's git status** with `mutants.py status -w <run>` before and after each phase below. Exit 1 means something changed it; if you didn't, undo it and say so in the report. After a change of your own, `status --mark`.

## 3. Baseline and mutants

```
py <skill-dir>/scripts/mutants.py baseline -w <run>
py <skill-dir>/scripts/mutants.py generate -w <run> [--max 100 | --all]
py <skill-dir>/scripts/mutants.py run -w <run>
```

- **`baseline`** must pass 3 of 3 runs. **FLAKY** or **FAILING** stops the run: report it (section 7) with the failing tests' names, or, if the user agrees, start over with a test command that leaves them out. To change the test command, run `init -w <run> --force` with the same options and the new command; nothing else carries over. It also runs one copy per sandbox at once; if that fails where one at a time passed (fixed ports, shared files), mutants run one at a time, and it says so. The per-mutant timeout is 3 times the median run plus 5 seconds, at least 10.
- **`generate`** makes the mutants, picks the sample (seeded, spread across files and operators) and says what it left out: tests, docs, vendored and generated code, migrations, constants files. With the branch scope it mutates only changed lines. It prints a time estimate; past two hours, suggest `--max 100` or a narrower scope before going on.
- **Announce** in one line, then carry on:
  > killer-tests: whole project, 300 of 812 mutants · 8 sandboxes, about 25 minutes · then writers for the survivors · new tests on a local branch · network off.
- **`run`** reports the score per file. If it's interrupted, `run --resume` finishes the rest. A file whose mutants all survive usually means the tests import an installed copy rather than the one in the sandbox; fix the test command before going further.

With **Measure only**, go to section 7.

## 4. Write tests for the survivors

1. `mutants.py slices -w <run>` groups the survivors into slices of 5 to 15 by file and function, and into batches. Slices in one batch touch different files.
2. For each slice in the batch: `mutants.py sandbox -w <run> write-S1 --slice S1`. The sandbox has the tests accepted so far in it, and the slice's faults in `scratch/mutants/` with a helper to put each one in and take it out. Write the brief from "Test writer" in `references/briefs.md`.
3. Send the batch in one message, at most 8 writers at a time: one Agent call each, `subagent_type: "general-purpose"`, `run_in_background: false`, a description like "Write tests: S1", the brief as the prompt. With more than 16 slices in all, tell the user how many writers that is and ask whether to do them all or the biggest files first (unattended: the biggest files first, recorded with `decide`).
4. Collect them **one at a time**, in order:
   ```
   py <skill-dir>/scripts/mutants.py collect -w <run> --sandbox write-S1 --accept
   ```
   It takes the writer's test changes as a patch, puts back any fault the writer left in, records equivalence claims, deletes the sandbox and runs `check-test`. **PASS** prints which faults the tests kill, and `--accept` adds the patch to the accepted list. **FAIL** says why in one line: the patch touches a non-test file, fails or is flaky on the real code, or kills none of its targets. Send one fresh writer for that slice, in a new sandbox (`sandbox -w <run> write-S1b --slice S1`), with the retry paragraph from `briefs.md`. If that fails too, leave the slice: its survivors stay, tagged "Test written but didn't kill it".
5. Make the next batch's sandboxes only after this batch is accepted, so they build on its tests. `accept` refuses a patch whose check ran before another patch was accepted; run `check-test -w <run> P4 --accept` again.
6. Repeat with `slices -w <run>` until the goal is met (`score` says when a target score is reached) or no untried survivors are left. `slices --again` makes slices from survivors a writer already tried; use it once at most.
7. **Thorough depth:** for each survivor a writer called equivalent, a judge from "Equivalence judge" in `briefs.md`, then `mutants.py equivalent -w <run> M21 --sandbox judge-M21`.

## 5. Review

When the last patch is accepted: `mutants.py sandbox -w <run> review --combined`, a brief from "Test reviewer", then `mutants.py review -w <run> --sandbox review`. For each issue that holds up:

- **Drop it:** `mutants.py drop -w <run> P3 --reason "<the issue>"`. Patches accepted after it come out too; run `check-test -w <run> P4 --accept` on each of them again, in order.
- **Or fix it:** drop it as above, then `mutants.py sandbox -w <run> fix-P3 --patch P3` (a copy with the accepted tests and P3's changes in), fix the test there yourself, and `collect -w <run> --sandbox fix-P3 --targets <P3's mutants> --accept`. Then re-check the patches that came out with it.

Run the review only once; fixes don't go back to the reviewer.

## 6. Land and rescore

- **A new local branch:** `mutants.py land -w <run>` makes `killer-tests/<date>` from the current commit in a separate worktree, so the user's checkout is never touched, with one commit per test file (`--per patch` for one per patch). It runs the full suite in a fresh copy of the branch. Nothing is pushed.
- **Leave them uncommitted:** `mutants.py land -w <run> --uncommitted`.
- **Report only:** don't land.

Then `mutants.py rescore -w <run>` runs the same sample again against the final tests (the branch, or the accepted patches), for a clean before and after. It warns if a mutant the old tests killed now survives: a patch weakened an existing test. Find the patch, drop it, and land again on a new branch.

## 7. Report

`mutants.py report -w <run>` writes `<run>/report.md` and `<run>/summary.json`: the score before and after, per file; the tests added, what each checks and which faults it kills; the survivors left, grouped by file with the mutated line, each tagged "No test written", "Claimed equivalent: <reason>" or "Test written but didn't kill it"; flaky tests found; the review; and what wasn't mutated. Read it, and add a short summary at the top if the run needs explaining. Then `mutants.py clean -w <run>`.

Give the user, in chat: the score before and after, how many tests were added, the three most telling survivors left (a sentence each), the branch name, and a link to `report.md`. A writer's note about a bug in the code goes in the chat too, with a suggestion to prove it with killer-bugs.

## Running unattended

killer-goal runs this skill overnight, and a user may start it and walk away. Then:

- **The setup answers come with the request.** Don't ask: use what was given and the defaults for the rest, and say so in one line.
- **When killer-goal names a branch, commit there** instead of making a new `killer-tests/<date>` branch. `init --branch <name>` and `land --branch <name>` take it and, when that branch is the current one, commit on it (only the test files, and only when they have no uncommitted changes of the user's).
- **Questions that come up later don't stop the run.** Take the safe choice, carry on, and record it with `mutants.py decide -w <run> "<text>"`. They're listed under "Decisions to check" in the report.
- **Nothing outward-facing happens unattended:** no push, pull request, publish, tag, upload or settings change. Prepare it, record it with `mutants.py wait -w <run> "<text>" --command "<cmd>"`, and it's listed under "Waiting for your OK".
- **Finish with `mutants.py report`,** so `summary.json` is there for whoever reads the run in the morning, and end with the one line it prints: `killer-tests: <done | partial | blocked> · <headline> · <path to report.md>`.

## When things go wrong

- **The suite is flaky or failing:** the run is blocked, and the report says which tests. Don't mutate anyway; don't "fix" the tests unasked. Offer to rerun with a test command that leaves them out.
- **The suite takes minutes:** the estimate says how long. Narrow the scope, sample fewer, or give a test command for the fast tests that cover the code in scope (a subset counts, as long as it's the same for every mutant).
- **Tests need a service or the network:** give the subset that runs offline as the test command, and say in the report what wasn't exercised.
- **Many mutants are "not viable":** the build broke on them, which is common in compiled languages. They're left out of the score and listed. If it's most of them, the language's build-error pattern may be missing something; say so.
- **A writer edits the project's code:** `check-test` refuses the patch. A fault left in by mistake is put back by `collect`. If the real project's `git status` changed, undo it and say which agent did it.
- **An agent wrote no usable `reply.json`:** `collect` exits 2 and keeps the sandbox. Send the same brief to a fresh agent once; the lead can also collect the tests left in the sandbox with `--targets <ids>`. If that fails too, note the gap in the report.
- **`land` says a patch doesn't apply:** the user changed those test files since the run started. Ask them to commit or put the changes aside, then land again.
- **A survivor sits in code no test can reach** (a `__main__` block, a platform branch): leave it, tagged. The report is more useful honest than full.
