# NFL dashboard view + shelved-model cleanup (#4b) — design spec

Sub-project **#4b of the NFL parlay-builder decomposition** (the frontend half of
the original #4; #4a backend BUILT 2026-07-29). Build order: #1 → #2 → #3 → #4a
(BUILT) → **#4b (this doc)**. Frontend-focused; NFL is offseason, so the NFL view
is verified structurally now (honest empty state) and shows real cards once the
first weekly build lands at preseason (~August).

Two cohesive parts, both aligning the dashboard with the **builder-first,
multi-sport** direction (README §16):
1. **NFL dashboard view** — surface NFL builder parlays + record via a sport
   selector on the builder page.
2. **Remove shelved-model surfaces** — delete the model-performance + edges
   frontend (the model is shelved; §16), leaving the builder as the product.

## Decisions (user-confirmed 2026-07-29)

- **URL-param sport selector** (MLB | NFL tabs → `?sport=`, server-rendered via
  Next `searchParams` — no client state). Reuses the existing builder layout
  parameterized by sport; scales to NBA/soccer next.
- **Honest NFL empty state** offseason/pre-first-build (not a hidden tab).
- **Delete the `/edges` and `/clv` route pages** (+ their home links), not just
  unlink. Backend endpoints stay untouched (frozen-serving; Budgerr still reads
  `/edges`).

## Mandatory reading for the implementer (UI work in `web/`)

- **PRODUCT.md** + **DESIGN.md** (repo root): near-black terminal surface, ONE
  signal-green accent, Geist Sans/Mono. Match `web/app/builder/` +
  `web/app/edges/` conventions and `web/app/builder/builder.module.css`.
- **`web/AGENTS.md`**: this Next.js is **16.x** with breaking changes from
  training data — READ `web/node_modules/next/dist/docs/` before writing Next
  code. In particular, **`searchParams` is async in Next 16** (a Promise the page
  must `await`); verify the exact API in the installed docs.
- **Guardrail (§15.8):** signal-green stays reserved for the ≥0.75 joint-prob
  rule already in the builder — the sport tabs + NFL sections use the muted
  terminal palette, NOT signal-green. No "+EV"/"edge"/"value" language.

## Key findings (from the code, 2026-07-29)

1. **Builder page is MLB-hardcoded** (`web/app/builder/page.tsx`, a server
   component): `Promise.all([getSavedBuilderParlays(10,"player"),
   getSavedBuilderParlays(10,"team"), getBuilderRecord(), getBuilderRecordDaily()])`
   — no `sport`. It slate-scopes both tiers to the latest slate present and renders
   a record panel, a framing blurb, "Tonight's low-risk parlays" (player), and
   "Team-market parlays" (team/NRFI-F5).
2. **`api.ts` builder fetchers take no `sport`.** `getSavedBuilderParlays(limit,
   tier)` hits `/parlay-builder/saved?limit&tier`; `getBuilderRecord()` /
   `getBuilderRecordDaily()` hit the record endpoints (which #4a gave a `?sport`).
   `BuilderRecord.tier` is typed `"player" | "team"`.
3. **The saved `tier` selector has no `game` entry.** Backend `TIER_TO_CLASS =
   {"player":"across_game","team":"team_tier"}` (`api/main.py`); NFL's game tier is
   `class='game_tier'` (#3), reachable today only via `tier=all`. The record
   labeler `_CLASS_TO_TIER = {"across_game":"player","team_tier":"team"}` +
   `_TIER_SORT_ORDER` similarly lack a `game_tier` entry (an unknown class falls
   through as its raw name, sorted last).
4. **Model surfaces are home-page links only** (`web/app/page.tsx`): `/edges`
   ("View tonight's edges"), `/clv` ("View model performance"), `/builder`. No
   global nav in `layout.tsx`. `web/app/edges/` and `web/app/clv/` are
   self-contained route dirs.

## Components

### A. Backend tier plumbing for the NFL game tier — `api/main.py` (additive; architect kickstarts)

- `TIER_TO_CLASS`: add `"game": "game_tier"` so `/parlay-builder/saved?tier=game`
  returns the NFL game tier. `tier=player/team/all` are unchanged (Budgerr-safe —
  they consume `tier=all`, unaffected).
- `_CLASS_TO_TIER`: add `"game_tier": "game"`; `_TIER_SORT_ORDER`: add `"game"`
  (after player/team) so `/parlay-builder/record` labels the NFL game tier `game`
  and sorts it deterministically.
- Tests (extend `tests/test_builder_record_api.py` + a saved-endpoint tier test if
  one exists, else `tests/test_parlay_builder_api.py`): `tier=game` maps to
  `game_tier`; a `game_tier` record row labels as tier `game`. Pure/fake-engine.
- Architect kickstarts `com.playstat.api` after this change.

### B. `api.ts` — additive `sport` on the builder fetchers

- `getSavedBuilderParlays(limit = 10, tier: "player" | "team" | "game" | "all" =
  "player", sport = "mlb")` → append `&sport=${sport}`.
- `getBuilderRecord(sport = "mlb")` → `/parlay-builder/record?sport=${sport}`.
- `getBuilderRecordDaily(sport = "mlb")` → `/parlay-builder/record/daily?sport=${sport}`.
- Widen `BuilderRecord.tier` to `"player" | "team" | "game"`.
- All defaults are `"mlb"`, so existing calls are unchanged.

### C. Sport selector + per-sport rendering — `web/app/builder/page.tsx` (+ a `SportTabs` component)

- The page reads `searchParams` (await per Next 16), resolves
  `const sport = raw === "nfl" ? "nfl" : "mlb"` (validate to the known set; unknown
  ⇒ mlb).
- **`SportTabs`** (new small component, e.g. `web/app/builder/SportTabs.tsx`): two
  `next/link`s — `/builder?sport=mlb`, `/builder?sport=nfl` — styling the active
  tab (terminal palette, not signal-green; accessible `aria-current="page"`).
- **Second-tier config is per-sport:**
  | sport | tier-2 fetch | tier-2 heading | tier-2 note | empty copy |
  |---|---|---|---|---|
  | mlb | `getSavedBuilderParlays(10,"team","mlb")` | "Team-market parlays" | NRFI/F5 coin-flip, higher-variance (as today) | "No team-market parlays tonight …" (as today) |
  | nfl | `getSavedBuilderParlays(10,"game","nfl")` | "Game-market parlays" | total/spread/moneyline; moneyline favorites clear the floor, totals/spreads rarely — higher-variance, may be empty | "No game-market parlays this week …" |
  - Player tier heading is sport-aware: mlb "Tonight's low-risk parlays" / nfl
    "This week's low-risk parlays" (NFL is a weekly Thu–Mon card, #4a).
  - The slate label ("Jul 28 slate · N saved") logic is unchanged, applied to the
    selected sport's rows.
- **NFL honest empty state** (when the sport's saved rows are empty — offseason or
  pre-first-build): a clear message, e.g. "No NFL parlays yet — the weekly card
  builds Thursday mornings once preseason odds open (~August)." Reuse the existing
  empty-state styling. MLB empty behavior is unchanged.
- Fetch shape: still one `Promise.all`, now sport-parameterized —
  `getSavedBuilderParlays(10,"player",sport)`, the per-sport tier-2 call,
  `getBuilderRecord(sport)`, `getBuilderRecordDaily(sport)`. The `fetchError`
  fallback is unchanged.
- **No change to `ConstructionList` / `BuilderControls` / `RecordPanel` internals**
  beyond what the widened tier type requires — they already render generic
  constructions + records. Verify the record panel renders a `game` tier row.

### D. Remove shelved-model frontend — `web/app/page.tsx` + route deletion

- `web/app/page.tsx`: remove the `/edges` ("View tonight's edges") and `/clv`
  ("View model performance") `Link`s. Keep "Build a low-risk parlay" (`/builder`)
  as the primary CTA and the team/player browse below. Adjust surrounding copy if
  it references "edges"/"model".
- **Delete** `web/app/edges/` and `web/app/clv/` route directories entirely.
- **Remove now-dead `api.ts` exports** that were used ONLY by those pages (verify
  no other importer first, via grep): candidates — `getEdges`,
  `getEdgeDistributions`, `getParlayRecommendations`, `getClvSummary`,
  `getBetPerformance`, and their now-orphaned types (`Edge`, `EdgeDistribution`,
  `PmfPoint`, `ParlayRecommendation`, `ParlayLeg`, `ClvSummary`, `BetPerformance`).
  Leave anything still imported elsewhere. This is dead-code cleanup — if in doubt,
  leave the export and note it.
- **Backend is untouched.** `/edges`, `/game-predictions`, `/parlay-recommendations`,
  `/bet-performance`, `/clv-summary` keep serving (frozen; Budgerr reads `/edges`).
  This is a pure frontend removal.

## Verification

- **Type/build:** `npm run build` (or `tsc --noEmit` + `next build`) clean in
  `web/` (agent runs `npm install` first — worktree lacks `node_modules`).
- **Backend tests** (Component A): `.venv/bin/python -m pytest -q` green
  (pure/fake-engine; NO live DB — `ingestion.db.get_engine()` is LIVE).
- **Architect (live/browser):** kickstart `:8000`; log into the dashboard
  (credentials with the user; behind login). Confirm: MLB builder tab byte-visually
  unchanged (record, player, team sections + slate label); NFL tab shows the honest
  empty state (offseason) with the sport tabs styled correctly; the home page shows
  only the builder CTA (+ browse); `/edges` and `/clv` now 404. Screenshot the
  builder page (both tabs) as proof.
- **`?sport=nfl` record** returns [] (until parlays settle) without error; MLB
  default unchanged.

## Out of scope

- Any backend endpoint removal/deprecation (frozen surfaces stay; Budgerr).
- NFL-specific leg rendering beyond the existing generic `ConstructionList` (game
  markets already carry `home_team`/`away_team`; the existing `LegMatchup` renders
  them — verify, don't redesign).
- NBA/soccer tabs (future sports; the selector is built to extend, but only
  mlb/nfl are wired now).
- The live-search "Build" control for NFL (BuilderControls hits `/parlay-builder`
  live; NFL live search works via the existing sport-agnostic path but is not a
  focus — the saved weekly card is the NFL surface).

## Done criteria

1. `/builder` has MLB|NFL sport tabs (`?sport=`, server-rendered); MLB unchanged,
   NFL shows player + game tiers with the honest empty state offseason.
2. `api.ts` builder fetchers take an additive `sport` (default mlb); backend
   `tier=game` + `game`-tier record labeling land (additive, tested, kickstarted).
3. `/edges` + `/clv` pages and their home links are gone (404); backend endpoints
   untouched; dead `api.ts` exports removed where safe.
4. `web/` build + `tsc` clean; backend suite green; architect browser-verifies both
   tabs + home + 404s.

## Split of labor

- **Worktree agent:** Components A (backend tier plumbing + tests), B (`api.ts`),
  C (sport selector + page), D (removal). Frontend build/tsc; backend tests
  pure/fake-engine, NEVER the live DB. Reads PRODUCT.md/DESIGN.md/web/AGENTS.md +
  the Next 16 docs. Does NOT push; does NOT touch backend endpoints.
- **Architect (reserved lanes):** the API kickstart after the `api/main.py`
  change, the browser/login verification (both tabs, home, 404s, screenshots),
  merge + README.
