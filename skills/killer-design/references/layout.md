# Layout and composition

## Contents
- Page structure
- Grid
- Rhythm and spacing
- Section patterns
- Breaking the grid on purpose
- Responsive
- Motion (and the wow moment: see `motion-and-wow.md`)

## Page structure: follow the visitor's questions

Order sections by what a visitor needs to know, in roughly this sequence:
1. What is this?
2. Is it for me?
3. How does it work, and what do I get?
4. Why should I trust it?
5. What does it cost, and how do I start?

Then come details, the FAQ and the footer. Not every page needs every step. Cut any section that exists only because templates have one. A small-business page might be "what we make → where and when → how to order → about us". A CLI tool might be "what it does → install → how it works → details → the repo".

**The first screen should answer the visitor's main question.** For a tool, that might be the install command. For a café, the hours and the address. For a portfolio, the work itself. It doesn't have to be a slogan.

## Grid

Every element should align to something. A shared left edge reads as designed. A stack of centered blocks reads as a template. Center only short, symmetrical content, such as a single statement or a closing call to action.

A "breakout" grid gives you a reading column, a wider column and full bleed without any wrapper divs:

```css
.page {
  --gutter: var(--space-s-l);
  --content: 42rem;   /* reading width */
  --wide: 76rem;      /* layouts, galleries, product shots */
  display: grid;
  grid-template-columns:
    [full-start] minmax(var(--gutter), 1fr)
    [wide-start] minmax(0, calc((var(--wide) - var(--content)) / 2))
    [content-start] min(var(--content), 100% - 2 * var(--gutter)) [content-end]
    minmax(0, calc((var(--wide) - var(--content)) / 2)) [wide-end]
    minmax(var(--gutter), 1fr) [full-end];
}
.page > * { grid-column: content; }
.page > .wide { grid-column: wide; }
.page > .full { grid-column: full; }
```

Inside wide sections, use a 12-column grid (`repeat(12, minmax(0, 1fr))`) or `auto-fit` patterns. Use `minmax(0, 1fr)`, not plain `1fr`, so long content can't push columns wider than the screen.

## Rhythm and spacing

- Use only the space tokens. Section padding usually comes from a fluid pair (`--space-xl-3xl`).
- Space within a group should be smaller than space between groups. This one rule fixes most layouts that look slightly off.
- Vary density on purpose. A dense spec table next to an airy one-line statement gives the page a rhythm. Uniform sections make a page monotonous.
- Use alternating backgrounds sparingly. A rule line, a change of grid or simply more space often separates sections more elegantly than a new background color.

## Section patterns

Choose each pattern because of the content, not out of habit.

**Hero**
- *Typographic statement*: an oversized, left-aligned headline with a one-line standfirst, and the action directly below. The product or a detail can sit beside or beneath it.
- *Split*: copy on one side, the real product on the other (a terminal session, an app window, a menu card). On mobile, copy comes first.
- *Editorial opener*: a kicker, headline, standfirst and meta line, like a magazine feature.
- *Utility first*: the thing itself above the fold, whether an install command with a copy button, a search box, or hours and an address.
- *Poster*: one huge word or number, very little text, a strong color field. Suits events and campaigns.

**Features and "how it works"**, as alternatives to three icon cards:
- A numbered list set large: "01 Watches the folder · 02 Encrypts locally · 03 Syncs to your bucket".
- A two-column definition list: the term on the left, the explanation on the right. Calm and very readable.
- One annotated product view with callouts.
- A step sequence with the real commands and output.
- A comparison table: honest, and useful for picking between options.
- Long-form prose with sticky subheads, for things that need explaining.

**Proof.** Use only what's real: one strong quote set large, a changelog or release history, the license and open-source status, real press, real numbers taken from the project.

**Pricing.** Use a table when comparing tiers, or a single clear statement when there's one price. Make numbers tabular.

**FAQ.** Use `<details>`/`<summary>`: accessible, no JavaScript, and easy to style. Answer real questions; don't pad.

**Closing call to action.** Repeat the primary action, and make it match the hero's.

**Footer.** Keep it useful: contact, key links, license or legal text, maybe a colophon naming the fonts. Don't dump a sitemap.

## Breaking the grid on purpose

Give the page one or two moments that step outside the system: an element that bleeds to full width, type overlapping an image, a giant numeral hanging in the margin, a rotated stamp or label. They stand out precisely because everything around them is disciplined. Three or more of them turns the page into chaos.

## Responsive

- **Design the phone layout as its own composition.** Don't just squash the desktop. Display sizes shrink through the fluid tokens, columns stack, and the first screen still shows the headline and the action. Keep DOM order the same as reading order, even if CSS reorders things visually.
- **Navigation.** With four links or fewer, keep them visible on mobile (wrapping or a compact row), because hamburger menus hide navigation. If you need a toggle, use a real `<button aria-expanded aria-controls>` that works by keyboard, or `<details>`.
- **Common causes of sideways scrolling**: fixed widths; `100vw`, which includes the scrollbar; long URLs, emails and code (use `overflow-wrap: anywhere`, or wrap code in a `pre` with `overflow-x: auto`); wide tables (put them in a scroll container); grid tracks of `1fr` with wide content (use `minmax(0, 1fr)`); and negative margins. Don't hide the problem with `overflow-x: hidden` on `body`. Fix the element. `shoot.mjs` names the culprit.
- **Touch targets**: at least 44px tall for primary actions, and never under 24px for anything tappable.
- **Text in images, and decorative elements**: check that they don't collide with text at 390px.

## Motion

Section-level motion stays small and purposeful: hover and focus transitions of 150–250ms with an ease-out curve, and subtle entrances that run once. That restraint gives the page's one wow moment room to land. The wow moment itself, scroll-driven scenes, pointer effects and all the recipes are in `motion-and-wow.md`.

- Content must be visible without JavaScript and without animation. Never hide content waiting for a script.
- No scroll-jacking, no parallax on text, and nothing autoplaying that can't be paused.
- `@media (prefers-reduced-motion: reduce)` gets a finished static design.
