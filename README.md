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
| `killer-design` | Only what it can't find in the project | Node.js. Installs Playwright into `~/.cache/killer-design` on first use |
| `killer-repo` | Scope, look, how to land the changes, and whether to convene a council | Node.js, and the GitHub CLI (`gh`) for the repo checks. Uses killer-design's Playwright if it's there, or installs its own into `~/.cache/killer-repo` |
| `killer-council` | Threshold, members, models, whether you take a seat, rounds and extras | Python 3.10 or later |

## Tested on

- Windows 11 · Claude Code 2.1.316 · Node.js 24.20.0 · Python 3.14.2: each skill run on a real project
- Ubuntu 24.04, Windows Server 2025 and macOS 15 in CI on every push: both installers (sh, Windows PowerShell 5.1, PowerShell 7), the packager and the council script, with Python 3.12 and Node.js 24
- *Not* tested: the skills themselves in Claude Code on macOS or Linux, or uploaded to claude.ai

## Known issues

- The first screenshot killer-design or killer-repo takes runs `npm install playwright` into its cache folder and downloads Chromium to Playwright's usual browser folder if it's missing, so that first run needs a network connection.
- killer-council's reviewers work in your folder, not a sandbox. Their brief tells them to change nothing, and `council.py verify` compares the work with a snapshot after every round, so a change gets caught, but nothing prevents it.
- A council that mixes providers (GPT, Gemini and others) needs T3 Code's delegation tools. Anywhere else, killer-council offers a mix of Claude models instead.
- GitHub has no API for a repo's social preview image, so killer-repo makes the card and tells you where to upload it.
- Animated SVGs made with killer-repo's `frames.mjs` keep moving in Chromium browsers for readers who turn motion off, because Chromium doesn't pass that setting into an SVG shown through `<img>`.

## Configuration

Both installers read two environment variables:

| Variable | Effect |
|---|---|
| `KILLER_SKILLS_VERSION` | A release tag to install, such as `v1.0.0`. Default: the latest release |
| `KILLER_SKILLS_DIR` | Where to install. Default `~/.claude/skills` |

In PowerShell, set one with `$env:KILLER_SKILLS_VERSION = 'v1.0.0'` before the install line. In sh, put it before `sh`: `... | KILLER_SKILLS_VERSION=v1.0.0 sh`.

To run the tests and build the release files into `dist/`:

```sh
python3 -I tests/test_council.py
python3 -I tests/test_package.py
python3 -I tests/test_install.py
python3 tools/package.py
```

## License

MIT
