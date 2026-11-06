---
name: killer-design
description: Design and build striking, memorable websites in hand-written HTML, CSS and a little vanilla JS (landing pages, product and project sites, portfolios, small-business and personal sites), and redesign existing ones, so visitors who load the page think "whoa". Sets an original direction per project or works within an existing brand, designs one wow moment for the first screen, writes real copy, builds a responsive token-based page with a full favicon and app icon set, then screenshots and motion-captures it at phone, tablet and desktop sizes with a bundled Playwright script and fixes what it sees. Use this whenever the user wants to create, design, build, mock up, restyle, redesign, refresh or "make nicer" a website, web page, landing page, homepage or a section of one, even if they only say "make a page for my project" or "this site looks dated", and to design, generate or check a site's favicon or app icons. For critique-only requests where nothing should change, design-critique fits better.
---

# Killer design

The bar is a site that people load and think "whoa": one that a top studio could have made for *this* project. Tidy, correct and tasteful is the floor, not the goal. A page that only avoids mistakes is forgettable. Three habits get you there, and this skill is built around them:

1. **Decide the design before writing code.** Generic sites come from never making a decision. Fonts, colors and layout default to what every template uses. A short written direction, rooted in the subject (or in the brand, if there is one), gives every later choice a reason.
2. **Design one wow moment.** Give the first screen a centerpiece, rooted in the concept, that makes someone stop: a live demo, brave typography, light that answers the pointer, a scene that builds as you scroll. Execute it to a high standard and keep everything around it calm so it has room. See `references/motion-and-wow.md`.
3. **Look at the result.** CSS written blind is always off: cramped mobile layouts, weak hierarchy, gray text nobody can read, animation that drags. Render the page, motion included, look at it like a critic, fix the problems, and look again.

The work goes in six steps: understand the project, set a direction, write the content, build, review the screenshots and fix, then polish and hand off. Redesigns add an inventory step first (see "Redesigning an existing site").

## 1. Understand the project

Read what already exists before asking anything. That means the README, docs, package or app metadata, existing copy, logos and brand files, screenshots, and any `DESIGN.md`. Work out:

- **What it is**, in one sentence a stranger would understand.
- **Who arrives, and what they need to decide or do.** Name the single primary action: install, preorder, sign up, get in touch.
- **The specifics**: real commands, numbers, names, hours, places, features, constraints, story. This is the raw material for copy and visuals, and specific detail is what keeps a site from feeling generic.
- **Personality**: three adjectives, plus one thing it is *not*. "Precise, quiet, trustworthy; not playful."
- **Constraints**: hosting (for example static GitHub Pages), anything that must stay, and existing brand assets.
- **Documented intent**: what the README or docs say the site is for, and what it deliberately leaves out ("one page, a button to GitHub; the full list lives elsewhere"). That is a decision someone already made. Keep to it, and offer anything beyond it as a proposal in the handoff rather than building it.
- **Whether a brand system exists**: a brand guide (`BRAND.md`, a style guide, a brand repo), design tokens, a `DESIGN.md`, or a consistent set of finished brand artwork (social cards, banners, README covers, the current site). If one exists, step 2 works differently. See "Working within an existing brand" below.

Ask the user only for things you can't find or sensibly infer *and* that would change the design. The obvious example is what the product actually does. For everything else, make a reasonable call and list your assumptions in the handoff. Reacting to a real page is easier for them than answering a questionnaire.

## 2. Set the design direction

If the project already has a brand system, its brand *is* the direction. Skip ahead to "Working within an existing brand" and come back for step 3. What follows is for projects that need a design invented.

"Clean and modern" is not a direction. It's what you get when no decision was made.

**Find the concept in the subject.** Ask what world the project lives in. A violin repair workshop suggests spruce and varnish, hand-lettered labels, measured drawings. A city open-data portal suggests transit maps, signage systems and timetables. A climbing gym suggests topo maps, chalk and route grades. Go past the first association to something specific: "paper and ink" applies to almost anything, while "a seed packet from a 1950s catalogue" doesn't. The concept should shape the type, palette, texture and layout. It should not turn into clip art or puns.

**Sketch three genuinely different directions** in a line or two each, varying the concept, type, color temperature and layout attitude. Then pick the one that best fits the audience and personality. The first idea that comes to mind is usually the most generic, which is why you write three. Check the chosen direction against "What makes a site look AI-made" near the end of this file. If it amounts to one of the stock palettes plus the usual sections, push it further.

**Choose the wow moment.** Ask what the single most interesting *true* thing about the project is, and how the first screen could *show* it rather than say it. Write two or three candidate moments and pick the one that's most on-concept and that you can execute beautifully. Read `references/motion-and-wow.md` for the technique menu and tested recipes. Then ask honestly whether someone seeing this would say "whoa" or "nice". If the answer is "nice", be braver: go bigger in scale, make a stronger contrast, make the moment more alive.

**Write the direction down** in `DESIGN.md` at the site root, using the template below. The file keeps future pages and future sessions consistent. **If a `DESIGN.md` already exists, follow and extend it** rather than inventing a new look, unless the user asked for a new direction.

Before choosing type and color, read `references/typography.md` and `references/color.md`. They contain font pairings and a palette method that avoid the usual defaults. `references/layout.md` covers compositions beyond "hero plus three cards".

```markdown
# Design: <project>

## Concept
One or two sentences: the world this lives in and the feeling a visitor should leave with.

## Audience and primary action
Who arrives, what they need to know, the one thing we want them to do.

## Type
Display: <family, weights>, and why. Text: <family, weights>, and why. Mono, if any.
Scale: base size and ratio (from scripts/scale.mjs).

## Color
Role -> value: bg, surface, ink, ink-muted, line, accent, on-accent, focus.
Usage rules (e.g. "accent only on the primary action and active states").

## Layout
Grid and widths, section rhythm, overall attitude (editorial, dense, airy, asymmetric...).

## Signature
The one or two moves people will remember this site by.

## Wow moment
What makes someone stop on the first screen, and how it works. Also how it looks with no JS, with reduced motion, and on a phone.

## Imagery and texture
What the visuals are and how they're made, given what assets exist.

## Motion
What moves, how and how much. Respects reduced motion.

## Voice
How the copy sounds; words to use and words to avoid.

## Alternatives considered
The other two directions, one line each, so the user can redirect.
```

Pick a direction and build it. Don't stop to ask the user to choose among abstractions. List the alternatives in the handoff so they can redirect once they've seen a real page.

## 3. Write the content first

Layout follows content. A design drawn around lorem ipsum produces generic boxes, and generic copy makes even a good layout read as a template. Write the real words before laying anything out:

- **Headline**: what it is, or what changes for the visitor, in plain words. Specific beats clever. "Book a court at any public tennis club in Leeds" beats "Your game, elevated."
- **Use the project's specifics**: real commands, real numbers, real names, the actual hours and place.
- **Cut stock marketing language**: unlock, elevate, seamless, empower, revolutionize, supercharge, effortless, cutting-edge, robust, leverage, game-changer, "take X to the next level", "in today's fast-paced world". If a sentence could appear on any website, rewrite it.
- **Avoid AI sentence shapes as well as AI words.** Watch for:
  - every section heading built on the same two-beat formula ("One binary. Pick how you get it.", "Get it back, as of now or as of then.");
  - "No X. No Y. Just Z.";
  - triplets of adjectives;
  - a pun in every heading.

  A plain label ("Install", "Restore a snapshot") is often stronger than a clever line, and it gives the one headline that *is* crafted room to stand out.
- **Button labels say what happens**: "Download the app", "Reserve a table", "Book a repair", not "Get started" or "Learn more".
- **Never present invented facts as real.** No made-up prices, addresses, hours, statistics, testimonials, customer logos or "trusted by 10,000 teams". These are the user's own projects, and fabricated claims are a liability that tends to ship by accident. When a section genuinely needs a fact you don't have, put in a visible stand-in, such as `<span class="todo">[price]</span>` styled so it stands out, and list each one in the handoff.

## 4. Build

**Tokens first.** Before any component, define the system as CSS custom properties at the top of `styles.css`: color roles, type steps, space steps, radii, shadows, text measure and motion timings. Generate the fluid type and space scales with:

```bash
node <skill-dir>/scripts/scale.mjs --min-ratio 1.2 --max-ratio 1.333   # pick ratios to suit the direction
```

After that, use tokens instead of hard-coded numbers. Hard-coded numbers drift, and the drift is what makes spacing look slightly off everywhere.

The scripts need Node 20 or newer. If `node` isn't on the PATH, run them with `~/.cache/killer-skills-node/node.exe` instead: the Windows installer puts Node there when it's missing.

**Fonts.** Self-host them from the killer-skills font pack, which has every face `references/typography.md` names:

```bash
node <skill-dir>/scripts/fonts.mjs list                       # families, with their weights or axes
node <skill-dir>/scripts/fonts.mjs add path/to/site "Newsreader" "IBM Plex Mono" --weights 400,600
```

`add` copies the files and their licences into `assets/fonts/` and writes `assets/fonts/fonts.css`. Link that stylesheet before `styles.css`, and preload only the face the first screen depends on (the script prints the line). It needs no network, and the finished site requests nothing from a font host. If the pack isn't installed or doesn't have the face, load it from Google Fonts as `references/typography.md` describes.

**Files.** For a new site, unless the project dictates otherwise:

```
site/            (or wherever the user or project wants it)
  index.html
  styles.css
  DESIGN.md
  icon.svg, favicon.ico, apple-touch-icon.png, icon-192/512.png, icon-maskable-512.png, manifest.webmanifest
  assets/        (svg, images, fonts/)
  script.js      (the wow moment and small interactions; keep it short)
```

**HTML.** Use semantic landmarks (`header`, `nav`, `main`, `section` with headings, `footer`), one `h1` and headings in order. Use `<button>` for actions and `<a>` for navigation, and give every form field a `<label>`. Every page needs `lang`, the viewport meta tag, a real `<title>`, a meta description, Open Graph tags, and a full favicon and app icon set.

**CSS.** Plain modern CSS, mobile first:
- `grid` and `flex` for layout, with intrinsic patterns like `repeat(auto-fit, minmax(min(100%, 18rem), 1fr))`.
- Fluid tokens via `clamp()`, and a reading measure around 60–70ch.
- `:focus-visible` styles that suit the design. Hover effects go inside `@media (hover: hover)`.
- `text-wrap: balance` on headings and `text-wrap: pretty` on paragraphs.
- `@media (prefers-reduced-motion: reduce)`.

**JavaScript.** Use it where it makes the experience: the wow moment, pointer effects, a theme switch that sweeps, a live demo, a mobile nav toggle (a `<button aria-expanded>`, or `<details>`). Keep it vanilla and hand-written in the site's own files: no frameworks, no CDNs, nothing loaded from elsewhere, and usually under a hundred lines. Build it as an enhancement. Content must be visible without JS. Never hide sections at `opacity: 0` while waiting for a script to reveal them, because when the script fails, the content is gone. Prefer CSS for anything CSS can do (entrances, typing effects, scroll-driven scenes).

**Motion.** Motion everywhere else stays quick and quiet: 150–250ms hover and focus transitions, small entrances. That calm is what lets the wow moment land. Every animation sits inside `@media (prefers-reduced-motion: no-preference)` or behind a `matchMedia` check, and the reduced-motion version is a finished static design. Recipes and performance rules are in `references/motion-and-wow.md`.

**Favicon and app icons.** Design the favicon as seriously as the hero: it's the most-seen piece of the design. At 16px a wordmark turns to mush, so most marks need a reduced version for small sizes, such as one bold letter or one shape, on a tile that reads on both light and dark tabs. Read `references/favicons.md`. Then build the set from one SVG and check it:

```bash
node <skill-dir>/scripts/icons.mjs build mark.svg --out path/to/site --bg "#hex" --name "Site" --theme-color "#hex"
node <skill-dir>/scripts/icons.mjs audit path/to/site     # preview sheet: tabs at 1x/2x, light/dark, home screens
```

**Visuals without stock photos:**
- **Software**: build the product's actual interface in HTML and CSS, such as a styled terminal session with real commands and output, or an app window with realistic data. It's more convincing than any illustration and stays on brand.
- **Typography as image**: oversized headlines, numerals, pull quotes, a menu set like a printed card.
- **SVG and CSS**: geometric shapes, diagrams, maps, charts, patterns, grain texture (an SVG `feTurbulence` filter at low opacity), simple inline icons with one consistent stroke width.
- **Be wary of drawing real things by hand.** Food, people, animals, plants, buildings and tools are where hand-coded SVG most often fails. Proportions drift, and one detail (a score mark on a loaf, a highlight, a hand) reads as something else and draws the eye for the wrong reason. A clumsy illustration hurts a page more than having none. So:
  - Prefer the visuals code does well: type, geometry, diagrams, real UI.
  - If an object really needs to appear, keep it flat and reduced: a silhouette and two or three shapes, no fussy details.
  - Judge it at its displayed size. Would a stranger name it in one second? Does any part of it stand out more than it should?
  - If it still looks off after one round of fixes, swap it for a simpler approach rather than refining it again.
- **Real photos**, when the project has them. When the design needs photos that don't exist yet, use art-directed placeholders: a block at the intended aspect ratio, filled with a palette tone, captioned with what the photo should show ("Photo: hands shaping a loaf, morning light"). List them in the handoff. Don't hotlink random stock-photo URLs. They break, and they look like every other site.
- **No emoji as icons.** They render differently on every platform and read as a placeholder.

## 5. Look at it, critique it, fix it

You can't judge a design you haven't seen. Render it:

```bash
node <skill-dir>/scripts/shoot.mjs path/to/site            # a folder with index.html, a .html file, or a URL
node <skill-dir>/scripts/shoot.mjs path/to/site --dark     # also render dark mode, if the site has one
node <skill-dir>/scripts/shoot.mjs path/to/site --motion   # also capture the first seconds, pointer and hover as a contact sheet
```

The script serves the folder locally and captures each of three widths (390 phone, 768 tablet, 1440 desktop). It takes viewport-sized slices from top to bottom (`-01.png` is the first screen) plus one full-page image. It also checks what is hard to see in code: sideways scrolling, text contrast, fonts that failed to load, broken images, missing focus styles, small tap targets, tiny text, line length, heading order, dead links, placeholder text and missing meta tags. It prints where it saved everything, along with FAIL, WARN and INFO lines, and writes `report.json`. It uses Playwright from the project if there is one, then the killer-skills runtime in `~/.cache/killer-skills` (the Windows installer puts Playwright and its Chromium there, so nothing downloads). Otherwise the first run installs Playwright and Chromium into `~/.cache/killer-design`, which takes a minute or two and needs the network.

Then actually look, using the Read tool on the PNGs. Start with the desktop and mobile `-01` slices, because first impressions decide whether anyone scrolls. If the page moves, read the `-motion.png` contact sheets next: they're the only way to see the entrance, the pointer response, the hover and the reduced-motion state. Use `--motion` on at least the first and last rounds. Then go through every slice. Review it like a design lead who didn't make it, using `references/review.md`, and run the AI-made test from the end of this file. Write down the three to five biggest problems in order of impact, then fix them. Fix structure (hierarchy, layout, rhythm) before details. Shoot again and repeat. Plan on at least two rounds. Stop when another round wouldn't change anything a visitor would notice.

Treat FAIL lines as bugs. WARN lines need judgment.

If the script can't run (no Node, even in `~/.cache/killer-skills-node`, or no network for the first install), say so plainly and review the code against the checklist instead. Never claim to have checked something visually when you haven't.

## 6. Polish and hand off

Run through the polish checklist at the end of `references/review.md`, then shoot one last time. In the handoff, tell the user:

- What you built and where (file paths), and how to view it.
- The direction in two sentences, and what the signature is.
- The final screenshots: give the paths of the desktop and mobile `-01` images, and show them inline if your interface can display images.
- Every stand-in that needs real content (prices, photos, facts), and every assumption you made.
- The two alternative directions, one line each, as an offer: "If this isn't the feel you want, I could take it toward…".
- Anything you couldn't verify.

## Working within an existing brand

When a project has a real brand system (see step 1), someone has already made the big design decisions. Your job is craft within that system, not a new concept laid on top of it. Inventing a fresh direction for a branded site produces something clever that doesn't look like the rest of the brand, and the owner will rightly prefer a page that does.

- **Read the brand's finished artwork, not just its rules.** Look at the social card, banners, README covers, avatar and current site. They show how the brand composes a page, and the strongest move is often to make the site look like them. For example, if every cover is "name and tagline on the left, terminal panel on the right", that is the hero layout. A site that matches its own link preview feels deliberate.
- **The AI-made list doesn't apply to choices the brand made.** A dark field with an accent glow, a dot grid or a terminal window with traffic-light dots may be on that list as *defaults*. If the brand specifies them, they're the brand: use them as specified. The list still applies to anything the brand leaves open.
- **Keep the brand's values**: colors, type sizes, weights, tracking, radii, voice rules, banned words. Paste tokens verbatim from the brand's token file so they're easy to keep in sync. Treat `shoot.mjs` WARN lines (such as text under 12px) as a judgment call when the brand specifies that size. Deviate only for an actual accessibility failure, such as contrast below AA or tap targets under 24px. Then fix it on the page and report it as a fix to make in the brand itself, with the measured numbers.
- **Don't write a parallel rulebook.** If a brand guide exists, don't create a new `DESIGN.md` that restates it. Point to the brand guide in the README, and record only decisions specific to this page, if there are any.
- **The brand sets the vocabulary, not the ambition.** A brand system is still no excuse for a basic page. Find the wow moment *inside* the brand: make its signature elements come alive. A brand whose covers show a terminal could have a terminal that actually types and runs. A brand with an accent glow could have the glow follow the pointer across its texture. A brand with two themes could switch between them with a sweep. The brand's rules constrain the materials, not how brave you are with them.
- **Where you add value**: the wow moment, hierarchy, layout within the brand's patterns, content that answers the visitor's first question on arrival, responsive behaviour, accessibility, and the small craft details.
- **In the handoff**, keep "if this isn't the feel you want" alternatives within the brand: different compositions, not different identities.

A dated site with no real system (a few colors, a logo, inconsistent styling) isn't a brand system. Keep the brand-recognition pieces from "Redesigning an existing site" step 3, and design a new direction as usual.

## Redesigning an existing site

A redesign that breaks the signup form or drops a page counts as a regression, however good it looks. Before you change anything:

1. **Screenshot the current site**: `shoot.mjs <path> --label before`. This is your baseline and the user's before-and-after.
2. **Inventory what must survive**: every piece of content and every page, all links and anchor IDs (other sites may link to them), forms (the `action`, the `method`, every field `name`, hidden inputs), scripts such as analytics, and meta and SEO tags. Write the list down and check it again at the end.
3. **Decide what to keep for brand recognition**: name, logo, and perhaps a signature color. If the site belongs to a real brand system, keep all of it and follow "Working within an existing brand". Diagnose what's actually wrong (dated type, weak hierarchy, clutter, generic copy), because that diagnosis shapes the new direction.
4. **Choose between restyling and rebuilding.** If the markup is reasonable, keep it and replace the CSS. If it's tangled (tables for layout, deeply nested divs), rebuild the markup but keep the content, IDs and form fields.
5. **Copy**: tightening is welcome, but don't silently change meaning, claims, prices or legal text. Tell the user what you rewrote.
6. **Work within the existing stack.** If the site uses a framework or templating, the method here still applies.
7. **After**: shoot it again, compare against the before images, and check the inventory item by item, the form first. Include the before and after screenshot paths in the handoff.

## What makes a site look AI-made, and what to do instead

People have learned to recognise the look of AI-built sites, and once they spot it they trust the whole thing less. The look comes from a stack of moves that every landing page has, each chosen because landing pages have it rather than because this content needed it. Models reproduce that average very reliably. No single move gives it away; the giveaway is several of them together.

**Moves that currently read as AI-made.** These are the defaults capable models reach for today, and they've already moved on from the 2023 clichés:

- A pill-shaped badge or chip above the headline ("● v0.9 · MIT", "BETA · Welcome to…", "✨ New").
- A headline whose last phrase is set in italic and/or the accent color ("The notes app you *host yourself.*").
- A primary button with a → arrow beside an outlined "ghost" button, with a row of ✓ checkmark facts underneath.
- A product mockup with macOS traffic-light dots, and "floating" status chips or toasts with shadows overlapping its edges.
- A grid of feature cards, each with an icon in a tinted rounded square, a bold title and two lines of text.
- A small uppercase, often monospace and accent-colored, label above *every* section heading ("HOW IT WORKS", "WHY I BUILT THIS").
- Step cards with circled numbers joined by a line, and tag chips inside the cards.
- Alternating tinted background bands for every section; wave or curve SVG dividers between them.
- A dark, rounded call-to-action card near the end, with a soft radial glow.
- A big decorative quote mark plus an avatar circle holding an initial.
- Rows of pill-shaped chips for lists (integrations, platforms, tags).
- Two stock palettes. "Tech": a near-black navy hero, a mint or teal accent and a glow. "Warm": cream paper, a soft serif display and a terracotta accent.

**The older tells**, which still count: purple-to-blue gradients; Inter, Roboto, Poppins or Montserrat by default, or Space Grotesk as the automatic "tech" font; emoji as icons; glassmorphism; floating blurred blobs; bento grids for even content; invented logo walls, testimonials and statistics; a shadow and `border-radius: 16px` on everything; low-contrast gray text meant to look elegant; every section centered with identical padding.

**The test.** Before handing off, look at the screenshots and count how many items from these lists appear. One or two, chosen deliberately, is fine. Three or more means the page will read as AI-made, so replace the weakest ones with something the concept suggests. Also describe the page in one sentence. If that sentence would fit a thousand other sites ("dark hero with a terminal mockup and feature cards"), it isn't designed yet.

What strong sites do instead:

- **One typographic voice with real contrast in scale.** Display type several times the body size creates hierarchy without decoration.
- **A restrained palette.** Mostly neutrals tinted toward the concept's hue, plus one accent used rarely enough to mean something.
- **Varied composition on a consistent system.** A dense spec table, an airy one-line statement, an asymmetric split: each section takes the form its content needs, but all of them share one grid and one spacing scale.
- **Specificity.** Real details, the real product interface, a real voice.
- **A signature.** One memorable move that comes from the concept, such as numerals hung in the margin, a menu set like a printed card, or a live-looking terminal.
- **Craft in small things.** Focus and hover states, a styled `::selection`, balanced headings, icons aligned optically with text, nothing that jumps around while loading.

## Reference files

- `references/typography.md`: choosing and pairing fonts (with options that avoid the defaults), scales, loading (self-hosted from the font pack, or Google Fonts), finishing details. Read it when choosing type.
- `references/color.md`: building a palette in OKLCH, contrast targets, dark mode, gradients and texture. Read it when choosing color.
- `references/layout.md`: page structure, grids, rhythm, section patterns, responsive strategy. Read it when planning the page.
- `references/motion-and-wow.md`: what a wow moment is, the technique menu, tested recipes (entrances, typing terminal, pointer light, scroll scenes, theme sweep, generative canvas, tilt), and performance and accessibility rules. Read it in step 2, when choosing the wow moment, and again in step 4.
- `references/review.md`: the critique questions for screenshot review, common fixes and the final polish checklist. Read it in steps 5 and 6.
- `scripts/shoot.mjs`: screenshots, motion contact sheets (`--motion`) and automated checks (step 5).
- `references/favicons.md`: the six icon files a site needs, designing for 16px, dark-mode favicons, the Apple and maskable rules, and what to do when a brand already ships icons. Read it when making or checking a favicon.
- `scripts/icons.mjs`: `build` turns one SVG into the full favicon and app icon set; `audit` checks a site's icons and renders a preview sheet (step 4, and in step 6 for any site).
- `scripts/scale.mjs`: fluid type and space tokens (step 4).
