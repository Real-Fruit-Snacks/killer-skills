# Slides craft

How to make a deck people remember: the story, the slide, type and colour for a room, charts, motion, notes and endings. killer-design's guides, if installed (`../killer-design/references/typography.md`, `color.md`, `layout.md`, `motion-and-wow.md`), go deeper on type pairings, palettes in OKLCH, grids and motion. This file covers what changes when the page is a projected slide and a person is talking over it.

## Contents
- The story comes first
- One idea per slide, and the title is the claim
- A menu of slide forms
- Type for the back of the room
- Colour for a projector
- Layout: a grid the eye can trust
- Charts and numbers
- Builds and motion
- Speaker notes
- Openings and endings
- Decks people read on their own
- What makes a deck look generated, and what to do instead

## The story comes first

A deck is a talk with pictures, not a document with a speaker. Write the talk before the slides:

1. **The one sentence** the talk exists to land: what the room should think or do afterwards. "Moving three night-bus stops would fix most late arrivals, and the council can approve it in March."
2. **The key messages:** the three (at most five) things people should leave with. These are what the blind audience is checked against, so they must be specific claims, not topics. "Costs" is a topic; "the night service now costs less than the day service did in 2019" is a claim.
3. **The arc.** Most talks that work move from where the room is to where it could be, and back and forth between the two: what is, what could be, what is, what could be, then the new bliss and the ask (Nancy Duarte's shape). Problem, stakes, turn, evidence, ask works too. Pick one and know where each slide sits in it.
4. **The outline as claims:** one sentence per slide, numbered. If a slide has no claim, it's either a breather on purpose (a section break, a picture held in silence) or it goes. Read the claims alone, in order: they should make the argument by themselves.
5. **The moments.** Mark the two or three slides that should land hardest: a number that turns the room, a before and after, a live demo, a quote from someone real, one quiet slide after a loud one. Spend design time there. Everything else stays calm so they stand out.

**Name things before you use them.** A slide that says "each killer skill" before any slide has said what one is loses the room; the audience spends the slide guessing. The first time a name, product or term appears, the slide says what it is, in the title or right under it.

Time it: about one to two minutes per content slide is normal for a conventional talk. A talk built of single-statement slides can move much faster. The notes, at 130 to 150 words a minute, are the real clock.

## One idea per slide, and the title is the claim

- **Write the title as a full-sentence claim, not a topic.** "Q3 results" tells the room nothing. "Bookings doubled after we dropped the sign-up form" tells them what to see in the chart below it. Research on slide design (Michael Alley's assertion-evidence work) finds audiences remember more this way, and it's what makes a deck survive being read without its speaker.
- **Then show the evidence, don't list it.** Under the claim goes the thing that proves it: a chart, a picture, a diagram, a number, a quote. Not three bullets restating the title.
- **One idea per slide.** If a slide needs "and" in its claim, it's probably two slides. Slides are free; a slide the room can't take in while listening costs you the room.
- **Bullets are a last resort.** When something really is a list, keep it short (at most five items, a line each) and ask whether it's actually a sequence (a numbered diagram), a comparison (a table), or a set of parts (a labelled picture).
- **Never present invented facts as real.** No made-up figures, quotes, logos, customers or results. Mark what only the speaker knows as `<span class="todo">[figure from the Q3 report]</span>` and list every one in the handoff.

## A menu of slide forms

Choose each slide's form from what it has to say. A deck that varies its forms on one consistent system has rhythm; a deck where every slide is title-plus-bullets has none.

| Form | When | Notes |
|---|---|---|
| **Statement** | A claim that stands alone; the turn in the story | One sentence, set very large (96–160px), lots of space. The strongest form in the deck: use it for the moments |
| **Big number** | A figure that changes minds | The number at 200–400px, one line saying what it means. Round it ("2.4 million", not 2,412,903) unless the precision is the point. It must be a measurement with something to compare it to; a rule restated as a number ("0 bugs reported without proof") reads as a result, and the room notices it isn't one |
| **Evidence** | A claim with proof | The claim as the title; one chart, picture or diagram under it, with the part that proves it highlighted |
| **Diagram** | How something works, a sequence, parts of a system | Inline SVG, built up in steps if the order is the story. Label the parts directly |
| **Before and after** | A change | Two panels, the same framing, the difference obvious at a glance |
| **Picture** | A place, a person, a product, a thing | Full bleed, the claim set over a calm area of it (with a scrim if needed). Real photos only; no stock handshakes |
| **Quote** | A real person's words that carry weight | The quote large, the name and role small. Only quotes someone actually said, with their permission if they're private |
| **Code or command** | Developer talks | Ten lines at most, 32px or larger, with the line that matters highlighted. Show output if the output is the point |
| **Comparison** | Choosing between options | A table with few rows, the winning column marked, numbers aligned |
| **Section break** | A long talk's chapters | Sparingly: a short phrase and a number. Not before every section of a 10-minute talk |
| **The ask** | The end | What you want the room to do, specifically, with the link, date or name they need |

## Type for the back of the room

Sizes on the 1920×1080 canvas. `check` fails text under 20px and warns under 24px.

| Role | Size | Notes |
|---|---|---|
| Hero (opening, big statements) | 120–200px | Tight leading (0.95–1.05), slight negative tracking for sans |
| Slide claim (title) | 64–96px | Leading 1.05–1.15; two lines at most, balanced (`text-wrap: balance`) |
| Big numbers | 200–400px | Tabular or lining figures; the unit smaller beside it |
| Body and labels | 36–48px | Leading 1.25–1.4, at most about 40 characters a line |
| Small (captions, chart labels) | 28–32px | |
| Sources and footnotes | 24px | The floor. Anything smaller won't be read |

- **Choose type for a room, not a screen.** Very thin weights (100–300) break up on projectors and at a distance; keep light weights for sizes over 80px. Wide, open faces with a generous x-height read best from far away.
- **A deck needs one typographic voice with real contrast in scale.** A display face for claims and statements, a text face for the rest (or one family in two roles), and a mono for code if there's code. killer-design's typography guide has pairings by personality and the faces to avoid as defaults.
- **Set numbers properly:** `font-variant-numeric: tabular-nums` where they line up, lining figures in charts, real minus signs (−), en dashes in ranges (9–12), × for dimensions.
- **Left-align** body text and most claims. Centre only short, symmetric content: a statement, a big number, a closing line.

## Colour for a projector

Projectors wash colour out. Dark greys go black, pale tints vanish, and contrast that looks fine on a laptop falls apart in a lit room. `check` measures contrast on the render, but a projector is harsher than the render, so leave margin.

- **Decide light or dark from the room.** Dark slides with light text suit dark rooms and big stages (no glare from a white wall of light). Light slides suit lit rooms, meeting rooms and decks that get printed or read. Don't switch between them slide to slide, except as a deliberate moment.
- **Neutrals tinted toward the concept's hue,** not pure white or black: an off-white paper, a deep ink. Body text at 7:1 or better if the deck will be projected.
- **One accent, used to point:** the bar that matters in a chart, the word that matters in a claim, the active step in a diagram. If it's everywhere, it points at nothing.
- **Never let colour carry the meaning alone.** About one man in twelve finds red and green hard to tell apart. Label directly, or add a shape or a word.
- **Text on photos** needs a calm area, a darker crop, or a scrim (a gradient of the background colour at 50–80% behind the text). `check` reads the actual pixels, so it will tell you.

killer-design's colour guide has the OKLCH method and worked palettes. Derive the palette from the subject's world, as it describes; don't reuse its examples.

## Layout: a grid the eye can trust

- **One grid for the whole deck:** for example 12 columns inside 136px side margins and 104px top and bottom margins, with a 32px gutter. Every slide aligns to it.
- **Consistent positions.** Claims start at the same place on every slide of the same form, so the eye doesn't hunt. Flip through the contact sheet: anything that jumps between slides without a reason is a defect.
- **Asymmetry and space.** Most strong slides are mostly empty. Put the content on a third, not in the centre, and let the space around it do the work.
- **Vary the form, not the system.** A full-bleed picture after two dense slides, a single statement after a chart: rhythm comes from changes in density on a constant grid.
- **One or two moments that break the grid on purpose:** a number that runs off the edge (`data-bleed`), a picture that bleeds. They work because everything else is disciplined.
- **No furniture on every slide:** no logo in the corner of each slide, no footer with the event name and date, no slide numbers in a box. If numbers help the Q&A, set them small and quiet in a corner.

## Charts and numbers

- **One message per chart, and the title says it.** "Late arrivals fell 60% after the timetable change", not "Late arrivals 2023–2025".
- **Highlight the series that proves the claim** in the accent colour; set the rest in a muted neutral. Label lines and bars directly; drop the legend.
- **Strip everything else:** gridlines, borders, 3D, shadows, axis titles that repeat the claim. Keep an axis only if the room needs its values.
- **Show the comparison that makes the point:** before and after, us and them, plan and actual. A number alone has no size.
- **Build complex charts in steps** (`data-step`): the axes and the baseline first, then the line that matters.
- **Draw charts as inline SVG** from the real data, so they're sharp and their labels are checked. Labels at 28px or larger.
- If the dataviz skill is installed, its guidance on form and colour applies; scale its sizes up for the room.

## Builds and motion

- **Build only when the order is the story:** a sequence, a reveal, a before then after. Don't build a list just because it's a list; the room reads ahead anyway.
- **Cut between slides.** A plain cut is the professional default. At most one signature transition, used for one of the moments, rooted in the concept. No fly-ins, spins, cube rotations or dissolves between every slide.
- **Motion inside a slide should explain something:** a line drawing itself along a route, a bar growing to its value, a number counting to its total. Keep it under a second.
- **Everything that moves goes inside `@media (prefers-reduced-motion: no-preference)`,** or behind a `matchMedia` check in a script. The reduced-motion version must be a finished slide. `check` fails anything that still moves with reduced motion on.
- killer-design's motion guide has tested recipes; on a slide, stop them at the end state rather than looping.

## Speaker notes

- **Write what you'll say, not what's on the slide.** Full sentences, in the speaker's voice, as they'd be spoken. The slide shows; the notes tell.
- **Open each slide's notes with the line that brings it on** ("So where does the time go?"), and end with the one that carries into the next slide. Transitions are where talks lose people.
- **Mark the pauses** after the moments: `<p><em>Pause.</em></p>`. The silence after a big number is part of the number.
- **Put the facts the speaker needs** where they need them: the exact figure and its source, the name to credit, the question to expect.
- **Length is time:** at 130 to 150 words a minute, a 10-minute talk is about 1,300 to 1,500 words of notes in all. `check` adds them up.

## Openings and endings

- **Open with the room's problem or a surprising true thing,** not with an agenda or a biography. Who you are takes one line, in the notes, said while the opening slide is up.
- **No agenda slide** in a talk under 20 minutes. In a longer one, a single slide naming three parts is enough, and only if the parts come back as section breaks.
- **End on the ask:** what you want people to do next, with what they need to do it (the link, the date, the person). Leave that slide up during questions.
- **Keep the how short.** A short link, one short command or a name. A long command line or URL becomes the thing people remember about the slide, and the ask gets lost behind it. Put the full version in the notes and the PDF.
- **Never end on "Questions?" or "Thank you".** They waste the slide that stays up longest.

## Decks people read on their own

A deck sent instead of a talk (a pitch someone forwards, a board pre-read, a proposal) has no speaker, so the slides carry everything:

- Claims as titles matter even more: read in order, the titles alone should make the case.
- Full sentences are fine, and more of them: up to about 75 words on a slide before `check` warns. Still one point per slide.
- Open with a summary slide that states the ask and the three messages. Busy readers stop after it.
- Sources and definitions go on the slide, small but at 24px or more, because nobody is there to explain them.

## What makes a deck look generated, and what to do instead

People have learned the look of a generated deck, and once they spot it they stop trusting it. No single move gives it away; several together do:

- a title and three bullets on every slide, every slide the same layout;
- an icon in a tinted circle beside each bullet; a row of three "feature cards";
- an agenda slide, "Key takeaways" with checkmarks, and "Thank you! Questions?" at the end;
- a gradient title slide (purple to blue, or teal to navy) with a glowing shape;
- a timeline of circles joined by a line, or a "roadmap" of pills;
- stock photos of handshakes, laptops and people pointing at screens;
- invented statistics in big type ("87% of teams…") and quotes nobody said;
- emoji as icons; glass cards with shadows; everything centred;
- a logo and footer on every slide;
- headings that are topics ("Overview", "Benefits", "Next steps") instead of claims.

**The test.** Before handing off, open the contact sheet and count how many of these appear. Two or more means rework: replace them with something the subject suggests. Then describe the deck in one sentence. If that sentence would fit a thousand decks ("a dark deck with icon bullets and charts"), it isn't designed yet.

What strong decks do instead: a visual idea from the subject's world, carried through every slide (a transit deck set like route maps and timetables; a craft talk set like a workshop's labels); claims you could quote; evidence you can see in one glance; real pictures; type at a scale that fills the room; and two or three moments the audience will describe to someone else afterwards.
