# Security

These skills run inside Claude Code with whatever access your session has. Their scripts run locally: they install Playwright from npm into `~/.cache/killer-design` or `~/.cache/killer-repo`, call GitHub's API through your `gh` login (killer-repo), and write council records under `~/.claude/council-runs` (killer-council). The installers download from this repo's GitHub releases, or copy from a folder of its release files, and check every archive against the release's `SHA256SUMS.txt`. Both unpack the font pack into `~/.cache/killer-skills-fonts`: Fontsource's packages from npm, each checked against the integrity hash pinned in `tools/fonts.json` when CI builds the pack. On Windows, `install.ps1` also unpacks the runtime (Playwright, Chromium and Mermaid, built in this repo's CI from the versions in `tools/runtime.json`) into `~/.cache/killer-skills`, and killer-design and killer-repo run Chromium from there. When Node.js or Python is missing, it unpacks the official nodejs.org or python.org download into `~/.cache/killer-skills-node` or `~/.cache/killer-skills-python`; CI checks each against the SHA-256 pinned in `tools/runtime.json` before packing it.

## Supported versions

Only the latest release gets fixes.

## Reporting

Use [private vulnerability reporting](https://github.com/Real-Fruit-Snacks/killer-skills/security/advisories/new). Don't open a public issue. I'll answer within a week.

Worth reporting: an installer that writes outside the skills folder and its four pack folders in `~/.cache`, or installs an archive that fails its checksum, a skill that changes something public (a repo's settings, a release, a push) without asking first, or a council brief that leaks the threshold to its members.
