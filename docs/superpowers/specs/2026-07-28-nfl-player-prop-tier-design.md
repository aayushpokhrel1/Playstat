# NFL player-prop builder tier — design spec

Sub-project **#2 of 4** in the NFL parlay-builder decomposition (user-confirmed
2026-07-28). Build order: #1 odds ingestion (BUILT) → **#2 builder + NFL
player-prop tier (this doc)** → #3 NFL game-markets tier + settlement → #4 NFL
chain + dashboard. Depends on #1 (NFL `prop_lines` ingestion) for real data; the
tier is built now (offseason) and verified structurally, then produces real
parlays once NFL odds land at preseason (~August).

Strategy context: inherits the MLB builder's proven honest core — rank on
DE-VIGGED MARKET probability, `model_prob` context-only (null for NFL), the §15.8
guardrails — and needs no prediction model.

## Goal

`optimizer.builder --sport nfl --save` builds and saves NFL across-game
**player-prop** parlays (`kind='builder'`, `sport='nfl'` in the legs blob) that
settle automatically. The builder is currently sport-blind; this makes it
sport-aware so NFL and MLB builder parlays never mix.

## Key findings (from reading the code)

1. **The builder is sport-blind.** `load_player_legs` (optimizer/builder.py:37)
   joins `prop_lines → games → players` with NO sport filter — it pools ALL of
   today's prop lines. Only MLB data exists today, so it's implicitly MLB; the
   moment #1's NFL `prop_lines` land, this query would mix an MLB leg and an NFL
   leg into one parlay. #2 must add sport filtering.
2. **Settlement already generalizes.** `settle_builder_parlays`
   (modeling/settle.py:396) settles a player leg via
   `player_game_stats[(player_id, game_id, stat_type)]` vs the leg's line — fully
   sport-agnostic. An NFL player parlay settles with ZERO settlement changes
   (the MLB-hardcoded `runs_inning_1`/`runs_f5` team-market path is #3's concern,
   not #2's). #2 only adds a test proving it.
3. `normalize_player_leg` and `builder_core.build` (the search) are already
   sport-agnostic (rank on market prob; labels are generic
   "{player} {stat_type} {side} {line}"). No change.

## Guardrails (inherited from §15.8, unchanged)

Rank on de-vigged MARKET probability (never `model_prob`); per-leg
`market_prob ≥ 0.55` favorite floor; 2–4 legs; across-game legs only; target
payouts 1.4x / 2.0x as FLOORS; paper-only; no "+EV"/"edge"/"value" language; no
signal-green. These are not re-litigated.

## Components

### 1. Loading — `optimizer/builder.py`
- Add a `sport` parameter (default `"mlb"`) to `load_player_legs`,
  `load_team_legs`, and `load_legs`, filtering the games join with
  `g.sport = :sport`.
- Add a `--sport` CLI argument (default `"mlb"`). The MLB daily chain invokes the
  builder with NO `--sport`, so it stays MLB — backward-compatible.
- No special-casing for NFL: `--sport nfl` on the default (non-`--team-only`)
  path yields player-only legs, because `load_team_legs` filters `game_lines` to
  the MLB-only markets `TEAM_MARKETS = ("first_inning_runs","f5_runs")`, which
  are empty for `g.sport='nfl'`. NFL team markets are #3.

### 2. Saving — `save_builds` (`optimizer/builder.py`)
- Add a `sport` parameter (default `"mlb"`) and write it into the JSONB wrapper:
  `{"class": <class>, "sport": <sport>, "legs": [...]}` (currently
  `{"class", "legs"}`). Thread `sport` from the CLI (`args.sport`).
- Backward-compatible: existing MLB rows have no `sport` key; readers treat an
  absent key as `"mlb"` (see endpoint below).

### 3. Settlement — no change
- `settle_builder_parlays` is unchanged. #2 adds a test: given a fixtured NFL
  player builder parlay (`sport='nfl'` blob, NFL player leg) and NFL
  `player_game_stats`, it settles win/loss correctly (over/under vs line, DNP
  void), exactly as MLB does.

### 4. Saved endpoint — `/parlay-builder/saved` (`api/main.py`)
- Add an ADDITIVE `sport: str = "mlb"` query param. Filter the builder rows with
  `COALESCE(legs->>'sport', 'mlb') = :sport`. Existing MLB rows (no `sport` key)
  read as `mlb`; Budgerr and the MLB dashboard, passing no `sport`, get exactly
  MLB (unchanged) — Budgerr-contract-safe (§7.1). The NFL dashboard (#4) passes
  `?sport=nfl`. This closes the contamination footgun on the primary consumer
  surface the moment any NFL builder row exists.
- The existing `tier` param (player/team/all) is orthogonal and unchanged; `sport`
  composes with it.

## Verification (offseason — no NFL `prop_lines` yet)

CRITICAL SAFETY: NO test DB; `ingestion.db.get_engine()` is LIVE. Tests are pure
or use the `_FakeEngine` isolation (tests/test_parlay_recommendations_api.py /
test_builder_record_api.py) — never write the live DB.

- **Loading:** `load_player_legs`/`load_legs` pass `sport` into the query
  (assert the SQL includes `g.sport = :sport` and the param is threaded); a
  fixture/DataFrame test that only the requested sport's legs survive.
- **Saving:** `save_builds(sport='nfl')` writes `legs->>'sport' = 'nfl'` in the
  JSONB; default writes `'mlb'`.
- **Endpoint:** `saved_builder_parlays(sport='nfl')` returns only `sport='nfl'`
  rows; `sport='mlb'` (default) returns MLB rows including legacy rows with no
  `sport` key (via the COALESCE default). Fake-engine.
- **Settlement:** a fixtured NFL player parlay settles correctly (fake-engine /
  pure leg_status).
- Real end-to-end (`--sport nfl --save` producing actual NFL parlays) is deferred
  to preseason once #1's ingestion lands NFL `prop_lines`.
- Architect kickstarts `:8000` after the `api/main.py` change and browser-checks
  the MLB dashboard is unaffected.
- Full suite stays green (currently 285).

## Done criteria

1. `optimizer.builder --sport nfl` loads only NFL legs; `--save` writes
   `kind='builder'` rows with `sport='nfl'` in the blob.
2. Default (`--sport` omitted) behavior is byte-identical to today for MLB.
3. `settle_builder_parlays` settles an NFL player parlay (test-proven), no code
   change.
4. `/parlay-builder/saved?sport=nfl` returns NFL rows; default/`mlb` returns MLB
   (incl. legacy no-`sport` rows); Budgerr's no-`sport` call is unchanged.
5. New unit tests green; full suite green.

## Out of scope (later sub-projects)
- NFL team-markets tier + game-market settlement, and NFL spread/moneyline
  ingestion + `game_lines` schema change (#3).
- NFL nightly chain (weekly cadence) + dashboard + record-endpoint sport-filtering
  (`/parlay-builder/record*`) (#4). The record endpoints only need sport-filtering
  before NFL parlays settle at preseason, which is after #4.
- Any change to the prediction model (separate track).
