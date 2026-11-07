---
name: killer-upgrade
description: "Bring a project's dependencies up to date without breaking it, each upgrade proven by the project's own build and tests in a fresh sandbox. You lead: ask a few setup questions (scope, majors, runtimes, CI actions, what happens after), record a baseline of what passes, ask each package manager what's outdated, and try the upgrades in batches. When a batch breaks, a script bisects it to the upgrade, or the pair, that broke it. A major that breaks the tests goes to a fresh migrator agent that changes the code, and the tests decide. Each proven step becomes one commit on a new local branch, checked again at the end. Covers npm, pnpm, yarn, pip, uv, poetry, Cargo, Go, Bundler, .NET and any tool you can describe. Use whenever the user wants to upgrade, update or bump dependencies, a lockfile, runtimes or CI actions, or asks what's outdated, even if they never say 'killer-upgrade'. For a vulnerability audit without upgrading, the ecosystem's audit command fits better; for a bug an upgrade exposed, killer-bugs."
---

# Killer Upgrade

You are the **lead**. You bring a project's dependencies up to date without breaking it. A script does the proving: it applies each upgrade in a fresh copy of the project, installs, builds and runs the tests, and compares the result with a baseline it recorded before anything changed. When a major version breaks the tests, a fresh migrator agent changes the code in its own copy, and the script checks that change the same way. You plan the run, keep the books and decide what to hold. You never call an upgrade safe yourself. It counts once `upgrade.py` has shown that everything that passed before still passes with it.

`scripts/upgrade.py` in this skill's folder keeps the books: the run folder, the baseline, the plan, every trial, the bisects, the migrations and the branch. Run it with `py` on Windows (or `python` if there's no `py`) and `python3` elsewhere. If none of those works on Windows, use `~/.cache/killer-skills-python/python.exe`; the Windows installer puts Python there when it's missing. `upgrade.py <command> --help` lists the options, and each command ends by saying what to run next.

## The rules that make it work

- **Proven against the baseline, or not landed.** `baseline` records what installs, builds and passes before any upgrade, over three test runs. A step is accepted only when a fresh sandbox with it applied passes everything the baseline passed. A test that already failed doesn't count against an upgrade, and a flaky one is ignored. A test that now fails, is now skipped or no longer runs does count.
- **Batches first; bisect finds what broke.** Patch and minor upgrades go in together. When a batch fails, `bisect` tries parts of it in fresh sandboxes until it has the one upgrade, or the pair that only breaks together, and accepts the rest.
- **One step at a time.** Each try starts from the steps accepted so far, so the patches compose in the order they were accepted. Run tries one after another, never side by side.
- **A migration changes the code, never the dependencies.** `migrate` refuses a change that touches a lockfile or a dependency list, so a migrator can't pin back or add a package. A changed test file becomes a decision for the user. Two failed attempts hold the upgrade.
- **Every agent is blind** to the run folder, to other agents and earlier attempts, and to your reasoning. Start agents fresh (`general-purpose`), never with `fork`, which would inherit this conversation.
- **Installs run code from the internet.** The sandboxes have the network on, because installs need a registry, and package install scripts (npm's `postinstall`, `setup.py`, `build.rs`) run there. The sandboxes are copies with their own home and temp folders and no credentials, not containers. That's why the side-effect check in section 2 comes before the baseline.
- **Nothing leaves the machine.** Upgrades land on a new local branch, one commit per step, and the branch is checked once more in a fresh sandbox. Nothing is pushed.

## 1. Setup: ask, don't make the user remember options

Skip anything the user already said ("just the patch versions", "upgrade lodash"). Before asking, look at the project for a minute: its manifests and lockfiles, whether it has tests and CI, whether its working tree is clean. Then ask in one AskUserQuestion call:

| Header | Question | Options (the first is the default) |
|---|---|---|
| Scope | What should be upgraded? | **Everything that's out of date (Recommended)** · **Security fixes only** · **Patch and minor only, no majors** · **One package**: type it under Other |
| Majors | What about major versions? | **Try them, and migrate the code if tests break (Recommended)** · **List them only** · **Try them, but hold any that break** |
| Also (multiSelect) | Upgrade these too? | **Runtime versions**: the Node.js, Python, Rust and Go versions the project pins · **CI actions**: needs `gh` · **Neither (Recommended)** |
| After | Once upgrades are proven, what then? | **Commit them on a new local branch (Recommended)**: one commit per step, nothing pushed · **Report, then ask** · **Report only** |

If AskUserQuestion isn't available, or the user said "just run it", use the defaults and say so in one line.

**Defaults:** everything that's out of date · majors tried, and migrated if they break · no runtimes or CI actions · committed on a new local branch · the network on in the sandboxes.

The answers become `init` options: `--scope all | security | minor | package:<name>`, `--majors try | list | hold`, `--also runtimes actions`, `--after commit | ask | report`.

## 2. Find what's there

1. **Init:**
   ```
   py <skill-dir>/scripts/upgrade.py init --repo <project> --target "<one line on what it is>" --scope all --majors try [--also runtimes actions] --after commit [--no-network] [--env NAME=VALUE ...]
   ```
   It prints the run folder (`~/.claude/killer-upgrade/<project>/<date>-upgrade/`); every ecosystem it found, with its manifest, lockfile and package manager, and whether that tool is installed here; the runtimes, CI action pins and base images; files that point at a private registry; and the install, build and test commands it found in package scripts, CI and Makefiles. `references/ecosystems.md` lists what it runs for each ecosystem.
2. **What it can't upgrade.** Describe an ecosystem it lists but doesn't upgrade (Maven, Gradle, pipenv, bun), or one it missed, with `upgrade.py custom`: a command that prints what's out of date as JSON, and one that applies an upgrade. Or add the change by hand with `upgrade.py add`. Packages from a private registry can't be installed in a sandbox, because credentials stay out: say so now, and hold them when they come up. With no lockfile, say the results aren't reproducible.
3. **The side-effect check.** Read the project's install, build and test scripts, and its own install hooks, for writes that environment variables don't redirect: the Windows registry and PATH, services, scheduled tasks, global installs, absolute paths into the real home folder, and anything that deploys or publishes. In every sandbox block, list the commands agents must not run, with the reason. Prefer `npm ci --ignore-scripts` (and pnpm's) unless the project needs its dependencies' install scripts.
4. **Announce** in one line, then carry on:
   > killer-upgrade: npm and pip · everything out of date, majors tried and migrated · network on in the sandboxes: installs need the registry, and install scripts run there · each try about 2 min · lands on a new local branch, nothing pushed.

## 3. Baseline

```
py <skill-dir>/scripts/upgrade.py baseline -w <run> [--venv] --install -- npm ci --ignore-scripts --build -- npm run build --tests -- npm test
```

`--install`, `--build` and `--tests` each take `--` and one command, and each can be repeated. `--venv` gives every sandbox its own virtual environment, for pip projects. `--venv "py -3.12"` picks the Python. Python's embeddable package can't make one, and the script says so. The baseline installs, builds and runs the tests three times (`--runs`) in a fresh sandbox. It prints what passes, what already fails, what's flaky, how it read the test output, and how long one try takes.

- **Blocked** (exit 1): the install failed, or nothing passes. Fix the command, the network or the registry, and run it again. If it can't be fixed, go to section 10 and report.
- A test command that fails at the baseline and prints no per-test results checks nothing. Make it print them (`pytest -rA`, `unittest -v`, `go test -v`, a TAP reporter, or `--junit '<glob>'` for JUnit XML).

## 4. What's out of date, and the plan

```
py <skill-dir>/scripts/upgrade.py outdated -w <run>
py <skill-dir>/scripts/upgrade.py plan -w <run>
```

`outdated` asks each ecosystem's own tool, in a sandbox installed like the baseline, and classifies each upgrade as patch, minor or major (a 0.x minor counts as a major). It runs the audit tools that are installed, and marks the upgrades that fix an advisory. It lists what it couldn't check, and why. `plan` groups the upgrades into steps: the patch and minor upgrades of dependencies in one, dev dependencies in another, each major on its own, then each runtime and CI action. It prints an estimate of the time.

- To keep a package back: `upgrade.py hold -w <run> <package> --reason "<why>"`. To move something no tool reports (a base image, say): `upgrade.py add -w <run> --title "<commit subject>" --edit <file> <old> <new>`, or `... -- <command>`. You can also edit `plan.json` by hand; the script checks it whenever it reads it.
- If the estimate runs past an hour, tell the user the number, and carry on unless they stop you.

## 5. Try each step

```
py <skill-dir>/scripts/upgrade.py try -w <run> S1
```

Each try is a fresh sandbox: the accepted steps applied in order, then this step's upgrades, the install, the build, and the tests, up to three runs. It ends with one of three verdicts:

- **pass**: accepted, and its patch saved (exit 0)
- **fail**: it names the tests that passed at the baseline and don't now, and shows the output
- **install-fail**: the upgrade couldn't be applied or installed. It shows the tool's message: a peer-dependency conflict, a version that doesn't exist, a runtime this machine can't run.

`upgrade.py status -w <run>` shows every step and the next thing to do.

## 6. Bisect a failing batch

```
py <skill-dir>/scripts/upgrade.py bisect -w <run> S1
```

It tries halves of the batch in fresh sandboxes and follows the half that fails. When both halves pass but the whole fails, the cause is two upgrades together, and it narrows each half down to the smallest combination that still fails. It prints every trial, and ends with a line like "beta 2.0.0 → 2.0.1 breaks test_app.TestApp.test_dates; the other 3 pass together." Then it splits the batch: the rest becomes a new step, accepted from the trial that passed, and each culprit becomes a failing step of its own. Finding one culprit in n upgrades takes up to about 2·log2(n) + 1 trials. If no part of the batch fails on its own, the failure was flaky: the step goes back to pending, so try it once more.

## 7. Migrate a failing major

For each failing major, and each culprit from bisect whose failure points at the project's own code, unless the user chose to hold majors that break (then `upgrade.py hold -w <run> S3 --reason "<what broke>"`):

1. **Sandbox:** `upgrade.py sandbox -w <run> migrate-S3 --step S3`. It applies the accepted steps and this upgrade, installs them, and puts `failures.txt` (what broke, the commands, the failing output) in the scratch folder. It prints links to the package's changelog.
2. **Brief:** write it from the migrator template in `references/briefs.md`, with the sandbox block. Send it: `subagent_type: "general-purpose"`, `run_in_background: false`, a description like "Migrate omega 2.0". Several failing majors can go out at once, one agent each, in one message.
3. **Check:** `upgrade.py migrate -w <run> S3 --sandbox migrate-S3`. It takes the agent's diff, refuses it if it touches a lockfile or a dependency list, and tries the step with it in a fresh sandbox. On a pass, the step is accepted with the code change, and any changed test file becomes a decision for the user. On a fail, a fresh migrator gets a new sandbox (`migrate-S3-2`; `migrate` prints the command) with the new `failures.txt` and the same brief. Two failures hold the step, with the reason. Run the checks one after another.

A patch or minor that breaks the tests usually means the project leaned on behaviour that changed. Send a migrator only when the failure is in the project's own code. Otherwise hold it, with the failing test as the reason.

## 8. Read the changelogs of majors that passed (optional)

Passing tests don't cover everything a new major changes. For a major that passed, make a sandbox (`upgrade.py sandbox -w <run> read-S4 --step S4`), send a changelog reader from `references/briefs.md`, then record its reply with `upgrade.py notes -w <run> S4 --sandbox read-S4`. Every change it lists goes in the report. A change with a reproduction is rerun in a fresh copy and marked reproduced or not. The rest is advice, marked unchecked. A reproduced change that matters is a question for the user, not something to fix quietly.

## 9. Land

With "Commit them on a new local branch":

```
py <skill-dir>/scripts/upgrade.py land -w <run>
```

It makes `killer-upgrade/<date>` from the commit the run started at, with one commit per accepted step, in order. Each commit holds the files exactly as that step's try tested them, manifest and lockfile together, with the step's title as its subject and the upgrades listed in the body. It works in a separate worktree, so the user's working tree and branch stay as they were. It refuses to start if a file the upgrades change was edited since the run began. Then it installs, builds and tests the branch in a fresh sandbox, three runs, against the baseline, and reruns the audits. If that final check fails, the branch stays for inspection, and the report says not to merge it.

With "Report, then ask", report first and ask whether to land; if the user says yes, land and run `report` again. With "Report only", don't land.

## 10. Report

`upgrade.py report -w <run>` writes `<run>/report.md` and `summary.json`. The report covers what was upgraded (old → new, by step, with its commit), what was held and why, what failed and what bisect found, the majors and their code changes, behaviour changes from changelog readers, advisories fixed and left, runtimes and actions, the final check against the baseline, and what wasn't checked. Read it, then give the user, in chat:

- the headline
- the held upgrades, with their reasons
- the decisions to check
- a link to `report.md`

Then `upgrade.py clean -w <run>` deletes the sandboxes and the run's package cache.

## Running unattended

killer-goal runs this skill overnight, and a user may start it and walk away. Then:

- **The setup answers come with the request.** Don't ask: use what was given and the defaults for the rest, and say so in one line.
- **When killer-goal names a branch, commit there** instead of making a new `killer-upgrade/<date>` branch: `upgrade.py land -w <run> --branch <name>`. When that branch is the current one, it commits on it.
- **Questions that come up later don't stop the run.** Take the safe choice (hold an upgrade rather than force it, leave a runtime minimum alone), carry on, and record it with `upgrade.py decide -w <run> "<text>"`. They're listed under "Decisions to check" in the report. After "Report, then ask", don't land: record the land command with `wait` instead.
- **Nothing outward-facing happens unattended:** no push, pull request, publish, tag, upload or settings change. Prepare it, record it with `upgrade.py wait -w <run> "<text>" --command "<cmd>"`, and it's listed under "Waiting for your OK".
- **Finish with `upgrade.py report`,** so `summary.json` is there for whoever reads the run in the morning, and end with one line: `killer-upgrade: <done | partial | blocked> · <headline> · <path to report.md>`. `report` prints that line.

## When things go wrong

- **Before and after each phase, run `upgrade.py status -w <run>`.** Its first line says whether the real project changed since init. If it did and you didn't change it, find which agent did, undo it, and tell the user.
- **The baseline is blocked:** usually the network, or a private registry's credentials. Say what failed. Never work around it by copying credentials into a sandbox.
- **Every step fails on the same test:** it probably depends on the network, the clock or the machine. Run the baseline again with `--runs 5` to see whether it's flaky.
- **The project changed under the run** (try says an accepted patch no longer applies, or land says a file changed since the run began): commit or stash the edit if it's the user's, or start a new run from the current commit.
- **A batch is install-fail on a peer-dependency conflict:** bisect finds the package that conflicts and accepts the rest. Hold it, or move it together with its peer in a step you add by hand.
- **A migrator wrote no usable `reply.json`:** `migrate` still checks its diff, and the report says there's no summary of the change.
- **The tests are too slow for many tries:** narrow the scope (security fixes only, one ecosystem), or record the baseline with `--runs 1` and say flaky tests won't be caught.
