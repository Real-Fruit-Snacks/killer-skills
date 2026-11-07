# Craft

How to draw SVG that holds up: at 16 px and at 1024, on light and dark, in every engine, read by a screen reader and shown by GitHub. `svg.mjs check` measures most of this; this file is how to get it right the first time. If killer-design is installed, its `references/color.md` (palettes in OKLCH, contrast) and `typography.md` (faces and pairings) go deeper than this does.

## Contents

- Concept first
- Grids and keylines
- Small sizes
- Strokes, corners and icon sets
- Colour
- Type
- Light and dark
- Accessibility
- Motion
- File hygiene
- READMEs and GitHub
- Clichés to avoid

## Concept first

A mark carries one idea. Find it in the subject's world, past the first association: a bakery isn't a wheat sheaf, it's the score mark on a loaf, the shape of a peel, a ticket from the queue. Write three concepts in a line each, pick the one that is most specific to this subject and that you can draw well, and say in words how it's built before you write a coordinate.

Draw what code draws well: geometry, letterforms, simple silhouettes, diagrams. Real things drawn by hand (animals, people, food, tools) are where SVG goes wrong: a proportion drifts or one detail reads as something else. Keep them flat and reduced, two or three shapes, and judge them at their real size. If it still looks off after one round of fixes, simplify the concept rather than refine the drawing.

## Grids and keylines

- **Pick a grid and keep to it:** a 24-unit viewBox for UI icons, 32 for favicons and app marks, 48 or 64 for logos with more detail. `viewBox="0 0 24 24"`, with the main edges on whole numbers.
- **Keylines** keep shapes of different kinds looking the same size: in a 24 grid, a circle about 20 across, a square about 18, a wide rectangle 20 by 16, a tall one 16 by 20, inside a 2-unit margin. A circle drawn as wide as a square looks smaller.
- **Snap to the pixel grid at the sizes that matter.** At 1x a 24-unit icon shown at 24 px puts each unit on a pixel: a horizontal edge at y=4 is sharp, at y=4.5 it's a blurred two-pixel line. A 2-unit stroke centred on a whole number covers whole pixels; a 1-unit stroke needs its centre on a half (x=4.5) to be sharp. At 16 px each unit is two thirds of a pixel, so check the 16 px render, not the maths.
- **Optical adjustment beats measurement:** pointed and round shapes overshoot the line slightly, a triangle's visual centre sits lower than its geometric one, and a play icon moves a little right of centre to look centred.

## Small sizes

At 16 px a mark has 256 pixels. Most logos and detailed icons don't survive that unchanged.

- **Make a small-size variant** when the full mark turns to mush at 16 to 32 px: one letter, one shape, the simplest recognisable part. Save it as `<name>-small.svg`; the blind viewer is shown it at 32 px and under.
- **Counters and gaps need room:** a gap or hole under about 1.5 px at the smallest size closes up. In a 24 grid shown at 16 px, keep gaps at least 2 units and strokes at least 2 units.
- **Fill the square:** use 85 to 100% of the box for a favicon or app mark. A small mark floating in empty space looks lost beside other icons.
- **Few colours, strong contrast inside the mark:** two colours at most at 16 px; gradients and fine texture turn to noise.

## Strokes, corners and icon sets

- One stroke width across a set (2 units in a 24 grid is common), one corner radius, one cap and join style (`stroke-linecap="round"` and `stroke-linejoin="round"`, or both square), one level of detail. Put them on the root `<svg>` so every shape inherits them.
- Don't mix filled and outlined styles in one set unless filled means "selected".
- Open shapes read better small than closed ones with detail inside.
- Draw every icon in the set on the same grid and keylines, and look at them side by side on the sheet: weight differences show at once there.
- `vector-effect="non-scaling-stroke"` keeps a stroke the same width at every size; useful for diagrams, wrong for icons, whose strokes should scale with them.

## Colour

- **Limit the palette:** a mark is two or three colours, and it must also work in one (for print, embroidery, a monochrome UI). Check the one-colour version early: if the mark relies on two colours touching, it may vanish in one.
- **Icons use `currentColor`:** `stroke="currentColor"` or `fill="currentColor"` lets the page set the colour. Shown through `<img>` there is no page colour, so `currentColor` falls back to black: give a standalone icon file real colours, or a `prefers-color-scheme` rule.
- **Find colours in the subject**, as killer-design's colour guide does: a material, a place, a time of day. The stock gradients (purple to blue, orange to pink) say "template".
- Contrast is measured, not guessed: `check` reports how much of each graphic is within 1.5:1 of each background and the contrast of its main colours.

## Type

Text in an SVG shown as an image can't load a web font, and machines without the font you named show another face.

- **In a logo, draw the letters as paths.** Geometric letterforms (circles, straight stems, simple joins) are easy to construct by hand on the grid, and that's often the better mark anyway. A wordmark is lettering, not typing.
- **Or embed the font:** `@font-face { font-family: Mark; src: url(data:font/woff2;base64,...) format("woff2"); }` inside `<style>`. killer-design's `fonts.mjs list` shows the faces in the killer-skills font pack and where their `.woff2` files are. A whole face is 20 to 60 KB, which suits a banner or card more than an icon.
- **Or use a generic family** (`sans-serif`, `serif`, `monospace`, `system-ui`) for labels in a diagram, where the exact face doesn't matter. Each engine fills it with its own face; `check` warns when that's the only difference.
- `<text>` set in a family the file doesn't embed fails `check`.

## Light and dark

A dark shape on a transparent background disappears on a dark page. Three fixes, which can be combined:

1. **A colour that holds on both:** a mid-tone that contrasts with white and with near-black, or a light shape with a dark outline.
2. **A tile:** the mark on a rounded square in a brand colour. Its edge can still melt into a dark page; a slightly lighter tile or a 1-unit outline brings it back.
3. **A dark rule inside the SVG:** Chromium, Firefox and WebKit all apply it inside `<img>` too, so it also works in a README.

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img">
  <title>Ferry Co-op</title>
  <style>
    .ink { fill: #1b3a5c }
    @media (prefers-color-scheme: dark) { .ink { fill: #e9e4d8 } }
  </style>
  <path class="ink" d="..."/>
</svg>
```

Or make two files, `mark-light.svg` and `mark-dark.svg`, and add each to the run with the background it's for: `mark-dark.svg@dark`.

## Accessibility

- **Meaningful graphics:** `role="img"` on the `<svg>`, and `<title>` as its first child, saying what it is ("Ferry Co-op", not "logo"). A diagram also gets `<desc>` with a sentence or two on what it shows.
- **Decorative ones:** `aria-hidden="true"` and no title.
- Shown through `<img>`, the `alt` text is what's read, so the page needs one too; the title covers inline use.

## Motion

Animate with CSS (`@keyframes`) or SMIL (`<animate>`); scripts don't run in an image. Every animation stops for people who asked for less motion.

```css
/* CSS: only animate when motion is welcome */
@media (prefers-reduced-motion: no-preference) {
  .wheel { animation: turn 4s linear infinite; transform-origin: 16px 16px; }
}
@keyframes turn { to { transform: rotate(1turn) } }
```

CSS can't stop SMIL, so for SMIL hide the moving part and show a still one:

```css
.still { display: none }
@media (prefers-reduced-motion: reduce) { .moving { display: none } .still { display: inline } }
```

- The reduced-motion version is a finished still, not a frozen mid-step.
- Loops should be slow and small; an entrance should be done in under a second and stay done.
- `check` renders the motion by seeking every animation to set moments, with and without reduced motion, and fails a file whose reduced-motion frames still change at the end.
- Use `transform-origin` in user units (`16px 16px`) or `transform-box: fill-box`, and check the frames: rotation about the wrong point is the commonest animation bug.

## File hygiene

- A `viewBox`, always. No fixed `width` and `height` on the root unless the use needs a fixed size: an `<img>` with its own size scales the file either way.
- No editor metadata: Inkscape's `sodipodi:` and `inkscape:` attributes, Illustrator's `i:` and `x:` namespaces, `<metadata>`, comments. `minify` removes them.
- One or two decimals. Hand-written paths on a grid need none; exports from editors carry six. `minify` rounds them and checks the render didn't move.
- Unique ids, and every `url(#id)` and `href="#id"` pointing at one that exists. Two SVGs inlined on one page share an id space, so prefix ids with something specific to the file (`fc-grad`, not `a`).
- Nothing from outside the file: no `href` to another file or the web, no `@import`, no web fonts. Embed with `data:` URLs if you must.
- Size: an icon in about 1 KB, a logo in a few, a diagram or illustration in tens. `lint` warns past each kind's budget.

## READMEs and GitHub

GitHub shows an SVG in a README through `<img>`, which blocks scripts, external fonts, external images and links inside the SVG. For a mark that needs a different version on dark:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.svg">
  <img alt="Ferry Co-op" src="docs/logo-light.svg" width="240">
</picture>
```

Run the graphics with `--target github`: `check` then renders each one as an image under a strict Content-Security-Policy and opened directly, and fails any that differ or load anything, and `<foreignObject>` fails too.

## Clichés to avoid

These read as generated or stock: a lightbulb for ideas, a rocket for launch, a brain or circuit for AI, a shield with a tick for security, a globe for "global", hexagons for "tech", orbit rings around a dot, a speech bubble for chat, a generic mountain-and-sun for outdoors, a gradient blob, a monogram in a circle with a thin ring. One of them, drawn with a real idea inside it, can work. The test: describe the mark in one sentence. If the sentence fits a thousand other projects ("a blue shield with a checkmark"), it isn't designed yet.
