# The deck format

What `deck.mjs new` writes, how to build slides on it, and what `deck.mjs check` measures.

## Contents
- The files
- A slide
- Speaker notes
- Builds
- The canvas: px on 1920×1080, never the viewport
- Fonts
- Pictures, diagrams and video
- Marking what's deliberate
- The engine: keys, links, presenter view, the API
- Print and PDF
- What check measures, and what it can't

## The files

```
talk/
  deck.html          the slides and their speaker notes, in one file
  assets/theme.css   the look: tokens and slide styles. Yours to design
  assets/deck.js     the engine: keys, builds, presenter view, overview. Leave it
  assets/deck.css    the canvas, scaling and print rules. Leave it
  assets/fonts/      from fonts.mjs
  assets/images/     pictures and diagrams
```

The folder is the deck. Zip it, send it, open `deck.html` in any current browser, and it works with no network and no install. It's opened from disk (a `file://` address), and browsers treat that more strictly than a web server:

- **Classic scripts only.** `<script type="module">`, `import` and `fetch()` are refused for a file opened from disk.
- **No `<link rel="preload" ... crossorigin>`.** `fonts.mjs add` prints one; leave it out. The `@font-face` rule loads the font by itself.
- **Nothing from the network.** No CDNs, font hosts, analytics or hotlinked pictures. `check` blocks and fails every request that leaves the machine.

## A slide

```html
<section class="slide statement" data-title="Cost fell">
  <h2>Running the night service now costs less than the day service did in 2019</h2>
  <aside class="notes"><p>What you say here.</p></aside>
</section>
```

- Slides are `<section class="slide">` elements, direct children of `<main class="deck">`, in order.
- **`data-title`** is a two-to-four-word label for the overview, the presenter view, the contact sheet and the report. It isn't shown on the slide, and the blind audience never sees it.
- **Headings:** one `h1` on the opening slide; `h2` for each slide's claim after that.
- **Layouts are classes:** `slide statement`, `slide number`, `slide split`, `slide evidence`. Style them in `theme.css`. `slides-craft.md` has the menu.
- Put `data-minutes="20"` on `<main class="deck">` (`new --minutes 20` does it) and the presenter view's timer counts against the talk's length.

## Speaker notes

`<aside class="notes">` inside each slide holds what the speaker says, as paragraphs, written out. It's hidden from the audience and from the blind audience's images. It's shown in the presenter view, and under each slide in `deck.mjs pdf --notes`. `slides-craft.md` covers how to write them.

## Builds

`data-step` on an element hides it until its turn. Each press of → shows the next build; when they're all in, → goes to the next slide. Going back onto a slide shows it finished.

- `data-step` with no value: each element is the next build, in document order.
- `data-step="2"`: elements with the same number appear together. Numbered builds go in number order.
- The hidden state is `opacity: 0; visibility: hidden`, so nothing else on the slide moves when a build comes in. To give builds an entrance, style the hidden state in `theme.css`, inside the reduced-motion gate:

```css
@media (prefers-reduced-motion: no-preference) {
  .deck-js [data-step]:not(.is-shown) { transform: translateY(16px); }
  .deck-js [data-step] { transition: opacity 300ms ease, transform 300ms cubic-bezier(.2,.7,.2,1), visibility 300ms; }
}
```

- `check` measures every slide with all its builds in. `shoot --steps` saves the slide before each build (`slide-03-step-0.png` is before the first).
- A script can start something when a slide arrives: `document.addEventListener('deck:change', (e) => { if (e.detail.index === 7) ... })`.

## The canvas: px on 1920×1080, never the viewport

Every slide is a 1920×1080 canvas. The engine scales the whole canvas to fit the screen and letterboxes the rest, so a slide looks the same on a projector, a laptop and a phone. That only holds if nothing inside a slide depends on the window:

- **Size everything in px:** type, spacing, positions. No `vw`, `vh`, `vmin`, `clamp()` with viewport units, or media queries inside a slide. `check` renders each slide at 1920×1080 and again at 1024×768, and fails any slide whose text moves or changes size between the two.
- The slide clips at its edges. Keep text at least 96px from every edge; old projectors crop the frame, and a line hugging the edge looks like a mistake.
- `%` of a parent and `fr` in a grid are fine: they're relative to the canvas, not the window.

## Fonts

```bash
node <skill-dir>/scripts/fonts.mjs list                          # the families in the pack
node <skill-dir>/scripts/fonts.mjs add talk "Newsreader" "Public Sans"
```

Link `assets/fonts/fonts.css` before `theme.css`, without the preload line, and name the families in `theme.css` with a generic fallback (`"Newsreader", ui-serif, Georgia, serif`). `check` fails a declared font that didn't load and warns about a family the deck uses but doesn't ship, because that one shows only on machines that happen to have it.

Without the font pack, a system stack is a legitimate, deliberate choice: `system-ui, sans-serif`, or `ui-serif, Charter, "Iowan Old Style", Georgia, serif`. Don't load faces from Google Fonts: a deck must load nothing.

## Pictures, diagrams and video

- **Diagrams and charts as inline SVG** in `deck.html`. They stay sharp at any size, take the deck's fonts, and `check` measures their labels at their rendered size: a label of `font-size: 8` in a `viewBox` that's doubled is 16px, and fails.
- **Photos** in `assets/images/`, at most 1920px wide for a full-bleed picture (2400 if it's cropped in), as JPEG or WebP. Give each one `width` and `height`.
- **`alt` text** on every picture that carries content; `alt=""` on decoration.
- **Text on a photo:** `check` measures contrast against the photo's actual pixels behind the text, so a scrim, a darker crop or moving the text is the fix, not a different colour value.
- **Video:** `<video src="assets/clip.mp4" muted playsinline controls>`. Start it from a click, and never autoplay it with reduced motion on: `check` sees a picture that changes.

## Marking what's deliberate

| Mark | Means | What check does |
|---|---|---|
| `aria-hidden="true"` | Decorative text, like a giant numeral behind a title | Leaves it out of size, contrast and word counts, and says how many it skipped |
| `data-bleed` | A picture or shape meant to run past the slide's edge | Doesn't call it cut off |
| `alt=""` on an image | Decoration | May bleed; no alt-text warning |
| `class="todo"` | A stand-in only the speaker can fill: a name, a date, a figure | Lists it as a warning, so every stand-in reaches the handoff |

Use these only for what they say. Marking real content decorative to get past a check defeats the check, and the room still can't read it.

## The engine: keys, links, presenter view, the API

| Key | Does |
|---|---|
| →, ↓, space, Page Down, or a click on the right half | The next build, or the next slide |
| ←, ↑, Shift+space, Page Up, or a click on the left half | Back |
| Home, End | First and last slide |
| f | Full screen |
| p | The presenter view, in a second window: the current slide, the next one, the notes and a timer. Both windows follow each other |
| o | The overview: every slide in a grid; click one to go there |
| b or . | Black screen, and back |
| Escape | Leave the overview |
| + and − | In the presenter view, larger and smaller notes |

Clickers send Page Up and Page Down, so they work. A swipe works on a touch screen. `deck.html#/5` opens on slide 5; `#/5/2` on slide 5 after two builds. If the browser blocks the presenter window, allow pop-ups for the file.

`window.deck` has `count`, `index`, `step`, `steps(n)`, `title(n)`, `go(n, step)`, `next()`, `prev()`, `overview(on)` and `presenter()`. Slides count from 1, like the links. `go(n, 'last')` shows slide n with every build in. `deck.mjs` drives the deck through this API.

## Print and PDF

`deck.css` sets `@page { size: 1920px 1080px; margin: 0 }`: one slide per page, every build in, no notes. `deck.mjs pdf` exports it and checks the PDF has one 1440×810 pt page per slide. `deck.mjs pdf --notes` writes a second PDF with each slide on top and its notes under it, for rehearsing or for someone giving the talk in your place. Printing from the browser works too: in Chrome, Save as PDF with margins set to none and background graphics on.

## What check measures, and what it can't

All of it is measured in the real render at 1920×1080, with every build in.

| Check | How | Fails | Warns |
|---|---|---|---|
| Overflow | Each line of text against the slide's edges and against any box that hides its overflow; content pictures, diagrams and tables against the slide's edges | Text past the slide's edge or cut off by a box; a content picture cut off | Text spilling out of a card or panel that doesn't clip |
| Small text | The rendered size of every line, transforms and SVG scaling included | Under 20px | Under 24px (about 18pt projected) |
| Contrast | The text's colour against the pixels behind it: a second screenshot with the text's fill turned off, read at the worst tenth of the background | Under 4.5:1; under 3:1 for text of 40px, or 32px bold | |
| Density | Words visible on the slide, notes left out; list items | Talk: over 70 words. Read alone: over 120 | Talk: over 40 words. Read alone: over 75. Over 6 bullets |
| Notes | An `<aside class="notes">` with words in it | Missing, when notes were asked for | |
| Assets | Pictures, files, fonts, the console and every request, from the deck opened as a file | A picture or file that doesn't load, a font that didn't load, a script error, any request to the network | A font the deck doesn't ship; a picture without alt text |
| Motion | With reduced motion on: running animations and transitions, and whether the picture changes over 250ms | Anything moving | |
| Layout | Each text block's position and size at 1920×1080 and at 1024×768 | Any text that moves or changes size | |
| Navigation | `deck.go(n)` and `#/n` for every slide, and opening `deck.html#/<last>` | A slide that can't be reached | |
| Collisions | Lines of text overlapping other text | | Any overlap |
| Stand-ins | `class="todo"` | | Each one left |
| Timing | Slides against the talk's length (1 to 2 minutes a content slide); notes at 140 words a minute | | Far too many or too few slides; notes longer than the talk |

What it can't see: whether a slide is any good. A slide can pass every check and still be dull, unclear or ugly. That's what looking at the screenshots is for, and what the blind audience tests. It doesn't know where an SVG diagram's boxes are, so a label running past the edge of its box in a diagram is yours to spot. It also can't judge text inside a picture (a screenshot of a table), text drawn on a canvas, text written by CSS (`content:` in `::before` and `::after`), or the contrast of gradient-filled text. It says when it skipped gradient text, so put real words in the markup and check the rest by eye.
