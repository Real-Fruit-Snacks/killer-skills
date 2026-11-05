# Motion and the wow moment

## Contents
- What a wow moment is (and isn't)
- Choosing one
- Technique menu
- Tested recipes
- Performance and accessibility rules
- Checking it

## What a wow moment is (and isn't)

A wow moment is the thing that makes someone stop in the first few seconds and think "whoa". On the best sites it is:

- **One idea, executed brilliantly.** It's a centerpiece, not a sprinkle of effects. Everything else on the page stays calm so the moment has room.
- **Rooted in the concept or the brand.** It shows something true about the project: a backup tool's files visibly turning to ciphertext as they cross the waterline; a tool collection whose terminal actually runs; a bakery's week strip where Thursday's deadline stamps itself down. Because it carries meaning, it feels designed rather than decorated.
- **Fast.** It reads within 1–2 seconds of load, and the headline never waits for it.
- **Finished in every state.** With no JavaScript, with reduced motion and on a phone, the page still looks deliberate. The static state is a good design in its own right, not a broken animation.

Wow is also often *static*: type at a scale nobody else dares, an unexpected composition, a texture or light that feels physical. Motion is one tool among several. A page with brave typography and one perfect interaction beats a page with ten animations.

What wow is not, because these read as gimmicks or AI-made:

- effects soup, where everything fades, slides and floats;
- particle or blob backgrounds with no link to the subject;
- animated gradient text, glowing animated borders, cursor trails;
- preloader screens, scroll-jacking, parallax on text, autoplaying sound;
- motion that delays reading by more than about a second.

## Choosing one

In step 2, after the concept, ask: what is the single most interesting *true* thing about this project, and how could the first screen *show* it rather than say it? Write two or three candidate moments, pick the one that is most on-concept and that you can execute really well, and record it in `DESIGN.md` under "Wow moment", including how it looks without JS and with reduced motion.

Some starting points for the question:

| Project | A moment that shows it |
|---|---|
| A CLI or developer tool | The real command typing and running in the hero, with real output streaming in |
| A data or privacy product | The visitor's own action (typing, hovering) visibly transformed by the product, e.g. text encrypting as they type |
| A collection of things | The things themselves as the hero: a grid, a constellation or a list that assembles and reacts to the pointer |
| A physical business | One printed object rendered with care, e.g. a stamp, a ticket or a label, with a single satisfying motion |
| A portfolio | The work at a bold scale, with transitions between pieces that feel like turning pages |
| An event | Kinetic typography for the date and place, set as large as the screen allows |

## Technique menu

Pick based on the moment. Most are CSS-first, with a few lines of vanilla JS at most.

- **Choreographed entrance**: the first screen assembles in 600–1200 ms, with staggered elements and one clear order. *CSS only.*
- **Live demo in the hero**: a terminal that types and runs, or a UI that steps through a real workflow. *CSS typing plus timed reveals.*
- **Pointer-reactive light or field**: a glow, spotlight or dot grid that follows the cursor. *Custom properties plus about 10 lines of JS.*
- **Scroll-driven storytelling**: a diagram that builds, numbers that count, or a sticky scene that changes as you scroll. *CSS `animation-timeline`; no JS.*
- **Generative art from real data or the concept**: canvas or SVG, such as a field drawn from the project's own data, or a pattern from its domain. *Small canvas loop, paused off-screen.*
- **Kinetic type**: variable-font weight or width shifting, letters assembling, a headline that responds to scroll. *CSS animation of `font-weight` / `font-stretch`.*
- **Depth and light**: layered planes with subtle 3D tilt on pointer, realistic soft light, grain, blend modes. *CSS transforms plus a little JS.*
- **Delightful state changes**: a theme switch that sweeps in from the click point, copy buttons that confirm with character, hover that reveals a hidden detail (a tool's install line, a photo's caption). *View Transitions API, CSS.*

## Tested recipes

All of these were checked in Chromium. Each works without JS (or degrades to a finished static state) and respects reduced motion.

### Choreographed entrance (CSS only)

```css
@media (prefers-reduced-motion: no-preference) {
  .hero > * { animation: rise 800ms cubic-bezier(.2,.7,.2,1) both; animation-delay: calc(var(--i, 0) * 90ms); }
}
@keyframes rise { from { opacity: 0; transform: translateY(18px); } }
```
```html
<h1 style="--i:0">…</h1><p style="--i:1">…</p><a class="cta" style="--i:2">…</a>
```
Outside the media query nothing is hidden, so reduced motion and unsupported browsers simply show the page.

### Typing terminal (CSS only, text always in the DOM)

```css
.term .cmd, .term .out { display: block; white-space: pre; }
@media (prefers-reduced-motion: no-preference) {
  .term .cmd { clip-path: inset(0 100% 0 0); animation: type calc(var(--n) * 45ms) steps(var(--n)) forwards; animation-delay: var(--at); }
  .term .out { opacity: 0; animation: show 1ms forwards; animation-delay: var(--at); }
}
@keyframes type { to { clip-path: inset(0 0 0 0); } }
@keyframes show { to { opacity: 1; } }
```
```html
<span class="cmd" style="--n:26; --at:900ms">$ gh repo list my-org</span>
<span class="out" style="--at:2100ms">alpha   rust   does one thing</span>
```
`--n` is the number of characters in the command. Use a monospace font. Stagger output lines by 100–200 ms for a streaming feel. The text is readable by screen readers and search engines from the start.

### Pointer-reactive light

```css
.hero { background: radial-gradient(520px circle at var(--x, 72%) var(--y, 38%), color-mix(in oklch, var(--accent) 22%, transparent), transparent 65%), var(--bg); }
```
```js
const hero = document.querySelector('.hero');
if (matchMedia('(hover: hover) and (pointer: fine)').matches) {
  let raf = 0, px = 0, py = 0;
  hero.addEventListener('pointermove', (e) => {
    const r = hero.getBoundingClientRect(); px = e.clientX - r.left; py = e.clientY - r.top;
    if (!raf) raf = requestAnimationFrame(() => { hero.style.setProperty('--x', px + 'px'); hero.style.setProperty('--y', py + 'px'); raf = 0; });
  });
}
```
The fallback position (`72% 38%`) is the static design. Touch devices keep it.

### Scroll-driven reveal or scene (CSS only)

```css
@supports (animation-timeline: view()) {
  @media (prefers-reduced-motion: no-preference) {
    .reveal { animation: rise linear both; animation-timeline: view(); animation-range: entry 0% cover 35%; }
  }
}
```
Declare `animation-timeline` *after* the `animation` shorthand, because the shorthand resets it. For a sticky scene that plays as the visitor scrolls through it:
1. Make the section taller than the screen (`height: 200vh`) and give it `view-timeline-name: --scene`.
2. Put a `position: sticky; top: 0; height: 100vh` stage inside it.
3. Give the animated pieces `animation-timeline: --scene; animation-range: contain 0% contain 100%`.

The animation then runs from 0 to 100% while the section scrolls past.

### Theme switch that sweeps from the click point (View Transitions)

```css
::view-transition-old(root), ::view-transition-new(root) { animation: none; mix-blend-mode: normal; }
```
```js
button.addEventListener('click', (e) => {
  const apply = () => { /* flip the theme attribute and save it */ };
  if (!document.startViewTransition || matchMedia('(prefers-reduced-motion: reduce)').matches) return apply();
  const x = e.clientX, y = e.clientY, r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  document.startViewTransition(apply).ready.then(() => {
    document.documentElement.animate({ clipPath: [`circle(0 at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] },
      { duration: 550, easing: 'cubic-bezier(.2,.7,.2,1)', pseudoElement: '::view-transition-new(root)' });
  });
});
```

### Generative canvas, paused off-screen

```js
const c = document.querySelector('canvas.field'), ctx = c.getContext('2d');
const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
let visible = true;
new IntersectionObserver(([en]) => { visible = en.isIntersecting; }).observe(c);
function size() { const d = Math.min(devicePixelRatio, 2); c.width = c.clientWidth * d; c.height = c.clientHeight * d; ctx.setTransform(d, 0, 0, d, 0, 0); }
size(); addEventListener('resize', size);
function draw(t) { /* draw one frame from t; read colors from CSS custom properties */ }
if (still) draw(0); else (function loop(t) { if (visible) draw(t); requestAnimationFrame(loop); })(0);
```
Mark the canvas `aria-hidden="true"`, keep it behind the content (`z-index: -1` inside an `isolation: isolate` parent), and keep the drawing subtle enough that text on top still passes contrast.

### Subtle 3D tilt on pointer

```js
el.addEventListener('pointermove', (e) => {
  const r = el.getBoundingClientRect(), dx = (e.clientX - r.left) / r.width - 0.5, dy = (e.clientY - r.top) / r.height - 0.5;
  el.style.transform = `perspective(900px) rotateY(${dx * 8}deg) rotateX(${dy * -8}deg)`;
});
el.addEventListener('pointerleave', () => { el.style.transform = ''; });
```
Keep the angle within ±8°, add `transition: transform 200ms ease-out`, and gate it behind the same `(hover: hover) and (pointer: fine)` check.

## Performance and accessibility rules

- **Animate cheap properties**: `transform`, `opacity`, `clip-path`, and `filter` sparingly. Don't animate width, height, top or left on large elements.
- **The headline is visible within about a second.** Entrances total 1.2 s at most, and the LCP that `shoot.mjs --motion` reports stays under about 2.5 s.
- **Gate the effects.** Pointer effects only run on `(hover: hover) and (pointer: fine)`. Canvas loops pause off-screen and cap the device pixel ratio at 2. Nothing runs when the tab is hidden; `requestAnimationFrame` handles that.
- **Reduced motion gets a finished static design**, not a frozen half-animation. Put every animation inside `@media (prefers-reduced-motion: no-preference)`, or check `matchMedia` in JS.
- **No JavaScript means no missing content.** Never hide content in CSS waiting for JS to reveal it.
- **Keep the JS small.** Vanilla and hand-written, in the site's own files: no frameworks, no CDNs, no libraries for things CSS can do. A wow moment rarely needs more than 40–80 lines.
- **Keep motion within safe limits.** No flashing more than three times a second. Large-scale motion such as parallax and zooming planes stays gentle, because it can trigger vestibular problems.

## Checking it

Still screenshots can't show motion, so use:

```bash
node <skill-dir>/scripts/shoot.mjs path/to/site --motion
```

It adds a contact sheet per width (`-motion.png`). The sheet shows the first ~4 seconds as frames, then the pointer at three positions, the main button hovered, and the reduced-motion state. The report says whether anything moved on load, when it settled, whether the page responds to the pointer, and when the largest content painted (LCP). Read the sheet and ask:

- Do the first two frames already show the headline, or is the visitor waiting?
- Does the moment read as one clear idea?
- Does the pointer response feel alive rather than laggy or overdone?
- Does the reduced-motion frame look like a finished design?
