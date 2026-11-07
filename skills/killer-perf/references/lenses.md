# Lenses

Each finder looks for one kind of slowness across the whole scope. Looking by kind instead of by file catches the slowness that sits between files: a loop in one module calling something expensive in another. Recommend at most three, the ones whose "Offer it when" notes fit the project and the profile best; more finders mean more overlapping hypotheses, not proportionally more speed. The setup question has room for eight, in two groups of four. The user can add their own through "Other": turn it into a lens with a name, a "look for" list and a probe line in the same style.

Paste a lens, its "look for" list and its probe line into the finder brief as they are.

Let the profile choose. If it shows most of the time in one place, the lens that covers that place goes first, whatever the menu order.

## Startup and imports

**Menu:** Time spent before the real work begins.
**Lens:** everything that happens between the process starting and the first useful line of work.
**Look for:**
- heavy imports or requires at the top of a module that only one command uses
- work done at import time: reading config, compiling patterns, building tables, scanning folders
- plugin or entry-point discovery that walks every installed package
- start-up checks that hit the disk or the network every time (version checks, update checks, licence checks)
- interpreters started with options that turn off their caches (bytecode, JIT, module caches)
**Probe:** time the command with an argument that does almost nothing (`--version`, `--help`, an empty input). What's left is start-up. For Python, `python -X importtime` lists every import's cost; for Node.js, `--cpu-prof` on a near-empty run.
**Offer it when:** the target is start-up time or a command-line tool, or a run of the benchmark takes under a second.

## Repeated work and missing caching

**Menu:** The same thing computed or fetched more than once.
**Lens:** work whose result doesn't change but is done again: per item, per call, per request.
**Look for:**
- a value computed inside a loop that doesn't depend on the loop
- the same file, config or resource parsed several times in one run
- a function called with the same arguments many times, with no memoisation
- regular expressions, templates or queries compiled on every use
- lookups repeated where one pass could build an index
**Probe:** count calls. Add a counter or a print in your copy and run the benchmark; a function called 10,000 times with 12 distinct arguments is a lead. A cache must stay correct: say what invalidates it.
**Offer it when:** the profile shows a function with a high call count, or the same work appears under several callers.

## Algorithmic complexity

**Menu:** Work that grows faster than the input.
**Lens:** how the cost grows when the input doubles.
**Look for:**
- nested loops over the same data, or a loop that searches a list on every pass
- membership tests (`in`, `includes`, `indexOf`) on a list or array where a set or map would do
- strings built by repeated concatenation in a loop
- sorting inside a loop, or sorting when only the top few are needed
- deleting from the front of an array, or inserting into the middle, inside a loop
**Probe:** run the benchmark's code path on inputs of 1×, 2× and 4× the size in your copy. Time that doubles with the input is linear; time that quadruples is quadratic. Say which sizes you tried.
**Offer it when:** the work scales with an input (rows, files, names, records), or the profile shows one function's own time dominating.

## Input and output

**Menu:** Many small reads and writes, missing buffers, re-reading.
**Lens:** how the program moves bytes to and from disk, pipes and the console.
**Look for:**
- many small reads or writes where one buffered one would do; unbuffered streams
- the same file read more than once in a run
- flushing or syncing after every line or record
- printing or logging in a hot loop, especially to a console on Windows
- walking a folder tree repeatedly, or stat-ing the same paths again and again
**Probe:** count the system calls the code makes in your copy (wrap the open and write functions with a counter), or time the benchmark with its output sent to a file instead of the console. A flush or sync that guards against data loss on a crash isn't waste: leave it and say so.
**Offer it when:** the program reads or writes files, logs heavily, or the profile shows time in read, write, open, stat or flush.

## Process spawning and shelling out

**Menu:** Starting other programs, especially in a loop.
**Lens:** every place the code starts another process: a shell, git, a compiler, another script.
**Look for:**
- a subprocess started once per item where one call could take all the items
- a shell started just to run one program (`shell=True`, `exec` through `cmd` or `sh`)
- a program run to get a value that doesn't change during the run (a version, a path, a config value)
- interpreters started inside a loop to run a few lines
**Probe:** count the processes the benchmark starts (wrap the spawn function in your copy, or log each call). On Windows each one costs tens of milliseconds before it does anything.
**Offer it when:** the code calls subprocess, exec, spawn, system, os.popen or backticks, or the profile shows time waiting on child processes.

## Waits, sleeps, polling and timeouts

**Menu:** Time spent waiting on purpose.
**Lens:** every place the program waits: sleeps, polls, retries, timeouts, debounces.
**Look for:**
- fixed sleeps between checks of something that could be waited on directly (an event, a file, a process exit)
- polling intervals much longer than the thing usually takes
- retries with back-off on the happy path, or a timeout hit on every run
- waiting for a fixed time instead of for the condition
**Probe:** log each wait and how long it lasted in your copy. A wait the program needs (a rate limit, a lock held by someone else, a debounce users rely on) isn't waste: removing it changes behaviour. A wait for a condition that's usually already true is.
**Offer it when:** the code calls sleep, setTimeout, wait, retry or poll, or the profile shows time in sleep or in waiting on locks or events.

## Serial work that could overlap

**Menu:** Independent work done one at a time.
**Lens:** tasks that don't depend on each other but run in sequence.
**Look for:**
- independent disk or network calls made one after another
- per-file or per-item work with no shared state, done in a single loop
- a pipeline whose stages wait for the whole previous stage to finish
**Probe:** check the tasks really are independent: what each reads and writes, and whether the order of the output depends on the order they run in. Overlapping them must keep the output's order and its error behaviour exactly as they were. Say how many CPUs this machine has; a gain on 16 cores may be a loss on 2.
**Offer it when:** the work is a list of independent items, or the profile shows long waits with the CPU idle.

## Memory churn and copying

**Menu:** Allocating, copying and converting more than needed.
**Lens:** data copied, converted or allocated again and again.
**Look for:**
- whole lists, strings or buffers copied where a view, slice or iterator would do
- data converted back and forth between formats (bytes and text, JSON and objects) in a loop
- large structures built only to be read once and thrown away
- garbage-collector time showing in the profile
**Probe:** measure peak memory in your copy (`tracemalloc` in Python, `process.memoryUsage()` in Node.js) alongside the time. Less copying must not mean sharing something a caller later changes.
**Offer it when:** the program handles large data, the profile shows the garbage collector or allocation, or memory is the target.
