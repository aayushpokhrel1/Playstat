---
name: Playstat
description: A terminal-native betting analytics dashboard where model edges earn trust before they earn attention.
colors:
  ink-black: "oklch(0.09 0 0)"
  surface: "oklch(0.14 0.004 150)"
  surface-raised: "oklch(0.185 0.005 150)"
  ink: "oklch(0.96 0.004 150)"
  muted: "oklch(0.60 0.006 150)"
  border: "oklch(0.26 0.006 150)"
  signal-green: "oklch(0.72 0.16 150)"
  signal-green-deep: "oklch(0.50 0.14 150)"
  edge-amber: "oklch(0.75 0.15 70)"
typography:
  display:
    fontFamily: "var(--font-geist-sans)"
    fontSize: "clamp(1.75rem, 3vw, 2.5rem)"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "var(--font-geist-sans)"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "var(--font-geist-sans)"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "var(--font-geist-sans)"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "0.02em"
  data:
    fontFamily: "var(--font-geist-mono)"
    fontSize: "0.9375rem"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "-0.01em"
    fontFeature: "tnum"
rounded:
  sm: "4px"
  md: "6px"
  full: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "40px"
components:
  button-primary:
    backgroundColor: "{colors.signal-green}"
    textColor: "{colors.ink-black}"
    rounded: "{rounded.sm}"
    padding: "10px 20px"
  button-primary-hover:
    backgroundColor: "{colors.signal-green-deep}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "10px 20px"
  badge-edge:
    backgroundColor: "{colors.edge-amber}"
    textColor: "{colors.ink-black}"
    rounded: "{rounded.full}"
    padding: "2px 10px"
---

# Design System: Playstat

## 1. Overview

**Creative North Star: "The Trading Desk at Close"**

Playstat reads like a terminal built by someone who trusts the numbers, not a SaaS dashboard trying to look trustworthy. The surface is near-black, quiet, and gets out of the way; the one signal color that lives on top of it — a controlled, deep green — is reserved for the moments a prediction actually clears the bar against the market. Nothing else on screen competes with that signal. Data is set in a tabular monospace so columns of odds and predicted lines line up the way a spreadsheet or a terminal would, while UI chrome (nav, labels, buttons) stays in a clean technical sans. This system explicitly rejects the generic SaaS-dashboard-in-a-box look — pastel KPI tiles, rounded-everything card grids, gradient accents — and it rejects actual sportsbook chrome — promo banners, gamified color, odds-app skins. The tone is analyst, not marketing; precision, not promotion.

**Key Characteristics:**
- Near-black terminal surface, restrained to a single signal color used sparingly and meaningfully
- Tabular monospace for every number; sans only for UI chrome and prose
- Flat by default — depth comes from tonal layering (surface steps), not shadows
- Motion limited to responsive feedback: state changes and transitions, no choreography
- Every colored number carries its calibration context nearby, never floats alone

## 2. Colors

A near-black terminal base with one deliberate signal color; everything else is tonal neutral.

### Primary
- **Signal Green** (`oklch(0.72 0.16 150)`): The one meaningful accent. Used only where a model prediction clears the edge threshold, on the primary action button, and on active/selected states — never decoratively. Deep variant (`oklch(0.50 0.14 150)`) is its hover/pressed state.

### Secondary
- **Edge Amber** (`oklch(0.75 0.15 70)`): Reserved for "worth a second look" states — a calibration warning, a line that just moved, a parlay leg flagged for correlation risk. Distinct in both hue and role from Signal Green so the two are never confused at a glance.

### Neutral
- **Ink Black** (`oklch(0.09 0 0)`): Page background. Pure neutral, no hue tint — the terminal reads as genuinely dark, not "dark mode as an afterthought."
- **Surface** (`oklch(0.14 0.004 150)`): First tonal step up from Ink Black, for grouped data regions (a table, a player panel). Carries the faintest trace of the signal hue — just enough that the whole system reads as one family.
- **Surface Raised** (`oklch(0.185 0.005 150)`): Second tonal step, for anything that needs to read as "above" Surface — an open row, a focused input.
- **Ink** (`oklch(0.96 0.004 150)`): Primary text. Nearly white, with the same faint hue trace as Surface so text and surface feel like one palette, not white-on-black defaulted.
- **Muted** (`oklch(0.60 0.006 150)`): Secondary text — labels, timestamps, secondary stats. Ink pulled toward Ink Black, same hue family.
- **Border** (`oklch(0.26 0.006 150)`): Hairline dividers between data rows and panels.

### Named Rules
**The One Signal Rule.** Signal Green appears only when a real, calibration-checked edge exists — never as branding, never on a button that isn't the primary action, never as page furniture. Its rarity is what makes it mean something.

**The No-Tint-Drift Rule.** Every neutral (Surface, Ink, Muted, Border) shares Signal Green's hue (150°) at near-zero chroma. This is what keeps a mostly-gray-and-black screen from reading as generic dark mode — it's one palette at every step, not a neutral gray system with a color bolted on.

## 3. Typography

**UI Font:** Geist Sans (with system-ui fallback) — already wired via `--font-geist-sans` in `web/app/layout.tsx`.
**Data Font:** Geist Mono (with ui-monospace fallback) — already wired via `--font-geist-mono` in the same file, currently unused; this system puts it to work.

**Character:** Geist Sans carries the interface itself — quiet, technical, gets out of the way. Geist Mono is reserved for anything that is a number a user needs to compare against another number: predictions, lines, odds, edges. The pairing is the typographic version of the One Signal Rule — one family for chrome, one family for the thing you actually came to read.

### Hierarchy
- **Display** (600, `clamp(1.75rem, 3vw, 2.5rem)`, 1.1): Page-level headers only (e.g. a team or player name at the top of a detail view). Rare.
- **Headline** (600, 1.25rem, 1.3): Section headers — "Tonight's Edges," a team name above its roster.
- **Body** (400, 0.9375rem, 1.5): Prose, descriptions, anything read rather than scanned. Cap at 65–75ch where it wraps.
- **Label** (500, 0.75rem, uppercase optional, +0.02em): Column headers, metadata tags, timestamps.
- **Data** (500, 0.9375rem, 1.4, tabular figures): Every stat, line, odd, prediction, and edge value. Always monospace, always tabular-figure-aligned in a column.

### Named Rules
**The Tabular Figures Rule.** Any numeral rendered in the Data role uses `font-variant-numeric: tabular-nums` so columns of stats stay vertically aligned — non-negotiable for a scan-fast use case.

## 4. Elevation

Flat by default. Depth is conveyed through the three-step tonal ladder (Ink Black → Surface → Surface Raised), not shadows — a shadow on a near-black background reads as murky, not elevated. The one exception is a soft ambient glow used sparingly behind an active Signal Green state, which reads as "lit up" rather than "elevated."

### Shadow Vocabulary
- **Signal Glow** (`box-shadow: 0 0 0 1px oklch(0.72 0.16 150 / 0.4), 0 4px 16px oklch(0.72 0.16 150 / 0.15)`): Behind an actively-selected edge or the primary CTA on hover. Never used on neutral elements.

### Named Rules
**The Flat-By-Default Rule.** Surfaces are flat at rest; depth comes from the tonal ladder. Shadows appear only as Signal Glow, and only in response to a signal-colored state.

## 5. Components

### Buttons
- **Shape:** Small radius (`4px`) — precise, not soft.
- **Primary:** Signal Green background, Ink Black text (white-on-saturated-fill would fight the palette; Ink Black reads cleanly against this L/chroma), `10px 20px` padding.
- **Hover / Focus:** Background shifts to Signal Green Deep; focus adds a 1px Signal Green outline offset 2px (never a glow on a resting element).
- **Ghost:** Transparent background, Ink text, Border-color 1px outline — used for secondary actions (view detail, dismiss).

### Badges
- **Edge Badge:** Edge Amber fill, Ink Black text, full pill radius, `2px 10px` padding — flags a line worth a second look. Never combined with Signal Green in the same badge; they're separate signals.

### Data Surfaces (not "cards")
- **Corner Style:** `6px` radius, deliberately smaller than a typical SaaS card to avoid reading as a padded tile.
- **Background:** Surface, stepping to Surface Raised on hover/focus for an interactive row.
- **Shadow Strategy:** None at rest; see Elevation.
- **Border:** 1px Border color, used to separate regions rather than to frame every element — not every data grouping needs a full outline.
- **Internal Padding:** `md` (16px) for panels, `sm` (8px) for individual data rows.

### Inputs / Fields
- **Style:** Surface Raised background, 1px Border stroke, `4px` radius.
- **Focus:** Border shifts to Signal Green, no glow (glow is reserved for signal states, not routine focus).

### Navigation
- **Style:** Label-weight type, Muted by default, Ink on active/hover, Signal Green as the active-item underline (2px, not a filled pill background).
- **Mobile:** Collapses to a bottom bar; same color logic, larger tap targets (44px minimum).

## 6. Do's and Don'ts

### Do:
- **Do** keep Signal Green reserved for real, calibration-checked edges and the one primary action per screen.
- **Do** set every stat, line, odd, and prediction in Geist Mono with tabular figures.
- **Do** build depth with the Ink Black → Surface → Surface Raised ladder, not shadows.
- **Do** pair a colored number with its calibration context (e.g. sample size, confidence) rather than letting a bold green figure stand alone.

### Don't:
- **Don't** build the generic SaaS-dashboard-in-a-box look — no pastel KPI tiles, no rounded-everything card grids, no gradient accents.
- **Don't** borrow sportsbook chrome — no promo banners, no gamified color, no odds-app skin. The tone is analyst, not marketing.
- **Don't** use `border-left`/`border-right` accent stripes on rows or panels.
- **Don't** apply drop shadows on the near-black surface for routine elevation — it reads as murky, not layered.
- **Don't** mix Edge Amber and Signal Green in the same element; they're distinct signals and must stay visually separable.
