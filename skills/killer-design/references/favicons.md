# Favicons and app icons

The favicon is the most-seen piece of a site's design: tabs, bookmarks, history, search results, home screens. At 16px it either reads instantly or it's a smudge. Design it as seriously as the hero.

## Contents
- The files a site needs
- Designing for 16px
- Dark mode
- Home-screen icons
- When a brand already has icons
- Building and checking

## The files a site needs

Six files cover every browser and platform. Anything beyond that (`mstile-*`, `safari-pinned-tab.svg`, `browserconfig.xml`, dozens of PNG sizes) is obsolete, so leave it out.

| File | Size | Used by |
|---|---|---|
| `icon.svg` | vector, square viewBox | Chrome, Firefox, Edge tabs (sharp at any size; can adapt to dark mode) |
| `favicon.ico` | 16, 32 and 48 inside one file | Safari, older browsers, apps that fetch `/favicon.ico` |
| `apple-touch-icon.png` | 180×180, opaque | iOS and iPadOS home screen, some link previews |
| `icon-192.png` | 192×192 | Android home screen, the PWA install prompt |
| `icon-512.png` | 512×512 | Android splash screen, install dialogs |
| `icon-maskable-512.png` | 512×512, full-bleed | Android adaptive icons (circle, squircle and other masks) |

```html
<link rel="icon" href="/favicon.ico" sizes="32x32">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0c0d10" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#f6f5f2" media="(prefers-color-scheme: light)">
```

`sizes="32x32"` on the `.ico` stops Chrome from preferring it over the SVG. The manifest lists the three PNGs (the maskable one with `"purpose": "maskable"`) plus `name`, `short_name`, `theme_color` and `background_color`.

## Designing for 16px

A 16px favicon has 256 pixels. Logos, wordmarks and lockups almost never survive that size unchanged, so most good sites use a *reduced* version of the mark for the favicon.

- **One idea.** Use a single letter, a monogram, or the simplest recognisable part of the mark. Three letters at 16px are each about 4px wide: mush. One bold letter, or one shape, reads.
- **Fill the square.** Use roughly 85–100% of the canvas. A small mark floating in empty space looks lost next to other tabs.
- **Use heavy strokes and big counters.** Keep strokes at least 2px at 16px (an eighth of the canvas) and avoid hairlines, thin serifs and fine texture. Open shapes survive better than enclosed detail.
- **Use few colors with strong contrast.** One or two colors, and contrast within the icon of at least 3:1.
- **Snap to the pixel grid.** Draw on a 16- or 32-unit viewBox with integer coordinates for the main edges, so they land on whole pixels instead of blurring across two.
- **Convert text to paths.** Favicon SVGs can't load web fonts, so `<text>` falls back to whatever font the system has.
- **Make the reduced mark a deliberate variant.** Keep the full mark for 180px and up, where there's room. Use the reduced mark for 16–48px (the `.ico`) and for the SVG favicon.

## Dark mode

Browser tab strips are light or dark, and an icon that's a dark shape with a transparent background disappears in dark mode. There are two fixes, and they can be combined:

1. **Put the mark on a tile:** a rounded square in a brand color that contrasts with *both* light and dark tabs. This is the most robust fix, and it's the only one that also works in the `.ico`, which can't change with the theme.
2. **Add a dark-mode rule inside the SVG favicon.** Chrome and Firefox honour it:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <style>
    .mark { fill: #1b3a5c }
    @media (prefers-color-scheme: dark) { .mark { fill: #f2efe6 } }
  </style>
  <path class="mark" d="…"/>
</svg>
```

If the icon is a dark tile, its edge still vanishes against a dark tab strip. A 1px lighter outline, or a slightly lighter tile in the dark rule, brings the silhouette back.

## Home-screen icons

- **Apple touch icon:** 180×180, fully opaque, square corners. iOS rounds the corners itself and fills any transparent pixels with black, so pre-rounded corners show as black wedges. Use the full mark on its background color, with about 10–15% padding if the mark isn't already a full-bleed tile.
- **Maskable icon:** the background color fills the whole 512×512 square, and everything that matters sits inside the central circle (80% of the width). Android crops to circles, squircles and teardrops, and anything outside that circle may be cut.
- **192 and 512:** the full mark, with transparency allowed.

## When a brand already has icons

If the project's brand system ships icons ("copy, don't redraw"), copy them and don't make new ones. Still run the audit. It often finds real problems in brand kits, such as an unreadable 16px wordmark, transparent corners on the Apple icon, or a tile that disappears on dark tabs. Report those as fixes to make in the brand, with the preview sheet as evidence. If you draft a better small-size variant, put it in a proposal folder for the owner to review, not in the site.

## Building and checking

```bash
node <skill-dir>/scripts/icons.mjs audit path/to/site        # check what a site has and render a preview sheet
node <skill-dir>/scripts/icons.mjs build mark.svg --out path/to/site --bg "#hex" --name "Site" --theme-color "#hex"
```

`build` turns one square SVG into the six files and the manifest. It keeps existing files unless you pass `--force`, prints the `<head>` tags, then audits the result. For a mark that needs a reduced small-size version, build from the reduced SVG, then replace `apple-touch-icon.png`, `icon-192.png` and `icon-512.png` by building from the full mark into a scratch folder.

`audit` checks:
- that the files exist, with their real pixel sizes;
- SVG problems: text, external references, a non-square viewBox;
- whether there's a dark-mode variant;
- Apple touch icon transparency, the maskable safe zone and the manifest's icons;
- how much of the 16px icon stands out on light and dark tabs.

Then read the preview sheet (`<label>-icons.png`). It shows the icon in real tab strips at 1x and 2x, as zoomed pixels, in light and dark mode, and on iOS and Android home screens. Ask:
- **Would a stranger recognise this at 16px on both tab colors?**
- **Does any detail turn to noise?**
- **Is anything cropped on the home screens?**
