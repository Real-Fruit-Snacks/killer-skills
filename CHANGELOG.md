# Changelog

Each release publishes every skill on its own (`<skill>.zip`, `<skill>.tar.gz`), all of them together (`killer-skills.zip`, `killer-skills.tar.gz`) and `SHA256SUMS.txt`. The asset names don't change, so `https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/<file>` always fetches the newest.

## 1.1.0 (2026-11-05)

Offline: nothing the skills used to fetch on first use has to come from the network any more, on Windows.

- New release asset, `killer-skills-runtime-windows-x64.zip` (125 MB): Playwright 1.64.1, the Chrome 156 headless shell it launches, and Mermaid 11.17.2. `install.ps1` installs it into `~/.cache/killer-skills` when killer-design or killer-repo is among the skills, unless it's already up to date there or killer-design already has Playwright and its Chromium. `KILLER_SKILLS_RUNTIME=skip` turns that off. Built by `tools/build-runtime.ps1` from the pins in `tools/runtime.json`.
- killer-design and killer-repo look for Playwright in the project, then the runtime, then their own caches, and point the runtime's Playwright at its own Chromium. Their npm installs, when one is still needed, use `--prefer-offline`, so a seeded npm cache works without the network.
- killer-repo ships `vendor/github-markdown.css` (5.9.0) and `vendor/marked.esm.js` (18.1.3), so it no longer installs github-markdown-css.
- killer-repo's `render.mjs` makes an approximate render when GitHub's Markdown API can't be reached, or with `--offline`: marked, with GitHub's main rules applied (alerts, task lists, linked images, heading anchors, stripped styles and scripts). Every screenshot is labelled, and the report says `"renderer": "approximate"` and why. Mermaid diagrams come from the runtime when it's installed.
- killer-repo's `audit.mjs` says once that it's offline when no outside link answers, instead of warning about each one.
- CI builds the runtime whenever the scripts or the pins change, and runs every Chromium script from it with the network cut off (`tests/test_runtime.py`).

## 1.0.0 (2026-11-05)

First release. killer-council moved here from agent-toolkit, which now takes all three skills from this repo.

- killer-design: designs and builds websites in hand-written HTML and CSS, and screenshots them at phone, tablet and desktop sizes (`scripts/shoot.mjs`). Makes and audits favicon and app icon sets (`scripts/icons.mjs`).
- killer-repo: rebuilds a GitHub repo page around one wow moment. `scripts/render.mjs` renders a README through GitHub's Markdown API in light and dark at desktop and phone widths, `scripts/audit.mjs` checks links, images, anchors and the repo's settings, `scripts/card.mjs` renders a social card and `scripts/frames.mjs` turns stills into an animated SVG.
- killer-council: blind scoring councils with a threshold the members never see. `scripts/council.py` keeps the books: sealed thresholds, snapshots and restore, a leak check on every brief, tallies with vetoes and plateau detection, and a holdout juror.
- `install.ps1` and `install.sh` install every skill, or only the ones you name, from the latest release or a pinned one, and check each archive against `SHA256SUMS.txt` first.
