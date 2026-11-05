# killer-skills

Claude Code skills for sites, repo pages and review councils

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/cover-dark.svg">
  <img alt="install.sh in a terminal: installed killer-council, killer-design and killer-repo, 3 skills in ~/.claude/skills" src="docs/assets/cover-light.svg" width="100%">
</picture>

```powershell
irm https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.ps1 | iex
```

```sh
curl -fsSL https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.sh | sh
```

The skills I reach for when I want a job done properly rather than quickly. Each one does the whole job, then checks its own work before handing it back: killer-design screenshots the site it built, killer-repo renders the README the way GitHub will, and killer-council has fresh agents score the work against a bar they never see. They run in Claude Code and assume Node.js and Python on your PATH.

## What's in it

| Skill | Does | Download |
|---|---|---|
| `killer-design` | Designs and builds a website in hand-written HTML and CSS, then screenshots it at phone, tablet and desktop sizes and fixes what it sees | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-design.zip) |
| `killer-repo` | Rebuilds a repo page around one true wow moment: README, description, topics, social card and community files, rendered as GitHub shows them | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-repo.zip) |
| `killer-council` | Sends blind reviewer agents to score work from 1 to 100, fixes what they find and reconvenes until it passes your threshold | [zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-council.zip) |

In Claude Code, type a skill's name as a command (`/killer-repo`) or ask in plain words ("make this README better"). killer-council and killer-repo open with a few setup questions, so there are no options to remember. killer-design reads the project first and asks only what it can't work out.

Each skill is its own download. Name one to install just that skill:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.ps1))) killer-repo
```

```sh
curl -fsSL https://raw.githubusercontent.com/Real-Fruit-Snacks/killer-skills/main/install.sh | sh -s -- killer-repo
```

Or unzip a download into `~/.claude/skills` yourself; [killer-skills.zip](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-skills.zip) has all three. The installers check every archive against the release's `SHA256SUMS.txt` before they replace anything, and replace an installed skill whole, so files an older version had don't linger.

| Skill | Asks you | Needs |
|---|---|---|
| `killer-design` | Only what it can't find in the project | Node.js, and Playwright with Chromium: the runtime below on Windows, otherwise installed into `~/.cache/killer-design` on first use |
| `killer-repo` | Scope, look, how to land the changes, and whether to convene a council | Node.js, Playwright as for killer-design, and the GitHub CLI (`gh`) for the exact render and the repo checks |
| `killer-council` | Threshold, members, models, whether you take a seat, rounds and extras | Python 3.10 or later |

### The Windows runtime

On Windows, `install.ps1` also installs the runtime when killer-design or killer-repo is one of the skills: Playwright 1.64.1, the Chrome 156 headless shell it launches and Mermaid 11.17.2, a 125 MB download unpacked into `~/.cache/killer-skills`. It skips it when killer-design already has Playwright and its Chromium, and doesn't download it again when it's up to date. With the runtime, neither skill downloads anything; CI checks that by running every Chromium script from the runtime with the network cut off. It's also a [download of its own](https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/killer-skills-runtime-windows-x64.zip): unzip it into `~/.cache/killer-skills`.

What still needs the network, runtime or not: Claude Code itself, and parts of killer-repo. Its exact README render goes through GitHub's Markdown API; offline it makes an approximate render instead, labelled as one on every screenshot. Its link checks, its reads of the repo's settings through `gh`, and anything it publishes need GitHub too.

## Tested on

- Windows 11 · Claude Code 2.1.316 · Node.js 24.20.0 · Python 3.14.2: each skill run on a real project
- Ubuntu 24.04, Windows Server 2025 and macOS 26 in CI on every push: both installers (sh, Windows PowerShell 5.1, PowerShell 7), the packager and the council script, with Python 3.12 and Node.js 24
- Windows Server 2025 in CI whenever the scripts or the runtime pins change, and for every release: the runtime, with shoot.mjs, icons.mjs, render.mjs, card.mjs and frames.mjs run from it while npm, Playwright's downloads, `gh` and `fetch` can't reach the network
- *Not* tested: the skills themselves in Claude Code on macOS or Linux, or uploaded to claude.ai

## Known issues

- The runtime is built for Windows only. On macOS and Linux, the first screenshot killer-design or killer-repo takes runs `npm install --prefer-offline playwright` and downloads Chromium if it's missing, so that first run needs the network unless npm's cache already has Playwright.
- killer-repo's approximate render (offline, or with `render.mjs --offline`) uses the vendored marked with GitHub's main rules applied. Footnotes, math, emoji shortcodes and `#123` / `@user` links don't render the way GitHub draws them.
- killer-council's reviewers work in your folder, not a sandbox. Their brief tells them to change nothing, and `council.py verify` compares the work with a snapshot after every round, so a change gets caught, but nothing prevents it.
- A council that mixes providers (GPT, Gemini and others) needs T3 Code's delegation tools. Anywhere else, killer-council offers a mix of Claude models instead.
- GitHub has no API for a repo's social preview image, so killer-repo makes the card and tells you where to upload it.
- Animated SVGs made with killer-repo's `frames.mjs` keep moving in Chromium browsers for readers who turn motion off, because Chromium doesn't pass that setting into an SVG shown through `<img>`.

## Configuration

The installers read these environment variables:

| Variable | Effect |
|---|---|
| `KILLER_SKILLS_VERSION` | A release tag to install, such as `v1.0.0`. Default: the latest release |
| `KILLER_SKILLS_DIR` | Where to install. Default `~/.claude/skills` |
| `KILLER_SKILLS_RUNTIME` | `skip` stops `install.ps1` installing the Windows runtime |

In PowerShell, set one with `$env:KILLER_SKILLS_VERSION = 'v1.0.0'` before the install line. In sh, put it before `sh`: `... | KILLER_SKILLS_VERSION=v1.0.0 sh`.

To run the tests and build the release files into `dist/`:

```sh
python3 -I tests/test_council.py
python3 -I tests/test_package.py
python3 -I tests/test_install.py
python3 tools/package.py
```

To build the Windows runtime (versions pinned in `tools/runtime.json`) and run every Chromium script from it with the network cut off:

```powershell
.\tools\build-runtime.ps1 -Out dist
python -I tests/test_runtime.py dist/killer-skills-runtime-windows-x64.zip
```

## License

MIT
