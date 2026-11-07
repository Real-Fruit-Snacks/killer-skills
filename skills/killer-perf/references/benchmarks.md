# Writing a benchmark

A benchmark is one command, run from the project copy, that does the thing the user wants faster. `perf.py` times the whole process from start to exit, or reads a number the command prints. Everything else in the run rests on it, so spend the time here.

## Pick the command users wait on

- **A command-line tool:** its most common real invocation, on a realistic input. `mytool convert samples/big.csv`, not `mytool --help`, unless start-up is the target.
- **The test suite:** the project's own test command. Slow tests are usually a few slow files; a second benchmark for the slowest one makes gains easier to see.
- **Start-up:** the command with an argument that does almost nothing (`--version`, an empty input).
- **A function or a page:** a small script that calls it the way callers do, many times, and prints how long the calls took as a `METRIC` line (below). Process start-up then doesn't drown the gain.
- **A build:** the build command, from a clean state each time: the setup step cleans, or the command does.

One to three benchmarks. Each one multiplies the time every comparison takes.

## Make it repeatable

- **The same input every time.** Commit the input to the project, or generate it from a fixed seed in the setup step. Protect it with `bench add --protect <path>` so no candidate can shrink it.
- **No network.** With the network off, a benchmark that needs a service needs a local stand-in: a fixture file, a recorded response, a server the setup step starts on this machine. If the user chose to allow the network, say in the report that the numbers include it.
- **No state carried between runs**, or the same state every run: a cache the first run fills makes later runs faster for both sides alike, which is fine; a database that grows with every run isn't. The warm-up runs absorb one-off costs like compiled bytecode.
- **A build step goes in `--setup`**, run once per copy before any timing, so it isn't timed.

## Make the output check mean something

The output check compares each run's standard output with the unchanged project's, with timings (numbers followed by `ms`, `s`, `µs` or `ns`) and `METRIC` lines masked. It's half of what stops a fast but wrong change.

- **If the command writes files instead of printing,** end the benchmark with a line that prints a hash of them, so a change to the files changes the output.
- **If part of the output varies legitimately** (a timestamp, a temp path, a random id), mask it with `--output-normalise '<regex>'`. Mask the varying part only, not the whole line.
- **If the output can't be made stable,** `--no-output-check --why "<reason>"`. It's recorded under "Decisions to check", and the tests are then the only guard.

## Print a METRIC when wall time is the wrong measure

A line `METRIC: <name>=<number> [unit]` on standard output gives `perf.py` a number to decide on instead of the process's wall time: the time spent in the part that matters, peak memory, requests per second. With one `METRIC` name, it decides; with several, choose with `--metric <name>`. Use `--higher-is-better` for throughput.

## Very fast and very slow commands

- **Under 50 ms a run:** process start-up dominates. `bench add` loops the command for you (`--inner N`) and says so, but a gain inside a few milliseconds of work still won't show against the start-up's noise. Use a bigger input, or a `METRIC` that times the work itself.
- **Over 2 minutes a run:** `bench add` cuts it to 6 runs each, the fewest that can reach p < 0.01, and gives the time a comparison will take. Tell the user before starting. A smaller input that still exercises the slow path is usually the better fix.

## When the noise test fails

`bench noise` runs the unchanged project against itself. A band over ±10% means the machine or the benchmark is too noisy to see a real change. In order of what usually helps:

1. Close other heavy work on the machine (builds, other benchmarks, a busy browser), and plug in a laptop.
2. More warm-up (`bench noise --warmup 3`) when the first runs are slow.
3. More runs (`bench noise --repeat 30`).
4. A larger input, so each run is longer and the jitter smaller in proportion.

Then run `bench noise` again. Nothing is compared on a benchmark until its noise test is clean.
