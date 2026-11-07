# What check measures

`svg.mjs check -w <run>` runs everything below that applies, writes `<run>/check.json` (with `summary.FAIL`, `summary.WARN` and `summary.INFO` lists, in the shape killer-design's `shoot.mjs` uses) and exits 1 on any FAIL. Each step also runs on its own: `lint`, `render`, `engines`, `contrast`, `github`, `motion`. FAIL lines are bugs. WARN lines are judgment calls: read them, decide, and say what you decided.

## lint: the file itself, no browser

| Rule | Level | What fails it |
|---|---|---|
| `xml` | FAIL | Not well-formed XML: an unclosed tag, an unquoted or repeated attribute, a bare `&`, an undeclared entity or namespace prefix. Shown as an image, a file like this draws nothing. Line and column are given. External entities in a DOCTYPE are refused outright |
| `root` | FAIL | The root isn't `<svg>`, or has no `xmlns="http://www.w3.org/2000/svg"` (as an image it then draws nothing) |
| `viewbox` | FAIL | No `viewBox` (with a fixed width and height it can't scale; with neither it has no shape), or one that isn't four numbers with a positive width and height |
| `script` | FAIL | `<script>`, an `on...=` handler, or a `javascript:` URL |
| `external` | FAIL | Anything loaded from outside the file: an `href`, `src` or `url()` to the web, to `file:` or to another file beside it, or `@import`. Links (`<a href>`) don't count |
| `foreign-object` | FAIL for `--target github`, else WARN | `<foreignObject>`: HTML inside the SVG, which engines draw differently |
| `font` | FAIL | Text set in a family the file doesn't embed (`@font-face` with a `data:` URL) and that isn't generic (`sans-serif`, `serif`, `monospace`, `system-ui` and the like) |
| `font` | WARN | For a logo or icon, text in a generic family (each system's face differs); for any kind, text naming no font at all |
| `ids` | FAIL | An id used twice, or a `url(#id)` or `href="#id"` that points at no id |
| `motion` | FAIL | CSS animation or SMIL with no `@media (prefers-reduced-motion)` rule anywhere |
| `metadata` | WARN | Comments, `<metadata>`, a DOCTYPE, Inkscape, Sodipodi, Illustrator, Sketch or Affinity markup, with the bytes `minify` saves |
| `decimals` | WARN | Numbers in geometry with more than 3 decimals, with the bytes rounding to 2 saves |
| `size` | WARN | Over the kind's budget: icon 2 KB, logo 8 KB, animation 30 KB, diagram 40 KB, banner 60 KB, illustration 80 KB, card 100 KB |
| `title` | WARN | No `<title>` and not `aria-hidden="true"`; for a diagram, no `<desc>` |
| `raster` | WARN | An embedded PNG, JPEG, GIF or WebP, which blurs when the graphic scales |

The font check follows CSS the way a browser would, near enough: a `style` attribute, then `<style>` rules whose selectors match (type, class, id, descendant and child selectors are matched; anything fancier is assumed to match), then the `font-family` attribute, then the parent. Only the first family in each list counts, because that's the one a machine with the font installed shows.

## render: every size, background and engine

Each file is shown through `<img>` in a page, at every size of the run, on each background (`light` is #ffffff, `dark` is #0d1117, GitHub's dark page) and on a checkerboard, at 1x. The page's colour scheme follows the background, so a `prefers-color-scheme` rule in the file switches as it would. A size is the graphic's longer side; the other follows the viewBox.

- **Animated files** are rendered as the still they show with reduced motion on: opened as a document, with every animation seeked to the end (or to 10.77 s if something loops). Browsers don't pass an emulated reduced-motion setting into an `<img>`, so that's the only way to see that still reliably.
- **Files with text** are rendered a second time with the text taken out, so `engines` can tell a different typeface from a different drawing.
- **Engines:** Chromium is required. Firefox and WebKit are Playwright's own builds, found by their revision folder (`firefox-1555`) in Playwright's caches, or in the folders `KILLER_SVG_BROWSERS` lists. One that isn't there is **not checked**: `render` says why and records the command that installs it under "Waiting for your OK". Chromium runs with `--disable-threaded-animation`, so a seeked animation paints the moment asked for.
- Each file gets a contact sheet in `<run>/renders/<file>-sheet.png`: every engine and background at 1x, and the sizes up to 48 px enlarged 6x.

## engines: the same file in Chromium, Firefox and WebKit

Each other engine's render is compared with Chromium's, size by size, on each background the file is for. A pixel counts as different only when no pixel in the other render's 3x3 neighbourhood is within 64 of 255 on every channel, looked at from both sides, so a one-pixel shift and anti-aliased edges don't count.

| Result | When |
|---|---|
| FAIL | At least 4 differing pixels and 10% or more of the graphic's pixels differ, or the colours are 40 levels apart on average |
| WARN | 3% or more differ (and at least 3 pixels), or 16 levels apart on average. Blur, drop shadows and blending at 16 px often land here |
| WARN (text) | The full renders differ but the text-free ones match: the engine filled the font with a face of its own |
| INFO | Matches at every size and background |

A FAIL comes with a diff image in `<run>/engines/`: Chromium, the other engine, and the differences in red. The usual causes, measured with this suite's engines (Chromium 156, Firefox 157, WebKit 27):

- **The CSS `d` property** (`path { d: path("...") }`): WebKit ignores it and draws the attribute.
- **`context-stroke` and `context-fill`** in markers: WebKit doesn't draw them.
- **`<foreignObject>`**: HTML inside SVG; fonts and layout differ in every engine.
- **Filters at small sizes**: blur and shadow differ by a few levels at 16 px. A WARN, not a FAIL; but a 16 px icon rarely needs a filter.
- **Text in a generic family**: each engine has its own default face. Draw the letters, or embed the font.

## contrast: does it stand out on each background?

From Chromium's render on a transparent background, every pixel at least half opaque is painted over each background the file is meant for, and counted as melting in when it's within 1.5:1 of it.

| Result | When |
|---|---|
| FAIL | 90% or more of the graphic melts into a background at some size: it disappears there. Also when nothing at all is visible |
| WARN | Half or more melts in. For a graphic that fills its box (a tile, a card), this is its edge melting into the page |
| WARN | A main colour covering a fifth or more of the graphic is under 3:1 against the background, the contrast WCAG 1.4.11 asks of the parts of a graphic people need to see. A navy hull on a near-black page is 2.2:1: still there, but it sinks |
| INFO | It stands out, with its main colours and their contrast against the background |

The main colours are taken from the largest render, where anti-aliasing doesn't blur them. A file limited to some backgrounds (`logo-dark.svg@dark`) is measured only on those.

## github: as GitHub shows it

With `--target github` (or `svg.mjs github <svg>` on any file), each file is rendered twice in Chromium at each size and background: through `<img>` in a page with `Content-Security-Policy: default-src 'none'; img-src 'self' data:`, and opened directly as a document, where its references could load. FAIL when the two differ (with a diff in `<run>/github/`), and FAIL for every request the file made, from beside it or from the network (network requests are blocked and recorded, so this works offline). An animated file is compared by what it loads, not by pixels. The lint's `script`, `external`, `font` and `foreign-object` failures belong here too.

## motion: frames over time

For an animated file (or any file in an `animation` run), the file is opened as a document twice, once with motion allowed and once with `prefers-reduced-motion: reduce`, and every CSS and SMIL animation is seeked to the same moments: 0 to 7 s early on, then three irregular moments at the end (9.54, 10.16 and 10.77 s for a loop) so a loop can't happen to look still. Nothing depends on how fast the machine is.

| Result | When |
|---|---|
| FAIL | With reduced motion on, the last three frames still differ: it never stops |
| WARN | With reduced motion on it moves for more than a second before it stops; or an `animation` run's file doesn't visibly move at all |
| INFO | How long it moves and when it settles, with and without reduced motion |

The frames are saved as a sheet in `<run>/motion/`: look at it for rotation about the wrong point and for a reduced-motion still that looks frozen mid-step.

## minify

Removes comments, processing instructions, the XML declaration (when it says UTF-8 or nothing), the DOCTYPE, `<metadata>`, editor namespaces and their attributes, unused namespace declarations, empty groups, and whitespace between elements (never inside text), and rounds numbers in geometry attributes to `--decimals` (2). Then it renders the original and the smaller file in Chromium at the run's sizes and backgrounds (an animated file at four moments) and writes only if no render moved: no more than 0.2% of the graphic's pixels (and at least 3) differ, and colours are within 3 levels on average. Otherwise it refuses, writes nothing, and says where it differs in viewBox units.

## The numbers in summary.json

`files`, `check_runs`, `checks_failing`, `checks_warning`, `check_current` (the last check ran on the files as they are), `engines_checked`, `engines_not_checked`, `rounds`, `reads_landed`, `reads_total`, `sizes_recognised`, `agents` (each distinct reply a viewer or matcher wrote counts once), `minified` and `exported`.
