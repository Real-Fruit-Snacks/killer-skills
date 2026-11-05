# Changelog

Each release publishes every skill on its own (`<skill>.zip`, `<skill>.tar.gz`), all of them together (`killer-skills.zip`, `killer-skills.tar.gz`) and `SHA256SUMS.txt`. The asset names don't change, so `https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/<file>` always fetches the newest.

## 1.0.0 (2026-11-05)

First release. killer-council moved here from agent-toolkit, which now takes all three skills from this repo.

- killer-design: designs and builds websites in hand-written HTML and CSS, and screenshots them at phone, tablet and desktop sizes (`scripts/shoot.mjs`). Makes and audits favicon and app icon sets (`scripts/icons.mjs`).
- killer-repo: rebuilds a GitHub repo page around one wow moment. `scripts/render.mjs` renders a README through GitHub's Markdown API in light and dark at desktop and phone widths, `scripts/audit.mjs` checks links, images, anchors and the repo's settings, `scripts/card.mjs` renders a social card and `scripts/frames.mjs` turns stills into an animated SVG.
- killer-council: blind scoring councils with a threshold the members never see. `scripts/council.py` keeps the books: sealed thresholds, snapshots and restore, a leak check on every brief, tallies with vetoes and plateau detection, and a holdout juror.
- `install.ps1` and `install.sh` install every skill, or only the ones you name, from the latest release or a pinned one, and check each archive against `SHA256SUMS.txt` first.
