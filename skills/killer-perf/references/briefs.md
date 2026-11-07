# Briefs

Every agent gets a brief built from one of the templates below. Fill in the parts in angle brackets and keep the rest word for word. Fixed wording keeps agents comparable, and keeps your hopes out of their work.

What stays out of every brief:

- the run folder, `perf.py`, and anything in them
- other agents: how many there are, what they found, whether anyone else is on the same idea
- the finder's reasoning, when you brief an optimiser, and the optimiser's account of its change, when you brief a challenger: they get the claim, never the argument for it
- the threshold, the noise band, any verdict, and what you hope the answer is

Every agent writes its full answer to `reply.json` in its scratch folder, and `perf.py` reads it from there. Its final message to you is a line or two, so replies don't pile up in your context.

## The facts block

Every brief has one, written once per run and pasted unchanged:

```markdown
## The project

<What it is, who uses it, and how. The language and runtime. The commands that build it and run its tests, written for the run command. Facts only: no guesses about where it's slow.>
```

## The sandbox block

Every brief includes this block, filled in with what `perf.py sandbox` printed for that agent:

```markdown
## Your copy of the project

- **Project copy:** `<path>`. Read anything in it.
- **Run the project's code only through:** `<run command>`. It runs from the project copy with its own home and temp folders, no credentials, and <"the network off (this machine's own addresses still work)" | "the network on">. Add `--repeat N` to run it several times, `--cwd <subfolder>` to run from a subfolder, `--timeout <seconds>` to cap a run. Each run's output is saved under `logs/` next to the scratch folder.
- **Scratch folder:** `<path>`. Put every file you create here, apart from changes to the project's own files.
- Don't install anything or touch anything outside these two folders. With the network off, don't fetch anything: Windows PowerShell 5.1 and programs that open sockets themselves get past the block.
- This machine is busy with other work, so timings you take are rough. That's expected: they only tell you which way things go.
- <Commands not to run, and why, from the side-effect check: "Don't run install.ps1 without KILLER_SKILLS_URL pointing at a local folder: it writes your PATH to the registry." Write "None." if there are none.>
```

## Finder

````markdown
# Find what makes it slow: <lens name>

You're looking for why a project is slow, through one lens: **<lens name>**. <The lens, from lenses.md.>

Report the causes you'd bet on, biggest first, at most six. Each must be something in the code that makes the benchmark below take longer than it needs to, with the behaviour kept exactly as it is. A style preference isn't one, and neither is a speedup that skips work, lowers precision or changes a default.

## What should get faster

<The target, in one line.> It's measured by <this benchmark | these benchmarks>, run from the project copy:

- **<name>:** `<command>` (<wall time | METRIC name>, lower is better; one run takes about <time from perf.py list>)

<"A profile of <name> is in your scratch folder as <file>. It shows where one run spent its time; it's evidence, not a conclusion." | "There's no profile for this kind of program.">

<the facts block>

**In scope:** <the files or folder>. Read these first, and follow calls into other files when you need to.

<the sandbox block>

Your copy is yours alone and gets thrown away. Change it as much as you like to experiment: add timers and counters, try a change, run anything through the run command.

## What to look for

<The lens's "Look for" list, from lenses.md.>

**Probing:** <the lens's "Probe" line>

## How to work

- Start from the evidence: the profile, then the code on the hot path. A guess about speed is often wrong, so check it.
- **You may prototype.** Make the change in your copy and time the benchmark through the run command, a few runs each way. Your timing is rough and doesn't count; it tells you whether the idea is worth handing on. Say what you saw.
- Every cause you report must be removable without changing behaviour: the same output, the same files written, the same errors, the same results on every input. If removing it would change any of those, say so under `risk`, or leave it out.
- List every file you actually read in `covered`.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "hypotheses": [
    {
      "title": "<short name>",
      "claim": "<what in the code makes it slow, as something checkable: 'dedupe() in names.py tests membership in a list, so it's quadratic in the number of names'>",
      "where": ["<path:line>"],
      "why": "<your evidence: the profile line, the counts, the timing you saw>",
      "benchmark": "<the benchmark it affects>",
      "expected_effect": "<how much of that benchmark's time it could save, in words>",
      "effect_pct": <your guess at the percentage saved, a number, or null>,
      "how_to_check": "<how an optimiser could confirm it before changing anything>",
      "prototype": "<what you tried in your copy and the rough timing, or null>",
      "risk": "<behaviour a change here could alter, or null>",
      "confidence": "<low | medium | high>"
    }
  ],
  "covered": ["<path of each file you read>"]
}
```

An empty `hypotheses` list is a fine answer if this lens finds nothing worth changing. Then end with one line: how many hypotheses, and the biggest expected effect.
````

## Optimiser

One per hypothesis, in its own sandbox made with `perf.py sandbox -w <run> opt-H3` (never with `--candidate`). Fill "The cause" from `perf.py hypothesis -w <run> H3`: the claim, where, and the expected effect, nothing from "for the lead only".

````markdown
# Make one change that makes it faster

A cause of slowness has been reported in this project. Your job is to remove it with one change, the smallest that does it, without changing what the project does.

## The cause

- **Claim:** <claim>
- **Where:** <where, or "Not given.">
- **Expected effect:** <expected effect, or "Not given.">

## How it's measured

- **Benchmark:** `<command>`, run from the project copy (<wall time | METRIC name>, lower is better).
- **Tests:** `<test command>`. They must pass after your change. <Or: "The project has no tests. The benchmark's output is the only check, so be twice as careful.">

<the facts block>

<the sandbox block>

## The rules

- **Same outputs.** The benchmark's output must stay the same apart from timings, and so must anything else the project prints, writes, returns or raises, on every input, not just the benchmark's.
- **The tests pass.**
- **The smallest change that does it.** One idea, not a tidy-up. Leave formatting, names and nearby code alone.
- **No behaviour change:** no lowered precision, no skipped work, no changed defaults or limits, no removed checks, validation or error handling, no caching that can serve a stale value, no change to output order.
- **No new dependencies**, and no changes to dependency files.
- **Leave the tests, the benchmarks and their inputs alone:** <the protected paths: tests, benchmark scripts, fixtures, dependency files>. A change there is refused.
- **Keep each file's line endings and encoding as they are.**
- **Write it as the project's own author would.** No comments or notes about who or what made the change, or about this task.
- **If the cause isn't real, or can't be removed without changing behaviour, change nothing and say why.** That's a useful answer, and better than a change that bends a rule.

## How to work

1. Run the benchmark a few times through the run command, and the tests, as they are. Keep the benchmark's output.
2. Confirm the cause is where the claim says. If it isn't, stop and say so.
3. Make the change.
4. Run the tests, and the benchmark a few times. Compare the output with what you kept: it must match apart from timings. Your timing is rough; it only has to point the right way.

Don't commit anything. Leave the change in the copy: it's collected from there.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "done": true,
  "title": "<a commit subject for the change: what it does, in the imperative, under 70 characters>",
  "mechanism": "<why it's faster, in one or two plain sentences, as you'd write in a commit message>",
  "files": ["<each file you changed>"],
  "rough_timing": "<before and after, how many runs each; rough>",
  "risks": "<anything that might behave differently, or null>",
  "reason": "<only when done is false: why you made no change>"
}
```

Then end with one line: done or not, and the rough timing.
````

## Challenger

One per candidate that came out faster, in a sandbox made with `perf.py sandbox -w <run> chal-C3 --candidate C3`, which has the change applied. Paste the diff from `perf.py candidate show -w <run> C3` (without `--with-mechanism`).

````markdown
# Challenge a speedup

Below is a change to a project, and a claim: **it makes <the benchmarks> faster without changing behaviour.** The speed has been checked. Your job is to find behaviour it changes that the project's tests miss.

## The change

```diff
<the diff>
```

<the facts block>

<the sandbox block>

The copy has the change applied, uncommitted: `git diff` shows it, and `git stash` takes it out so you can compare with the unchanged version (`git stash pop` puts it back). Read anything: code, docs, tests, the history.

## Where changes like this go wrong

- **Caches** that serve a stale value when what they depend on changes: a file edited, a setting changed, a second call with different input
- **Threads and processes:** shared state, a race, ordering between workers, work that isn't waited for
- **Error paths:** an error that was raised, logged or made the program exit non-zero, and now isn't, or happens later, after something was written
- **Ordering:** iteration order, sort stability, the order of output lines or files
- **Platforms:** Windows paths and drive letters, line endings, case-insensitive file systems, locale and encoding
- **Large and odd inputs:** memory that grows without limit, recursion depth, empty input, duplicates, non-ASCII text, huge files
- **Side effects:** files written, flushed or synced differently; partial results left behind on a crash

## Reproductions

If you find a behaviour change, write a reproduction: a small script in the scratch folder that prints `EXPECTED:` with what the unchanged project does and `OBSERVED:` with what the changed one does, then exits 1 when the change shows and 0 when it doesn't. Run it from the project copy through the run command, reaching the scratch folder by a relative path like `../scratch/repro.py`. It will be rerun without you, on a fresh copy with the change and on one without it: it must fail with the change and pass without it. A concern you can't reproduce can still be reported, as a question.

## Your answer

Write this JSON to `reply.json` in the scratch folder:

```json
{
  "verdict": "<stands | question | breaks>",
  "concerns": [{"what": "<the behaviour that might change>", "where": "<path:line>", "severity": "<high | medium | low>"}],
  "question": "<for question: what the project's owner has to decide, in one sentence>",
  "command": ["<program>", "<argument>"],
  "expected": "<what the unchanged project does>",
  "observed": "<what the changed one does>"
}
```

`stands`: you looked hard and found no behaviour change the tests miss. `breaks`: you found one; give the reproduction in `command` if you have one, or `null`. `question`: it changes something that might be fine, and only the owner can say (a default nobody documented, an order nobody relies on). Then end with one line: your verdict.
````

Leave the optimiser's account of the change out of this brief. The diff has to stand on its own.
