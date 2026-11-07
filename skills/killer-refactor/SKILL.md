---
name: killer-refactor
description: "Restructure code without changing what it does, and prove it. You lead: ask a few setup questions (which code, whether the interface may change, how hard to check, what then), cut the refactor into small steps, and record what the code does now: the tests run 3 times, recorded runs of its commands with their output and files, its public names and signatures, and pinning tests for barely tested code that must catch a planted fault. Refactorer agents make one step each in a sandboxed copy; a script verifies each in a fresh copy against all of that, and a blind challenger's probes run on the original and the changed code. Steps that hold are committed one each on a new local branch, checked again as a whole. Use whenever the user wants to refactor, restructure, split a module, extract functions, rename across a codebase, remove dead code, untangle logic, replace a deprecated API or move files, even if they never say 'killer-refactor'. For bug fixes, killer-bugs fits better; for speed, killer-perf."
---

# Killer Refactor

You are the **lead**. Fresh agents make the changes, write the pinning tests and look for what the changes break. Each one sees only its own job and its own copy of the project. You turn the user's refactor into small steps, record what the code does now, keep the books, and land what's proven if the user wants. You never call a step behaviour-preserving yourself. A step counts only when `refactor.py verify` passes it in a fresh copy (the tests pass, every recorded run gives the same output, the public interface is the same or changed exactly as planned, every pin passes) and then a challenger's probes find no input where the original and the changed code differ.

`scripts/refactor.py` in this skill's folder runs the checks and keeps the books: run folders, sandboxes, the baseline, recorded runs, the interface, pins, steps, probes, metrics, landing and the report. `scripts/faults.py` plants the small faults a pin has to catch. Run it with `py` on Windows (or `python` if there's no `py`) and `python3` elsewhere. If none of those works on Windows, use `~/.cache/killer-skills-python/python.exe`; the Windows installer puts Python there when it's missing. `refactor.py <command> --help` lists the options.

## The rules that make it work

- **Only the script counts.** A refactorer's "the tests pass", a challenger's "looks equivalent" and your own reading are leads, not results. Every number in the report comes from `refactor.py`, never typed by you.
- **Record before you change.** The baseline, the recorded runs and the interface are taken from the original code before any step. A step is compared with them, never with the step before it alone, so small drifts can't add up unseen.
- **One step, one kind of change.** Move, then change; never both in one step, and never a fix inside a refactor. `references/craft.md` says how to cut a refactor into steps. A step that would fix a bug is a finding for the user, not a step.
- **The interface changes only as planned.** Each step in the plan lists the interface changes it may make (`rename`, `move`, `add`, `remove`, `change`). Any other difference rejects the step. The script carries renames through the code of the tests, pins, drivers and probes (names only, never strings, comments or data files); a step may change test files only by its planned renames, exactly.
- **Pin what the tests don't check.** `pin survey` plants faults function by function and names the code no test notices. A pinning test counts only when it passes 3 of 3 on the original and fails on a fault planted in the code it pins.
- **Every agent is blind** to the run folder, to other agents and to anyone's reasoning but its own. A challenger gets the plan and the step's diff, never the refactorer's account of it. Start agents fresh (`general-purpose`), never with `fork`, which would inherit this conversation.
- **Each agent works in its own sandbox,** a throwaway copy made by `refactor.py sandbox`. It runs the project only through the sandbox's `run.py`, with its own home and temp folders, no credentials, and a dead proxy for the network. These are environment variables, not walls: nothing redirects writes to the Windows registry, PATH, services or scheduled tasks. That's why the side-effect check in section 2 comes first.
- **Report what was checked,** not just what passed. Rejected steps and why, refused pins, probes that found nothing, flaky tests left out: all of it goes in the report.

## 1. Setup: ask, don't make the user remember options

Skip anything the user already said ("split utils.py, keep the API, just commit it"). Ask the rest with AskUserQuestion, in at most two calls. Before asking, look at the project for a minute: its language, whether it has tests and how long they take, whether it has a command line or examples that exercise the code in scope, and whether the code in scope is a library others import. Use that to put the right defaults first. Ask for the refactor in the user's words in chat if it isn't clear.

**Call 1**

| Header | Question | Options (the first is the default) |
|---|---|---|
| Scope | Which code? | **The folder or files you named (Recommended)** · **This branch's changes** · **The whole project** |
| Interface | May the public interface change? | **No: everything public stays the same (Recommended)** · **Only the renames and moves I asked for** · **Yes: it's internal code** |
| Depth | How hard should it check? | **Standard (Recommended)**: tests, recorded runs, interface, pins and a challenger with 3 probes per step · **Quick**: tests, recorded runs and interface, no pins or challenger · **Thorough**: 6 probes per step, more faults per pin, and pins for everything the survey names |

**Call 2**

| Header | Question | Options |
|---|---|---|
| Pins | Keep the pinning tests? | **Yes, add them to the project's tests (Recommended)** · **No, use them for this run only** |
| After | What happens to the changes? | **Commit them on a new local branch, one per step (Recommended)**: nothing pushed · **Leave them uncommitted** in the working tree · **Report only** |

Recommend "Only the renames and moves I asked for" instead when the request is a rename or a move. If AskUserQuestion isn't available, or the user said "just run it", use the defaults and say so in one line.

**Defaults:** the code the user named (this branch's changes if they named none and the branch is ahead) · interface stays the same · standard · keep the pins · commit on a new local branch.

## 2. Record what the code does now

1. **Init:**
   ```
   py <skill-dir>/scripts/refactor.py init --repo <project> --scope <path:<folder or file> | branch | repo> --target "<the refactor, in the user's words>" --interface <same | planned | free> --depth <standard | quick | thorough> --pins <keep | run> --after <commit | leave | report> --test '<test command as a JSON list>' [--include .venv node_modules] [--env NAME=VALUE] [--network]
   ```
   It prints the run folder (`~/.claude/killer-refactor/<project>/<date>-<slug>/`) and the base: the commit every copy is made from. Uncommitted changes are left out, and it says so; if they should count, ask the user to commit them first.
2. **The side-effect check,** before anything runs the project's code. Search the code the tests and recorded runs will exercise for writes that environment variables don't redirect: the Windows registry, the user's or system PATH, services, scheduled tasks, startup entries, global package installs, absolute paths into the real home folder, and anything that reaches the network. For each, find the project's own test hook and use it. List the commands agents must not run in every sandbox block.
3. **Baseline:** `refactor.py baseline -w <run>` runs the tests 3 times in a fresh copy. They must pass every time. A test that flips between runs is recorded as flaky and left out of every verdict from then on; the report says which. Tests that fail every time stop the run: narrow the command (`baseline -w <run> -- <command>`), record why with `decide`, and run it again.
4. **Recorded runs.** Read `references/craft.md` ("Good recorded runs"), then add three to six that exercise the code in scope: its command line, an example, and for a library a small driver script you write into your own scratch space, never into the project:
   ```
   py <skill-dir>/scripts/refactor.py golden add -w <run> <name> [--give <driver.py>] [--mask '<regex>'] -- <command>
   ```
   It runs the command in two fresh copies and keeps stdout, stderr, the exit code and every file it writes (in the project, the scratch folder and the sandbox's home folder), with times, durations, the copy's folder, ids and object addresses masked. A driver given with `--give` is kept in the run and reached as `../scratch/<name>`. Output that differs between the two copies is refused: mask what varies with `--mask`, or make it deterministic. Each run starts as a fresh copy would: what the last one wrote is cleared first. Include an error path or two (a bad argument, a missing file).
5. **The interface:** `refactor.py surface record -w <run>`. For Python it imports the code in scope in a fresh copy and lists every public module, function, class, method, property and constant with its signature. For other languages, give `--command '<a JSON list>'` that prints the interface one entry a line: `go doc -all ./...`, a `tsc --declaration` listing, or a small script you write and pass with `--give` (it's reached as `../scratch/<name>`) that prints each export with its parameters. For code nothing imports (a script), `--none --why "<why>"`. If a step will move code into a folder the scope doesn't cover, add `--folder <it>` now, or the moved names look removed.
6. **The plan.** Restate the refactor as small numbered steps (`references/craft.md`), each with the interface changes it may make, and show the user the list:
   ```
   py <skill-dir>/scripts/refactor.py plan -w <run> --add "<one step>" [--allow "rename pkg.mod.old pkg.mod.new"] [--allow "add pkg.newmod*"]
   ```
   With `--interface same`, only `add` rules are accepted: a new module with public names needs one; a private one (`_helpers.py`) isn't part of the interface.
7. **Write the facts block** every brief shares (`references/briefs.md`). Facts only.
8. **Announce** in one line, then carry on:
   > killer-refactor: tangled.py (1 file) · 4 steps, interface may change only by the rename asked for · 4 recorded runs, 31 interface entries · pins for the code the tests miss, a challenger per step · commit on a new branch.

Check the real project's `git status` with `refactor.py status -w <run>` before and after each of sections 3 to 6.

## 3. Pin what the tests don't check

Skip this at quick depth. `refactor.py pin survey -w <run>` plants up to 3 faults (5 on thorough) in each function in scope, runs the tests with each, and names the functions where the tests caught none. Those are where a refactor can change behaviour unseen. For each function that a step will touch (every one it names, on thorough), make a sandbox (`refactor.py sandbox -w <run> pin-<function>`) and brief a pin writer from `references/briefs.md`. Send them all in one message: one Agent call each, `subagent_type: "general-purpose"`, `run_in_background: false`. Then, for each:

```
py <skill-dir>/scripts/refactor.py pin collect -w <run> --sandbox pin-<function>
py <skill-dir>/scripts/refactor.py pin check -w <run> N1
```

`collect` takes only new test files and refuses anything else. `check` runs the pin's own command 3 times on the original code, then plants faults (6 by default) in the code it pins. It counts the pin only if it passed every run and failed on at least one fault; a pin that passes with every fault planted pins nothing and is refused. A refused pin can be redone once by a fresh writer, with the faults it missed in its brief.

## 4. One step at a time

Steps go in order, each on top of the ones already accepted. For the next step:

1. `refactor.py sandbox -w <run> ref-S1`: the original code with the accepted steps applied and committed, so its diff is this step alone.
2. Brief one refactorer from `references/briefs.md` with the step, its allowed interface changes, the test command and the recorded runs' commands. Send it; when it's back:
   ```
   py <skill-dir>/scripts/refactor.py candidate add -w <run> --step S1 --sandbox ref-S1
   py <skill-dir>/scripts/refactor.py verify -w <run> C1
   ```
3. `candidate add` saves the diff as `candidates/C1.patch` and deletes the sandbox. It refuses an empty change, a change to dependency, build or CI files, a change whose new lines say who or what made it, a refactorer that says it isn't done, and a copy made before the last step was accepted (it's stale: make a fresh one).
4. `verify` applies the accepted steps and this one in a fresh copy and prints a line per check: test files changed only by the planned renames, every recorded run the same, the interface against the plan, every pin, the tests. It exits 0 only for `verified`; otherwise the step is rejected and the lines say which check failed and where (`stdout line 6 was 'small 2', now 'small 3'`). Don't argue with it. Send a fresh refactorer with the same brief plus the failing line, in a new sandbox, at most twice; then record the step as not done with `decide` and move on. If the plan was wrong (the user did ask for that rename), fix it with `plan --replace S2 --allow ...` and verify again.

At quick depth `verified` is `accepted`. Otherwise go to section 5 before the next step.

## 5. Challenge each step

For each verified step: `refactor.py sandbox -w <run> chal-C1 --candidate C1` (the step applied, uncommitted, so `git diff` shows it and `git stash` shows the code before it), and a challenger brief with the plan's step and the diff from `refactor.py candidate show -w <run> C1`. Never the refactorer's account. Then:

```
py <skill-dir>/scripts/refactor.py probe -w <run> C1 --sandbox chal-C1
```

Each probe runs in two fresh copies: the original code, and the original with the accepted steps and this one. Names the plan renamed are carried through. Probes are compared exactly, with no masks but the copy's folder. A probe doesn't count when it can't reach the code on the original side (a missing module or file), when it differs only by a planned rename, or when the original code gives it different output on a second run; output the changed code stops repeating is a difference. When every probe gives the same output and there are enough of them (3 at standard depth, 6 at thorough), the step is `accepted`. Too few: another challenger, briefed the same.

When a probe shows a difference, the script finds which accepted step brought it in, rejects that step, marks the steps after it stale, and turns the probe into a recorded run, so any redo has to keep that behaviour. If the old behaviour looks like a bug the step fixes, it still isn't a refactor: record it with `refactor.py decide -w <run> "<what>"` for the user, keep it out, and suggest killer-bugs for the fix.

## 6. Measure and land

- **Metrics:** `refactor.py metrics -w <run>` counts, for the code in scope before and after the accepted steps: lines, functions, functions over 50 lines, deepest nesting, duplicated blocks, and for Python the cyclomatic complexity. Claims that the code is simpler, shorter or less tangled come only from this table. If a number got worse, say so.
- **Land,** with "Commit them": `refactor.py land -w <run>`. It makes the commits in a separate worktree from the base: first the pins (when kept), then one commit per accepted step, each message saying what was checked. Only when all of them exist does it make `killer-refactor/<date>` (or move the named branch), so a refusing commit hook leaves the user's checkout and branches as they were. Then it checks the branch as a whole in a fresh copy: tests, every recorded run, the interface, every pin. If that fails, the commits are taken back. With "Leave them uncommitted": `land --uncommitted` checks the steps together first, then applies them to the working tree only if the project is still at the base and those files are clean. With "Report only", don't land.

## 7. Report

`refactor.py report -w <run>` writes `<run>/report.md` and `<run>/summary.json`: the plan and each step's outcome, what landed, the baseline and flaky tests, the recorded runs, the interface, the survey, pins counted and refused, every verify and probe line, rejections and why, the before-and-after table, decisions, what's waiting for the user, and the agents sent by role. Read it, and add a short summary at the top if the run needs explaining. Then `refactor.py clean -w <run>`.

Give the user, in chat: the headline, the steps that landed (or are ready to), what was rejected and why in a line each, the metrics that changed, any behaviour a probe found that looks like a bug, and a link to `report.md`. Don't push anything, and don't open pull requests unless the user asks.

## Running unattended

killer-goal runs this skill overnight, and a user may start it and walk away. Then:

- **The setup answers come with the request.** Don't ask: use what was given and the defaults for the rest, and say so in one line.
- **When killer-goal names a branch, commit there** instead of making a new `killer-refactor/<date>` branch: `refactor.py init ... --branch <name>` or `refactor.py land -w <run> --branch <name>`. When that branch is the current one, it commits on it, in place.
- **Questions that come up later don't stop the run.** Take the safe choice, carry on, and record it with `refactor.py decide -w <run> "<text>"`: a step that keeps failing is left out, a rename the plan didn't foresee isn't added to it, a possible bug fix stays out. They're listed under "Decisions to check" in the report.
- **Nothing outward-facing happens unattended:** no push, pull request, publish, tag, upload or settings change. Prepare it, record it with `refactor.py wait -w <run> "<text>" --command "<cmd>"`, and it's listed under "Waiting for your OK".
- **Finish with `refactor.py report`,** so `summary.json` is there for whoever reads the run in the morning, and end with one line: `killer-refactor: <done | partial | blocked> · <headline> · <path to report.md>`.

## When things go wrong

- **The tests fail on the original code.** Nothing can be verified against them. Narrow the command to tests that pass, record that with `decide`, and run `baseline` again. A project with no tests at all: say so, rely on recorded runs, the interface and pins, and record it as a decision.
- **A recorded run won't repeat.** It reads the clock, a random seed, a hash order or the machine. Mask that part with `--mask`, pass a fixed seed, or write a driver that prints only what's deterministic. Never mask so broadly that the output says nothing.
- **The interface probe can't import the code.** It needs dependencies: start over with `init --include .venv` (or `node_modules`) or `surface record --python <the project's interpreter>`. Modules that run code at import are listed from their source instead, and marked `script`.
- **A test fails in verify that looks flaky** (it didn't flip in the baseline). Run `verify` once more. If it fails again, the rejection stands. If it passes, record the earlier failure with `decide`: a refactor can make a race more likely, so the user should know.
- **Verify rejects a step you're sure is right.** The check is the point. Read the failing line: a changed message, a different file written, a name left public. If the plan didn't foresee an interface change the user asked for, fix the plan; otherwise send a fresh refactorer.
- **A patch won't apply when landing** although it applied in a sandbox. This repository's line-ending settings check files out differently from a plain clone. The patches are in `<run>/candidates/`: say so and leave it for the user.
- **The real project changed** (`refactor.py status` exits 1). If you didn't do it, find which agent did, undo it, and say so in the report.
- **An agent wrote no usable `reply.json`:** send the same brief to a fresh agent once. If that fails too, note the gap in the report.
- **The code isn't Python.** The interface needs a listing command, `faults.py` plants faults in C-like code only by text, and complexity isn't counted. Say in the report which checks didn't apply.
