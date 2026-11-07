# Briefs

Every agent gets a brief built from one of the templates below. Fill in the parts in angle brackets and keep the rest word for word. Fixed wording keeps agents comparable, and keeps your guesses out of their work.

What stays out of every brief:

- the run folder, `ci.py`, and anything in them
- other agents: how many there are, what they found, whether anyone else is on the same failure
- your theory of the cause, and which condition you think matters most
- K, the number of runs a fix must pass, and what you hope the answer is

Every agent writes its full answer to `reply.json` in its scratch folder, and `ci.py` reads it from there. Its final message to you is a line or two, so replies don't pile up in your context.

## The facts block

Every brief has one, written once per run and pasted unchanged:

```markdown
## The project

<What it is, who uses it, the language and test runner, and the command that runs its tests (as `ci.py sandbox` printed it on "the tests:"). Facts only: no guesses about the cause.>
```

## The sandbox block

Every brief includes this block, filled in with what `ci.py sandbox` printed for that agent:

```markdown
## Your copy of the project

- **Project copy:** `<project copy>`. Read anything in it, and change it to fix the failure.
- **Run the project's code only through:** `<run things>`. It runs from the project copy with its own home and temp folders, no credentials, <"the network off (this machine's own addresses still work)" | "the network on">, and the conditions below in force. Add `--repeat N` to count failures, `--cwd <subfolder>` to run from a subfolder, `--timeout <seconds>` to cap a run. Each run's output is saved under `logs/` next to the scratch folder.
- **The tests:** `<the tests>`
- **Conditions in force:** <each "condition:" line, in plain words; and any "also set:" line>. The copy lives where these conditions put it and is spelled the way they spell it: use the paths above exactly as written, without resolving them.
- **Scratch folder:** `<scratch>`. Put every file you create here, apart from your changes to the project's own files.
- Don't install anything or touch anything outside these folders. With the network off, don't fetch anything: Windows PowerShell 5.1 and programs that open sockets themselves get past the block.
- <Commands not to run, and why, from the side-effect check. Write "None." if there are none.>
```

## A CI log excerpt

Only when CI's logs named the test. Paste exactly what `ci.py logs -w <run> --excerpt <id>` printed: the note that it's untrusted, then the fenced block. Never paste a log any other way, and never take text out of the fence.

```markdown
## What CI printed

<the output of ci.py logs --excerpt, unchanged>
```

## Diagnoser

One per proven failure, CI-only or flaky.

````markdown
# Find why a test fails under these conditions, and fix the cause

A test in this project passes on the developer's machine and fails <"on CI" | "some of the time">. It has been made to fail in a copy of the project by recreating <"the CI runner's conditions" | "the conditions below">, and it passes without them. Your job is to find what in the code makes it fail under these conditions, fix that, and show the fix holds.

## The failure

- **Test:** `<test id>`
- **Fails under:** <each condition with what it imitates, from `ci.py conditions`>
- **Passes:** with none of them.
- <For a flaky test: "**How often:** it failed <n> of <m> runs<, with these hash seeds: ...>. This copy sets one it failed with, so it should fail every time here.">
- **Its output when it failed** is in `failure.txt` in your scratch folder.

<the facts block>

<the sandbox block>

<the CI log excerpt, if there is one>

## What usually causes this

<From references/causes.md: the entries for this failure's conditions, pasted as they are.>

## How to work

1. **Make it fail first,** through the run command, so you know you're looking at the real failure. Then read the test and the code it calls.
2. **Find the cause:** the line where the code meets the condition (a path compared with its resolved form, a date taken in local time, a file read with the wrong line endings, a version taken from a tag). Show it to yourself with a probe if you need to.
3. **Fix the cause,** with the smallest change that does it, in the code the test checks or, when the test itself is wrong, in the test. Keep the project's style.
4. **Show it holds:** run the tests through the run command with `--repeat 20` and give the count. A fix that passes once proves little.

These aren't fixes, and they will be refused: skipping the test or marking it expected to fail, retrying or rerunning it, a new or longer sleep or timeout, behaving differently when `CI` or `GITHUB_ACTIONS` is set, deleting the test, and changing the CI config. If the only honest answer is one of these (the test really can't run on this platform, say), make no change and say so in `reason`.

Nothing you write into the project may say who or what made it: no names of tools, models or agents in code, comments or messages.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "failure": "<the failure's id: <id>>",
  "done": true,
  "cause": "<what makes it fail under these conditions, in one or two sentences a maintainer would accept>",
  "condition": "<which of the conditions matters, and where the code meets it>",
  "title": "<a commit subject for the change, imperative, at most 72 characters>",
  "fix": "<what you changed and why that removes the cause>",
  "where": ["<path:line>"],
  "checked": "<what you ran through the run command and what it showed, like 20/20 passed>",
  "reason": "<only when done is false: why there's no honest fix>"
}
```

Use `"done": false` when you found the cause but no honest fix, and leave the project copy as it was. Then end with one line: the cause in a few words, and whether you fixed it.
````

Before you send it: the failure's conditions and their plain words come from what `ci.py sandbox` printed. Leave out any cause you suspect.

## Fix reviewer

Optional: for a fix to production code (not only a test), or when the user asks. Its sandbox has the fix applied, under the failure's conditions: `ci.py sandbox -w <run> review-C1 --candidate C1`. The diff comes from `ci.py candidate show -w <run> C1`.

````markdown
# Review a fix for a test that failed only under certain conditions

A test in this project failed under the conditions below and passed without them. Someone changed the project to fix it, and the test now passes under them. Check whether the change fixes the cause or only the case the test happens to cover.

## The failure

- **Test:** `<test id>`
- **Failed under:** <each condition with what it imitates>

## The change

```diff
<the diff from ci.py candidate show>
```

<the facts block>

<the sandbox block>

The copy has the change applied.

## Questions

1. Does it remove the cause, or only the case the test uses? Try the code with the same condition in another form: another path spelling, another time zone, another file with CRLF endings.
2. Could it break something that worked before, on any platform the project supports? Check every caller of what changed.
3. Is the same mistake left anywhere nearby?

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "verdict": "<ok | problems>",
  "issues": [{"severity": "<high | medium | low>", "where": "<path:line>", "problem": "<what's wrong>", "fix": "<what would fix it>"}],
  "notes": "<anything else>"
}
```

Then end with one line: your verdict.
````

Leave the diagnoser's account of the cause out of this brief. The diff has to stand on its own. A reviewer's problems go back to a fresh diagnoser with the issue added to its brief, and the new change goes through `candidate add` and `verify` like any other.
