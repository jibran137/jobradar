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
