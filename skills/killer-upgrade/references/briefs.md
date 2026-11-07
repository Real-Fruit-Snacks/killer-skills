# Briefs

killer-upgrade sends two kinds of agent: a **migrator** for an upgrade that breaks the tests, and, optionally, a **changelog reader** for a major that passed. Build each brief from the template below. Fill in the parts in angle brackets and keep the rest word for word.

What stays out of every brief:

- the run folder, `upgrade.py`, and anything in them (plan.json, trial logs, earlier attempts)
- other agents: whether an earlier migrator tried this, and what it did
- how many attempts are left, and what happens if this one fails
- your own guess at the fix, and how confident you are

A second migrator gets the same brief as the first, with the new `failures.txt`. It never sees the first one's diff or reasoning. A fix handed down tends to be repeated, not rethought.

Every agent writes its full answer to `reply.json` in its scratch folder, and `upgrade.py` reads it from there. Its final message to you is a line or two.

## The sandbox block

Every brief includes this block, filled in with what `upgrade.py sandbox` printed for that agent:

```markdown
## Your copy of the project

- **Project copy:** `<path>`. Read anything in it. <package> <to> is already applied and installed in it<, on top of the other upgrades accepted so far>.
- **Run the project's code only through:** `<run command>`. It runs from the project copy with its own home and temp folders, no credentials, and <"the network on" | "the network off (this machine's own addresses still work)">. Add `--repeat N` to count failures, `--cwd <subfolder>` to run from a subfolder, `--timeout <seconds>` to cap a run. Each run's output is saved under `logs/` next to the scratch folder.
- **Scratch folder:** `<path>`. Put every file you create here, apart from your changes to the project itself.
- Don't install anything beyond the project's own install command, and don't touch anything outside these two folders. <With the network on: "You may read the package's release notes and migration guide online. Don't download or run anything else from the internet." With it off: "Don't fetch anything: Windows PowerShell 5.1 and programs that open sockets themselves get past the block.">
- <Commands not to run, and why, from the side-effect check: "Don't run `npm run deploy`: it publishes the site." Write "None." if there are none.>
```

## Migrator

For a step whose try failed: a major, or the culprit a bisect found. Make its sandbox with `upgrade.py sandbox -w <run> migrate-<step> --step <step>`; it writes `failures.txt` into the scratch folder.

````markdown
# Make the project work with <package> <to>

This project upgraded **<package>** from <from> to <to> (<ecosystem>, in <manifest>). With the new version, some of its tests fail. Your job is to change the project's code so that it works with <to>, and the tests pass again.

## What broke

`failures.txt` in your scratch folder lists the tests that passed before the upgrade and fail with it, the tests that already failed before (not your job), the tests that fail now and then anyway (ignore them), the project's commands, and the output of the failing run. In short: <the step's last result, like "test_app.TestApp.test_greet fails with AttributeError: module 'omega' has no attribute 'greet'">.

## Where to read about the change

<The links `sandbox` printed, and any you know: the package's changelog, release notes, migration guide.> <With the network on: "You may read them online." With it off: "The network is off. Read what came with the installed package instead: its CHANGELOG, README and type definitions.">

## The project

<What it is, and the commands that install, build and test it, written as run commands. Facts only.>

<sandbox block>

## The rules

- **Change the project's code, not its dependencies.** Don't pin <package> back, don't add, remove or change any dependency, and don't touch the lockfile. A script checks this and refuses the change if you do.
- **Don't skip, delete or weaken tests.** If a test checks behaviour that the new version changed on purpose, update what it expects, and say why under `tests_changed`. Every test file you change is shown to the project's owner.
- **Keep the change as small as the new version needs.** No refactoring, no style changes, nothing the upgrade doesn't require.
- **Write like the project's own author.** No notes about yourself, this task or the upgrade process in code, comments or docs.
- Run the tests through the run command until they pass. They will be run again in a fresh copy, without you, and only that counts.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "summary": "<what changed in <package> and what you changed for it, in two or three sentences>",
  "files": ["<each project file you changed>"],
  "tests_changed": [{"file": "<path>", "why": "<the behaviour the new version changed on purpose>"}],
  "tests_run": "<the command you ran last, and the line that says how many passed>",
  "behaviour_changes": ["<anything in the new version a user of this project would notice that the tests don't cover>"],
  "gave_up": "<only if you couldn't make it work: what's in the way>"
}
```

Then end with one line: done, or gave up and why.
````

## Changelog reader

Optional, for a major that passed its try. Tests don't cover everything a new version changes. Make its sandbox with `upgrade.py sandbox -w <run> read-<step> --step <step>` (the step is accepted, so the sandbox has it installed).

````markdown
# Read what changed in <package> between <from> and <to>

This project upgraded **<package>** from <from> to <to>, and its tests still pass. But tests don't cover everything. Your job is to read the package's release notes for every version in between and find the changes that could still affect this project: changed defaults, removed or renamed options, different errors, security behaviour, output formats, new deprecation warnings.

## Where to read

<The links `sandbox` printed, and any you know.> <With the network off: "The network is off. Read what came with the installed package: its CHANGELOG, README and type definitions.">

## The project

<What it is, who uses it, and how to run it with the run command. Facts only.>

<sandbox block>

Your copy has the new version installed. Change it as much as you like to try things; it gets thrown away.

## How to work

1. Read the release notes from <from> to <to>: every version in between, not just the last.
2. For each change that might matter, search the project for where it uses that feature. If it doesn't, write "not used here" and move on.
3. Where it does, try to show it: a small script in the scratch folder that uses the project the way the project uses it, prints a line starting `EXPECTED:` (what the project relies on) and one starting `OBSERVED:` (what happens with the new version), and exits 1 when the change shows and 0 when it doesn't. Run it from the project copy through the run command, reaching the scratch folder by a relative path like `../scratch/check_dates.py`. It will be rerun in a fresh copy without you.
4. A change you couldn't show is still worth listing, with an honest risk.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "read": ["<each release note or changelog you read>"],
  "changes": [
    {"change": "<what changed, as behaviour someone could notice>", "source": "<the version, and where it says so>",
     "affects": "<path:line where the project relies on it, or 'not used here'>", "risk": "<high | medium | low>",
     "command": ["<program>", "<argument>"], "expected": "<what the project relies on>", "observed": "<what you saw>"}
  ]
}
```

`command` is your script, as a list of arguments for the run command, or `null` if you couldn't show the change. Then end with one line: how many changes, and how many you showed.
````
