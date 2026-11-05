# README craft on GitHub

## What GitHub keeps and strips

Checked against GitHub's Markdown API, which `render.mjs` uses:

- **Kept, with a twist:** `<picture>` with `<source media="(prefers-color-scheme: dark)">`. GitHub wraps it in a `<themed-picture>` element and switches the sources to follow the reader's GitHub theme setting, not only their system theme. Use exactly `(prefers-color-scheme: dark)` or `(prefers-color-scheme: light)` as the media query; that's the form the switch understands. `render.mjs` follows the system theme, which matches readers whose GitHub theme is set to follow the system (GitHub's default). A combined query such as `(prefers-color-scheme: dark) and (max-width: 600px)` can only be checked on github.com after a push.
- **Kept:** `<details>` and `<summary>`; `align` on block elements; `width` and `height` on images; `<kbd>`, `<sub>`, `<sup>`, `<br>`; tables, task lists, footnotes and math (`$…$`, `$$…$$`); alerts (`> [!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]`, `[!CAUTION]`).
- **Rendered by github.com itself:** code blocks tagged `mermaid`, `geojson`, `topojson` or `stl`. `render.mjs` draws the Mermaid ones and marks the others.
- **Stripped:** `style` attributes, `<style>`, `<script>`, `<iframe>`, forms, and `<video>` that points into the repo. `id` and `name` values get a `user-content-` prefix, but `#links` to them still work.
- **Changed:** every image gets wrapped in a link to the raw file. To send clicks somewhere useful (the live app, the docs), wrap the image in your own link.
- **Images inside images:** an SVG shown through `<img>` can't run scripts or load external fonts or images. CSS animation and media queries inside it still work.
- **Paths:** relative paths resolve from the README's folder, and `/path` from the repo root. Letter case matters on GitHub even when it doesn't on your disk.

## Where a README shows up

- **The repo home page:** the README sits below the file list. On desktop the About panel is to the right; on phones it comes first and the README after it.
- **The GitHub mobile app:** narrower still, and it doesn't play animated images everywhere.
- **Package registries** (npm, PyPI, crates.io and others) render READMEs differently and often break relative image paths. If the README gets published there, use absolute `raw.githubusercontent.com` URLs for images, or check the registry's own preview.
- **Shared links** show the social card and description, not the README.

## Sizes

- The README column is about 830px wide on desktop and about 358px on a phone. Make images at roughly twice the width they're displayed at.
- Text inside an image has to survive phone width. If it can't, move the text out of the image and into the README.
- Keep the first screen's images well under 2 MB in total. A 12 MB GIF is the most common way to make a README crawl on mobile data.

## Dark mode

There are three ways to make an image work in both themes:

1. A `<picture>` with a light and a dark source.
2. An SVG with its own `@media (prefers-color-scheme: dark)` rules.
3. An image that works on both backgrounds: an opaque background with rounded corners, or mid-tone art.

Traps: a transparent PNG with black text vanishes on dark, and one with white text vanishes on light. Screenshots with a white background glare on dark mode, so frame them, or capture both themes if the app has them.

## Layout that works

- **A centered hero:** `<p align="center"><img …></p>`.
- **Side by side:** `<img width="49%">` pairs, or a table whose cells hold images.
- **Galleries and grids:** a table works as a grid. Keep it to two or three columns so it survives phone width.
- **Progressive detail:** `<details>` for long option lists, platform-specific steps, full references and changelogs. The page stays short and nothing is lost.
- **Alerts:** use them for the one warning that really matters (data loss, a security note), not for decoration. Three alerts in a row read as noise.
- **Headings as signposts:** readers scan headings to find their question. Name sections for what the reader wants ("Run it on a schedule"), not for document parts ("Usage").

## Writing

- **The one-liner says what it is and for whom, concretely.** It doubles as the description and the social card's text, so make it good enough for all three.
- **The voice comes from the project's personality.** First person is right for a solo maintainer. Dry, warm, terse or playful are all fine, as long as it's the same voice throughout.
- **Commands are copy-paste ready:** no `$` prompts in the block, the right language tag for highlighting, and one canonical install line per platform rather than five alternatives.
- **Show output:** what the reader will see, exactly. It proves the thing works and tells them when they've succeeded.
- **Badges** earn their place by carrying information a reader wants: CI status for a library people depend on, the latest version of a package. Decoration and badges for vanity metrics don't earn it.
- **Length follows content.** Long references belong in `docs/` or `<details>`. A short README for a small tool is a feature.
