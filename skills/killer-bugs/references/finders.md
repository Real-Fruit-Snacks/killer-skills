# Finders and kinds of bug

Two kinds of agent find bugs on every hunt: the **promise checker** (its own brief in `briefs.md`), which tests what the docs promise without reading the code, and the **read-through** below, one per slice of the code.

The eight **kinds of bug** after it are checklists, not agents. Each read-through gets the "Look for" list and the probe of every kind the user chose, under its own heading in the brief, and checks them all as it reads its slice. Reading is most of a hunt's cost, and one agent per kind would re-read the same code eight times. Two exceptions get their own agent:

- **Pairs that should agree,** when the project has pairs. Comparing two sides means running both systematically, which a reader won't do in passing. Name its sandbox `pairs`.
- **Every chosen kind, on a thorough run.** One agent per kind, across the whole scope, named `find-<kind>`, using the finder brief with just that kind.

The setup question has room for eight kinds, in two groups of four. Mark "(Recommended)" the ones the "Recommend it when" notes fit; since kinds no longer cost agents, recommend every one that fits, but not ones that don't, because a long checklist of irrelevant kinds dilutes the reader's attention. The user can add their own through "Other": turn that into a kind with a name, a lens and a short "Look for" list in the same style.

Paste each kind's lens, "Look for" list and probe into the briefs as they are.

## Read-through

**Always runs.** Split the scope into slices by folder, about 40 files or 5,000 lines at most, and send one read-through per slice. A small project is one slice.
**Lens:** every file in your slice, top to bottom, as a careful reviewer reads it, with the chosen kinds of bug as your checklist.
**Look for:**
- for each function: what input or state makes it do the wrong thing, and what it does when something it calls fails
- conditions that are off by one, inverted, or check the wrong thing; patterns that match too much or too little
- what the code claims (its names, comments, messages, docs) against what it does
- anything a caller can reach that loses or overwrites data, leaks a secret, or reports success after failing
**Probe:** for each suspicion, try it in your copy before reporting it.
**Recommend it when:** always.

## Failures

**Menu:** What happens when a step fails partway.
**Lens:** work that happens in steps, and what's left when a step in the middle fails.
**Look for:**
- several steps where a later one can fail after earlier ones changed things: files copied before the index is written, an old folder deleted before the new one is in place, validation done after writing
- errors caught and dropped, or turned into success; a zero exit code after a failure
- clean-up that doesn't run on the error path
- retries that repeat side effects
- error messages that blame the wrong thing, or hide the real cause
**Probe:** make one step fail. Use bad input in the middle of a list, a file that can't be written, a missing dependency, or a run killed partway with `--timeout`. Then look at what was left behind.
**Recommend it when:** always.

## Bad input

**Menu:** Empty, huge, odd or malformed input.
**Lens:** input the author didn't picture: from users, files, arguments and the environment.
**Look for:**
- empty strings, lists and files; very long input; zero, negative and very large numbers
- spaces, quotes, non-ASCII characters, line breaks and leading dashes in names, paths and arguments
- relative paths against absolute ones, paths that don't exist, a file where a folder is expected
- duplicates, missing fields and wrong types in structured input
- parsing that only works for the usual case: patterns that match too much or too little, splitting on a character that can appear in the value
**Probe:** write a small table of awkward values and run each through the real entry point.
**Recommend it when:** always.

## Re-runs and state

**Menu:** Running twice, upgrades and leftovers.
**Lens:** state that outlives a run: files, caches, markers, settings, databases.
**Look for:**
- running the same thing twice gives a different result than running it once
- upgrading from an older version's files, settings or cache
- leftovers from a crash or an interrupted run: lock files, half-written files, stale markers
- temp files that are never removed
- state shared between runs, users or projects that shouldn't be
**Probe:** run twice and compare the state. Make the leftovers a crash would leave, then run again. To test an upgrade, run the older version's copy the brief names (the lead makes it with `sandbox --rev`) to produce its state, then run this version on that state.
**Recommend it when:** the project writes files, caches, settings or any state that lasts between runs.

## Concurrency and files in use

**Menu:** Two at once, locks and open files.
**Lens:** things happening at the same time.
**Look for:**
- two copies running at once that collide on output paths, temp names or ports
- files another program has open: Windows can't rename or delete them
- check-then-act gaps: the file existed when checked and is gone when used
- async work nobody waits for; shared globals; timeouts that leave work half done
**Probe:** start two copies at once from a script in the scratch folder. Hold a file open from a helper process while the code runs.
**Recommend it when:** the project runs servers, background jobs, parallel work, or touches files other programs may have open.

## Platforms and versions

**Menu:** Windows, Linux, macOS, shells and runtime versions.
**Lens:** the same code on a different system or version.
**Look for:**
- path separators, drive letters, case-insensitive file systems, path length limits
- line endings and text encodings, including byte-order marks and the system code page
- shells and their versions: Windows PowerShell 5.1 against PowerShell 7, sh against bash, GNU tools against BSD ones
- the oldest runtime version the project says it supports
- environment variables and folders that differ between systems
**Probe:** run under each shell and runtime installed here. When a version the project supports isn't installed, say so and lower your confidence; don't guess.
**Recommend it when:** the project has shell scripts, claims to run on more than one system, or supports several runtime versions.

## Pairs that should agree

**Menu:** Things that should behave the same.
**Lens:** two ways of doing one job, compared.
**Look for:** whatever pairs the lead lists in the brief, such as:
- two installers for one product
- one script under two shells
- a command-line tool and the library it wraps
- the last release against this version, for behaviour that wasn't meant to change
- save against load, encode against decode
- a fast path against a slow path
**Probe:** put the same input through both sides and compare outputs, exit codes and side effects. Any difference is a candidate. The challenger decides which side is wrong.
**Recommend it when:** the project has such pairs. List them in the brief.

## Tests that prove nothing

**Menu:** Tests that pass even when the code is broken.
**Lens:** the project's own tests, and whether they'd notice a bug.
**Look for:**
- tests that assert nothing, or only that nothing was raised
- assertions about mocks instead of behaviour
- tests that never run: skipped, not collected, or named so the runner misses them
- important behaviour no test touches
**Probe:** break the code on purpose in your copy, one small change at a time. Flip a comparison, return early, drop a line. Then run the tests. A change no test notices is a candidate of kind `test-gap`, with the change as its trigger. If the project's test runner isn't installed here (and installing isn't allowed), say so, then work it out by reading: for each change, find the assertion that would catch it, and report the ones with none, at low confidence.
**Recommend it when:** the project has tests.

## Security and untrusted input

**Menu:** Input from outside reaching places it shouldn't.
**Lens:** data from outside the program (users, files, archives, the network) and where it ends up.
**Look for:**
- shell, SQL or path injection
- archive entries or user paths that write outside the target folder
- unsafe deserialisation or eval
- secrets in logs, error messages or files other users can read
- downloads run without checking them; certificate checks turned off
**Probe:** craft the input and show its effect inside the sandbox: a file written outside the target folder, a command that ran. Never against a real service.
**Recommend it when:** the project takes input from outside, builds commands or queries from it, unpacks archives, or handles credentials.

## The final pass (after fixes)

Not on the menu. One finder runs over the combined diff of all fixes, using the finder brief with this lens.

**Lens:** recent changes, and what they broke.
**Look for:**
- callers of changed code that now get something different
- cases the old code handled and the new code doesn't
- the same mistake left nearby
- tests that changed so they would pass rather than to check the fix
