# Per-day parlay drill-down — Design Spec

**Date:** 2026-08-07 · **Type:** dashboard feature (additive) · **Status:** design (approved in brainstorm)

## Problem

The builder record's per-day panel (`GET /parlay-builder/record/daily` + `RecordPanel.tsx`) shows
one aggregate row per slate date (`2026-08-06 · 2-6-2 · −5.45u`). It answers "how did that night
do?" but not "**which** parlays won, which lost, and **which leg** killed the losers." The user wants
to expand a day and see each settled parlay and its per-leg ✓/✗.

The data already exists: `recommendation_outcomes` has one row per settled parlay (`result`, `stake`,
`pnl`, `decimal_odds`, `n_legs`, `recommended_at`, and a per-leg **audit** JSONB `legs`). Each audit
leg carries `result` (`hit`/`miss`/`won`/`lost`/`void`), `actual`, `line`, `side`, `odds`, `game_id`,
and `market` (team) or `player_id`+`stat_type` (player). What the audit lacks is a **readable label**
(player legs have an id, not a name) — that lives on the recommendation (`parlay_recommendations.legs`,
each with `label` + `home_team`/`away_team`/`player_team_side` from §15.10).

## Approach (three-level drill-down)

```
Day row     2026-08-06  2-6-2  −5.45u          (exists today; click to expand)
 └ Parlay   Player 2.0x  LOSS  1.0u → −1.0u     (NEW level 2; click to expand)
    └ Leg   Aaron Judge hits o1.5 → 2  ✓        (NEW level 3)
    └ Leg   NRFI (Yankees @ Red Sox) → 2  ✗
```

- **Settled parlays only** (those with a `result`). Tonight's pending cards stay in the existing
  "Tonight's slate" section — not duplicated here.
- **Honest framing preserved**: paper-only caption, no +EV/edge/green. Result chips are neutral
  W/L/P styling (no signal-green for wins — green stays reserved for the ≥75% joint-prob rule).

## Architecture

### 1. New endpoint — `GET /parlay-builder/record/daily/parlays`

Dashboard-only, additive (NOT a Budgerr surface — Budgerr reads `/parlay-builder/saved`, `/games`,
`/box-scores`). Query params: `date=YYYY-MM-DD` (required), `sport=mlb` (default, mirrors the other
record endpoints' COALESCE-mlb convention). Returns the date's settled builder parlays, newest first:

```
[
  {
    "parlay_id": 297,
    "result": "loss",              // win | loss | push
    "tier": "team",                // from legs->>'class' via _CLASS_TO_TIER (player/team/game)
    "target_payout": 1.4,
    "combined_odds": 2.913,        // recorded decimal_odds
    "stake": 1.0,
    "pnl": -1.0,
    "legs": [
      {"label": "f5_runs under 5.5", "side": "under", "line": 5.5,
       "actual": 4.0, "result": "hit", "odds": -143, "book": null,
       "home_team": "…", "away_team": "…"}
    ]
  }
]
```

SQL: join `recommendation_outcomes ro` to `parlay_recommendations pr` on `parlay_id`,
`WHERE pr.kind='builder' AND COALESCE(pr.legs->>'sport','mlb')=:sport AND date(pr.created_at)=:date`,
`ORDER BY ro.parlay_id DESC`. Select `ro.parlay_id, ro.result, ro.stake, ro.pnl, ro.decimal_odds,
pr.target_payout, pr.legs->>'class' AS cls, pr.legs AS rec_legs, ro.legs AS audit_legs`.

### 2. Leg merge — recommendation labels ⋈ audit results

A pure helper `_merge_parlay_legs(rec_legs, audit_legs)` keys each list by
`settle.builder_leg_key(leg)` (player → `("player", player_id, game_id, stat_type)`; team →
`("team", game_id, market)`) and joins them: **label + team context** from the recommendation leg,
**result/actual/line/side** from the audit leg. Legs the audit lacks (shouldn't happen for a settled
parlay) fall back to the recommendation leg with `result=None`. Order follows the recommendation legs.
DB-free, unit-testable.

### 3. Pure shaper — `_shape_daily_parlays(rows)`

`rows` are `(parlay_id, result, cls, target_payout, combined_odds, stake, pnl, rec_legs, audit_legs)`.
Maps `cls→tier` via the existing `_CLASS_TO_TIER`, casts Decimals to float, calls `_merge_parlay_legs`,
returns a list of a new `DailyParlayOut` Pydantic model (+ nested `DailyParlayLegOut`). Mirrors
`_shape_builder_record_daily`'s DB-free, fake-engine-testable pattern.

### 4. UI — extend `RecordPanel.tsx`

The per-day panel already lazy-renders `daily` rows behind a click-to-expand. Add a **second-level
expander per day row**: on expand, fetch (client-side, on demand) `/parlay-builder/record/daily/parlays?date=<day>`
and render the parlay list; each parlay row is itself expandable to its legs. Reuse existing
`recordRow`/`recordFigure`/`recordMeta` styles; result chip is neutral (reuse the W/L/P text style,
no new green). Leg row shows `label`, `actual` vs `line`, and a ✓/✗/– glyph from `result`
(`hit`/`won`→✓, `miss`/`lost`→✗, `void`→–). Add `getDailyParlays(date, sport)` to `web/app/lib/api.ts`
+ the `DailyParlay`/`DailyParlayLeg` TS types.

## Guardrails / safety

- **Additive-only.** New endpoint + new schemas + new TS types + UI expansion. No change to
  `/parlay-builder/saved`, `/games`, `/box-scores`, or the existing `/record`/`/record/daily` shapes.
  Budgerr byte-unchanged.
- **No test DB.** Pure shaper + merge helper unit-tested; endpoint tested via the fake-engine/queue
  pattern (`tests/test_builder_record_api.py`). `builder_leg_key` is imported from `modeling.settle`.
- **API-imported module** (`api/main.py`) → architect kickstarts after merge.
- **Honest framing** (§15.8 #2): paper-only, neutral result chips, no +EV/green.

## Out of scope (v1)

- Pending (unsettled) parlays in this view — they live in the "Tonight's slate" section.
- Edge/CLV or model context per leg — this is a win/loss autopsy, not analysis.
- Any change to settlement logic — this is read-only surfacing of what settle already recorded.

## Testing

- `_merge_parlay_legs`: player + team legs matched by key; label from rec, result from audit; order
  preserved; audit-missing leg → `result=None`.
- `_shape_daily_parlays`: cls→tier mapping; Decimal→float; nested leg shape; empty rows → `[]`.
- Endpoint: fake-engine returns rows → correct `DailyParlayOut` list; `sport`/`date` params threaded;
  empty → `[]`.
