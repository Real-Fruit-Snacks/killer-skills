# Briefs

Every agent gets a brief built from one of the templates below. Fill in the parts in angle brackets and keep the rest word for word. Fixed wording keeps agents comparable: two finders of the same kind, briefed alike, are an honest check on each other.

What stays out of every brief:

- the run folder, `bugs.py`, and anything in them
- other agents: how many there are, what they found, whether anyone else is on the same bug
- the finder's reasoning, when you brief a prover or a challenger: they get the claim, never the argument for it
- how sure you are, and what you hope the answer is

Every agent writes its full answer to `reply.json` in its scratch folder, and `bugs.py` reads it from there. Its final message to you is a line or two, so replies don't pile up in your context.

## The sandbox block

Every brief includes this block, filled in with what `bugs.py sandbox` printed for that agent:

```markdown
## Your copy of the project

- **Project copy:** `<path>`. <"Read anything in it." | for the promise checker: "Run anything in it, but read only the files listed under 'What you may read'.">
- **Run the project's code only through:** `<run command>`. It runs from the project copy with its own home and temp folders, no credentials, and <"the network off (this machine's own addresses still work)" | "the network on">. Add `--repeat N` to count failures, `--cwd <subfolder>` to run from a subfolder, `--timeout <seconds>` to cap a run, `--stdin <file>` to feed it input. Each run's output is saved under `logs/` next to the scratch folder.
- **Scratch folder:** `<path>`. Put every file you create here.
- Don't install anything or touch anything outside these two folders. With the network off, don't fetch anything: Windows PowerShell 5.1 and programs that open sockets themselves get past the block.
- <Commands not to run, and why, from the side-effect check: "Don't run install.ps1 without KILLER_SKILLS_URL pointing at a local folder: it writes your PATH to the registry." Write "None." if there are none.>
```

## Reproductions

The finder, promise checker, copy finder and prover briefs all include this block:

```markdown
## How to write a reproduction

A reproduction is a small script in the scratch folder that makes the bug happen. It must:

- **Use a real entry point** if there is one: the command line, the installer, the function or API users call. If only internal code with input no caller would send triggers it, say so.
- **Say what it saw:** print a line starting `EXPECTED:` with the right behaviour and a line starting `OBSERVED:` with what happened, then exit 1 when the bug shows and 0 when it doesn't. A script that fails without printing `OBSERVED:` proves nothing.
- **Leave the project copy as it is:** keep everything it creates in the scratch folder, and point the project at temp folders inside it.
- **Detect a hang itself:** run the step in a child process with its own time limit, then print `OBSERVED:` and exit 1.
- **Live in a folder of its own** in the scratch folder, with any helper files it needs, like `empty-list/`. Run it from the project copy through the run command by a relative path, like `../repro/empty-list/check.py`, so it works in a fresh copy too. Only that folder travels with the bug.
- **Be as small as it can be.** If it depends on timing, run it with `--repeat 20` and give the count.

It will be rerun in a fresh copy of the project, without you, to check it.
```

## Promise checker

````markdown
# Write down what this project promises, then test it

Your job is to work out what this project should do from the outside, then check that it does. You don't read its source code: what the code does is exactly what's in question, and reading it makes its behaviour look intended.

## The project

<What it is, who uses it, and how they use it. Facts only.>

## What you may read

<The outside of the project: README, docs, the changelog, usage text saved in the scratch folder.>

When the docs don't say how to call something, you may read the names and parameters of the commands, functions or endpoints users call, but not the code inside them. Don't open other source files.

<sandbox block>

You can run the project as much as you like.

## How to work

1. **List the promises.** Each is one checkable statement with its source: a file and line with a short quote, "user", "general rule" or "inferred".
   - From the docs: every command, option, default, path, output, limit and example they describe.
   - From the project's owner: <the user's promises, or "None given.">
   - General rules, where they apply:
     - a run that fails changes nothing, or says what it left behind;
     - running the same thing twice gives the same result as running it once;
     - what goes in comes back out unchanged (save then load, encode then decode);
     - bad input gets a clear error, not a crash or a stack trace;
     - a failure exits non-zero, and success exits zero.
   - Mark a promise "inferred" when you're reading between the lines rather than quoting.
2. **Test each promise** through the public interface, in your copy. Where a promise covers a range of inputs, try many: a small generator in the scratch folder, with no libraries to install, that tries awkward values (empty, very long, spaces, quotes, non-ASCII, line breaks, leading dashes, paths that don't exist).
3. **Each broken promise is a candidate bug.** Write a reproduction for it.
4. **Rate each one** high, medium or low:
   - **high:** loses or corrupts data, breaks the main use, leaks a secret, or reports success after failing
   - **medium:** wrong results, or a failure users notice and can work around
   - **low:** a misleading message, a stack trace instead of an error, or an edge case with an easy workaround

<reproductions block>

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "promises": [
    {"id": "P1", "promise": "<one checkable statement>", "source": "<file:line and a short quote | user | general rule | inferred>",
     "test": "<how you tested it>", "result": "<kept | broken | untestable>"}
  ],
  "candidates": [
    {"title": "<short name>", "claim": "<what goes wrong, as behaviour someone could observe>",
     "trigger": "<the input or steps>", "expected": "<what the promise says>", "observed": "<what happened>",
     "promise": "<the promise's id, like P3>", "where": ["<the doc's file:line the promise comes from; empty for a general rule or --help text>"],
     "kind": "<bug | mismatch>",
     "source_kind": "<general | usage | tests | docs | changelog | inferred>", "severity": "<high | medium | low>",
     "confidence": "<low | medium | high>", "command": ["<program>", "<argument>"], "runs": "<failed>/<total> or null"}
  ],
  "covered": ["<each file you read>"]
}
```

`command` is your reproduction, as a list of arguments for the run command, run from the project copy. Use `null` for a broken promise you couldn't write one for. `kind` is `bug` when the promise is a general rule or the program's own `--help` text, which comes from the code itself; it's `mismatch` when the promise comes from the docs, the README, a wiki or the changelog, because docs go out of date and either side could be wrong. Then end with one line: how many promises, how many broken, how many reproduced.
````

## Finder

Used for every read-through, the Pairs agent, and on a thorough run each kind's own agent.

````markdown
# Find bugs: <the slice, or the kind>

You're looking for bugs in a project. <For a read-through: "Read every file in your slice, top to bottom, as a careful reviewer reads it, with the kinds of bug below as your checklist." For one kind's own agent, or Pairs: that kind's lens, from finders.md.>

Report the bugs you'd bet on, worst first, at most ten. Each must be behaviour someone could observe: an output, a file, an exit code, a crash, a hang. A style preference, or a guess about what the author meant, isn't a bug.

## The project

<What it is, who uses it, how to build, run and test it with the run command. Facts only.>

**In scope:** <the files or folder; or the changed files, with how to see the diff; or this read-through's slice>. Read these first, and follow calls into other files when you need to.

<For Re-runs and Pairs, if you made one: "An older version (<tag or commit>) is at <path>, with its own run command <command>.">

<sandbox block>

Your copy is yours alone and gets thrown away. Change it as much as you like to experiment: break code on purpose, add prints, run anything through the run command. Your reproductions must still work on an unchanged copy.

## What to look for

<For a read-through: the read-through's own "Look for" list, then each chosen kind under its own heading (`### Failures`, `### Bad input`, ...) with its "Look for" list and its "Probe" line, from finders.md. For one kind's own agent: that kind's list and probe. For Pairs, also list the pairs.>

## How to work

- Read the code in scope with your lens in mind. When you suspect something, try it in your copy: a quick probe beats a long argument.
- Look across files. Bugs often sit between a caller and what it calls.
- For each bug you make happen, write a reproduction. A suspicion you couldn't trigger can still be reported, with no command and an honest confidence.
- Rate each one high, medium or low:
  - **high:** loses or corrupts data, breaks the main use, leaks a secret, or reports success after failing
  - **medium:** wrong results, or a failure users notice and can work around
  - **low:** a misleading message, a stack trace instead of an error, or an edge case with an easy workaround
- List every file you actually read in `covered`, so it's clear afterwards what nobody looked at.

<reproductions block>

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "candidates": [
    {
      "title": "<short name>",
      "claim": "<what goes wrong, as behaviour someone could observe: what happens, not why>",
      "cause": "<why it happens, if you know; kept for whoever fixes it, and kept from whoever checks it>",
      "trigger": "<the input or steps that make it happen>",
      "expected": "<what should happen instead>",
      "observed": "<what you saw when you tried it, or null>",
      "where": ["<path:line>"],
      "kind": "<bug | test-gap | mismatch>",
      "severity": "<high | medium | low>",
      "confidence": "<low | medium | high>",
      "command": ["<program>", "<argument>"],
      "runs": "<failed>/<total>, or null if you ran it once"
    }
  ],
  "covered": ["<path of each file you read>"]
}
```

`mismatch` means the docs and the code disagree and either could be wrong. `command` is your reproduction, as a list of arguments for the run command, or `null` if you have none. An empty `candidates` list is a fine answer if you found nothing. Then end with one line: how many candidates, and how many you reproduced.
````

## Prover

For a high or medium suspect that has no working reproduction yet.

````markdown
# Reproduce a reported bug

Someone reported the behaviour below in a project. Your job is to find out whether it really happens, by making it happen. You're not fixing anything, and you're not judging whether it matters.

## The report

- **Claim:** <claim>
- **How it might be triggered:** <trigger, or "Not given.">
- **Expected instead:** <expected, or "Not given.">
- **Where to look:** <where, or "Not given.">

## The project

<What it is, and how to run it with the run command. Facts only.>

<sandbox block>

<reproductions block>

## How to work

Try the trigger, then two or three variations. If it doesn't show after an honest attempt, the verdict is `not-reproduced`: say what you tried. Don't stretch the claim to fit something else you found; a different bug goes in `notes`.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "verdict": "<reproduced | internal-only | not-reproduced | impractical>",
  "command": ["<program>", "<argument>"],
  "expected": "<what should happen>",
  "observed": "<what happened, quoted from the output>",
  "runs": "<failed>/<total>, or null if you ran it once",
  "entry_point": "<how a user reaches it: the command, function or file>",
  "notes": "<what you tried, anything surprising, other bugs you came across>",
  "would_take": "<only for impractical: what it would take to see it happen>"
}
```

`internal-only` means it happens, but only when internal code is called with input no caller sends. `impractical` means it can't be made to happen on this machine at all, however you try: it needs a full disk at the wrong moment, a race too narrow to hit, another operating system, or a real service. Say what it would take. Don't use it for a bug you simply couldn't trigger: that's `not-reproduced`. Then end with one line: your verdict.
````

Before you send it: if the finder ran the trigger and saw it, put that in "How it might be triggered" as steps. Leave out the finder's explanation of *why* it happens.

## Challenger

For a group of up to five proven bugs from the same area of the code; one report is a group of one.

````markdown
# Challenge bug reports

Below <is a bug report | are <n> bug reports> from <the area: the files or feature>, each with a reproduction that makes it happen. Your job is to try to knock each one down, on its own. Reports go wrong in a few ways:

- **Intended:** the docs, a comment, a test or a commit message says it should work this way.
- **Unreachable:** no real user can get there; it needs input or a state that nothing produces.
- **Wrong reproduction:** it misuses the project, tests the wrong thing, or expects the wrong value.
- **Not this project's bug:** it comes from the environment, the sandbox, or a dependency's documented behaviour.
- **The docs are out of date, not the code:** when a report says the code breaks a promise in the docs, the docs can be the side that's wrong. Check which changed more recently (`git log -1 --format=%cs -- <file>` for the doc and for the code), what the tests assert, and what the changelog says. If the code changed on purpose and the docs weren't updated, the code is right.

If none of these holds after an honest look, the report stands. Then judge how much it matters.

## The reports

<For each one:>
### <id>
- **Claim:** <claim>
- **Expected:** <expected>
- **Observed:** <observed>
- **Reproduction:** `<command>`, run from the project copy. Its files are already in the scratch folder.
- **Where:** <where>

## The project

<What it is and who uses it. Facts only.>

<sandbox block>

Read anything you like: code, docs, tests, the git history. Rerun each reproduction, and change it to test your doubts.

## Severity and likelihood

- **high:** loses or corrupts data, breaks the main use, leaks a secret, or reports success after failing
- **medium:** gives wrong results, or fails in a way users notice and can work around
- **low:** cosmetic, a misleading message, or an edge case with an easy workaround
- **common:** normal use hits it
- **occasional:** a plausible setup or input hits it
- **rare:** it needs unusual input, timing or setup

## Your answer

Write this JSON to `reply.json` in the scratch folder, one entry per report:

```json
{
  "verdicts": [
    {
      "id": "<the report's id>",
      "verdict": "<stands | question | knocked-down | docs-stale>",
      "reason": "<the evidence: quote the doc, test, code, output or git history that settles it>",
      "severity": "<high | medium | low>",
      "likelihood": "<common | occasional | rare>",
      "question": "<only for question: what the project's owner has to decide, in one sentence>"
    }
  ]
}
```

Use `question` when it might be deliberate and only the owner can say: the docs are silent and either behaviour is a reasonable choice. Use `docs-stale` when the code is right and the docs describe something older. Then end with one line: how many stand.
````

Group by area so the challenger reads that code once, and keep the group's reports otherwise unrelated: never the finder's reasoning, the `cause`, or how many finders found each.

## Code checker

For a high- or medium-severity bug a prover found impractical to make happen here. It's checked by reading instead of running, so the chain has to be explicit.

````markdown
# Check a bug report against the code

Below is a bug report that can't be made to happen on this machine: <what it would take, from the prover>. Your job is to decide whether the code really allows it, by tracing it. You're not fixing anything.

## The report

- **Claim:** <claim>
- **How it would happen:** <trigger>
- **Expected instead:** <expected>
- **Where to look:** <where>

## The project

<What it is and who uses it. Facts only.>

<sandbox block>

Read anything you like: code, docs, tests, the git history. You may run parts of it to check single steps, even if the whole bug can't be shown.

## How to work

1. **Trace the chain** from a real entry point (a command, a function users call, a request) to the failure. Each step is one thing the code does, with its `file:line` and the line quoted.
2. **Try to break it.** Look for the check, retry, lock, error handler or caller that would stop it at each step. Look for docs or tests saying it's intended.
3. **Decide:**
   - `holds`: every step is there and nothing stops it;
   - `breaks`: some step doesn't happen, or something stops it (say which);
   - `question`: it might be deliberate, and only the project's owner can say.

## Severity and likelihood

Use the challenger's scale: high, medium or low; common, occasional or rare.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "verdict": "<holds | breaks | question>",
  "chain": ["<file:line, the quoted line, and what it does>"],
  "reason": "<why it holds, or the step that breaks it>",
  "severity": "<high | medium | low>",
  "likelihood": "<common | occasional | rare>",
  "question": "<only for question: what the owner has to decide>"
}
```

Then end with one line: your verdict.
````

Give it the claim and the prover's "what it would take", never the finder's reasoning.

## Copy finder

````markdown
# Find copies of a bug

<What the project is, and how to run it with the run command. Facts only.>

These bugs were proven in this project. The same mistake often appears in more than one place. For each one, find every other place it does.

## The bugs

<For each confirmed high- or medium-severity bug:>
### <id>
- **What goes wrong:** <claim>
- **The mistake behind it:** <the cause, in one or two sentences>
- **Where it was found:** <where; ignore this place>
- **Its reproduction:** `<command>`. Its files are in the scratch folder; adapt it for each copy.

## Where to look

<The scope. If the user allowed other projects: "Also read these folders; don't run anything in them: <paths>.">

<sandbox block>

<reproductions block>

## How to work

- Search for the mistake, not the text. The same mistake can be written differently: another function, another file, another language in the same project.
- For each copy, check it really has the same problem where it sits, and write a reproduction for it.

## Your answer

Write this JSON to `reply.json` in the scratch folder, one candidate per copy:

```json
{
  "candidates": [
    {
      "title": "<short name>",
      "copy_of": "<the id of the bug it copies>",
      "claim": "<what goes wrong here, as behaviour someone could observe: what happens, not why>",
      "trigger": "<the input or steps that make it happen>",
      "expected": "<what should happen instead>",
      "observed": "<what you saw when you tried it, or null>",
      "where": ["<path:line; for another project, its folder too>"],
      "kind": "bug",
      "severity": "<high | medium | low: how much it matters here, which can differ from the original>",
      "confidence": "<low | medium | high>",
      "command": ["<program>", "<argument>"],
      "runs": "<failed>/<total>, or null if you ran it once"
    }
  ],
  "covered": ["<path of each file you read>"]
}
```

`command` is your reproduction for this copy, or `null` if you have none. Then end with one line: how many copies, how many reproduced.
````

## Fix reviewer

````markdown
# Review a fix

A bug in this project was fixed. Check the fix.

## The bug

- **Claim:** <claim>
- **Reproduction:** `<command>`, run from the project copy. Its files are in the scratch folder.

## The fix

```diff
<the diff, test included>
```

<sandbox block>

The copy has the fix applied.

## Questions

1. Does the fix remove the cause, or only the case the reproduction uses? Try inputs near it.
2. Could the change break something that worked before? Check every caller of what changed, and run the tests.
3. Is the same mistake left anywhere nearby?
4. Does the new test check behaviour a user would notice, and would it fail if the bug came back?

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

Leave your own explanation of the fix out of this brief. The diff has to stand on its own.
