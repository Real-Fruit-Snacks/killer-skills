---
name: killer-docs
description: "Make a project's docs true and followable, and prove it. You lead: ask a few setup questions (which docs, which journeys a newcomer tries, what happens after, the network), then a script runs every shell command and code sample in the docs, in order, in a fresh sandbox and compares shown output with real output; checks each command's --help against the docs; and checks links and anchors. Blind newcomer agents who have never seen the code follow only the docs to install it, run the quickstart and each guide, and record every place they get stuck or guess. Fixes go into the docs on a local branch; then the blocks run again and a different fresh newcomer walks the same path. Use whenever the user wants a README, tutorial, getting-started guide, CONTRIBUTING, docs folder or --help text checked, tested or fixed, or asks whether someone new could follow it, even if they never say 'killer-docs'. For the README as a landing page, killer-repo fits better; for writing docs from scratch, engineering:documentation."
---

# Killer Docs

You are the **lead**. A script runs what the docs say to run, and fresh newcomer agents try to follow them. You plan the run, decide which failures are the docs' fault, fix the docs if the user wants, and keep the books honest. You never declare a doc correct yourself. A block counts when the script ran it in a fresh copy of the project and it passed. A journey counts when a newcomer who had never seen the code got through it, and a fix counts when a rerun passes or a *different* fresh newcomer passes that step.

`scripts/docs.py` in this skill's folder keeps the books: run folders, sandboxes, the block runs, help and link checks, findings, walks, commits and the report. Run it with `py` on Windows (or `python` if there's no `py`) and `python3` elsewhere. If none of those works on Windows, use `~/.cache/killer-skills-python/python.exe`; the Windows installer puts Python there when it's missing. `docs.py <command> --help` lists the options.

## The rules that make it work

- **Every runnable block runs, or says why not.** `run-blocks` runs each doc's blocks in document order in its own fresh sandbox, as one reader at one terminal: the folder a block `cd`s into and the variables it sets carry on to the next block. A block that didn't run (a placeholder, another system, the network, a tool that isn't installed) is listed with its reason. It never counts as passing.
- **Shown output is checked, with the noise taken out.** Dates, times, durations, version numbers, long hex ids, line endings, spacing and path separators are normalised; a line of `...` in the docs stands for lines left out. Different words or a missing line still differ.
- **Newcomers read only the docs.** A newcomer gets a clean sandbox and the list of doc files, and is told not to open the code. Anything it learns from the code hides a gap. Nothing enforces this, so the report says so, and `collect` flags files it says it read outside its list and project files it changed.
- **A different newcomer after the fixes.** The second walk is always a fresh agent with the same brief, never the first one resumed. Its brief doesn't say it's a second walk.
- **Fix the docs, not the code.** When the docs are right and the program is wrong, that's a finding for killer-bugs (`update --kind code`); leave the code. `commit` refuses files that aren't docs.
- **Every agent is blind** to the run folder, to other agents, and to your reasoning and hopes. Start agents fresh (`general-purpose`), never with `fork`, which would inherit this conversation.
- **Sandboxes are copies, not containers.** They have their own home, temp and app-data folders, no credentials and a dead proxy for the network, and `pip install`, `npm -g`, `cargo install` and `go install` land inside them. Nothing redirects the Windows registry, the PATH, services or scheduled tasks, and Windows PowerShell 5.1 ignores the proxy. That's why the side-effect check comes before anything runs.
- **Report what was checked,** not just what was found. "This doc works" and "nothing in this doc could run here" must never look the same.

## 1. Setup: ask, don't make the user remember options

Skip anything the user already said ("check the README, just report"). Before asking, look at the project for a minute: where its docs are, its languages, whether it has a CLI, guides or a CONTRIBUTING file, install steps that fetch from a registry, servers, and whether the current branch is ahead of `main`. Use that to put the right defaults first. Ask the rest with AskUserQuestion, in at most two calls.

**Call 1**

| Header | Question | Options (the first is the default) |
|---|---|---|
| Scope | Which docs should I check? For one file or folder, type it under Other. | **All the docs (Recommended)** · **README and getting started** · **This branch's changes** (recommend it instead when the branch is ahead) · **CLI help text only** |
| Walks (multiSelect) | Which journeys should a blind newcomer try? | **Install and first run (Recommended)** · **Each guide or how-to** · **Contributing: build and run the tests** · **Upgrading from the last version** |
| After | Once it's checked, then what? | **Fix the docs on a new local branch (Recommended)**: one commit per doc or group of findings, nothing pushed · **Report, then ask** · **Report only** |
| Network | Can the sandboxes use the network? | **No (Recommended)**: install steps that fetch are skipped and listed · **Yes**: needed to check installs from a registry; they really install, into the sandbox |

**Call 2**

| Header | Question | Options |
|---|---|---|
| Depth | How deep? | **Standard (Recommended)**: the script's checks and the newcomer walks · **Thorough**: also a fact checker that reads the docs against the code, for defaults, limits, paths and settings no example exercises |

If AskUserQuestion isn't available, or the user said "just run it", use the defaults and say so in one line.

**Defaults:** all the docs (or this branch's changes, when it's ahead of main) · the install-and-first-run walk · fix on a new local branch · network off · standard depth.

**With killer-repo:** killer-repo decides what the README says and how it looks; killer-docs checks that what it says is true and can be followed. When both run, run killer-docs second, on killer-repo's branch (`init --scope branch`, `branch --branch <that branch>`).

## 2. Map the docs

1. **Init:**
   ```
   py <skill-dir>/scripts/docs.py init --repo <project> --scope <all | start | cli | path:<file or folder> | branch[:<base>] | changes> --target "<one line on what it is>" [--after fix|ask|report] [--depth standard|thorough] [--network] [--timeout 60] [--first README.md] [--cli 'NAME=COMMAND' ...] [--exclude ...] [--include node_modules ...]
   ```
   It prints the run folder (`~/.claude/killer-docs/<project>/<date>-<slug>/`), the docs in scope (Markdown, reStructuredText, text in `docs/`, man pages, `examples/`), the commands the project ships (package.json `bin`, pyproject scripts, setup.cfg, Cargo, `cmd/*/main.go`, `bin/`), the Python and shells the sandboxes will use, and a command the README runs with no way found to run it. Give that one with `--cli 'name=python -m name'`. `--first README.md` runs the README's blocks first in every other doc's sandbox, for guides that assume the install is done.
2. **Extract:** `docs.py extract -w <run>` prints every runnable block: its place, its shell or language, its section, and whether it will run here or why not. Read the whole table. Check the classification looks right (a console block's commands and output, the output block tied to the code before it).
3. **The side-effect check.** Read every block that *will* run, and every command a newcomer will follow, for things the sandbox doesn't redirect: the registry and the PATH, services, scheduled tasks and startup entries, absolute paths into the real home, global installs and anything that reaches the network. The script already refuses the obvious ones (`sudo`, system package managers, `docker`, `setx`, `git push`, writes to absolute paths, editors). Leave out anything else with `docs.py skip -w <run> b7 --reason "<why>"`. In every sandbox block, list the commands agents must not run, with the reason.
4. **Journeys.** For each walk the user chose, at most six:
   ```
   py <skill-dir>/scripts/docs.py journey -w <run> add install --goal "<what a newcomer must reach>" --docs README.md docs/install.md [--start "README.md#install"] [--check "<command that passes once the goal is reached>"]
   ```
   Give a goal check whenever there is one (`greet --version`, `npm test`, a file the quickstart writes). It runs in the newcomer's own sandbox when it finishes, so "reached" doesn't rest on the agent's word. For "Each guide", one journey per guide. For "Contributing", the goal is building and running the tests. For "Upgrading", save the last release's docs into the newcomer's scratch folder (`git show <tag>:README.md`) and list them among what it may read; its goal is moving a setup that follows those docs to the current version with the upgrade notes.
5. **Save the project's state:** `docs.py guard -w <run> --save`. Run `docs.py guard -w <run>` after every phase; it exits 1 if the real project's git status changed. If you didn't change it, an agent did: undo it and say so in the report.
6. **Announce** in one line, then carry on:
   > killer-docs: all the docs (README.md and 4 guides, 63 blocks, 41 will run here) · 2 newcomer walks, then a fresh pair after fixes · fix on a new local branch · network off.

## 3. Run the blocks, the help and the links

```
py <skill-dir>/scripts/docs.py run-blocks -w <run>
py <skill-dir>/scripts/docs.py help-drift -w <run>
py <skill-dir>/scripts/docs.py links -w <run>
```

`run-blocks` runs three docs at a time, each in its own sandbox; docs that start a server run one after another, so they can't fight over a port. Each block's result is one of: passed, differs (with a short diff), failed (with its exit code), timed out (60 s by default: probably interactive or a server), and the not-checked kinds: skipped with its reason, no-tool (needs something not installed here), network (failed the way an offline install fails) and blocked (a command the project installs, after the step that installs it didn't run). Failing, differing and timed-out blocks become findings, proven by the run itself, with their reproduction: the doc's blocks up to that one, which `docs.py repro -w <run> D3` reruns in a fresh copy. One that fails after an earlier block didn't run is only a candidate (see Triage).

`help-drift` runs each command's `--help` (then `-h`, then `help`), and its subcommands' help, and compares them with the docs: an option or subcommand the docs use that the help doesn't have is high severity; one the help has that no doc mentions is low. `links` checks relative links, anchors (with GitHub's heading slugs) and image paths, letter case and files git ignores; with the network on it checks web links too, and only a 404 or 410 counts as broken.

**Triage.** `docs.py list -w <run>` shows the findings. Read each failure's output (`docs.py show -w <run> D3`, or the block's log in `<run>/logs/`). Decide whose fault it is:

- **The docs are wrong:** keep it; correct its severity with `update --severity` if the guess is off.
- **It failed after a block that didn't run here** (the finding is a candidate, and says which): read the output. If the missing step can't be the cause (an option the program rejects, say), `update D3 --status proven --note "<why>"`. Otherwise leave it; the report lists it on its own.
- **The environment, not the docs** (a service the tests need, a tool the docs do list as a prerequisite): `docs.py update -w <run> D3 --status dismissed --note "<why>"`. Dismissals are in the report with the reason.
- **The code is wrong and the docs are right:** `update D3 --kind code`. It goes in the report's section for killer-bugs, and nothing changes.

**Annotations.** These HTML comments go on the line before a block, are invisible on GitHub, and `extract` honours them. Add one as part of a fix when a block legitimately can't be checked as it stands:

| Annotation | Means |
|---|---|
| `<!-- docs: skip -->` | Don't run it. It's still listed as unchecked |
| `<!-- docs: output-may-differ -->` | Run it, but don't compare its output (random ids, sizes, live data) |
| `<!-- docs: setup -->` | Run it, but don't report it; later blocks need it |
| `<!-- docs: needs-network -->` | Skip it unless the run allows the network |
| `<!-- docs: background -->` | Start it in the background (a server), wait 3 s (`background=10` for longer), stop it after the doc's last block |
| `<!-- docs: timeout=300 -->` | Let it run this many seconds |

## 4. The newcomer walks

For each journey, make a sandbox (`docs.py sandbox -w <run> walk-install-1`) and write the brief from the newcomer template in `references/briefs.md`, with the journey's goal, doc list, starting point and the sandbox block. Send every journey's newcomer in one message: one Agent call each, `subagent_type: "general-purpose"`, `run_in_background: false`, a description like "Follow the docs: install". Each writes `reply.json` in its scratch folder and answers in a line. Then:

```
py <skill-dir>/scripts/docs.py collect -w <run> --sandbox walk-install-1 --journey install
```

`collect` records the walk (reached, reached-with-help or stuck, where it got stuck, what it guessed), keeps its steps, runs the journey's goal check in the newcomer's sandbox, and adds its findings. It **reruns each reproduction in a fresh copy**: one that prints `OBSERVED:` and exits 1 makes the finding proven; one that doesn't stays reported, with a note. A finding with no reproduction (most "unclear" and "missing" ones) stays reported; the second walk is what settles it. It flags project files the newcomer changed and files it says it read outside its list. Then it deletes the sandbox. If an agent wrote no `reply.json`, save its reply to a file and pass the file instead of `--sandbox`. `walk-result` records or corrects an outcome by hand, labelled as such.

**Thorough depth:** also send one fact checker, with the template in `references/briefs.md`, in its own sandbox. It may read the code. `collect -w <run> --sandbox facts --as fact-checker` adds its wrong claims as findings and reruns their reproductions.

## 5. Report, then fix or ask

Run `docs.py report -w <run>`. It writes `<run>/report.md` and `<run>/summary.json`. Give the user, in chat: each journey's outcome, the blocks run and how many failed or differed, the top three findings (a sentence each, with the doc and line), what wasn't checked, and a link to `report.md`. Quote the numbers from the report; don't count yourself.

With "Report, then ask", ask which findings to fix (AskUserQuestion, multiSelect, the top findings plus "All of them"). With "Report only", stop here and go to section 7.

## 6. Fix

1. **Branch:** `docs.py branch -w <run>` makes and switches to `killer-docs/<date>`. Nothing is ever committed on the user's branch.
2. **Fix the docs,** worst first. Keep each doc's voice, structure and formatting; change what's wrong, add what's missing, and don't rewrite what works. Try each new or changed command in a sandbox of your own first (`docs.py sandbox -w <run> lead`, then its run command); recheck runs every block again anyway. Never add attribution of any kind: no `Co-Authored-By`, no "Generated with", no mention of AI or agents.
3. **Commit** one doc, or one group of related findings, at a time:
   ```
   py <skill-dir>/scripts/docs.py commit -w <run> -m "<what was wrong and what changed>" --findings D1 D4 -- README.md
   ```
   It refuses code files, an attribution line in the message or the diff, and files that had the user's own uncommitted changes.
4. **Recheck:** `docs.py recheck -w <run>` runs the blocks, the help and the links again, compares them with the first run block by block, and reruns the newcomers' reproductions. Findings whose check passes now are marked fixed.
5. **Second walk:** a *fresh* newcomer for each journey, in a fresh sandbox (`walk-install-2`), on the fixed docs. Its brief is the first one word for word, with only the sandbox block's paths changed. `collect` it the same way; it's recorded as walk 2. A first-walk finding is fixed when this newcomer passed that section of the docs cleanly (no failure, guess or workaround there). One it never reached stays open, with a note.
6. If a journey is still stuck, fix again and send another fresh newcomer, at most twice. Then rerun `report`.

## 7. Close

`docs.py clean -w <run>` deletes any sandboxes left. Check `guard` one last time. Don't push, open a pull request or publish anything unless the user asks.

## Running unattended

killer-goal runs this skill overnight, and a user may start it and walk away. Then:

- **The setup answers come with the request.** Don't ask: use what was given and the defaults for the rest, and say so in one line.
- **When killer-goal names a branch, commit there** instead of making a new `killer-docs/<date>` branch: `docs.py branch -w <run> --branch <name>`. When that branch is the current one, commits go on it.
- **Questions that come up later don't stop the run.** Take the safe choice, carry on, and record it with `docs.py decide -w <run> "<the call>"`. They're listed under "Decisions to check" in the report. "Report, then ask" becomes "Report only".
- **Nothing outward-facing happens unattended:** no push, pull request, publish, tag, upload or settings change. Prepare it, record it with `docs.py wait -w <run> "<what>" --command "<the exact command>"`, and it's listed under "Waiting for your OK".
- **Finish with `docs.py report`,** so `summary.json` is there for whoever reads the run in the morning, and end with the line it prints: `killer-docs: <done | partial | blocked> · <headline> · <path to report.md>`.

## When things go wrong

- **Most blocks don't run here** (another system, the network, placeholders): say so plainly; the report lists every one with its reason. For installs from a registry, offer the user a run with `--network`. Warn that it really installs, into the sandbox.
- **A block needs something that isn't installed** (a database, `make`, Docker): it's listed as no-tool, not as a finding. If the docs never say it's needed, that's a "missing" finding of its own; add it with `docs.py add`.
- **A block starts a server, or waits for input:** it times out and is reported. If the docs are right, add `<!-- docs: background -->` (for a server whose next blocks talk to it) or `<!-- docs: skip -->` as part of the fix.
- **Two docs start servers on the same port at once:** `run-blocks --jobs 1`.
- **A newcomer read the code, or changed project files** that the docs didn't tell it to: weigh its findings with that in mind, and send a fresh newcomer if its outcome depended on it.
- **An agent wrote no usable `reply.json`:** `collect` exits 2 and keeps the sandbox. Send the same brief to a fresh agent once. If that fails too, note the gap in the report.
- **The real project changed** (`guard` exits 1): find out which agent did it, undo it, and say so.
- **A huge docs site:** runs stop at 300 blocks (`init --max-blocks`) and say what was left; split it with `run-blocks --doc`.
- **`docs.py` stops with a traceback:** it exits 2, and nothing it was doing counts as checked. Fix the cause and run it again; don't report around it.
