# Security

These skills run inside Claude Code with whatever access your session has. Their scripts run locally: they install Playwright from npm into `~/.cache/killer-design` or `~/.cache/killer-repo`, call GitHub's API through your `gh` login (killer-repo), and write council records under `~/.claude/council-runs` (killer-council). The installers download from this repo's GitHub releases and check every archive against the release's `SHA256SUMS.txt`.

## Supported versions

Only the latest release gets fixes.

## Reporting

Use [private vulnerability reporting](https://github.com/Real-Fruit-Snacks/killer-skills/security/advisories/new). Don't open a public issue. I'll answer within a week.

Worth reporting: an installer that writes outside the skills folder or installs an archive that fails its checksum, a skill that changes something public (a repo's settings, a release, a push) without asking first, or a council brief that leaks the threshold to its members.
