---
name: killer-release
description: "Get a release right the first time and prove it installs. You lead: ask a few setup questions (what kind of release, how far to go, where to test-install, release notes), then bump every place the version appears in one commit on a local branch, check the changelog against what really changed with a blind reviewer, build the artifacts in a clean copy, and install them from scratch in an empty sandbox with the network off, where the installed tool must print the new version and run. A script-run gate checks versions, changelog, tag, tests, build, every install and AI attribution. Tagging, pushing and publishing are written down as exact commands and run one at a time, only after the user's OK. Use whenever the user wants to cut, prepare, ship, tag or publish a release, bump a version, write release notes, or asks whether a version is ready to ship, even if they never say 'killer-release'. For checking a deploy to production (migrations, feature flags, rollback), engineering:deploy-checklist fits better."
---

# Killer Release

You are the **lead**. You get a release ready so it's right the first time: the version bumped everywhere it appears, a changelog and notes that match what changed, the artifacts built, and those artifacts installed from scratch where nothing of the project can help them. Then you stop. A release counts as ready when the script's gate passes, and the core of that gate is a clean install that printed the new version. Your word, or an agent's, that it works isn't part of it.

`scripts/release.py` in this skill's folder does the work and keeps the books: the release shape, the version sites, the changes, the bump, the build, the clean installs, the gate and the publish plan. Run it with `py` on Windows (or `python` if there's no `py`) and `python3` elsewhere. If none of those works on Windows, use `~/.cache/killer-skills-python/python.exe`; the Windows installer puts Python there when it's missing. `release.py <command> --help` lists the options. Exit 0 means fine, 1 means a check failed, 2 means bad input or setup.

## The rules that make it work

- **It installs, or it isn't ready.** `smoke` installs the built artifacts in an empty sandbox (no project source, its own home, the network off, the user's Python and npm settings hidden) the way a user would, and the installed tool must print the new version and run a real command. A command that resolves to a copy outside the sandbox fails, because it tests the wrong thing.
- **Every version site moves together.** `bump` finds them all (manifests, `__version__`, README pins, lock files, the changelog heading) and changes them in one commit; `versions` and the gate fail on any that disagree.
- **The changelog says what changed, no more.** `changelog-check` maps entries to commits and back; a blind reviewer judges each claim against the diff. An unsupported entry gets fixed or kept as a decision the user sees.
- **Build from commits, in a clean copy.** `build` runs from the release branch in a fresh sandbox. The bytes the clean installs passed are the bytes the publish plan uploads; the gate checks the hashes, and a branch that moved after the build means build and install again.
- **Nothing outward without a yes, one step at a time.** `publish-plan` writes the commands and runs nothing. Each tag, push and upload runs through `publish --step N --yes` only after the user said yes to that step in this session. `guard` catches a tag or push the run didn't make.
- **No AI attribution, anywhere.** `release.py commit` refuses a message that credits an AI, and the gate fails on one in the release commits, the notes or the changelog. The history must read as the user's own work.
- **Agents are blind and work in sandboxes.** Start them fresh (`general-purpose`), never with `fork`. They get the claim and the facts, never the script's suspicions or your reasoning.
- **Report what wasn't checked:** platforms nobody installed on, an install that needed the network, a remote tag check skipped offline.

## 1. Setup: ask, don't make the user remember options

Look first: do section 2 up to and including `versions` (none of it changes the project), so the defaults fit. Skip anything the user already said ("ship a patch, just prepare it"). Ask the rest with AskUserQuestion, in at most two calls.

**Call 1**

| Header | Question | Options (the first is the default) |
|---|---|---|
| Version | What kind of release? | The kind `changes` suggests first, marked (Recommended): **Major** when options, commands or files went away or a commit says BREAKING (on 0.x that's a minor) · **Minor** for new options, commands or features · **Patch** when it's only fixes. Then the others, and **A specific version**: type it under Other |
| Landing | How far should it go? | **Prepare everything, I'll publish (Recommended)**: a local branch, its commits, the artifacts built and test-installed, the publish commands written down · **Publish after I OK each step**: at the end I ask before each tag, push and upload · **Check only**: no changes, a report on whether it's ready |
| Smoke | Where should the built release be test-installed? | **This machine, in a clean sandbox (Recommended)** · **Also through the project's CI on a branch**: only if a workflow exists; pushing the branch needs your OK |
| Notes | Release notes? | **Draft them from the changelog (Recommended)** · **The changelog section as it is** · **None** |

**Call 2**, only for what applies:

| Header | Question | Options |
|---|---|---|
| Newcomer | Should a fresh agent follow the install instructions as a first-time user? Ask when the README or notes have install steps. | **Yes (Recommended)**: its stuck points become findings · **No** |
| Network | The package needs dependencies that aren't on this machine. How should the clean install get them? Ask when the package has dependencies. | **From a folder I'll name (Recommended)**: still offline · **From the network**: the report says the install went online |

If AskUserQuestion isn't available, or the user said "just run it", use the defaults and say so in one line.

**Defaults:** the kind `changes` suggests · prepare everything · this machine · draft notes · a first-time installer when there are install steps · the network off.

**Check only** means: `bump` without `--apply`, the changelog checked and reviewed but not fixed, the current commit built and installed, the gate, and the report. Nothing in the project changes. Pass `--landing check` to `init` and the script refuses to change anything.

## 2. Map the release

1. **Init:**
   ```
   py <skill-dir>/scripts/release.py init --repo <project> [--package <folder>] [--test "<test command>"] [--landing prepare|publish|check] [--notes draft|as-is|none] [--branch <name>]
   ```
   It prints the run folder (`~/.claude/killer-release/<project>/<date>-<slug>/`), the version and where it comes from, every version site, the examples it leaves alone, the last release and the commits since, the entry points, likely build commands, whether CI publishes on a tag (it reads `.github/workflows`), the smoke recipes that fit, and tag signing. `references/checks.md` says what each kind of site is. A monorepo with no top-level manifest needs `--package`. `init` reads files and git; it runs nothing of the project's.
2. **The side-effect check.** Before anything runs the project's code or its installer (`changes` runs its `--help`), search them for writes environment variables don't redirect: the Windows registry and the user's PATH; services, scheduled tasks and startup entries; global installs and absolute paths into the real home; anything that reaches the network. For each one, find its own setting (an install folder, a dry-run flag) and use it, pointed into the sandbox with `{home}`. Put the commands not to run in every sandbox block. If even `--help` isn't safe to run, use `changes --no-help`.
3. **Changes:** `release.py changes -w <run>`. Commits since the last release, files by area, and the user-visible surface: `--help` options before and after (run in a sandbox; name the command with `--help-cmd "<command>"` when it finds none), dependencies, commands, files and documented settings added or removed. It suggests a kind of release. With no tag, it's the first release and covers all history.
4. **Versions:** `release.py versions -w <run>`. A site that already disagrees is a finding to fix in the bump.
5. **Learn the build and the tests.** Read the README, CONTRIBUTING and the CI config. If a release workflow exists, it's the truth about what ships: build the same way it does.
6. **Guard:** `release.py guard -w <run>` before and after each phase. It fails on a tag, a push or a commit to the user's branch that this run didn't make. If one shows up, find what did it, undo it, and tell the user.
7. **Announce** in one line, then carry on:
   > killer-release: greet-tool 0.1.0 -> 0.2.0 (minor) · 4 version sites · 5 commits since v0.1.0 · build `python -m build`, clean installs with pip and the archive · notes drafted · prepare only, nothing published.

## 3. Bump

```
py <skill-dir>/scripts/release.py bump -w <run> <patch|minor|major|X.Y.Z> [--pre rc]
```

The preview shows every edit as a diff and changes nothing. Read it: every site there, the examples left alone, the changelog section dated. Then the same command with `--apply` switches to a new local branch, `killer-release/v<version>`, and commits the edits as "Release <version>: bump the version". It refuses uncommitted changes to tracked files: ask the user where they belong, don't commit them yourself. Lock files that record the project's own version are rewritten by their own tool (`npm version`) in a sandbox, or listed for the user. When the version comes from tags, there's nothing to edit; the tag is the bump.

## 4. Changelog

1. On the release branch, write or fix the version's section in the changelog's own style. `bump` dated it or turned `Unreleased` into it, but never writes entries. Each entry is something a user can notice. Say breaking changes first and call them breaking.
2. `release.py changelog-check -w <run>` maps each entry to the commits and surface changes behind it, and each change to its entries. Look at every NOTHING BEHIND IT and NO ENTRY. The matching is by words, so it's a lead, not a verdict.
3. **Release reviewer:** `release.py sandbox -w <run> review`, then the brief from `references/briefs.md`. One fresh agent (`general-purpose`, `run_in_background: false`, description "Review the changelog"). Then `release.py collect -w <run> --sandbox review --as reviewer`. Its unsupported, overstated and unclear entries and its missing changes become findings `F1`...; the gate waits on every unsupported, overstated and missing one.
4. For each finding, fix the changelog and run `release.py update -w <run> F1 --status fixed`, or keep it with `--status kept --note "<why>"`, which records a decision for the user.
5. Commit through the script, so no AI credit gets in: `release.py commit -w <run> -m "Release <version>: changelog: <what and why>" CHANGELOG.md`. Rerun `changelog-check` after big changes.

## 5. Build

```
py <skill-dir>/scripts/release.py build -w <run> [--artifacts "dist/*" ...] [--twice] [--tag-in-sandbox] [--net] -- <build command>
```

It builds the release branch's commit in a fresh sandbox and copies what it made into `<run>/artifacts/` with a `SHA256SUMS.txt` beside them. It finds wheels, sdists, `npm pack` tarballs, zips, `target/release` binaries and the like by itself; name anything else with `--artifacts`. Read the warnings: an artifact whose name lacks the new version means the build reads the version from somewhere `bump` didn't touch. `--twice` builds again in another sandbox and says whether the bytes match. A build that needs the network (Python's build isolation fetches its backend) fails offline: install the backend, or pass `--net` and say so.

## 6. The clean install: the core check

For each way users install the release, follow `references/recipes.md`:

```
py <skill-dir>/scripts/release.py smoke -w <run> --install <pip|npm|archive|script|binary> [--then "<a real command>"] [--expect "<text>"] -- <tool> --version
```

It prints PASS or FAIL with what it ran and what came out. If the user chose the network for dependencies, add `--net`; if they named a folder, `--wheelhouse <folder>`. On FAIL, fix the cause on the release branch, commit with `release.py commit`, build again and install again. Never weaken the check to pass it: `--net`, `--wheelhouse` and `--no-version-check "<why>"` are for releases that really need them, and each one shows in the report. Several recipes per release are normal (a wheel and an installer, Windows and POSIX installers).

**First-time installer** (if chosen): `release.py sandbox -w <run> newcomer --empty` makes a sandbox holding only the artifacts. Brief it from `references/briefs.md` and send one fresh agent (description "Install as a newcomer"), then `release.py collect -w <run> --sandbox newcomer --as installer`. Its stuck points and guesses become findings; fix the docs or keep them as decisions.

**Through CI** (if chosen): the branch push is outward. Record it with `release.py wait -w <run> "Push the release branch to run CI" --command "git push -u origin <branch>"`, and run it only after the user's OK.

## 7. Notes

`release.py notes -w <run>` drafts `<run>/notes.md` from the changelog section, with the install lines from the README moved to the new version and a compare link (`--as-is` for the section alone). Edit it in the project's voice: what a user gets first, plain words, nothing the changelog doesn't back up. Skip this step for "None".

## 8. The gate

```
py <skill-dir>/scripts/release.py check -w <run> [--test "<test command>"] [--net]
```

It prints PASS, FAIL or SKIP for each item in `references/checks.md` (versions, changelog, tag, tree, tests, build, smoke, attribution, outward, review) and exits 1 if any fails. Fix and run it again. Pass the test command once; the run remembers it. `--no-tests "<why>"` is for a project with no tests, and the report says so.

## 9. Publish

`release.py publish-plan -w <run>` writes `<run>/publish.md`: the exact commands, in order, to land the branch, tag the checked commit (signed if the project signs its tags), push the tag, upload to the registry and create the GitHub release with the checked artifacts and the notes. When CI publishes on a tag push, the plan is "push the tag; CI does the rest" and names the workflow. Nothing runs, and each step is recorded as waiting.

- **Prepare everything, I'll publish:** stop here. Show the plan.
- **Publish after I OK each step:** for each step in order, ask with AskUserQuestion, showing the step's exact commands: **Run step N (Recommended)** · **Stop here**: the rest stays in publish.md. On a yes, `release.py publish -w <run> --step N --yes`. It refuses unless the gate passed for this exact commit and the earlier steps are done, and checks the remote doesn't have the tag before pushing it. Never pass `--yes` for a step the user didn't say yes to in this session.

## 10. Report

Run `release.py report -w <run>`. It writes `<run>/report.md` and `<run>/summary.json`: the result, the gate, the clean installs, the artifacts and their hashes, the changes, the review findings, decisions to check, what's waiting for the user's OK, and what wasn't checked. Then give the user, in chat: the headline, what the clean installs printed, the decisions to check, the publish steps waiting, and a link to `report.md`. Then `release.py clean -w <run>` deletes the sandboxes.

## Running unattended

killer-goal runs this skill overnight, and a user may start it and walk away. Then:

- **The setup answers come with the request.** Don't ask: use what was given and the defaults for the rest, and say so in one line.
- **When killer-goal names a branch, commit there** instead of making a new `killer-release/v<version>` branch. Pass it to `init --branch <name>` (or `bump --apply --branch <name>`); when that branch is the current one, the bump commits on it.
- **Questions that come up later don't stop the run.** Take the safe choice, carry on, and record it with `release.py decide -w <run> "<text>"`. They're listed under "Decisions to check" in the report. With dependencies missing for the clean install and no folder of them named, install them from the network (`smoke --net`) and record that with `decide`; the report says the install went online.
- **Nothing outward-facing happens unattended:** no push, pull request, publish, tag, upload or settings change, and never `publish`. Prepare it, record it with `release.py wait -w <run> "<text>" --command "<cmd>"` (`publish-plan` does this for every step), and it's listed under "Waiting for your OK".
- **Finish with `release.py report`,** so `summary.json` is there for whoever reads the run in the morning, and end with one line: `killer-release: <done | partial | blocked> · <headline> · <path to report.md>`.

## When things go wrong

- **The clean install printed the old version:** a site `bump` missed, a version read from a generated file, or a stale artifact. Find where the build gets its version, fix it on the release branch, commit, build and install again.
- **The fix is more than the version or the changelog:** a small packaging fix that the release can't ship without (a file left out of the package, an entry point, a version read from the wrong place) goes on the release branch, one commit each through `release.py commit`, and gets its own changelog entry if users would notice. Anything bigger is the blocker: stop and report it.
- **A dependency isn't available offline:** use `--wheelhouse` with a folder made from the release's own requirements, or `--net` with a decision saying why.
- **The command ran from outside the sandbox:** the release didn't install a command where users would find it (a missing entry point, a bin not linked). That's a real bug in the release; fix it.
- **The gate says the build is stale:** the branch moved after the build (a changelog fix, say). Build and install again; that's the point.
- **`bump --apply` refuses uncommitted changes:** ask the user whether they belong in this release. Never commit them to their branch or stash them for them.
- **The tag already exists**, locally or on the remote: someone released this version. Ask; usually the next version is the answer.
- **The project signs commits or tags:** commits use the user's git config as it is, and the plan uses `git tag -s`. Never bypass signing; if it can't sign unattended, stop and record it.
- **The real project changed and you didn't do it:** `guard` says what. Undo it, tell the user, and say which step did it.
- **An agent wrote no usable `reply.json`:** send the same brief to a fresh agent once. If that fails too, note the gap in the report.
