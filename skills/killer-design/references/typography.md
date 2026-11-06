# Typography

Type is most of a website's personality. If you get the typography right, a page with almost no decoration still looks designed. If you get it wrong, no amount of decoration saves it.

## Contents
- Choosing faces
- Pairings by personality
- Scale and hierarchy
- Spacing, measure and rhythm
- Loading fonts
- Finishing details

## Choosing faces

Start from the direction's adjectives. **Choose the display face first**, because it carries the personality. Then choose a text face that's comfortable at 16–18px for long reading and doesn't fight the display face. One versatile family can be the strongest choice: a variable family with weights and optical sizes, used for both display and text. Use at most two families plus an optional monospace.

Ways to pair that tend to work:
- **Contrast in structure, harmony in proportion**: a serif display with a sans text face of similar x-height, or the reverse.
- **Same designer or superfamily**: IBM Plex Sans with Plex Mono, Source Serif with Source Sans, Alegreya with Alegreya Sans.
- **One family, two roles**: a variable face with an optical-size axis (Newsreader, Literata, Source Serif 4, Bricolage Grotesque) set big and tight for display and at text size for body.

Avoid as unexamined defaults: Inter, Roboto, Open Sans, Lato, Montserrat, Poppins, Raleway, Nunito, and Playfair Display as the "elegant" option. Also avoid the faces that current AI-made sites reach for: Space Grotesk or Geist with Geist Mono for "tech", and Fraunces or Instrument Serif (often with an italic accent word) for "warm" or "editorial". None of these are bad typefaces. The problem is that they come with every template, or every generated page, so they signal that nobody chose. Use one when you have a specific reason, and write the reason in `DESIGN.md`.

A system font stack is a legitimate deliberate choice for utilitarian, brutalist or very fast sites. Use `system-ui, sans-serif`, or for serif `ui-serif, Charter, "Iowan Old Style", Georgia, serif`. It's a choice when the rest of the design commits to the austerity.

## Pairings by personality

These are all free on Google Fonts, and every one is in the killer-skills font pack (see Loading fonts). Each row offers several display faces because a list anchors. If every warm site takes the first name in its row, the table becomes a template of its own. Read across the row and choose by the concept's specific character (its era, its material, how loud it is) after looking at the actual glyphs, since two faces with the same label can feel very different. If you used a face on the previous project, choose another.

| Personality | Display options | Text options | Mono / extra |
|---|---|---|---|
| Warm, crafted, handmade (makers, food, local shops) | Young Serif, Gloock, Alegreya, Hepta Slab, Libre Caslon Display | Alegreya Sans, Literata, Hanken Grotesk, Figtree | Caveat, very sparingly, for a handwritten note |
| Precise, technical, engineered (dev tools, infra) | IBM Plex Sans, Red Hat Display, Chivo, Commissioner, Martian Mono (wide, as display) | IBM Plex Sans, Red Hat Text, Public Sans | IBM Plex Mono, Red Hat Mono, Chivo Mono, Azeret Mono |
| Quiet, trustworthy, institutional (privacy, finance, health) | Source Serif 4, Literata, Public Sans, IBM Plex Serif | Source Sans 3, Public Sans, Literata | IBM Plex Mono |
| Editorial, literary (writing, research, essays) | Newsreader, Spectral, Crimson Pro, EB Garamond, Petrona | Newsreader, Literata, Source Serif 4 | — |
| Refined, calm, luxurious (studios, architecture, hospitality) | Cormorant Garamond (light, large), Bodoni Moda, Gilda Display, Italiana, Marcellus | Albert Sans, Jost, Hanken Grotesk | — |
| Bold, loud, energetic (events, music, campaigns, sport) | Anton, Big Shoulders Display, Archivo (condensed, 800–900), Saira Condensed | Archivo, Barlow | — |
| Industrial, workshop, utilitarian | Barlow Condensed, IBM Plex Sans Condensed, Zilla Slab, or a system stack | Barlow, IBM Plex Sans | IBM Plex Mono, DM Mono |
| Friendly, human, approachable (community, education) | Bricolage Grotesque (heavy, opsz), Gabarito, Rubik | Figtree, Onest, Rubik | — |
| Systematic, modernist (design studios, data, transit-like clarity) | Schibsted Grotesk, Familjen Grotesk, Archivo, Public Sans | Same family | DM Mono |
| Retro, quirky, art-school | Syne (700–800), Unbounded, Rubik Mono One | Space Mono, Work Sans | — |
| Accessible, civic, public service | Atkinson Hyperlegible Next, Lexend, Public Sans | Atkinson Hyperlegible Next, Public Sans | Atkinson Hyperlegible Mono |

## Scale and hierarchy

Generate the scale with `scripts/scale.mjs`. Choose the ratio to suit the direction:
- **1.125–1.2**: dense, informational, UI-like; documentation.
- **1.25–1.333**: a general-purpose marketing or product site.
- **1.414–1.5+**: editorial or poster-like, with dramatic display type.

Fluid scaling lets the ratio grow with the screen (for example 1.2 on phones and 1.333 on desktop), so headlines are dramatic on large screens without overflowing on small ones.

Timid scales are a major reason pages look templated. If the hero headline is only about twice the body size, the page has no focal point. Hero display type on desktop is often 4–7rem, and sometimes more for poster-like designs. To go beyond the generated steps, write one clamp by hand, e.g. `clamp(3rem, 1rem + 8vw, 8rem)`, and check it at 390px.

Hierarchy comes from size, weight, color, case and space together. Use two or three of them at once, not all five.

## Spacing, measure and rhythm

- **Body text**: 16–18px (`--step-0`), line-height 1.5–1.7. Reading-heavy sites should lean toward 18px and 1.6.
- **Headings**: line-height 1.0–1.2, getting tighter as the size grows. Large display sans usually wants slight negative tracking (`letter-spacing: -0.01em` to `-0.03em`). Serifs usually need less.
- **All-caps labels**: small, `letter-spacing: 0.06–0.12em`, and only for short labels.
- **Measure**: 55–75 characters per line for body text (`max-width: 65ch` on the text block, not on the section).
- **Space a heading close to its own text** and farther from the previous section. Proximity shows what belongs together.
- **Paragraph spacing**: about one line of space (`margin-block: 0 1em`), or use the space tokens.

## Loading fonts

Self-host from the killer-skills font pack when it's installed. It has every face named in this file, as latin and latin-ext `.woff2` files with their licences:

```bash
node <skill-dir>/scripts/fonts.mjs list [text]                 # what's in it: weights, or a variable family's axes
node <skill-dir>/scripts/fonts.mjs add path/to/site "Newsreader" "Public Sans"
node <skill-dir>/scripts/fonts.mjs add path/to/site "IBM Plex Mono" --weights 400,600
```

`add` copies each family into `assets/fonts/<id>/` and writes `assets/fonts/fonts.css`, with one `@font-face` per file, `font-display: swap` and each subset's `unicode-range`. Link it before your own stylesheet and use the family names as usual. A variable family is one file per style that covers every weight (and its width or optical-size axes); for a static family, pass `--weights` with only the weights the design uses. Adding a family again replaces it. This works offline, and the finished site requests nothing from a font host.

If the pack isn't installed or lacks the face, use the Google Fonts css2 API:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,300..800;1,6..72,300..800&family=Public+Sans:wght@400..700&display=swap">
```

- Request only the weights, styles and axes you use. Ranges like `300..800` load a single variable file.
- Axis tags go in alphabetical order with lowercase tags before uppercase ones (`ital,opsz,wght,SOFT,WONK`). The API rejects other orders.
- Always use `display=swap`.
- Load the real italic and bold rather than letting the browser fake them. Faux bold and italic look smeared.
- Write a fallback stack in the same spirit: `"Newsreader", ui-serif, Georgia, serif`.
- Self-hosting is better for privacy and for offline use. Without the pack, that means downloading the `.woff2` files into `assets/fonts/` and writing the `@font-face` rules yourself, and it needs the network. If the project's audience or values call for it (privacy tools especially), self-host and say so.
- `shoot.mjs` reports whether each web font actually loaded. A FAIL there means visitors see the fallback.

## Finishing details

These cost almost nothing and are what make type look professionally set:

- `text-wrap: balance` on headings, so no line ends with a lone word. `text-wrap: pretty` on paragraphs.
- `font-optical-sizing: auto` (the default) for faces with an opsz axis. Let display sizes get the display cut.
- `font-variant-numeric: tabular-nums` for prices, tables and anything that lines up. Use `oldstyle-nums` in serif prose if the face supports it.
- Real punctuation: curly quotes and apostrophes (’ “ ”), en dashes for ranges (9–12), em dashes in sentences, × for dimensions, and `&nbsp;` between a number and its unit, or before a final short word you don't want stranded.
- `hanging-punctuation: first` (Safari only; harmless elsewhere) for pull quotes.
- Style `::selection` with palette colors.
- Underlines: `text-underline-offset: 0.2em; text-decoration-thickness: 1px` (or `from-font`). It looks far better than the default.
- Don't letter-space lowercase body text. Don't justify text on the web unless you also enable `hyphens: auto` and have checked it.
