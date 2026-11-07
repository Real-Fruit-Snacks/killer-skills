---
name: killer-goal
description: "See a goal through, unattended and overnight if need be: 'get this ready for 2.0', 'add CSV export with tests and docs'. You lead: ask a few setup questions, write done-when checks and lock them before any work starts, plan the steps (other killer skills run unattended, or direct work), and work through them on a new local branch. A ledger keeps the state through context summaries, and a Stop hook keeps the session going while steps are left. After every step a script runs the checks in a fresh copy of the branch and guards the user's own branch; a blind acceptance judge has to agree before the goal counts as met. Nothing is pushed or published unless the user chose that at setup. Use whenever the user hands over a goal bigger than one task, says 'work on this overnight' or 'keep going until it's done', or wants several killer skills run in a row, even if they never say 'killer-goal'. For one known bug, debug fits better; for one kind of job, the matching killer skill does."
---

# Killer goal

You are the **lead**, and you're the main session: agents can't start agents, so the skills in the plan run here, one at a time, through the Skill tool. You turn the user's goal into checks that say when it's done, plan the steps, run them, and keep the books. You never call the goal met yourself. It's met when every command check passes in a fresh copy of the branch and a blind judge, who never sees how the work went, agrees on every check.

The conversation will be summarised several times in a night, so **the ledger is the memory, not the conversation.** `scripts/goal.py` keeps it: the checks, the plan, every outcome, the guard and the budget. Run it with `py` on Windows (or `python` if there's no `py`) and `python3` elsewhere. If none of those works on Windows, use `~/.cache/killer-skills-python/python.exe`; the Windows installer puts Python there when it's missing. Below, `goal.py` means `py <skill-dir>/scripts/goal.py`, where `<skill-dir>` is the folder this file is in (usually `~/.claude/skills/killer-goal`); in PowerShell, put `&` before a quoted command. `goal.py <command> --help` lists the options.

> **Lost track? After any context summary, run `goal.py status` from the project folder before anything else.** With no `-w` it finds the run going there. Its last line is the next action, with the exact command. Do that. The keep-going hook's message, if you got one, has the full command too.

## The rules that make it work

- **Done-when before work.** The checks are written from the user's words and locked before anything changes. After the lock a check changes only through `check amend --reason`, and the report lists every amendment. Moving the goalposts has to be visible.
- **The ledger is the memory.** Record every outcome with `goal.py` before you do anything else. Never carry state in your head across a step.
- **Proof over narrative.** A step's result is its skill's script-made summary, or a command check the script ran. Your description of what happened counts for nothing.
- **One branch.** Everything lands on `killer-goal/<date>-<slug>`. The user's own branch never moves. `guard` runs after every step, and `next` won't hand out another step until it has.
- **Nothing outward-facing.** No push, pull request, publish, tag, upload or settings change, except the one draft pull request at the end if the user chose it at setup. The rest is prepared and recorded with `goal.py wait`.
- **Never end your turn while `next` hands out a step,** unless the run is paused. The keep-going hook catches you if you do; don't rely on it.
- **The final judge is blind.** It gets the goal, the checks and a copy of the branch. It never gets the plan, the ledger, a step's report or your account of the night.
- **The budget is real.** When time or agents run out, stop starting steps, wrap up and report what's left.

## 1. Setup: ask, don't make the user remember options

Restate the goal in one sentence. Then look at the project for a minute: its languages, how its tests run, its docs and releases, the branch it's on, uncommitted work, ignored folders the tests need (`node_modules`, a virtual environment), and anything that reaches the network. If "done" is genuinely ambiguous ("make it better"), ask one plain question in chat first.

Skip anything the user already said. Ask the rest with AskUserQuestion, in one call:

| Header | Question | Options (the first is the default) |
|---|---|---|
| Time | How long can it work? | **Up to 10 hours (Recommended)** · **Up to 3 hours** · **Up to 1 hour** · **Until it's done, no limit** |
| Alone | What may it do without you? | **Commit to a new local branch; everything else waits for you (Recommended)** · **Also open a draft pull request at the end**: needs `gh`; the only outward step it takes, and only because you chose it · **Plan and report only, no changes** |
| Depth | How thorough should each step be? | **Standard (Recommended)** · **Thorough**: the skills' thorough depths; several times the cost · **Light**: quick depths, fewer agents |
| Plan | See the plan before it starts? | **Yes: show me the plan and the done-when checks, then I'll say go (Recommended when you're still here)** · **No, start now** |

Then run `goal.py hook status`. If the hook isn't installed and the run is over an hour, ask once more:

| Header | Question | Options |
|---|---|---|
| Keep going | Claude Code ends a turn when I stop calling tools, and an overnight run can stop after step 3 of 9. A Stop hook prevents that. It adds one entry to `~/.claude/settings.json` (backed up first), does nothing unless a killer-goal run is going, and Claude Code picks it up after you open `/hooks` once or restart. Install it? | **Install it; I'll open /hooks now (Recommended)** · **Carry on without it**: then I must never end my turn while steps are left |

To install: `goal.py hook install` (or `--project` for this project's `.claude/settings.local.json`). Ask the user to open `/hooks` before they leave. `hook remove` takes out only its own entry.

If AskUserQuestion isn't available, or the user said "just run it", use the defaults and say so in one line.

**Defaults:** up to 10 hours · a local branch, nothing outward · standard depth · show the plan if the user is still here, otherwise start · the hook is never installed without a yes.

Then **init**:

```
goal.py init --repo <project> --goal "<the user's words>" --hours <10 | 3 | 1 | 0> --alone <branch | draft-pr | plan-only> --depth <standard | thorough | quick> [--agents <cap>] [--include node_modules ...] [--network]
```

It makes the run folder and the goal branch from where the user is, and switches to it. Uncommitted work becomes the branch's first commit, "Work in progress from before the run", so the user's branch stays exactly as it was and nothing is lost (`--dirty stop` refuses instead). It records the starting state for the guard: every branch, remote-tracking ref, tag and the stash. `--include` copies ignored folders the tests need into every sandbox; `--network` lets the checks' sandboxes reach it.

## 2. Done-when checks

Write the checks from the user's words, not from the plan. See `references/recipes.md` for checks that fit common goals.

```
goal.py check add -w <run> "<what must be true, in a sentence>" -- <command>        # passes on exit 0
goal.py check add -w <run> "<what must be true>" --shell "<a command line with a pipe>"
goal.py check add -w <run> "<a statement only a careful reader can check>" --kind judge
```

- **Prefer command checks.** Every goal needs at least one. A judge check is for what no script can settle ("the README shows CSV export with an example that works").
- **For a feature, write the acceptance tests first,** in the project's own test style, commit them on the goal branch ("Add acceptance tests for CSV export"), then `goal.py checks run -w <run>`. They must fail now; a check that already passes proves nothing about the work.
- **Do killer-bugs' side-effect check** before the first `checks run`: the checks run project code in sandboxes, which don't redirect writes to the registry, the user's PATH, services, scheduled tasks, global installs or absolute paths into the real home.
- **Lock:** `goal.py lock -w <run>`. It locks the checks and the contents of the files they run, so weakening the acceptance test fails its check until it's amended with a reason.

## 3. Plan

Build the plan from `references/skills.md` (what each skill does, its setup questions and the safe unattended answer for each, what it leaves behind) and `references/recipes.md`. Each step is a skill run unattended or direct work you do yourself:

```
goal.py plan add -w <run> "<title>" --skill killer-tests --after S1 --moves C1,C3 --agents ~8 --answers "Scope: this branch's changes. Goal: close the gaps. Size: 300, sampled. After: commit on the branch killer-goal named."
goal.py plan add -w <run> "<title>" --kind direct --moves C2
```

Setup answers are plain words, one per header, with the run's depth in them. "This branch's changes" means the changes since the commit the goal branch started from; `next` prints it. `plan load -w <run> plan.json` takes a list of steps; `plan list`, `plan edit` and `plan drop --reason` change it (once the run has started, every change needs a reason and goes in the report).

Announce in one line:

> killer-goal: 6 steps (killer-upgrade, direct work, killer-tests, killer-bugs, killer-docs, killer-release prepare) · 5 done-when checks · up to 10 hours · branch killer-goal/2026-11-07-csv-export · nothing pushed.

If the user wanted to see the plan, show the steps and the checks and end your turn: the run hasn't started, so the hook leaves you alone. When they say go (or straight away if they didn't ask): `goal.py go -w <run>`. That starts the clock and prints the **lead session marker**, the line the hook uses to know this session is the lead.

## 4. The loop

```
goal.py next -w <run>
```

prints one of:

- **`NEXT S3: ...`**: the step, its skill and whether it's installed, the setup answers, the checks it should move, the branch, and what to run. Then:
  1. `goal.py step start -w <run> S3`
  2. **A skill step:** invoke the Skill tool with the step's skill, and as the args the exact "Running unattended for killer-goal ..." text `next` printed. Let the skill run to its end. Its one-line summary is the end of the skill, not of your turn: go straight on to 3. **Direct work:** a test that fails without the change, then the change; commit each logical change on the goal branch with a plain message that says what changed and why. No AI attribution, ever.
  3. `goal.py step done -w <run> S3 --summary <its run folder>` (the folder its report.md is in). It copies the skill's headline, report, commits, decisions and waiting items into the ledger. killer-design and killer-repo write no summary.json: `--headline "<its one-line summary>" --report <the handoff or screenshots its last line names>`. `next` prints the right form. Then copy their "Decisions to check" and "Waiting for your OK" into the ledger with `decide` and `wait`. If it failed: `step failed S3 --reason "..."`. If only the user can unblock it: `step blocked S3 --reason "<the question>"`, and move on.
- **`IN PROGRESS S3`**: a step was started and not recorded. Finish it, or record what happened.
- **`FIRST: ...`**: the guard or the checks are due. Run what it says: `goal.py guard -w <run>`, then `goal.py checks run -w <run>`.
- **`NONE: ...`** or **`STOP: ...`**: no step can run, or the time or agents are spent. Go to section 5.

Then `next` again. Keep going until it says NONE or STOP. Never end your turn in between.

- **A failed step gets one retry with a different approach:** `step start S3 --approach "<what's different>"`. A second failure marks it failed. Steps that need it wait; the rest go on.
- **A skill that stops to ask** despite the unattended text: answer with the safe choice from `references/skills.md`, carry on, and record it with `goal.py decide -w <run> "<the call>"`.
- **Agents:** `step done` adds the skill's agent count from its summary, or the plan's estimate. No skill writes its count yet, so it's the estimate: `goal.py budget -w <run> --agents +N` adds what the step really used beyond it.
- **A step that commits elsewhere:** `step done` warns when a skill says it committed to another branch. Bring the commits onto the goal branch (`git merge --ff-only <that branch>`) before the guard.

## 5. The final check and the blind judge

1. `goal.py checks run -w <run> --final`.
2. `goal.py judge-brief -w <run> [--forbid "<command>: <why>"]` makes the judge's sandbox from the goal branch and prints its brief, filled in from `references/briefs.md`: the goal, the checks, the starting commit, the sandbox. Send exactly that text to one fresh agent: `subagent_type: "general-purpose"`, `run_in_background: false`, description "Acceptance check". Never `fork`: a fork inherits this conversation.
3. `goal.py judge -w <run> --sandbox judge` reads its `reply.json`. A missing or malformed reply is refused: send the same brief to a fresh judge once (`judge-brief` again makes a new sandbox).

**Met** only when every command check passes in the final run and the judge says `met` on every check. Otherwise **partly met** or **not met**, with what's missing. Don't argue with the judge. If it found a real gap and there's time, plan a step for it, run it, and do this section again.

## 6. Report and hand off

`goal.py report -w <run>` writes `report.md` and `summary.json`: the result, the done-when table with the evidence and the judge's word, what each step did, the branch's commits and diffstat, every decision and waiting item (the skills' and yours), what didn't work, amendments, the budget, and how to review. Read it. Then give the user, in chat: the result, the top three things done, the decisions to check, what's waiting, and a link to `report.md`.

If the user chose the draft pull request and the result is met or partly met: `goal.py pr -w <run>`. It's the only command that pushes. It refuses if the user didn't choose it, if the guard has a problem, or if the branch starts with their own uncommitted work (that waits for them instead). It writes a plain description from the ledger and opens the pull request as a draft.

Then `goal.py stop -w <run>`. That clears the active marker, so the hook goes dormant. The user stays on the goal branch; the report says how to merge it.

## Running unattended

killer-goal is what runs overnight. When the user starts it and walks away:

- **The setup answers come with the request.** Don't ask: use what was given and the defaults for the rest, and say so in one line. With nobody there, don't show the plan and wait, and don't install the hook: that changes their settings, so it needs a yes. Without it, never end your turn while steps are left.
- **Name the branch for every skill you run.** The unattended text from `next` tells the skill to commit on the goal branch instead of making its own.
- **Questions that come up later don't stop the run.** Take the safe choice, carry on, and record it with `goal.py decide`. If only the user can answer, `step blocked` and move to another step.
- **Nothing outward-facing happens unattended** beyond the draft pull request, if it was chosen. Record everything else with `goal.py wait -w <run> "<what>" --command "<the exact command>"`.
- **Finish with `goal.py report`,** so `summary.json` is there for whoever reads the run in the morning, and end with the line it prints: `killer-goal: <done | partial | blocked> · <headline> · <path to report.md>`.

## When things go wrong

- **You don't know where you are** (a summary, a restart, a long skill run): `goal.py status`. It lists the checks, every step and its outcome, the step in progress with its setup answers and the run folder its skill probably made, and the next action. If that run folder has a `summary.json`, the skill finished: record it. If it doesn't, carry on with the skill from its own ledger (its `status` or `list` command) or record the step as failed and retry it. killer-council writes its summary.json last, with `council.py summary` after `report.md`; until then its `ledger.md` shows how far it got. killer-bugs writes its summary.json with `bugs.py report`, once before its fixes and again after, so check its `bugs.py list` before calling it finished. For direct work, `git log --oneline <base>..` and `git status` show what's committed and what isn't; `status` prints the base.
- **After a restart or `/clear`, the hook leaves the session alone:** the run is claimed by the old session. `goal.py resume -w <run>` prints a new lead marker, and the next stop claims the run for this session.
- **The hook let the turn end and `status` says STALLED:** it blocked three times while nothing in the ledger or on the branch changed, or twelve times without a step starting or finishing. Make real progress (record the step's outcome), or `pause --reason` if nothing can move. Recording progress wakes it.
- **The guard fails.** Read each problem; it says how to put it right.
  - *Your branch moved:* copy anything worth keeping onto the goal branch, then put it back with the `git branch -f` line the guard prints. Tell the user in the report.
  - *Remote-tracking refs changed or FETCH_HEAD changed:* something pushed or fetched. A push can't be undone from here. Find which step did it, record it with `decide`, and once it's understood `guard --accept "<what happened>"`. The report lists it.
  - *HEAD off the goal branch, a new branch, a tag, the stash changed, AI attribution in a commit:* undo it as the guard says, then run the guard again.
- **A skill isn't installed:** `next` says so. `step skipped --reason`, or do the work directly as a new direct step.
- **The checks can't run in a sandbox** (they need an ignored folder, a service or the network): amend the check to a command that runs offline from the branch, with a reason. Next time, pass `--include` or `--network` to `init`.
- **An acceptance test was wrong:** fix it on the branch, then `check amend <id> --reason "<what was wrong>"`. That re-locks its file; the report shows the amendment.
- **Time runs out mid-step:** finish the bookkeeping (`step failed` or `blocked` with "out of time"), then wrap up. The last stretch before the deadline (10% of the time, 10 to 45 minutes) is kept for the final check, the judge and the report.
- **The user comes back and wants to change course:** `pause` first, talk, then `plan edit`/`plan drop` with reasons, and `resume`. A stopped run can be resumed too; `--hours N` adds time after the deadline.
