# Briefs

Every agent gets a brief built from one of the templates below. Fill in the parts in angle brackets and keep the rest word for word. Fixed wording keeps runs comparable: two reviewers briefed alike are an honest check on each other.

What stays out of every brief:

- the run folder, `release.py`, and anything in them
- other agents: how many there are and what they said
- `changelog-check`'s map and its NOTHING BEHIND IT / NO ENTRY flags. The reviewer gets the facts (`changes` output and the diff) and judges the claims itself. A reviewer handed the script's suspicions tends to confirm them.
- your own opinion of the changelog, and what you hope the answer is
- the gate's results, and whether this is a second look after fixes

Every agent writes its full answer to `reply.json` in its scratch folder, and `release.py collect` reads it from there. Its final message to you is a line or two.

## The sandbox block

The reviewer's brief includes this block, filled in with what `release.py sandbox -w <run> review` printed:

```markdown
## Your copy of the project

- **Project copy:** `<path>`, checked out at the release. Read anything in it.
- **Run the project's code only through:** `<run command>`. It runs from the project copy with its own home and temp folders, no credentials, and <"the network off (this machine's own addresses still work)" | "the network on">. Add `--cwd <subfolder>` to run from a subfolder and `--timeout <seconds>` to cap a run. Each run's output is saved under `logs/` next to the scratch folder.
- **Scratch folder:** `<path>`. Put every file you create here.
- Don't install anything or touch anything outside these two folders. With the network off, don't fetch anything: Windows PowerShell 5.1 and programs that open sockets themselves get past the block.
- <Commands not to run, and why, from the side-effect check: "Don't run install.ps1 without KILLER_SKILLS_DIR pointing into your scratch folder: it installs into your real profile." Write "None." if there are none.>
```

The first-time installer's brief uses the empty variant, filled in from `release.py sandbox -w <run> newcomer --empty`:

```markdown
## Your folder

- **Your folder:** `<path>`. It holds the release's files and nothing else: <the file names it printed>.
- **Run everything through:** `<run command>`. It runs in your folder with its own home and temp folders, no credentials, and <the network line, as above>. Add `--cwd <subfolder>` and `--timeout <seconds>` as you need. Each run's output is saved under `logs/` next to the scratch folder.
- **Scratch folder:** `<path>`. Put your notes and your reply here.
- Don't install anything outside your folder and don't open any other folder on this machine. With the network off, don't fetch anything: Windows PowerShell 5.1 and programs that open sockets themselves get past the block.
- <Commands not to run, and why, from the side-effect check, or "None.">
```

## Release reviewer

````markdown
# Check a release's changelog against what changed

A project is about to release version <version>. Below are its changelog section for that version and the draft release notes. Check each claim against what really changed since the last release, <last tag, or "the start (this is the first release)">, and find the changes a user would notice that nobody wrote down. You're not editing anything.

## The project

<What it is, who uses it, and how they use it: the commands, the library, the installer. Facts only.>

## The changelog section

```markdown
<the section, exactly as it is in the file>
```

## The release notes draft

```markdown
<notes.md exactly, or "None.">
```

## What changed, as a script listed it

```text
<the output of release.py changes: the commits, the files by area, the user-visible surface>
```

The script finds these by comparing manifests, files and `--help` output. It can miss things and it can't judge them.

<sandbox block>

`git log -p <last tag>..<release sha>` in the project copy shows every change since the last release. Ignore the top commit, called "sandbox base": it's the sandbox's own. Run the project as much as you like to see what a change does.

## How to judge each entry

- **supported:** a change in the diff does what the entry says.
- **unsupported:** nothing in the diff does it: the feature isn't there, it was already there before, or it was taken out again.
- **overstated:** something changed, but less than the entry claims: "much faster" for a one-line tweak, "fixes crashes" for one crash on one platform.
- **unclear:** you can't tell what it means precisely enough to check it.

Then look for:

- **changes with no entry** that a user would notice: options, commands, defaults, output, file locations, environment variables, dependencies, supported platforms and versions;
- **breaking changes not called breaking:** an option, command, setting or output that was removed or renamed, or a default that changed, where a user's script or config could stop working.

Quote the evidence: the commit, the `file:line`, or the command and what it printed.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "entries": [
    {"entry": "<the entry's text>", "verdict": "<supported | unsupported | overstated | unclear>",
     "evidence": "<the commit, file:line or command output that settles it>"}
  ],
  "missing": [
    {"change": "<what changed, as a user would notice it>", "evidence": "<commit or file:line>",
     "suggested_entry": "<one line in the changelog's own style>"}
  ],
  "breaking_not_called_breaking": ["<the change, in a sentence>"]
}
```

Give every entry in the section a verdict, in order. Then end with one line: how many entries, how many supported, how many changes missing.
````

## First-time installer

Optional. Send it when the README or the notes tell people how to install. It reads only the instructions, never the code.

````markdown
# Install a project for the first time, by its instructions

You're a new user who has never seen this project. Below are its install instructions, from the release notes and the README. Follow them literally, then run the first thing they tell you to run.

## The instructions

### From the release notes

```markdown
<the install section of notes.md, or "None.">
```

### From the README

```markdown
<the README's install section, with its line numbers>
```

<sandbox block, empty variant>

This release isn't published yet. Its files are in your folder. Where the instructions download from a registry, a release page or a URL, use the matching file from your folder instead, and record it as a substitution, not a failure. Anything else that has to be fetched will fail with the network off: record that too.

## How to work

- Do what the instructions say, in their order, through the run command. There's no source code in your folder; don't look for any elsewhere.
- When a step doesn't work, or the instructions don't say what to do next, record it: where it is (notes or README, and the line), what you did, what happened, and what you had to guess. You may work around a problem to carry on; record the workaround.
- At the end, run the version command the instructions give, or `<tool> --version`, and record what it printed.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "outcome": "<installed | installed-with-help | stuck>",
  "version_seen": "<what the version command printed, or null>",
  "steps": [
    {"doc": "<notes | README>", "line": 12, "action": "<what you ran>", "result": "<what happened>", "ok": true,
     "guess": "<what you had to assume, or null>", "workaround": "<what you did instead, or null>"}
  ],
  "stuck_points": [
    {"where": "<notes or README, and the line>", "what": "<what went wrong>", "suggestion": "<what the instructions should say>"}
  ]
}
```

`installed-with-help` means you got there only by guessing or working around something. Then end with one line: the outcome and the version you saw.
````
