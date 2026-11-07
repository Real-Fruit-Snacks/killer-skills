---
name: killer-bugs
description: "Hunt for bugs in a project with blind agents, and report only the proven ones. You lead: ask a few setup questions (scope, kinds of bug, depth, what happens after), have a promise checker test what the docs promise without reading the code, and send a read-through per slice that checks every chosen kind of bug. Finders reproduce what they find; a script reruns every reproduction in a fresh, isolated copy before it counts. The worst suspects, up to the depth's limit, go to a prover if they lack a reproduction, then to a challenger that tries to knock them down. Survivors are searched for copies, ranked, and fixed if the user wants, each with a test that fails without the fix. Use whenever the user wants to find bugs, hunt or review for bugs, run a bug bash, check a repo or branch for bugs before a release or merge, or asks what's broken in their code, even if they never say 'killer-bugs'. For one known bug, debug fits better; for stronger tests, killer-tests; for scoring work against a bar, killer-council."
---

# Killer Bugs

You are the **lead**. Fresh agents do the finding, proving and challenging. Each one sees only its own job and its own copy of the project. You plan the run, keep the books, decide what's worth proving, and fix what's proven if the user wants. You never declare a bug real yourself. A bug counts once a reproduction has been rerun by the script in a fresh copy and shown it, and, for a serious one, a challenger couldn't knock it down. A serious bug that can't be made to happen here at all (it needs a full disk, a narrow race, another system) can still be reported as *argued*, once a code checker has traced it from an entry point to the failure. It's reported apart from the proven bugs, never as one.

`scripts/bugs.py` in this skill's folder keeps the books: run folders, sandboxes, the bug list, coverage, fix checks and the record kept between runs. Run it with `py` on Windows (or `python` if there's no `py`) and `python3` elsewhere. If none of those works on Windows, use `~/.cache/killer-skills-python/python.exe`; the Windows installer puts Python there when it's missing. `bugs.py <command> --help` lists the options.

## The rules that make it work

- **Proven, argued, or not reported as a bug.** Suspicions nobody reproduced or argued go in their own section of the report, never among the bugs.
- **One reader per slice, and a budget per depth.** Reading is where a hunt's cost goes. Each read-through agent reads its slice once and checks every chosen kind of bug as it goes; it doesn't take one agent per kind re-reading the same code. Each depth has a limit on how many suspects go on to be proven and challenged. `bugs.py forward` applies it, and the report says how many agents the run really used.
- **A reproduction says what it saw.** It's a script that prints `EXPECTED:` and `OBSERVED:` lines, exits 1 when the bug shows and 0 when it doesn't. `bugs.py` reruns every one in a fresh copy of the project. A failure without an `OBSERVED:` line proves nothing: it may be a crash, a typo or a missing command.
- **Severity decides what gets proven.** Finders rate each suspect high, medium or low, and the worst go forward first. Without this, a hunt spends its budget on `NaN` in an error message while a bug that deletes files waits in the queue.
- **Promises before code.** The promise checker works out what the project should do from its docs alone. Then the code's behaviour can't quietly become the definition of correct.
- **Every agent is blind** to the run folder, to other agents, and to anyone's reasoning but its own. A prover gets a claim to test, never the argument for it. An agent handed an argument tends to agree with it. Start agents fresh (`general-purpose`), never with `fork`, which would inherit this conversation.
- **Each agent works in its own sandbox,** a throwaway copy of the project made by `bugs.py sandbox`. It runs the project's code only through the sandbox's `run.py`, with its own home and temp folders, no credentials, and a dead proxy for the network. The project's own addresses on this machine still work. These are environment variables, not walls. Windows PowerShell 5.1 and raw sockets ignore the proxy. Nothing redirects writes to the Windows registry, the user's PATH, services or scheduled tasks. That's why the side-effect check in section 2 comes before any agent runs anything.
- **A fix counts when `check-fix` passes:** the test fails with the fix taken out and passes with it in.
- **Report what was checked,** not just what was found. "No bugs in X" and "nobody read X" must never look the same.

## 1. Setup: ask, don't make the user remember options

Skip anything the user already said ("hunt bugs in this branch, just report"). Ask the rest with AskUserQuestion, in at most two calls. Before asking, look at the project for a minute: its languages and size, whether it has tests, scripts for more than one shell, pairs like two installers, servers, and whether the current branch is ahead of `main`. Use that to put the right defaults first.

**Call 1**

| Header | Question | Options (the first is the default) |
|---|---|---|
| Scope | What should I check for bugs? | **Whole project (Recommended)** · **This branch's changes** against main (recommend it instead when the branch is ahead) · **Uncommitted changes** · **One folder**: type it under Other |
| Kinds (multiSelect) | Which kinds of bug should the hunt look for? The read-through checks every one you pick as it reads, so more kinds don't mean more agents. | The first four kinds from `references/finders.md`, the ones that fit this project marked "(Recommended)" |
| More kinds (multiSelect) | And these? | The other four, marked the same way |
| Depth | How deep? | **Standard (Recommended)**: the worst 15 high- and medium-severity suspects proven and challenged; about 10 to 25 agents · **Quick**: the worst 5 high-severity ones; about 5 to 10 agents · **Thorough**: also one agent per chosen kind and a second read-through per slice, and up to 30 suspects of any severity; about 30 to 60 agents |

**Call 2**

| Header | Question | Options |
|---|---|---|
| After | Once bugs are proven, what then? | **Report, then ask which to fix (Recommended)** · **Fix every proven bug**: on a new local branch, one commit per bug, nothing pushed · **Report only** |
| Network | Can the sandboxes use the network? | **No (Recommended)**: a dead proxy, so most tools can't reach it; this machine's own addresses still work · **Yes**: for projects whose tests need it |
| Your seat | Do you want to add to the hunt? | **No, just run it (Recommended)** · **I suspect some bugs**: they get proven like anyone's · **I'll say what must always be true**: the promise checker tests it · **Both** |
| Copies | When a serious bug is proven, where should I look for copies of it? | **This project (Recommended)** · **My other projects too**: name the folder; they're read, not run |

If the user wants a seat, ask for their suspicions or promises in chat after the questions and wait. If AskUserQuestion isn't available, or the user said "just run it", use the defaults and say so in one line.

**Defaults:** the whole project (or this branch's changes, when it's ahead of main) · the recommended kinds · standard depth · report, then ask · no network · no seat · copies in this project only.

## 2. Map the project

1. **Init:**
   ```
   py <skill-dir>/scripts/bugs.py init --repo <project> --scope <repo | branch | branch:<base> | changes | path:<folder>> --target "<one line on what it is>" --depth <quick | standard | thorough> [--network] [--after ask|fix|report] [--include node_modules ...] [--exclude vendor/ LICENSE '*.svg' ...] [--env NAME=VALUE ...]
   ```
   It prints the run folder (`~/.claude/killer-bugs/<project>/<date>-<slug>/`), the scope and anything earlier runs recorded. Leave vendored code, licences, images and generated files out with `--exclude`, so coverage measures the code. If there's a record, run `bugs.py recheck -w <run>`. It reruns earlier runs' reproductions and reports any fixed bug that came back.
2. **Learn to run it.** Read the README, CONTRIBUTING and the CI config for the build and test commands. Make your own sandbox (`bugs.py sandbox -w <run> map`) and run them there through its `run.py`. The baseline is what passes and fails now. A failing test is a candidate (`bugs.py add --source tool:tests`), not background noise.
   - **Ignored files the tests need** (`node_modules`, a virtual environment): start over with `init --include <them>`.
   - **Things the project finds through the home folder** (a cache of downloaded tools, a config file it reads): point the sandboxes at a copy with `init --env NAME=VALUE`, or the project's own setting for it. Never point them at the real folder if anything writes there.
3. **The side-effect check.** Search the code agents will run for writes that environment variables don't redirect:
   - the Windows registry, and the user's or system PATH;
   - services, scheduled tasks, startup entries and shortcuts;
   - global package installs, and absolute paths into the real home folder.

   Also look for anything that reaches the network. For each one, find the project's test hook (a URL override, a dry-run flag, an environment variable its tests use) and put it in the facts block. In every sandbox block, list the commands agents must not run, with the reason.
4. **Run the checkers the project already uses** (its linters, type checker, compiler warnings) if they're installed. Never install one. Findings about behaviour become candidates with `--source tool:<name>`; style findings don't.
5. **Write the facts block** every brief shares: what the project is, who uses it, and the commands that build, run and test it in a sandbox. Keep it to facts; your guesses about where the bugs are stay out.
6. **Announce** in one line, then carry on:
   > killer-bugs: whole project (57 files) · promise checker and 2 read-throughs checking 5 kinds of bug · then the worst 15 high- and medium-severity suspects proven and challenged, about 15 to 20 agents · report, then ask · network off.

## 3. Find

Make one sandbox per agent, named for its role, so the report can count agents: `promises`, `read-<slice>`, `pairs`, `find-<kind>`. Write each brief from `references/briefs.md`:

- **The promise checker:** save each command's usage text (`--help` output) into its scratch folder first, and list the doc files it may read.
- **One read-through per slice.** A slice is a folder or group of files, about 40 files or 5,000 lines at most; a small project is one slice. Each read-through's brief carries the "Look for" list and probe of every kind the user chose (`references/finders.md`), so one agent checks them all as it reads.
- **A Pairs agent,** when the project has pairs and Pairs was chosen. Comparing two sides means running both systematically, which a reader won't do in passing.
- **On a thorough run,** add one agent per chosen kind across the whole scope (`find-<kind>`), and a second read-through per slice, briefed alike.

Re-runs and Pairs checks often need an older version to compare with. Make it with `bugs.py sandbox -w <run> old-<tag> --rev <tag>` and name it in the briefs that need it.

Send them all in one message: one Agent call each, `subagent_type: "general-purpose"`, `run_in_background: false`, a description like "Find bugs: src/core", and the brief as the prompt. Each agent writes `reply.json` in its scratch folder and answers you in a line. Then, for each:

```
py <skill-dir>/scripts/bugs.py collect -w <run> --sandbox read-core --source hunter:read-core#1
```

`collect` adds the candidates and keeps the finder's reproductions. It **reruns each one in a fresh copy**: one that shows the bug makes that candidate `proven`, one that doesn't leaves a note. It also checks every `file:line` the agent gave and notes the ones that don't exist. Then it deletes the sandbox. If an agent didn't write `reply.json`, save its reply text to a file and pass that file instead of `--sandbox`.

## 4. Triage

- **Merge** candidates that are the same bug (the same cause, not just the same file): `bugs.py merge -w <run> <keep> <duplicates...>`. A working reproduction carries over to the kept one. A bug found separately by two finders is the strongest early signal. The report says so.
- **Add the user's suspicions** with `bugs.py add --source user --severity <guess>`.
- **Check the severities.** Finders rate their own finds; set a better guess with `bugs.py update -w <run> B7 --triage high` where one is plainly off, and rate any that have none. Rate by what happens to a user: lost or overwritten data, leaked secrets and false success are high, whatever the code looks like.
- **Inferred promises:** a broken promise marked "inferred" isn't a candidate until the user confirms the promise. Ask about them in one batch.
- **Coverage:** run `bugs.py coverage -w <run>`. If files that matter went unread, send one more read-through at them.

Then pick what goes forward:

```
py <skill-dir>/scripts/bugs.py forward -w <run>
```

| Depth | Goes forward | At most |
|---|---|---|
| Quick | high | 5 |
| Standard | high and medium | 15 |
| Thorough | everything | 30 |

It ranks by severity, then by how many finders found the bug separately, then by confidence. It prints which suspects need a prover (no working reproduction yet), which are ready for a challenger, and which go to a code checker. The rest are over the limit or below the depth, and the report lists them as not tried. Rate anything it calls unrated first. Don't hand-pick around it; if the order looks wrong, fix the severities and run it again. If many serious suspects are over the limit, say so in the report and offer a second pass; don't raise the limit mid-run.

## 5. Prove

Only suspects going forward that aren't proven yet need a prover: the finder had no reproduction, or its reproduction didn't show in a fresh copy. One fresh prover each, in parallel batches, each in its own sandbox (`prove-B3`). The brief gives the claim, the trigger, what was expected and where; never the finder's explanation, who found it, or how many did. Then:

```
py <skill-dir>/scripts/bugs.py update -w <run> B3 --as prover --sandbox prove-B3
```

`update` reads the prover's `reply.json` and keeps its reproduction. It **reruns that reproduction in a fresh copy**, whatever the prover says: one that doesn't show makes the bug `suspected`, and one that fails without an `OBSERVED:` line is flagged inconclusive. For an inconclusive one, read the output; if it isn't the bug, `update B3 --status suspected`. Then it deletes the sandbox.

**When it can't be made to happen here.** Some real bugs can't be run on this machine: a full disk between two writes, a race too narrow to hit, another operating system, a real service. The prover then answers `impractical` and says what it would take. For a high- or medium-severity one, `forward` lists it for a **code checker** (`references/briefs.md`): a fresh agent, in its own sandbox (`code-B3`), that traces the claim through the code from an entry point to the failure, quoting each step, and tries to break the chain. Record it with `update B3 --as code-check --sandbox code-B3`. A chain that holds makes the bug `argued`, reported in its own section with the chain. One that breaks dismisses it. An argued bug is never counted as proven, and it's fixed only with a test that fails before the fix, at whatever level a test can reach it.

## 6. Challenge

One fresh challenger per proven bug going forward, in its own sandbox. Copy the reproduction (`<run>/repros/B3/`, or the finder's `<run>/scratch/<sandbox>/`) into its scratch folder first. Record it with `update B3 --as challenger --sandbox challenge-B3`. The verdict sets the status:

- `stands`: confirmed, with severity and likelihood
- `question`: it might be deliberate. Ask the user, in one batch at the end. Their answer makes it `--status confirmed` or `--status intended`.
- `knocked-down`: dismissed, with the reason in the report

## 7. Copies

For each confirmed high- or medium-severity bug, write down the mistake behind it in a sentence or two. Send a copy finder with the bug's reproduction in its scratch folder. Collect it with `--source copy-of:B3`; its adapted reproductions are rerun like any finder's. A copy that reproduces is the same mistake, so it doesn't need its own challenger. Mark it `update Bn --status confirmed --severity <the original's> --note "copy of B3"`. That doesn't hold when it sits somewhere the behaviour might be intended, and then it gets a challenger. Copies in other projects the user named stay suspected, with their paths: they get proven in a run on that project. Don't send copy finders after low-severity bugs.

## 8. Report

Run `bugs.py coverage -w <run>`, then `bugs.py report -w <run>`. It writes `<run>/report.md`:

- proven bugs, ranked by severity, then likelihood, each with its reproduction command
- argued bugs, each with its chain and what it would take to see it happen
- questions for the user
- bugs reproduced but not challenged
- suspects that weren't proven, including any over the limit
- low-severity suspicions nobody checked
- dismissed bugs, with the reason
- the promises checked
- coverage
- the run details: the depth, how many agents it started (by role), and how long it took

Read it, and add a short summary at the top if the run needs explaining.

**If the host has a ReportFindings tool** (Claude Code's findings view), also report through it. `bugs.py findings -w <run>` prints the list in that tool's shape, worst first: proven and challenged bugs as `CONFIRMED`; argued ones, questions and unchallenged reproductions as `PLAUSIBLE`. Pass it as `findings`. `report.md` stays the full record.

Then give the user, in chat:

- the count of proven bugs
- the top three, a sentence each
- the questions that need them
- what wasn't covered
- a link to `report.md`

With "Report, then ask", ask which bugs to fix (AskUserQuestion, multiSelect, the top bugs plus "All of them"). With "Report only", stop here and go to section 10.

## 9. Fix

Work on a new local branch, `killer-bugs/<date>`. For each bug, worst first:

1. **Test first.** Add a regression test to the project's own tests, written like the others. If the project has no tests, don't invent a framework: the saved reproduction is the test (`check-fix --repro B3`).
2. **Fix the cause, every copy of it.** A fix that only handles the reproduction's exact input isn't a fix.
3. **Prove it:**
   ```
   py <skill-dir>/scripts/bugs.py check-fix -w <run> --bug B3 --tests <test files or globs> -- <test command>
   ```
   It runs the test with your fix, then again with the fix taken out. Both must hold: fails without, passes with. Read the "without" output: the failure must be the bug, not a missing file or import the fix added.
4. **Run the full test suite** in a sandbox and compare with the baseline: nothing that passed before may fail now.
5. **Fresh fix review:** a fix reviewer gets the claim, the reproduction and the diff, and nothing of your reasoning. Fix what it finds, then run steps 3 and 4 again.
6. **Commit** that one fix with a plain message saying what was wrong and what changed, then `bugs.py update -w <run> B3 --status fixed`. Nothing is pushed.

A bug the user marked intended, or an open question, never gets "fixed". When all fixes are in, run the final pass: one finder over the branch's combined diff, with the lens from `references/finders.md`. Anything it finds goes through triage like any other candidate. Rerun `report`.

Offer to keep the promise checker's tests in the project as a spec, so a future change that breaks a promise fails the test suite. If the run turned up test gaps (bugs the suite never noticed), offer killer-tests: it finds the rest of the code the tests don't really check, and proves each new test catches a fault.

## 10. Close

`bugs.py close -w <run>` writes this run's outcomes to the project's record, so the next run reruns their reproductions. Proven bugs stay open until fixed. Bugs the user called intended aren't raised again. Then `bugs.py clean -w <run>` deletes any sandboxes left. Don't push anything, and don't open pull requests unless the user asks.

## Running unattended

killer-goal runs this skill overnight, and a user may start it and walk away. Then:

- **The setup answers come with the request.** Don't ask: use what was given and the defaults for the rest, and say so in one line. "Report, then ask" becomes "Report only" unless the request says to fix.
- **When killer-goal names a branch, fix on it** instead of making `killer-bugs/<date>`.
- **Questions that come up later don't stop the run.** Inferred promises stay unconfirmed, challengers' `question` verdicts stay open, and suspects over the depth's limit stay over it. Record each call with `bugs.py decide -w <run> "<text>"`; the report lists them under "Decisions to check".
- **Nothing outward-facing happens unattended:** no push, pull request or issue. Record anything that would need one with `bugs.py wait -w <run> "<text>" --command "<cmd>"`; it's listed under "Waiting for your OK".
- **Finish with `bugs.py report` and `bugs.py close`.** `report` writes `summary.json` beside `report.md` for killer-goal, and prints the one-line summary to end with: `killer-bugs: <done | partial> · <headline> · <path to report.md>`.

## When things go wrong

- **Before and after each phase, check the real project's `git status`.** Agents work in sandboxes, but they're agents. If the real project changed, find out which agent did it, undo it, and tell the user.
- **The tests can't run in a sandbox** (they need a service, a device or the network): say so in the brief's facts. Provers then use whatever entry points do run. The report says what couldn't be exercised.
- **A prover can't reproduce something you're sure of:** it stays suspected. Don't argue it into the proven list; give a new prover a sharper trigger if you have one.
- **A huge project:** ask which part matters. Otherwise slice the read-through by folder, and say so in each brief. Coverage shows what was left.
- **An agent wrote no usable `reply.json`:** send the same brief to a fresh agent once. If that fails too, note the gap in the report.
