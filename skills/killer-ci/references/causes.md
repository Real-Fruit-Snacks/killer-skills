# Causes and fixes, by condition

What each condition usually breaks, what the fix looks like, and what only hides it. Paste the entries for a failure's conditions into its diagnoser's brief, as they are. "Not a fix" lists what `ci.py candidate add` refuses, and what it can't see but you should.

## Paths: shortpath

**Imitates:** GitHub's Windows runners keep the temp folder under an 8.3 short name (`C:\Users\RUNNER~1\AppData\Local\Temp`). On macOS, `$TMPDIR` is under `/var`, which is a symlink to `/private/var`. Elsewhere the condition reaches the copy through a symlink.

**Breaks:** code that makes one path relative to another, or compares two, when one side has been resolved and the other hasn't. git reports its top folder resolved (`git rev-parse --show-toplevel`); so do `os.path.realpath`, `Path.resolve()`, `fs.realpathSync.native()`, Go's `filepath.EvalSymlinks`, and on POSIX the current folder itself (`os.getcwd()`, `process.cwd()`). `TEMP`, `TMPDIR`, `tempfile.mkdtemp()` and paths a user types are as given. Typical errors: `ValueError: ... is not in the subpath of ...`, "outside repository", a file "not found" in a set of known paths, a check that a folder isn't inside another that passes when it shouldn't.

**Fix:** resolve both sides before comparing or making one relative to the other (Python `Path(p).resolve()`, Node `fs.realpathSync.native()`, which also handles a path that doesn't exist yet if you resolve its nearest existing parent). In tests, pass paths in as `mkdtemp()` gives them, and compare against the resolved form or accept either.

**Not a fix:** skipping on Windows or macOS, replacing `/private` or `~1` in strings, a `sys.platform` branch around the comparison.

## Paths: spaces

**Imitates:** a checkout or temp folder whose path has spaces and non-ASCII letters (a user folder like `C:\Users\Zoë Smith`).

**Breaks:** commands built as strings and split on spaces (`shell=True`, `os.system`, Node's `exec`, Makefiles, `.split(" ")`), unquoted paths in shell scripts, file paths turned into URLs without encoding, and printing a path to a console that isn't UTF-8 (`UnicodeEncodeError` on Windows' cp1252).

**Fix:** pass commands as argument lists; quote paths in shell (`"$path"`); encode file URLs (`Path.as_uri()`, `url.pathToFileURL`); write text with an explicit `encoding="utf-8"`.

## Paths: longpath

**Imitates:** a deep checkout on Windows, where paths over 260 characters fail unless long paths are turned on (in the registry and in the program).

**Breaks:** deeply nested fixtures or generated folders: `FileNotFoundError`, `[WinError 206] The filename or extension is too long`, git's "Filename too long". If `ci.py conditions` says long paths are turned on here, only programs that aren't long-path aware notice.

**Fix:** shorter generated paths (hash long names), creating test folders under the temp folder rather than deep in the tree, `git config core.longpaths true` for tools that clone.

## Time zones: tz

**Imitates:** hosted runners run in UTC; developers rarely do. Pacific/Kiritimati (UTC+14) moves every time after 10:00 UTC to the next day; America/St_Johns (UTC−3:30, with summer time) catches code that assumes whole-hour offsets.

**Breaks:** dates taken in local time (`date.today()`, `datetime.fromtimestamp(ts)` without `tz`, `new Date().getDate()`, Go's `time.Local`), comparisons of naive and aware datetimes, "midnight" boundaries, expected strings formatted in the developer's zone.

**Fix:** aware datetimes in UTC (`datetime.now(timezone.utc)`, `fromtimestamp(ts, tz=timezone.utc)`), formatting with an explicit zone, tests that set the zone they mean.

**Not a fix:** setting `TZ` in the CI config, or a test that only passes when the machine is in UTC.

## Locales: locale

**Imitates:** `LANG` and `LC_ALL` as a runner or a user sets them. Windows ignores them for most programs; on Linux a locale that isn't installed falls back to C, and `ci.py conditions` says so.

**Breaks:** locale-aware formatting and parsing (`locale.format_string`, `atof`: `1,5` in de_DE), case changes in Turkish (`I` lowercases to `ı` with `toLocaleLowerCase`, Java's `toLowerCase()` with no locale), sorting with collation (`sort`, `localeCompare`), and default file encodings taken from the locale.

**Fix:** locale-independent APIs for anything a machine reads (`str.lower`/`casefold`, `toLowerCase()` with `Locale.ROOT`, plain `float()`), explicit encodings, `LC_ALL=C.UTF-8` for tools whose output a test reads.

## Line endings: crlf, lf

**Imitates:** Git for Windows turns `core.autocrlf` on, so Windows runners check text files out with CRLF; Linux and macOS runners use LF. A developer on Windows with autocrlf off (or on Linux) never sees CRLF.

**Breaks:** tests that compare file bytes with expected strings, snapshot files, hashes of checked-in files, parsers that split on `"\n"` and leave `"\r"`, and shell scripts checked out with CRLF (`/bin/sh^M: bad interpreter`).

**Fix:** a `.gitattributes` that says what each kind of file is (`*.txt text eol=lf`, `*.sh text eol=lf`, `*.bin -text`); reading text in text mode, or normalising line endings before comparing; writing files with an explicit newline.

**Not a fix:** `git config core.autocrlf false` as a CI step: the next contributor on Windows hits it again.

## Shallow clones: shallow

**Imitates:** `actions/checkout` fetches one commit and no tags unless told otherwise (`fetch-depth: 0`).

**Breaks:** `git describe` ("No names found"), versions from git tags (setuptools-scm, versioneer, `git describe` in a build script), commit counts, `git merge-base` with main, changelog generators.

**Fix:** code that works without tags (a fallback version, `git describe --always`), or a version that isn't taken from git. When the project genuinely needs history to build, `fetch-depth: 0` in the workflow is the fix, but it changes the CI config, so the scan refuses it and the user decides (`ci.py decide --allow`).

## Test order: order

**Imitates:** runners that shuffle or reorder tests, and a new test added before an old one.

**Breaks:** tests that rely on another test having run first (a module-level list, a cache, a singleton), state one test leaves behind (environment variables, the current folder after `os.chdir`, patched functions, files), and class or module fixtures that do more than set up.

**Fix:** each test sets up what it needs (`setUp`, fixtures), and puts back what it changes (`addCleanup`, `tearDown`, `monkeypatch`); no state shared between tests through module globals.

## Hash seeds: hashseed

**Imitates:** Python gives every process a random hash seed, so the order of a set or dict of strings changes from run to run. A test that depends on it fails at random, often about half the time.

**Breaks:** output built by iterating a set (`", ".join(names_set)`), JSON from a set, `hash()` of a string in expected output, "first item" of a set.

**Fix:** sort before output or comparison; use a list or a dict (which keeps insertion order) where order matters.

## Shared state between runs

**Shows as:** a test that fails only on the second or later run in the same copy (`hunt` without `--fresh`), or only when another test ran first.

**Breaks:** temp files at fixed paths, fixed ports, files left in the project, a database or cache not cleaned up.

**Fix:** a unique temp folder per test, port 0 (let the OS choose), cleanup in `addCleanup` or a fixture.

## Timing: load

**Imitates:** a shared runner that's slower and busier than this machine. macOS runners stretch a 30 ms sleep past 100 ms, unevenly.

**Breaks:** fixed sleeps that wait for another thread or process to finish, timeouts tuned on a fast machine, assertions on how long something took, polling with a short overall deadline, races.

**Fix:** wait for the event itself (`Event.wait`, `join`, a condition variable, polling for the state with a generous overall deadline in place of a fixed sleep), assert on the outcome rather than the duration, and decide timing tests by something counted (a fake clock, the number of calls) rather than by the clock.

**Not a fix:** a longer sleep, a bigger timeout, a retry or rerun. They make the failure rarer, which makes it harder to find later, not gone.

## CI variables and terminals: ci-env

**Imitates:** `CI=true` and `GITHUB_ACTIONS=true`, `TERM=dumb`, `NO_COLOR=1`, and no terminal (every sandbox gives the tests an empty stdin).

**Breaks:** output that changes without colour or a terminal (snapshot tests, `isatty()` checks), prompts that wait for input and get end-of-file, tools that behave differently when `CI` is set (stricter linters, no progress bars, different defaults).

**Fix:** tests that set colour and terminal behaviour explicitly, and never prompt.

**Not a fix:** the project's code doing something else when `CI` is set.

## Read-only checkouts: readonly

**Imitates:** builds whose source tree is read-only (packaging, sandboxed build systems, some container setups). The `.git` folder stays writable, and on Windows a file that exists can still be deleted.

**Breaks:** tests that write outputs, caches or reports into the project or next to `__file__`.

**Fix:** write into a temp folder (`tempfile.mkdtemp()`, pytest's `tmp_path`, `os.tmpdir()`).

## File modes: umask

**Imitates:** a runner or container with umask 077 (POSIX only).

**Breaks:** tests that check a file is group- or world-readable, and files another process must read.

**Fix:** set the mode the file needs explicitly (`os.chmod`, `open(..., mode)` with `os.open`) when it matters; don't assert on bits the umask decides.

## The network: network

**Imitates:** a runner with the network on. Every sandbox has a dead proxy by default, so a test that reaches the network fails in every copy, baseline included.

**Breaks:** tests that call real services (and fail on CI with rate limits or outages).

**Fix:** a fake or recorded service, or a local server on 127.0.0.1.

## What no condition recreates

Say so in the report rather than guessing:

- another version of the language or a tool (`ci-read` lists them): install it and start a run with it;
- a case-sensitive file system (Linux) against a case-insensitive one (Windows, macOS by default): an import or path that differs only in case works here and fails there. Check file names and imports by hand;
- a container image, services the workflow starts, other hardware, fewer CPUs or less memory than this machine.
