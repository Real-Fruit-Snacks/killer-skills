# Briefs

Every agent gets a brief built from one of the templates below. Fill in the parts in angle brackets and keep the rest word for word. Fixed wording keeps the walks comparable: the newcomer after the fixes gets exactly the brief the first one got, so a better outcome comes from better docs and not from a better brief.

What stays out of every brief:

- the run folder, `docs.py`, and anything in them
- other agents: how many there are, what they found, whether this is the first walk or the second
- the findings so far, the fixes you made, and what you expect the newcomer to trip on
- how sure you are, and what you hope the answer is

Every agent writes its full answer to `reply.json` in its scratch folder, and `docs.py collect` reads it from there. Its final message to you is a line or two, so replies don't pile up in your context.

## The sandbox block

Every brief includes this block, filled in with what `docs.py sandbox` printed for that agent:

```markdown
## Your copy of the project

- **Project copy:** `<path>`.
- **Run the project's commands only through:** `<run command>`. It runs from the project copy with its own home and temp folders, no credentials, and <"the network off (this machine's own addresses still work)" | "the network on">. Add `--cwd <subfolder>` to run from a subfolder and `--timeout <seconds>` to cap a run. Each run's output is saved under `logs/` next to the scratch folder.
- **Scratch folder:** `<path>`. Put every file you create here.
- <Only when `sandbox` printed a python line: "`python` and `pip` here are a private copy for this sandbox, so installs stay in it. Use them as the docs say, as if they were your own." Or: "`python` runs here, but `pip` can't install anything in this sandbox: count an install step that needs it as one you couldn't follow.">
- Don't install anything outside the sandbox or touch anything outside these two folders. With the network off, don't fetch anything: Windows PowerShell 5.1 and programs that open sockets themselves get past the block.
- <Commands not to run, and why, from the side-effect check: "Don't run install.ps1: it writes your PATH to the registry." Write "None." if there are none.>
```

## Reproductions

The newcomer and fact checker briefs both include this block:

```markdown
## How to write a reproduction

A reproduction is a small script in the scratch folder that shows the problem happening. Give one for each `wrong` or `stale` finding where you can; most `unclear` and `missing` findings won't have one, and that's fine. It must:

- **Start from a fresh copy of the project:** do any setup step it needs (the install step from the docs) itself, so it works without anything you did by hand.
- **Say what it saw:** print a line starting `EXPECTED:` with what the docs say, and a line starting `OBSERVED:` with what happened, then exit 1 when the problem shows and 0 when it doesn't. A script that fails without printing `OBSERVED:` proves nothing.
- **Leave the project copy as it is:** keep everything it creates in the scratch folder.
- **Detect a hang itself:** run the step in a child process with its own time limit, then print `OBSERVED:` and exit 1.
- **Run from the project copy** through the run command, reaching the scratch folder by a relative path like `../scratch/install.py`, so it works in another copy too.

It will be rerun in a fresh copy of the project, without you, to check it. `command` in your answer is how to run it: a list of arguments for the run command, like `["python", "../scratch/install.py"]`.
```

## Newcomer

One per journey, and the same brief, word for word, for the fresh newcomer who walks the journey again after the fixes.

````markdown
# Follow the docs: <the journey's goal, in a few words>

You're new to this project. You've never seen its code, and you won't: your job is to **<the journey's goal, as a sentence>**, using only its docs, the way someone who just found it would. Where the docs don't get you there, that's exactly what's worth knowing.

## What you may read

<Each doc file in the journey, as a path in the project copy.>

Read only these. Don't open the source code, the tests, the CI configuration, the git history or any other file in the project copy, even when you're stuck: a newcomer wouldn't know where to look, and what you'd learn there hides a gap in the docs. If a doc links somewhere outside this list, or to a website, don't open it: record it as a step you couldn't follow.

You may read what the program prints, its `--help` and its error messages: a newcomer sees those too.

<sandbox block>

## How to work

1. Start at <the doc and section, like "README.md, the Install section">, and follow the docs literally, in order, until you reach the goal.
2. Run each command the docs give through the run command: `npm test` becomes `<the run command, ending -- npm test>`. A line with pipes, `&&`, redirects or variables needs a shell around it: `<the run command, ending -- sh -c "<the line>">`<, and for a PowerShell line `... -- powershell -NoProfile -Command "<the line>"`, when this machine runs Windows>.
3. Each run starts again in the project folder: a `cd` or an `export` doesn't carry over to the next run. When the docs move into a folder, add `--cwd <folder>` to the runs after it; when they set a variable, set it in the same shell line as the command that needs it. That's the sandbox, not the docs, so don't report it.
4. When the docs show what a command prints, compare. A different version number, date or time is fine; different words or a missing line aren't.
5. Record every step: the doc file and line, what you did, what happened, whether it worked, and anything you had to guess.
6. When a step doesn't work, or the docs don't say what to do, try what a sensible newcomer would try, for a few minutes at most. If you find a way past it, keep going and record the workaround. If you can't, stop there: that's where you got stuck.
7. Stop when you've reached the goal, or when you're stuck.

## What counts as a finding

- **wrong:** the docs say something that isn't so: a command that fails, an option that doesn't exist, output that's different
- **missing:** a step the docs leave out: something you had to install, set up or know that they never mention
- **unclear:** you had to guess, and a newcomer could reasonably guess wrong
- **stale:** it worked, but the docs describe it as it used to be
- **broken-link:** a link or an anchor that goes nowhere

Rate each one: **high** if it stops a newcomer or does damage, **medium** if it costs real time but there's a way past it, **low** for a wrinkle.

<reproductions block>

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "journey": "<the journey's name>",
  "outcome": "<reached | reached-with-help | stuck>",
  "stuck_at": {"doc": "<file>", "line": 0, "what": "<the step you couldn't get past>"},
  "steps": [
    {"doc": "<file>", "line": 0, "action": "<what you did>", "result": "<what happened, quoted where it matters>",
     "ok": true, "guess": "<what you had to assume, or null>", "workaround": "<what you did instead, or null>"}
  ],
  "guesses": ["<each thing you had to guess>"],
  "findings": [
    {"title": "<short name>", "kind": "<wrong | missing | unclear | stale | broken-link>", "severity": "<high | medium | low>",
     "doc": "<file>", "line": 0, "told": "<what the docs say, quoted>", "happened": "<what really happened>",
     "guess": "<or null>", "workaround": "<or null>", "command": ["<program>", "<argument>"]}
  ],
  "read": ["<each file you read>"]
}
```

`reached` means you got to the goal by following the docs. `reached-with-help` means you got there, but only with a workaround or a guess the docs didn't support. `stuck` means you couldn't. `stuck_at` is `null` unless you got stuck. `command` is `null` for a finding with no reproduction. Then end with one line: your outcome and how many findings.
````

## Fact checker

Thorough depth only. It may read the code: it checks what the docs claim, not whether they can be followed.

````markdown
# Check what the docs claim, against the code

The docs below describe a project. Some of what they say gets tested by running their examples. Much doesn't: defaults, limits, file locations, environment variables, config keys, error messages, what happens with bad input, what a command leaves behind. Your job is to check those claims against the code and against what the program really does.

## The docs

<The doc files in scope, as paths in the project copy.>

## The project

<What it is, who uses it, and how to build and run it with the run command. Facts only.>

<sandbox block>

Read anything you like: the docs, the code, the tests.

## How to work

1. List the claims the docs make that their code blocks don't exercise. Each is one checkable statement, with its file and line and a short quote.
2. Check each one in the code, and by running it where you can.
3. Each claim that's false is a finding. Write a reproduction for each one you can.
4. When the docs and the code disagree, say which is wrong: `kind: wrong` when the docs are wrong, `kind: code` when the docs describe what a user would expect and the code doesn't do it.

<reproductions block>

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "claims": [
    {"claim": "<one checkable statement>", "doc": "<file>", "line": 0, "verdict": "<true | false | unclear>",
     "evidence": "<the code, test or output that settles it>"}
  ],
  "findings": [
    {"title": "<short name>", "kind": "<wrong | stale | missing | code>", "severity": "<high | medium | low>",
     "doc": "<file>", "line": 0, "told": "<what the docs say, quoted>", "happened": "<what the code really does>",
     "command": ["<program>", "<argument>"]}
  ],
  "read": ["<each file you read>"]
}
```

Then end with one line: how many claims, how many false, how many reproduced.
````
