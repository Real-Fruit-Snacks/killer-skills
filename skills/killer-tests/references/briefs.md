# Briefs

Every agent gets a brief built from one of the templates below. Fill in the parts in angle brackets and keep the rest word for word. Fixed wording keeps writers comparable, and keeps the lead's hopes out of them.

What stays out of every brief:

- the run folder, `mutants.py`, and anything in them
- the mutation score, the goal, how many survivors there are in all, and how many writers there are
- other agents: what they wrote, claimed or found
- for a judge: the writer's reason for calling a fault equivalent. The judge gets the change, never the argument
- for the reviewer: which faults the tests were written for. It reviews the tests as tests
- how sure you are, and what you hope the answer is

Every agent writes its full answer to `reply.json` in its scratch folder, and `mutants.py` reads it from there. Its final message to you is a line or two, so replies don't pile up in your context.

## The sandbox block

Every brief includes this block, filled in with what `mutants.py sandbox` printed for that agent:

```markdown
## Your copy of the project

- **Project copy:** `<path>`. Read anything in it.
- **Run the project's code only through:** `<run command>`. It runs from the project copy with its own home and temp folders, no credentials, and <"the network off (this machine's own addresses still work)" | "the network on">. Add `--repeat N` to run it several times, `--cwd <subfolder>` to run from a subfolder, `--timeout <seconds>` to cap a run. Each run's output is saved under `logs/` next to the scratch folder.
- **The tests:** `<the test command>`, through the run command: `<run command with the test command filled in>`.
- **Scratch folder:** `<path>`. Put every file that isn't a test here.
- Don't install anything or touch anything outside these two folders. With the network off, don't fetch anything: Windows PowerShell 5.1 and programs that open sockets themselves get past the block.
- <Commands not to run, and why, from the side-effect check. Write "None." if there are none.>
```

## Test writer

One per slice from `mutants.py slices`. Its sandbox comes from `mutants.py sandbox -w <run> write-S1 --slice S1`, which puts the slice's faults in `scratch/mutants/`.

````markdown
# Write tests that catch these faults

This project's tests pass. They would also pass with any of the faults below in the code: each one is a small change to one line, the kind of slip a person makes (`<` for `<=`, a condition turned around, a call left out, a value dropped). Nothing checks that behaviour yet. Write tests that fail when a fault is in and pass when it isn't, so the day someone makes that mistake for real, the tests catch it.

## The project

<What it is, who uses it, how its tests are organised and run. Facts only.>

## The faults

<N> faults, in <files>, mostly in <functions>. Each is in `<scratch>/mutants/mutants.md` as a diff: `-` is the line as it is, `+` the fault, with the lines around it.

To try a test against a fault, put the fault in, run the tests, then take it out:

```
python ../scratch/mutants/mutant.py M17          (from the project copy)
python ../scratch/mutants/mutant.py M17 --undo
python ../scratch/mutants/mutant.py --list       which faults are in now
```

Take every fault out before you finish. The project's own code must end up exactly as it started.

<sandbox block>

## How to write the tests

- **In the project's own style.** The same framework, folder, file naming, fixtures and assertion style as the tests already there. Add to the test file where such a test belongs, or start a new one named like its neighbours.
- **Through the public interface:** the function, class, command or endpoint a user calls. Reach inside only when nothing public reaches the line.
- **On behaviour a user would notice:** return values, output, files written, errors raised. Not log lines, timestamps or the exact wording of an error, unless that wording is the promise.
- **Steady:** no sleeps, no network, no dependence on test order, the clock, the time zone, the machine, or files outside the project copy.
- **Each test passes on the code as it is, every time, and fails with its fault in.** Check both, with the run command: the suite with the fault in, and the suite with it out. Run it a few times (`--repeat 3`).
- **Change nothing but tests.** If a test needs a helper or fixture, it goes with the tests.
- **Equivalent faults:** if no input or sequence of calls could ever tell a fault apart from the real code, it's equivalent. Say so with a one-line reason instead of writing a test. "Hard to test" isn't equivalent; "both branches return the same value when x equals lo" is.
- **A bug in the code:** if a test you're sure is right fails on the code as it is, the code may be wrong. Don't commit a failing test and don't fix the code; describe it in `notes`.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "tests": [
    {"file": "<the test file, relative to the project copy>", "name": "<the test's name>",
     "checks": "<what it checks, in a short plain sentence for a commit message, like 'an empty cart totals 0'>",
     "targets": ["M17", "M18"]}
  ],
  "equivalent": [{"mutant": "M21", "reason": "<why no input can tell them apart>"}],
  "notes": "<anything else: a fault you couldn't reach, a bug you think the code has>"
}
```

Leave your test files in the project copy; they're collected from there. Then end with one line: how many tests, how many faults they target, how many you called equivalent.
````

When a writer's tests fail the check and you send a fresh writer for the same slice, add this paragraph after "The faults", filled in from `check-test`'s verdict, and nothing about the first writer:

```markdown
An earlier attempt at these faults didn't hold up: <the script's FAIL reason, word for word, like "the new tests are flaky: the suite failed 1 of 3 runs on the real code" or "the tests pass on the real code but kill none of their targets (M5, M7)">.
```

## Equivalence judge

Thorough depth only: one per survivor a writer called equivalent. Make its sandbox with `mutants.py sandbox -w <run> judge-M21 --mutants M21 --bare`.

````markdown
# Can this change ever be seen?

Below is a line of code in a project and a changed version of it. Decide whether any input, or sequence of calls, makes the program behave differently with the change in: a different return value, output, file, exception or side effect. You're not writing tests, and you're not judging whether the difference would matter.

## The change

In `<file>`, line <n>, in `<function>`. The diff is in `<scratch>/mutants/mutants.md` (one change, <M21>). Put it in with `python ../scratch/mutants/mutant.py <M21>` from the project copy, and take it out with `--undo`.

<sandbox block>

You can read the whole project and run anything through the run command.

## How to work

- Read the code around the line, and every caller that reaches it.
- Look for an input that tells the two apart, and try it with the change in and out.
- If you find one, the verdict is `different`, with the input. If an honest search finds none, and you can say why none can exist, the verdict is `equivalent`.
- Take the change out before you finish.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{"verdict": "<equivalent | different>", "reason": "<why, in a sentence or two>",
 "input": "<for different: the input or calls that tell them apart, and what each version does>"}
```

Then end with one line: your verdict.
````

Record it with `mutants.py equivalent -w <run> M21 --sandbox judge-M21`. A `different` verdict with an input is worth a new writer: put the input in the writer's brief as a fact ("Calling clamp(10, 0, 10) behaves differently with the change in").

## Test reviewer

One, after the last tests are accepted. Make its sandbox with `mutants.py sandbox -w <run> review --combined`: the accepted tests are in its copy, and the same tests as one diff are in `scratch/tests.diff`.

````markdown
# Review new tests

Someone added the tests in `<scratch>/tests.diff` to this project; they're in the project copy too, and they pass. Find the ones that will cause trouble later. Don't rewrite them, and don't comment on style.

## The project

<What it is and how its tests run. Facts only.>

<sandbox block>

## What to look for

- **Timing:** sleeps, waits on the clock, timeouts that a slow machine would miss.
- **Order:** a test that only passes after another one, or that leaves state another one depends on.
- **The environment:** absolute paths, the time zone, the locale, line endings, the operating system, a tool that may not be installed, the network.
- **Incidental output:** asserting the full text of an error, a timestamp, the order of a set or dict, float formatting, a whole printed page when one value is the point.
- **The mock, not the code:** a test whose assertions only check what a mock or stub was told to return.
- **The wrong reason:** a test that passes even if the code it names is broken, or that duplicates an existing test.

Run them as much as you like: `--repeat 5`, a different `--cwd`, one test file on its own.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{"verdict": "<ok | problems>",
 "issues": [{"file": "<test file>", "test": "<test name>", "kind": "<timing | order | environment | incidental | mock | wrong-reason>",
             "problem": "<what's wrong, as something that would happen>", "fix": "<what would fix it>"}]}
```

Then end with one line: your verdict and how many issues.
````
