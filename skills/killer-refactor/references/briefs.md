# Briefs

Every agent gets a brief built from one of the templates below. Fill in the parts in angle brackets and keep the rest word for word. Fixed wording keeps agents comparable, and keeps your hopes out of their work.

What stays out of every brief:

- the run folder, `refactor.py`, and anything in them
- other agents: how many there are, what they did, whether an earlier attempt at the same step was rejected (give the failing check's line instead, as a fact about the code)
- the refactorer's account of its change, when you brief a challenger: the diff has to stand on its own
- the survey's numbers when you brief a pin writer, beyond which faults its code let through
- how sure you are, and what you hope the answer is

Every agent writes its full answer to `reply.json` in its scratch folder, and `refactor.py` reads it from there. Its final message to you is a line or two, so replies don't pile up in your context.

## The facts block

Every brief has one, written once per run and pasted unchanged:

```markdown
## The project

<What it is, who uses it, and how: imported as a library, run as a command, or both. The language and runtime. The commands that build it and run its tests, written for the run command. Facts only.>
```

## The sandbox block

Every brief includes this block, filled in with what `refactor.py sandbox` printed for that agent:

```markdown
## Your copy of the project

- **Project copy:** `<path>`. Read anything in it.
- **Run the project's code only through:** `<run command>`. It runs from the project copy with its own home and temp folders, no credentials, and <"the network off (this machine's own addresses still work)" | "the network on">. Add `--repeat N` to run it several times, `--cwd <subfolder>` to run from a subfolder, `--timeout <seconds>` to cap a run. Each run's output is saved under `logs/` next to the scratch folder.
- **Scratch folder:** `<path>`. Put every file you create here, apart from changes to the project's own files.
- Don't install anything or touch anything outside these two folders. With the network off, don't fetch anything: Windows PowerShell 5.1 and programs that open sockets themselves get past the block.
- <Commands not to run, and why, from the side-effect check: "Don't run install.ps1 without KILLER_SKILLS_URL pointing at a local folder: it writes your PATH to the registry." Write "None." if there are none.>
```

## Refactorer

One per step, in a sandbox made with `refactor.py sandbox -w <run> ref-S2`. Its copy has every accepted step applied and committed, so `git diff` in it shows only what this agent changes.

````markdown
# Make one refactoring step

This project is being restructured in small steps, each of which must leave its behaviour exactly as it is. Your job is one step.

## The step

**<S2. The step, from the plan.>**

The public interface may change only like this: <the step's allowed changes, in words: "tangled.format_report is renamed to tangled.render_report, everywhere it's used" | "a new module tangled_parse may appear" | "No public name, parameter or default may change.">

<For a redo: "An earlier attempt at this step changed behaviour: <the failing check's line, quoted>. Keep that behaviour.">

## How it's checked

After you finish, your change is checked in a fresh copy, without you. All of these must hold:

- **The tests:** `<test command>` pass.
- **These commands give exactly the same output, exit code and files as before your change:** <each recorded run's command, one a line>. Run them before you start and keep their output to compare.
- **The public interface** (public modules, functions, classes, methods, constants and their parameters and defaults) is the same, apart from the changes allowed above.
- **Tests you can't see** pin what the code does now, and inputs nobody has tried yet will be compared between the old and new code.

<the facts block>

<the sandbox block>

## The rules

- **Behaviour stays exactly the same,** on every input, not just the ones the tests use: return values, output and its order, files written, errors raised and their messages, exit codes, what's printed to stderr, defaults, edge cases (empty input, None, zero, negative numbers, duplicates, unicode).
- **This step only, and one kind of change.** Don't tidy nearby code, rename other things, fix bugs or reformat. If you find a bug, leave it and say so under `noticed`: fixing it would change behaviour.
- **Move, then change.** When code moves, it moves as it is. Re-export moved names from where they were, so callers see the same names, unless the step says they move.
- **Test files change only by the step's renames,** exactly: the same text with the old name replaced by the new one. Don't add, remove or rewrite tests.
- **No new dependencies,** and no changes to dependency, build or CI files.
- **Keep each file's line endings and encoding as they are.**
- **Write it as the project's own author would.** No comments or notes about who or what made the change, or about this task.
- **If the step can't be done without changing behaviour, change nothing and say why.** That's a useful answer.

## How to work

1. Run the tests and each command above through the run command, as the code is now. Keep their output in the scratch folder.
2. Make the change.
3. Run the tests and the commands again, and compare the output with what you kept: it must match exactly. Search the project for every use of anything you moved or renamed, including strings, docs and config.

Don't commit anything. Leave the change in the copy: it's collected from there.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "done": true,
  "title": "<a commit subject for the step: what it does, in the imperative, under 70 characters>",
  "summary": "<what moved or changed and why behaviour is the same, in one or two plain sentences, as you'd write in a commit message>",
  "files": ["<each file you changed or added>"],
  "checked": "<what you ran and compared>",
  "noticed": "<bugs or oddities you saw and left alone, or null>",
  "reason": "<only when done is false: why you made no change>"
}
```

Then end with one line: done or not.
````

## Pin writer

One per function the survey named, in a sandbox made with `refactor.py sandbox -w <run> pin-<function>`. Its copy is the original code, with no steps applied.

````markdown
# Pin down what some code does now

This project is about to be restructured. Some of its code is barely tested: small faults planted in it pass the tests. Your job is to write tests that record exactly what that code does now, so a change to its behaviour can't go unnoticed. You're not judging whether the behaviour is right: if it looks like a bug, pin it as it is and say so under `noticed`.

## The code to pin

- **<file>:<function>** <"(faults the tests let through here: '>= → >' on line 9, '12 → 13' on line 18)" | "">

<the facts block>

<the sandbox block>

## The rules

- **New test files only,** in the project's test folder and named the way its tests are (`tests/test_pin_<function>.py`, `<name>.test.js`), written in the project's own test style and framework. Don't change any existing file.
- **Through the code's real interface:** call the function (or the command that reaches it) the way the project's code does.
- **Pin what it does now, exactly:** return values, output, exceptions and their messages, edge cases (empty, None, zero, negative, boundaries like `>=` against `>`, long and unicode input). Each boundary in the code deserves a test on each side of it.
- **Deterministic:** no clock, randomness, network or real home folder. They'll be run several times and must pass every time.
- **Write them as the project's own author would.** No comments about who or what wrote them.

## How to work

1. Read the code to pin and the existing tests.
2. Write the tests, and run them through the run command several times (`--repeat 3`).
3. Check they'd notice a change: change a comparison or a constant in the code to pin, run them, and see them fail; then put the code back exactly as it was.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "files": ["<each new test file>"],
  "command": ["<program>", "<argument>"],
  "targets": ["<file>:<function>"],
  "what": "<what the tests pin, in a sentence>",
  "noticed": "<behaviour that looks like a bug, pinned as it is, or null>"
}
```

`command` runs only your new tests, as a list of arguments for the run command, and must name your files (for Python with unittest: `["python", "-m", "unittest", "tests/test_pin_shorten.py"]`). Then end with one line: how many tests, and which code they pin.
````

## Challenger

One per verified step, in a sandbox made with `refactor.py sandbox -w <run> chal-C2 --candidate C2`, which has the step applied, uncommitted. Paste the diff from `refactor.py candidate show -w <run> C2` (without `--with-summary`).

````markdown
# Find an input where the old and new code differ

Below is one step of a refactor, and a claim: **it doesn't change what the code does.** The tests pass with it, and some recorded commands give the same output. Your job is to find an input where the code before this change and after it behave differently.

## The step

**<S2. The step, from the plan.>** Interface changes it may make: <the allowed changes, or "none">.

```diff
<the diff>
```

<the facts block>

<the sandbox block>

The copy has the change applied, uncommitted: `git diff` shows it, and `git stash` takes it out so you can try the code before it (`git stash pop` puts it back).

## Where refactors go wrong

- **Edge cases moved code no longer handles:** empty input, None, zero, negative numbers, duplicates, unicode, very long input, a missing file
- **Boundaries:** `<` against `<=`, off by one in a slice or a range, the first or last element
- **Order:** of output lines, of dict or set iteration, of side effects, of which error comes first
- **Errors:** an exception type or message that changed, an error that now happens later (after something was written), or not at all
- **State:** a default argument now shared, a cache, a global, an import that now runs code at a different time
- **Names:** something still reachable by its old name, or no longer reachable; a re-export that was dropped
- **Types:** a list that became a generator or a tuple, an int that became a float, a copy that became a reference

## Probes

Write each idea as a probe: a small script in the scratch folder that uses the project's public interface, the way a caller would, and prints what it observes, so its output can be compared. It must:

- **Print, not judge:** print the results (return values, `repr` of errors, files written), one per line. It isn't told which version it's running on.
- **Be deterministic:** the same output every run of the same code. Output is compared exactly, with nothing masked but the copy's folder, so print no clock, randomness, ids or object addresses.
- **Run from the project copy** through the run command, reaching the scratch folder by a relative path like `../scratch/p1.py`. For Python, start it with `import os, sys; sys.path.insert(0, os.getcwd())`: the script's own folder is the scratch folder, not the project. A probe that can't import the code doesn't count.
- **Use only names that exist before and after the change.** For a renamed name, use the new one: the script translates the code (not strings or output) for the original side.
- **Catch its own errors:** print the exception's type and message rather than crashing, so the comparison shows them.

Write at least <3 | 6> probes, each trying a different idea. Before you answer, run each one with the change and with it stashed. A probe that shows a difference is the best answer; a set of honest probes that show none is a good answer too.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "probes": [{"name": "<short name>", "what": "<the idea it tests>", "command": ["<program>", "../scratch/<file>"]}],
  "found": "<the difference you saw, quoted, or null>",
  "notes": "<anything else, or null>"
}
```

Each probe is rerun in two fresh copies without you: the code before the change and the code after it. Then end with one line: how many probes, and whether any showed a difference.
````

Leave the refactorer's account of the change out of this brief. The diff has to stand on its own.
