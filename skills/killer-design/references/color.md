# Color

## Method

1. **Start from the concept, not from "brand blue".** Name a material or a place, such as spruce and varnish, slate roof, oxidized copper, newsprint, terminal phosphor, sea glass or a clay court. Take the hue from it. This alone moves a site away from the default indigo.
2. **Choose neutrals first.** They cover 80–90% of the page. Tint them slightly toward the concept's hue (chroma about 0.005–0.03 in OKLCH), because pure `#fff`, `#000` and `#888` grays look sterile. The background doesn't have to be white: warm paper, cool fog and deep ink are all backgrounds.
3. **Use one accent** for the primary action and a few highlights. It works because it's rare. A second accent is fine for small, specific jobs, such as status or a highlight mark.
4. **Name colors by role**, not by hue, so the system can change later:

```css
:root {
  --color-bg:        oklch(0.97 0.012 85);
  --color-surface:   oklch(0.94 0.018 80);
  --color-ink:       oklch(0.24 0.03 50);
  --color-ink-muted: oklch(0.45 0.03 55);
  --color-line:      oklch(0.86 0.02 75);
  --color-accent:    oklch(0.52 0.14 42);
  --color-on-accent: oklch(0.98 0.01 85);
  --color-focus:     oklch(0.52 0.14 42);
}
```

5. **Work in OKLCH.** `oklch(L C H)` takes lightness from 0 to 1, chroma from 0 to about 0.37 (real colors rarely go above 0.25), and hue in degrees. Equal L looks about equally light across hues, which HSL does not manage. That makes scales, hover states and contrast predictable. To make a hover state, change L by about 0.05–0.08 and keep C and H.

## Contrast

- Body text: at least **4.5:1**. For long reading, aim for 7:1.
- Large text (24px and up, or 18.66px bold and up): at least **3:1**.
- Focus indicators and essential UI boundaries (input borders, for example): at least **3:1** against what's next to them.

The usual failure is "muted" gray text on an off-white background, chosen to look refined. Rough OKLCH guides, which `shoot.mjs` will confirm:

| Background L | Ink for body text | Muted text (still AA) |
|---|---|---|
| ~0.97 (light paper) | L ≤ 0.35 | L ≤ 0.50 |
| ~0.20 (dark) | L ≥ 0.85 | L ≥ 0.65 |

- White text on an accent button needs the accent at roughly L ≤ 0.55. If the accent is lighter (yellows, limes, light teals), use dark text on it instead.
- Placeholder text and disabled states still need to be readable, so don't fade them below about 3:1.

## Dark mode

Don't add it by default. Add it when the direction calls for it, or when the user asks. When you do:
- It isn't an inversion. Use a background around L 0.15–0.22 (not pure black), body text around L 0.88–0.94 (not pure white, which glares against dark), and slightly lift the lightness of surfaces to show elevation instead of relying on shadows.
- Desaturate the accent slightly or raise its L so it doesn't vibrate.
- Implement it with the same role tokens redefined under `@media (prefers-color-scheme: dark)`, and add `color-scheme: light dark` on `:root` so form controls and scrollbars match.
- Check it with `shoot.mjs --dark`.

A site that's dark by design (not as a mode) is a direction choice like any other. Just make sure it comes from the concept and isn't the "techy" default.

## Gradients and texture

- If you use a gradient, keep it within one hue family or neighbouring hues, and interpolate in OKLCH (`linear-gradient(in oklch, …)`) to avoid muddy grey middles. Large, saturated, multi-hue gradients are the cliché to avoid.
- A subtle grain gives flat color a printed, physical quality. Use an inline SVG with `<feTurbulence type="fractalNoise" baseFrequency="0.8">` as a background at 3–8% opacity, or as a `::before` overlay with `pointer-events: none`.
- Tints of the accent (a low-chroma, high-L version) make good backgrounds for callouts and highlighted rows.

## Worked examples of the method

These show the method. Don't reuse them: derive yours from your own concept. Note that "cream paper with a terracotta accent" and "near-black navy with a mint accent" have become the stock AI palettes, so if your concept lands on either one, push the hue, temperature or lightness somewhere more specific.

- **Spruce and varnish** (violin workshop): pale spruce background `oklch(0.95 0.025 85)`, dark varnish ink `oklch(0.27 0.05 45)`, an amber varnish accent `oklch(0.62 0.13 65)` with dark text on it (it is too light for white), and a grain-line `oklch(0.84 0.03 75)`.
- **Newsprint** (editorial): slightly yellowed paper `oklch(0.96 0.01 95)`, near-black ink `oklch(0.2 0.01 260)`, and a single red spot color `oklch(0.55 0.2 27)` for links and kickers.
- **Ledger** (accounting or invoicing app): cool paper `oklch(0.98 0.004 250)`, blue-black ink `oklch(0.22 0.02 255)`, ruled lines `oklch(0.88 0.01 250)`, and a ledger-green accent `oklch(0.5 0.1 160)`.
- **Darkroom** (photography portfolio): warm near-black background `oklch(0.16 0.01 30)`, raised surface `oklch(0.2 0.01 30)`, text `oklch(0.9 0.01 60)`, muted text `oklch(0.68 0.015 50)`, and a safelight-red accent `oklch(0.6 0.19 28)` used only for the current item and the contact link.
