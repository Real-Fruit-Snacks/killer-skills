# Changelog

Each release publishes every skill on its own (`<skill>.zip`, `<skill>.tar.gz`), all of them together (`killer-skills.zip`, `killer-skills.tar.gz`), the packs (`killer-skills-fonts.zip` and `.tar.gz`, and for Windows `killer-skills-runtime-windows-x64.zip`, `killer-skills-node-windows-x64.zip` and `killer-skills-python-windows-x64.zip`), both installers and `SHA256SUMS.txt`. The asset names don't change, so `https://github.com/Real-Fruit-Snacks/killer-skills/releases/latest/download/<file>` always fetches the newest.

## 1.2.0 (2026-11-05)

Offline, all the way: a folder of release files installs everything with no network, and the skills no longer need Node.js, Python or a font host from outside.

- Both installers are release assets now. Run as a file from a folder holding the release's files (`powershell -ExecutionPolicy Bypass -File .\install.ps1`, or `sh install.sh`), they install from that folder and download nothing. A pack that isn't in the folder is skipped with a note; a skill that isn't there fails cleanly.
- New: the font pack, `killer-skills-fonts.zip` and `.tar.gz` (14 MB). It has 74 faces, every one killer-design's typography guide names plus JetBrains Mono, as latin and latin-ext `.woff2` files with their licences, from Fontsource and pinned in `tools/fonts.json`. Both installers put it in `~/.cache/killer-skills-fonts` with killer-design or killer-repo.
- New: killer-design's `scripts/fonts.mjs`. `list` shows what's in the pack; `add <site> "Family" ...` copies faces into `assets/fonts/` and writes `fonts.css` with `font-display: swap` and each subset's `unicode-range`. `--weights` picks a static family's weights. The typography guide now self-hosts from the pack first and falls back to Google Fonts.
- New, for Windows: `killer-skills-node-windows-x64.zip` (Node.js 24.22.2, 35 MB) and `killer-skills-python-windows-x64.zip` (Python 3.14.10's embeddable package, 12 MB). `install.ps1` installs Node.js with killer-design or killer-repo when there's no Node.js 20 or later on the PATH, and Python with killer-council when there's no Python 3.10 or later. The skills say to use them when `node` or `py` isn't there.
- `KILLER_SKILLS_RUNTIME` covers every pack: `skip` installs none, and the new `all` installs every pack the skills use even when Node.js, Python or killer-design's Playwright is already there. `KILLER_SKILLS_CACHE` is documented.
- Every pack is written with fixed timestamps and order (`tools/packs.py`), so it's the same bytes until its pins change and the installers don't download it again for a new release. The runtime is written this way for the first time, so updating from 1.1.0 downloads it once more.
- The installers rename an installed skill or pack aside before swapping in the new one. If a running session holds it open, the install stops with that folder unchanged, instead of leaving it half replaced.
- killer-repo said Node 18+; Playwright needs Node.js 20 or later, and its SKILL.md says so now.
- Skill names starting `killer-skills-` are kept for the packs; `tools/package.py` refuses them.
- CI builds every pack whenever the scripts, the builders or the pins change. On Windows it runs every Chromium script on the packed Node.js, screenshots a page set in the pack's fonts, and runs killer-council's tests on the packed Python, all with the network cut off. The installer tests now cover installing from a folder with the network pointing nowhere.

## 1.1.0 (2026-11-05)

Offline: nothing the skills used to fetch on first use has to come from the network any more, on Windows.

- New release asset, `killer-skills-runtime-windows-x64.zip` (128 MB): Playwright 1.64.1, the Chrome 156 headless shell it launches, and Mermaid 11.17.2. `install.ps1` installs it into `~/.cache/killer-skills` when killer-design or killer-repo is among the skills, unless it's already up to date there or killer-design already has Playwright and its Chromium. `KILLER_SKILLS_RUNTIME=skip` turns that off. Built by `tools/build-runtime.ps1` from the pins in `tools/runtime.json`.
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
