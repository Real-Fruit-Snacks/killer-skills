# killer-skills

Claude Code skills that do the whole job, then prove it

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/cover-dark.svg">
  <img alt="install.sh in a terminal: installed killer-bugs, killer-goal, killer-release and killer-tests among them, 11 skills in ~/.claude/skills" src="docs/assets/cover-light.svg" width="100%">
</picture>

```powershell
irm https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.ps1 | iex
```

```sh
curl -fsSL https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.sh | sh
```

The skills I reach for when I want a job done properly rather than quickly. Each one does the whole job, then checks its own work with something a script runs, not an agent's word: killer-design screenshots the site it built, killer-bugs reruns every bug in a fresh copy before reporting it, killer-tests keeps a test only if it catches a fault planted in the code, killer-perf counts a speedup only when it beats the measured noise, and killer-release isn't done until the built release installs from scratch. killer-goal takes a goal in your words, pins down what "done" means before any work starts, and runs the others through the night on a branch of its own. They run in Claude Code and need Node.js or Python; on Windows the installer brings both when they're missing.

## What's in it

| Skill | Does | Download |
|---|---|---|
| `killer-goal` | Takes a goal in your words and sees it through overnight: done-when checks locked before work starts, a plan of other killer skills and direct work on its own local branch, a script that reruns the checks and guards your branch after every step, and a blind judge that has to agree before it calls the goal met | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-goal.zip) |
| `killer-design` | Designs and builds a website in hand-written HTML and CSS, then screenshots it at phone, tablet and desktop sizes and fixes what it sees | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-design.zip) |
| `killer-slides` | Builds a presentation as one self-contained HTML file with speaker notes and a presenter view, measures every slide in the real render for overflow, small text, contrast and clutter, then has a blind agent who sees only the slide images say what the talk argues | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-slides.zip) |
| `killer-repo` | Rebuilds a repo page around one true wow moment: README, description, topics, social card and community files, rendered as GitHub shows them | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-repo.zip) |
| `killer-council` | Sends blind reviewer agents to score work from 1 to 100, fixes what they find and reconvenes until it passes your threshold | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-council.zip) |
| `killer-bugs` | Reads each slice of the project once with every chosen kind of bug in mind, has the worst suspects made to happen in a sandboxed copy and challenged before they're reported, then fixes what's proven if you want, each with a test that fails without the fix | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-bugs.zip) |
| `killer-tests` | Plants small faults in sandboxed copies of your code to find what the tests miss, has blind writer agents add tests for those gaps, and keeps a test only when it passes on the real code and fails on its fault, then gives the mutation score before and after | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-tests.zip) |
| `killer-docs` | Runs every command and code sample in the docs in a sandboxed copy of the project and checks the output they show, checks `--help` and links against what the docs say, then has blind newcomer agents follow the docs to install and use it; fixes the docs on a local branch and has a fresh newcomer walk them again | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-docs.zip) |
| `killer-perf` | Makes code measurably faster: blind finder agents look for one kind of slowness each, optimisers make one change each in a sandboxed copy, and a script times every change against the unchanged code, interleaved, behind the tests and an output check. Only gains its confidence interval and significance test prove get committed, with their numbers | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-perf.zip) |
| `killer-upgrade` | Brings dependencies up to date in batches, proving each upgrade with the project's own build and tests in a sandboxed copy. When a batch breaks, it finds the one upgrade (or the pair) that broke it, sends migrator agents to fix the code for major versions, and lands each proven step as a commit on a new local branch | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-upgrade.zip) |
| `killer-release` | Gets a release right the first time: bumps every place the version appears, has a blind reviewer check the changelog against what changed, builds in a clean copy, then installs the built files from scratch in an empty sandbox with the network off, and isn't ready until the installed tool prints the new version. Tags, pushes and uploads wait for your OK | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-release.zip) |

In Claude Code, type a skill's name as a command (`/killer-repo`) or ask in plain words ("make this README better", "get this ready for 2.0 overnight"). Every skill but killer-design opens with a few setup questions, so there are no options to remember; killer-design reads the project first and asks only what it can't work out. Run by killer-goal, the others take their answers from its plan and don't stop to ask.

Each skill is its own download. Name one to install just that skill:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.ps1))) killer-repo
```

```sh
curl -fsSL https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.sh | sh -s -- killer-repo
```

Or unzip a download into `~/.claude/skills` yourself; [killer-skills.zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-skills.zip) has all eleven. The installers check every archive against the release's `SHA256SUMS.txt` before they replace anything, and replace an installed skill whole, so files an older version had don't linger.

| Skill | Asks you | Needs |
|---|---|---|
| `killer-goal` | How long it may work, what it may do without you, how thorough each step is, whether you see the plan first, and whether to install the keep-going hook | Python 3.10 or later, git, the killer skills its plan uses, and `gh` only for the draft pull request |
| `killer-design` | Only what it can't find in the project | Node.js 20 or later, and Playwright with Chromium: the runtime below on Windows, otherwise installed into `~/.cache/killer-design` on first use. Web fonts come from the font pack |
| `killer-slides` | Talk length, look, speaker notes and whether a blind reader checks it, then the three things people should leave with | Node.js 20 or later, and Playwright with Chromium as for killer-design. Faces come from the font pack |
| `killer-repo` | Scope, look, how to land the changes, and whether to convene a council | Node.js 20 or later, Playwright as for killer-design, and the GitHub CLI (`gh`) for the exact render and the repo checks |
| `killer-council` | Threshold, members, models, whether you take a seat, rounds and extras | Python 3.10 or later |
| `killer-bugs` | Scope, which kinds of bug, how deep (each depth caps how many suspects get proven), what happens once bugs are proven, the network, whether you add suspicions or promises, and where to look for copies | Python 3.10 or later, git, and whatever the project needs to build and run its tests |
| `killer-tests` | Scope, goal, how many mutants and where the new tests go; the network and an equivalence judge only when they matter | Python 3.10 or later, git, and whatever the project needs to run its tests |
| `killer-docs` | Which docs, which journeys a newcomer should try, what happens once they're checked, the network, and how deep | Python 3.10 or later, git, and the shells the docs use (sh on macOS and Linux; Git's sh, PowerShell or cmd on Windows) |
| `killer-perf` | What should get faster, how far, how careful the measurements are, what happens to proven gains, the network, and which kinds of slowness to look for | Python 3.10 or later, git, and whatever the project needs to build, run and test; Node.js only to profile a Node.js benchmark |
| `killer-upgrade` | What to upgrade, what to do about major versions, whether runtimes and CI actions are included, and what happens once upgrades are proven | Python 3.10 or later, git, the project's own package managers and whatever it needs to build and run its tests, and the network for installs; `gh` for CI actions |
| `killer-release` | What kind of release, how far to go (prepare, publish step by step, or check only), where to test-install, release notes, and whether a newcomer agent tries the install steps | Python 3.10 or later, git, and whatever the project needs to build. A full Python (with venv or pip) for the pip recipe, npm for the npm recipe |

### The packs

Beside the skills, the installers put the packs those skills use into `~/.cache`, each in its own folder, checked against `SHA256SUMS.txt`, and only when it's missing or out of date:

| Pack | For | Size | What's in it |
|---|---|---|---|
| [Fonts](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-skills-fonts.zip) | killer-design, killer-slides, killer-repo | 14 MB | 74 faces, every one killer-design's typography guide names plus JetBrains Mono, as latin and latin-ext `.woff2` files with their licences, from Fontsource. killer-design's `fonts.mjs` copies them into a site |
| [Runtime](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-skills-runtime-windows-x64.zip), Windows | killer-design, killer-slides, killer-repo | 128 MB | Playwright 1.64.1, the Chrome 156 headless shell it launches and Mermaid 11.17.2. Skipped when killer-design already has Playwright and its Chromium |
| [Node.js](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-skills-node-windows-x64.zip), Windows | killer-design, killer-slides, killer-repo | 35 MB | Node.js 24.22.2, only when there's no Node.js 20 or later on the PATH |
| [Python](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-skills-python-windows-x64.zip), Windows | the other eight skills | 12 MB | Python 3.14.10's embeddable package, only when there's no Python 3.10 or later. It has no `venv`, so killer-release's pip recipe needs a full Python |

With them, the skills download nothing. CI checks that with the network cut off: it runs every Chromium script from the runtime on the packed Node.js, screenshots a page set in the pack's fonts, runs killer-slides' browser checks, and runs every Python skill's tests on the packed Python. A pack is the same bytes from one release to the next until its pins change, so updating the skills doesn't download it again. `install.sh` installs the font pack; the Windows packs come with `install.ps1`.

### Installing offline

Download the release's files into one folder: the two installers, `SHA256SUMS.txt`, the skills and whichever packs you want. The [release page](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest) lists them all. Then run an installer from that folder. It installs from the folder and downloads nothing; a pack that isn't there is skipped with a note.

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

```sh
sh install.sh
```

Name skills after either to install only those. `install.ps1` reads the `.zip` files and `install.sh` the `.tar.gz` files.

What still needs the network, packs or not: Claude Code itself, and parts of killer-repo. Its exact README render goes through GitHub's Markdown API; offline it makes an approximate render instead, labelled as one on every screenshot. Its link checks, its reads of the repo's settings through `gh`, and anything it publishes need GitHub too.

## Tested on

- Windows 11 · Claude Code 2.1.316 · Node.js 24.20.0 · Python 3.14.2: killer-design, killer-repo and killer-council each run on a real project. killer-bugs ran partway on three real projects while its design was changing; the current design hasn't had a complete run. killer-slides made a real deck and ran a real blind audience round, and killer-release read this repo and smoke-installed it from a folder. The rest are tested by their scripts' own tests, with the agents' replies written by the tests; they haven't yet been run end to end in a session
- Ubuntu 24.04, Windows Server 2025 and macOS 26 in CI on every push: both installers (sh, Windows PowerShell 5.1, PowerShell 7), downloading and from a folder, the packager, fonts.mjs, the shared sandbox kit and every skill's scripts, with Python 3.12 and Node.js 24
- Windows Server 2025 in CI whenever the scripts or the pack pins change, and for every release: every pack, with shoot.mjs, icons.mjs, render.mjs, card.mjs, frames.mjs and fonts.mjs run on the packed Node.js, killer-slides' browser checks, and every Python skill's tests on the packed Python, while npm, Playwright's downloads, `gh` and `fetch` can't reach the network
- *Not* tested: the skills themselves in Claude Code on macOS or Linux, or uploaded to claude.ai

## Known issues

- The runtime, Node.js and Python packs are built for Windows only. On macOS and Linux, the first screenshot killer-design or killer-repo takes runs `npm install --prefer-offline playwright` and downloads Chromium if it's missing, so that first run needs the network unless npm's cache already has Playwright.
- The font pack covers latin and latin-ext. A site in Cyrillic, Greek or Vietnamese needs its faces from Google Fonts.
- killer-repo's approximate render (offline, or with `render.mjs --offline`) uses the vendored marked with GitHub's main rules applied. Footnotes, math, emoji shortcodes and `#123` / `@user` links don't render the way GitHub draws them.
- killer-council's reviewers work in your folder, not a sandbox. Their brief tells them to change nothing, and `council.py verify` compares the work with a snapshot after every round, so a change gets caught, but nothing prevents it.
- The sandboxes killer-bugs, killer-tests, killer-docs, killer-perf, killer-upgrade, killer-release and killer-goal run code in are copies of the project with their own home, temp and app-data folders, no credentials in the environment, and a dead proxy for the network unless the run allows it. They aren't containers: Windows PowerShell 5.1 and programs that open sockets themselves can still reach the network, and a program that asks Windows for the profile folder directly gets the real one. The agents' briefs tell them not to fetch anything, and the lead checks the real project's `git status` between phases.
- killer-bugs proves a fix by taking it out with git (`bugs.py check-fix`), so fixing needs a git repository. Hunting and proving work in any folder.
- killer-goal keeps going through the night with a Stop hook in `~/.claude/settings.json`, installed only when you say yes. Claude Code may only pick it up after you open `/hooks` or restart. The hook does nothing unless a goal is running, and lets the turn end if the run stalls.
- killer-goal's script blocks the one outward step it can take, a draft pull request, unless you chose it. The skills it runs are kept from pushing by their instructions, and its `guard` notices a push or fetch after the fact; it can't undo one.
- killer-upgrade has run npm and pip for real. Its pnpm, yarn, uv, poetry, Cargo, Go, Bundler and .NET commands follow their docs and are checked against saved output only. Its sandboxes have the network on, and dependencies' install scripts run in them.
- killer-perf's numbers are relative to the machine they're measured on. When the noise band is wider than ±10%, it compares nothing.
- Outside Python, killer-tests finds where to plant faults in masked text rather than a parse, so code written without spaces around operators gets fewer of them.
- killer-docs' newcomers are told not to read the code, and `collect` flags files they say they read outside their list, but nothing stops them.
- killer-slides' decks are tested in Chromium only. Its checks don't measure text drawn on a canvas, text added by CSS, text inside pictures or gradient-filled text.
- A council that mixes providers (GPT, Gemini and others) needs T3 Code's delegation tools. Anywhere else, killer-council offers a mix of Claude models instead.
- GitHub has no API for a repo's social preview image, so killer-repo makes the card and tells you where to upload it.
- Animated SVGs made with killer-repo's `frames.mjs` keep moving in Chromium browsers for readers who turn motion off, because Chromium doesn't pass that setting into an SVG shown through `<img>`.

## Configuration

The installers read these environment variables:

| Variable | Effect |
|---|---|
| `KILLER_SKILLS_VERSION` | A release tag to install, such as `v1.0.0`. Default: the latest release |
| `KILLER_SKILLS_DIR` | Where to install. Default `~/.claude/skills` |
| `KILLER_SKILLS_CACHE` | Where the packs go. Default `~/.cache`. The skills' scripts read it too |
| `KILLER_SKILLS_RUNTIME` | `skip`: the skills only, no packs. `all`: every pack the skills use, even when Node.js, Python or killer-design's Playwright is already there, as when setting up a machine that will be offline |

Each skill keeps its runs in `~/.claude/killer-<name>` and its sandboxes, if it has any, in the temp folder; `KILLER_<NAME>_HOME` and `KILLER_<NAME>_SANDBOXES` move them, as in `KILLER_BUGS_HOME`. killer-council has no sandboxes, and its runs go in `~/.claude/council-runs` unless you name another folder when you start one.

In PowerShell, set one with `$env:KILLER_SKILLS_VERSION = 'v1.0.0'` before the install line. In sh, put it before `sh`: `... | KILLER_SKILLS_VERSION=v1.0.0 sh`.

To run the tests and build the release files into `dist/`:

```sh
python3 -I tools/sync.py --check
python3 -I tests/test_council.py
python3 -I tests/test_kit.py
python3 -I tests/test_bugs.py
python3 -I tests/test_tests.py
python3 -I tests/test_docs.py
python3 -I tests/test_perf.py
python3 -I tests/test_upgrade.py
python3 -I tests/test_release.py
python3 -I tests/test_goal.py
python3 -I tests/test_slides.py
python3 -I tests/test_package.py
python3 -I tests/test_fonts.py
python3 -I tests/test_handoff.py
python3 -I tests/test_install.py
python3 tools/package.py
```

To build the packs (pinned in `tools/runtime.json` and `tools/fonts.json`) and test them all with the network cut off:

```powershell
.\tools\build-runtime.ps1 -Out dist
python -I tools/packs.py node --out dist
python -I tools/packs.py python --out dist
python -I tools/packs.py fonts --out dist
python -I tests/test_runtime.py dist/killer-skills-runtime-windows-x64.zip --node dist/killer-skills-node-windows-x64.zip --python dist/killer-skills-python-windows-x64.zip --fonts dist/killer-skills-fonts.zip
```

Scripts that two skills share have one source each: `tools/shared/kit.py` and `run.py` (the Python skills' sandboxes), `tools/shared/handoff.mjs` (killer-design's and killer-repo's books) and killer-design's `fonts.mjs`. Edit the source, then run `python3 tools/sync.py` to copy it into each skill; CI fails when a copy drifts.

`python3 tools/packs.py pin` moves the pins to the newest Node.js 24, Python 3.14 and Fontsource releases; add a font family to `tools/fonts.json` first to pack it too.

To work on the skills offline you need the source (the release's "Source code" archive), Node.js 20 or later and Python 3.10 or later; on Windows the Node.js and Python packs are enough, with `~/.cache/killer-skills-node` on the PATH for the tests. The tests above then need no network, except `test_runtime.py`, which needs the packs. Building the packs, moving the pins and publishing need the network.

## License

MIT
