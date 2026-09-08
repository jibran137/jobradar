# Design theme — warm paper / teal

Color scheme used on the personal site (`jibran137.github.io`, commit
`734aed6` "Apple HIG-inspired redesign"). Documented here so it can be
reused/reskinned for JobRadar's UI (`templates/*.html`).

## Palette (CSS custom properties)

```css
:root {
  /* surfaces */
  --bg: #F1ECE2;              /* page background, warm paper */
  --bg-elevated: #F8F5EF;     /* cards, panels */
  --bg-elevated-2: #ECE4D4;   /* table rows / subtle fill */

  /* text */
  --ink: #241F17;             /* primary text */
  --ink-2: #6E6459;           /* secondary text */
  --ink-3: #8B8173;           /* tertiary / labels */

  /* borders */
  --line: rgba(55,42,24,0.10);
  --line-strong: rgba(55,42,24,0.18);

  /* accent (teal) */
  --accent: #0E8C7B;
  --accent-hover: #0B7568;
  --accent-on: #FFFFFF;       /* text/icon color on accent fill */
  --accent-soft: rgba(14,140,123,0.10);

  /* status */
  --live: #2FAE60;            /* "live"/active/success indicator */

  /* glass / nav */
  --glass-bg: rgba(241,236,226,0.72);
  --glass-line: rgba(55,42,24,0.08);

  /* shadows */
  --shadow-card: 0 1px 2px rgba(40,30,10,0.05), 0 16px 32px -18px rgba(40,30,10,0.18);
  --shadow-card-hover: 0 2px 4px rgba(40,30,10,0.06), 0 24px 48px -18px rgba(40,30,10,0.24);

  /* raw rgb (for rgba() composition elsewhere) */
  --sig-rgb: 14,140,123;      /* == --accent */
  --grid-rgb: 55,42,24;       /* == ink family */
}
```

## Typography

```css
--font: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text",
        "Helvetica Neue", Arial, sans-serif;
--mono: "IBM Plex Mono", ui-monospace, "SF Mono", monospace;
--ease: cubic-bezier(0.16, 1, 0.3, 1); /* transitions/hover easing */
```

## Usage notes

- `--bg` is the warm paper base — avoid pure white anywhere; `--bg-elevated`
  is the "card" surface, `--bg-elevated-2` for zebra-striped rows/tables.
- `--ink` / `--ink-2` / `--ink-3` step down in emphasis — body copy uses
  `--ink-2`, primary headings/values use `--ink`, meta/labels use `--ink-3`.
- `--accent` (teal) is the single call-to-action / link / highlight color.
  Use `--accent-soft` for hover backgrounds, not a lighter accent tint.
- `--live` (green) is reserved for "active/running/live" status pills —
  keep it distinct from `--accent` so status and action don't blur.
- Buttons: primary = `--accent` fill + `--accent-on` text, hover =
  `--accent-hover`. Secondary/ghost buttons use `--ink` border + `--bg`.
- Nav/header uses a frosted-glass look: `--glass-bg` + `backdrop-filter:
  blur(...)` + `--glass-line` border.

## Swapping the theme

To retheme JobRadar with a different palette, replace the values above
(keep the variable names) and re-derive:
- `--accent-hover` ≈ accent darkened ~10%
- `--accent-soft` ≈ accent at 10% alpha
- `--live` should stay visually distinct from `--accent` (different hue,
  not just a shade of it)
- `--sig-rgb` / `--grid-rgb` are the same colors as `--accent` / `--ink`
  in raw `r,g,b` form, for use inside `rgba(var(--sig-rgb), 0.x)` calls

Source of truth for the live version: `jibran137.github.io/index.html`
(`:root` block near the top of the `<style>`).

## Apple HIG principles applied

The redesign leaned on a handful of concrete Apple Human Interface
Guidelines patterns, not just the color swap above. Reuse these when
retheming a page, not just the palette:

- **Liquid glass nav.** The header is `position: sticky; top: 0`, with
  `background: var(--glass-bg)` (a translucent version of the page bg)
  plus `backdrop-filter: blur(24px) saturate(1.6)`. This is the "materials"
  pattern from HIG — content scrolls under a frosted bar, not a flat opaque
  one. Border is a hairline (`1px solid var(--glass-line)`, ~8% opacity),
  never a hard line.
- **System typography stack.** `--font` leads with `-apple-system,
  BlinkMacSystemFont, "SF Pro Display", "SF Pro Text"` before falling back —
  i.e. render in the OS's native system font on Apple devices rather than a
  webfont for body/UI text. Only headings/labels use a deliberate mono
  webfont (`IBM Plex Mono`) as an accent, loaded via Google Fonts
  `preconnect` for speed.
- **Type hierarchy via size + weight + tracking, not color.** Headings use
  `clamp()` for fluid responsive sizing (e.g. `clamp(2.7rem, 6.2vw, 4.9rem)`
  for the hero), tight negative `letter-spacing` at large sizes
  (`-0.025em` to `-0.028em`, HIG's "tight tracking at display sizes"), and
  `font-weight: 700` for headings vs `400–600` for body/UI. Eyebrows/labels
  use small caps-like treatment: `text-transform: uppercase`, wide
  `letter-spacing: 0.1em`, mono font, small size (`0.74rem`).
- **Continuous corner radii, not fixed pixel boxes.** Cards/panels use
  large radii (`22px`–`24px`) that scale with the element's size; pills and
  buttons use `border-radius: 980px` (i.e. fully round — HIG's capsule
  button shape), not `4px`/`8px` generic rounding.
- **Restrained, layered shadows.** `--shadow-card` and
  `--shadow-card-hover` are two-layer: a tight near-black low-opacity
  shadow for contact, plus a large soft diffuse one for elevation — not a
  single flat `box-shadow`. Elevation increases subtly on hover, not on a
  toggle state.
- **Motion: scale, not move.** Interactive elements (`.btn`, `.bar-cta`)
  use `transform: scale(1.03–1.04)` on hover with `var(--ease)` —
  `cubic-bezier(0.16, 1, 0.3, 1)`, an ease-out curve — instead of
  translating position or changing size abruptly. Color/background
  transitions are separately timed and shorter (`.2s`) than transform
  (`.3s`), so hover feels layered, not simultaneous.
- **Status/liveness as its own signal.** The `--live` green is never reused
  for anything else (not links, not buttons) — HIG treats system status
  color as reserved vocabulary. A live status pill pairs a small pulsing
  dot (`@keyframes pulse` — expanding ring, fading opacity) with text,
  echoing the iOS "recording/live" indicator pattern.
- **Generous whitespace, one accent per view.** Sections use large vertical
  rhythm (`padding: 96px 0` between blocks, `44px` before section content)
  and a single accent color per interactive context — never multiple
  competing accent hues on screen at once.

To apply this to JobRadar's Flask templates: keep body/UI text on the
system font stack, switch any flat-color header/toolbar to the sticky
frosted-glass treatment, round buttons to full pills, and swap hard
drop-shadows for the two-layer soft/tight combo above.
