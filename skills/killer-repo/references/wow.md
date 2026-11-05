# Wow moments

Ideas for the top of a README, grouped by the kind of project. Treat them as a spark, not a menu: the best wow moment comes from something true and particular about *this* project, and often isn't on any list. Read the section that fits, then go one step further than it does.

## What makes one work

- **It's true.** The real app, the real output, the real system. A mockup that looks better than the tool sets up a letdown.
- **It shows the point, not the parts.** One moment that captures why someone would use it, not a tour of every screen.
- **It reads in a few seconds,** at phone width too: a 1600px image shown 358px wide is at 22% scale, so 40px text in it ends up 9px.
- **It works in light and dark.**
- **It's light.** Motion on the first screen costs data; spend it only where motion is the point.
- **It can be remade.** The script or recipe that produced it lives in the repo.

## By kind of project

**Web apps and browser tools.** The app on its best screen, captured with Playwright at 2x and shown at width 100%. A short loop of the core interaction: the one gesture people come for, not a tour. If there's a hosted version, "try it now" may be the strongest primary action of all, with the image linking straight to it.

**Desktop apps.** The app mid-task, cropped to the window rather than a whole cluttered desktop. A loop of the interaction people install it for: the arrow being drawn, the area being blurred, the file landing where it should. Before and after works when the app changes something.

**Games and playful things.** Motion is the point: a few seconds of real play. Or a single frozen moment that tells a story on its own: the position one move from mate, the level just before the jump.

**Command-line tools.** The one command and the output people want, at real speed. If what they want is a file, an image or a change somewhere else, show *that* rather than a terminal. A long-running tool can show the end state, or a run compressed in time and labeled as such.

**Converters and media tools.** Before and after, side by side: input and output, or the output in its real context (the device it plays on, the page it prints to). Numbers belong next to the images: size, time, quality.

**Background services, sync and automation.** They're invisible by design, so the wow is making the invisible visible: the state before and after (out of sync, then in sync), a timeline of what it did overnight, the log line that means "it worked", a diagram of the loop it runs.

**Libraries and SDKs.** The smallest complete example and what it produces, side by side. Or the painful way next to this way, in the same number of lines. Real output under real code.

**Frameworks and platforms.** The shape of the system: how the pieces fit, in one diagram. A short transcript of a realistic session. Scale shown rather than claimed: sixty modules as a grid, not the word "comprehensive".

**Security tooling (for authorized testing).** What an operator actually gets, from a lab, never a real target: the findings, the evidence, the session. A diagram of the chain it covers. A clear statement of intended use belongs near the top; it's part of trust, not small print.

**Infrastructure, setup kits and dotfiles.** A map of what's inside, or what a machine looks like after one run: the inventory, the versions, the tools that appear. The one command, and everything it leaves behind.

**Generators and data tools.** A handful of real outputs fanned out, varied enough to show range.

**Collections of scripts.** The index *is* the wow when it works like a menu: one line each, grouped so anyone finds the one they need in five seconds, maybe with a tiny preview beside each.

**Docs, templates and starters.** The result they produce, rendered, rather than the files themselves.

## Formats, and how to make them

- **Still image** (PNG or WebP, at 2x the display width): Playwright for anything in a browser (set the viewport, use `deviceScaleFactor: 2`, hide dev chrome). For desktop apps, the app's own export, or an OS capture of just the window: on Windows, PowerShell with `System.Drawing` and the window's bounds; on macOS, `screencapture -l <window id>`; on Linux, `grim` or `import`.
- **Animated SVG:** CSS keyframes inside the SVG. It's tiny, crisp at any size, and can adapt to dark mode with an internal `@media (prefers-color-scheme: dark)`. Scripts don't run and external fonts don't load in README images, so use system font stacks or convert text to paths.
- **A sequence of real stills,** with no extra tools: capture each moment (Playwright, a window capture, `card.mjs`), then `node <skill-dir>/scripts/frames.mjs a.png b.png c.png --out demo.svg --durations 2500,1500,3000 --hold 1`. Each frame is re-encoded as WebP inside an SVG that cross-fades with CSS, so it plays inside a README `<img>`. `--hold` picks the still frame shown to readers who turn off motion. That frame was checked with the SVG opened on its own; Playwright's reduced-motion emulation doesn't reach SVGs inside an `<img>`. Every frame adds its weight, so use a few frames that tell the story, not a recording. Run it once per theme for a light and dark `<picture>` pair.
- **GIF, APNG or animated WebP:** record frames, for example with Playwright's `recordVideo` or a screen recorder, then convert with ffmpeg. For GIF, use `palettegen` and `paletteuse`, 10 to 15 fps and a small canvas. WebP and APNG are usually far smaller than GIF at the same quality.
- **Terminal sessions:** record a real run with its real timing (asciinema with agg, VHS, or a small script that turns captured output and timings into an animated SVG). Trim the dead time, and keep it short.
- **Diagrams:** a ```` ```mermaid ```` block renders on GitHub and follows the reader's theme. Hand-written SVG gives a designed diagram more control.
- **Before and after:** two images side by side, using `<img width="49%">` pairs or a two-column table, or one composite image with a clear divide.
- **Designed stills** (a hero, a poster, a map): build them in HTML and render with `card.mjs` at the size you need.
- **Real output as text:** when the output *is* the point, a code block of exactly what the tool prints can be the wow moment, with nothing else needed.
- **Video:** GitHub plays it only from an attachment URL, made by dragging the file into GitHub's web editor. A video committed to the repo doesn't play in the README. Release pages and the social card can carry more.
