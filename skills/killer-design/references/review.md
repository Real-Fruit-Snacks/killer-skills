# Review: the screenshot loop and the final polish

## How to look

1. Open the **desktop `-01` and mobile `-01`** slices first. That's what everyone sees, and most people decide in that moment. Squint, or imagine the image blurred, and ask what you see first, second and third. The answer should be the headline (or the thing itself), then the primary action, then the supporting visual. If your eye goes to a decoration, a badge or a background shape first, the hierarchy is wrong.
2. Go through every slice top to bottom at desktop, then mobile, then look at the tablet full-page image.
3. Read the script's FAIL and WARN lines.
4. Write down the **three to five biggest problems, in order of impact**, before you fix anything. Fix structure (hierarchy, layout, rhythm) before details (radii, shadows, exact colors). Small fixes don't save a weak structure.

Look as a critic who didn't make the page. The work isn't to defend it. The work is to find what a demanding design lead would circle in red.

## Questions to ask of every screenshot

**Wow**
- On the first screen, is there a moment that makes someone stop? Would they say "whoa" or only "nice"? If it's "nice", the page isn't done: be braver with scale, contrast or the centerpiece.
- Does the wow moment show something true about the project, or is it decoration?
- In the motion contact sheet, is the headline already readable in the first frames? Does the moment read as one idea? Is the pointer response alive without being frantic? Is the reduced-motion frame a finished design?

**Hierarchy**
- Is there one obvious focal point per screen?
- Is the contrast in scale big enough? Is the display type unmistakably the headline?
- Do secondary things look secondary? Is anything competing with the primary action?
- Does any single element pull attention it hasn't earned? A stray illustration detail, a decorative shape or an oddly colored word usually gets noticed first, for the wrong reason.

**Direction**
- Does this look like the concept in `DESIGN.md`, or like a template with new colors?
- With the logo hidden, would someone recognise this site? Is the signature visible on the first screen?
- Would a visitor say "this was made with AI"? Count the moves from "What makes a site look AI-made" in SKILL.md. Three or more means rework.

**Typography**
- Do headings break into balanced lines, without a lone word on the last line?
- Is the body line length about 45–80 characters, with comfortable line-height?
- Are the web fonts actually rendering (check the report), with real weights rather than faux bold?
- Is all-caps text letter-spaced, and small text still readable?

**Layout**
- Do edges line up: the left edges of text blocks, the tops of columns, the image edges against the text column?
- Is the spacing proportional, with more space between groups than within them? Any awkward gaps or crowded corners?
- Do sections vary in composition, or is every one a centered stack?
- At 1440, is anything stretched too wide or floating in empty space? At 390, is anything cramped, overlapping or crammed side by side?

**Color**
- Is the accent rare enough to mean something?
- Is muted text actually readable?
- Do any colors vibrate or clash? Does the page feel like the concept's material?

**Content**
- Is there any phrase that could appear on any website?
- Is any invented fact presented as real?
- Does the order of sections follow the visitor's questions? Does every button say what it does?

**Mobile**
- Is the first screen useful, with the headline and the action visible rather than just a big image or empty padding?
- Does the navigation work, and are the tap targets comfortable?
- Do tables and code blocks fit, or scroll inside their own box?

**Details**
- Are hover and focus states present and consistent?
- Are icons optically aligned with text, with one stroke weight throughout?
- Are images crisp and well cropped? Does the favicon read at 16px on light and dark tabs (`icons.mjs audit`)? Is `::selection` styled?

## Common problems and fixes

| What you see | Usual fix |
|---|---|
| No clear focal point | Make the headline much larger and heavier; remove or quiet competing elements; give it more space |
| Looks like a template | Strengthen the signature; replace the default font; replace card grids with a composition that fits the content; replace stock phrases with specifics |
| Cluttered | Remove elements rather than shrinking them; merge sections; add space between groups |
| Monotonous | Vary section composition and density; add one full-bleed moment; alternate alignment |
| Mobile first screen wasted | Cut hero padding and image height; get the action into the first ~600px; shrink the display step |
| Text feels cramped | Raise line-height and measure, increase paragraph spacing, increase body size |
| Colors muddy | Fewer colors; neutrals from one tinted family; the accent used in one role |
| An illustration looks clumsy, or one of its details looks wrong | Reduce it to a silhouette or geometric shapes, or replace it with type, a diagram or real UI. Don't polish it a third time |
| Reads as AI-made | Remove the stock moves (badge chip, accent-italic headline, icon cards, eyebrow on every section, glow CTA card) and put a concept-specific idea in their place |
| Competent but forgettable ("nice") | Push the wow moment: bigger type, a live demo, light that answers the pointer, a braver composition. Calm everything else down so it lands |
| Too many effects | Keep the single best moment and remove the rest. Wow is one idea, not ten |
| Things feel slightly off everywhere | Hard-coded numbers instead of tokens; inconsistent radii; misaligned edges. Audit against the tokens |

## Polish checklist (before handoff)

- [ ] The last `shoot.mjs` run shows no FAIL lines, or each remaining one is explained to the user.
- [ ] `<title>`, meta description, `og:title`, `og:description`, `og:image` (or a note that it's missing), `lang`, and the viewport meta tag.
- [ ] Favicon and app icons: `icons.mjs audit` shows no FAIL lines, and the preview sheet reads at 16px on both tab colors, with nothing cropped on the home screens.
- [ ] One `h1`; headings in order; `header`, `nav`, `main` and `footer` landmarks.
- [ ] Every interactive element has hover (inside `@media (hover: hover)`), `:focus-visible` and active states.
- [ ] Forms: real labels; correct `type`, `autocomplete` and `required` attributes; the correct `action` and `method`; a visible way to see success or failure.
- [ ] Images: `width`/`height` attributes so the layout doesn't jump, `alt` text (empty if decorative), `loading="lazy"` below the fold, sensible file sizes.
- [ ] Fonts: only the weights in use are loaded, `display=swap`, and preconnect is set (or the fonts are self-hosted).
- [ ] `prefers-reduced-motion` is respected, and nothing is hidden waiting for JS.
- [ ] The last `--motion` sheet shows the headline early, the moment working, and a finished reduced-motion state. LCP is under about 2.5s.
- [ ] No `href="#"` dead links. Placeholder links are visibly marked and listed.
- [ ] Copy: no unmarked placeholders; stand-ins and assumptions are listed for the user; real quotes and dashes.
- [ ] Printing works passably (optional, for content sites): `@media print` hides the nav and shows link URLs.
