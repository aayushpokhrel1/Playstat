# NFL odds ingestion — design spec

Sub-project **#1 of 4** in the NFL parlay-builder decomposition (user-confirmed
2026-07-28). Build order: **#1 odds ingestion (this doc)** → #2 builder + NFL
player-prop tier → #3 NFL game-markets tier + settlement → #4 NFL chain +
dashboard. #1 is the foundation and the only piece with real unknowns (SGO's
NFL market catalog), so it goes first and de-risks the rest.

Strategy context: the NFL builder inherits the MLB builder's proven honest core
(rank on de-vigged MARKET probability; `model_prob` context-only; §15.8
guardrails) and does NOT depend on the prediction model — NFL has no prop model,
and the builder already tolerates `model_prob = None`. The model's own
shelve-vs-measure question is a **separate track**, not part of this work.

## Goal

`odds_ingest --sport nfl` fetches NFL **player-prop and game-total** markets from
SportsGameOdds (SGO) and **appends snapshot rows** to `prop_lines` and
`game_lines`, matched to our existing NFL games/players. This lands the raw odds
data the rest of the NFL builder consumes. Nothing downstream (tiers, settlement,
chain, dashboard) is in scope here.

**Scope note (refined during planning, user-confirmed 2026-07-28):** `game_lines`
only has over/under columns (`line_value, over_odds, under_odds`) and
`collect_game_rows` handles only `betTypeID="ou"`, so **only the full-game total
(points O/U)** fits today's schema. **Spread and moneyline** are home/away markets
that need a `game_lines` schema change + collector generalization — deferred to
**#3**, where their settlement lives. **Append-only, not idempotent:**
`prop_lines`/`game_lines` are plain INSERTs on purpose — CLV needs multiple
snapshots per line over time to measure line movement, so each run appends.

## What already exists (no work)

- `SPORTS['nfl']['odds_league_id'] = 'NFL'` (`ingestion/config.py`).
- `ingestion/matching.py` `load_team_index` / `load_player_index` /
  `load_game_index` all take `sport` and filter `WHERE sport = :sport`. Our NFL
  data (855 games 2023-09-07 → 2026-02-08; ~225k player_game_stats rows across
  12 stat_types) resolves through them out of the box.
- `ingestion/odds_ingest.py` `ingest_odds(sport)` is fully sport-parameterized:
  it reads `STAT_MAPS[sport]`, `GAME_MARKETS.get(sport, {})`,
  `SPORTS[sport]['odds_league_id']`, and the sport-scoped matching indexes.
- `SportsGameOddsClient` (with the transport-retry hardening landed 2026-07-28),
  the `prop_lines` / `game_lines` upserts, and the `--sport` CLI are all
  exercised nightly by MLB.

## What's missing (the actual work)

`STAT_MAPS` and `GAME_MARKETS` have only `nba`/`mlb` entries. NFL odds have never
been ingested (`prop_lines`/`game_lines` for `sport='nfl'` = 0). The work is two
market maps plus a verification path.

### 1. `STAT_MAPS['nfl']` — player prop `statID` → our `stat_type`

Restricted, exactly like MLB, to markets we **already ingest actuals for** (so
every ingested prop is settleable downstream). Target the 12 NFL stat_types
present in `player_game_stats`:

`passing_yards`, `rushing_yards`, `receiving_yards`, `receptions`, `targets`,
`passing_tds`, `rushing_tds`, `receiving_tds`, `completions`, `carries`,
`pass_attempts`, `interceptions_thrown`.

The **literal SGO `statID` strings are intentionally not fixed in this spec** —
we have never hit the NFL feed, and guessing them would be a placeholder worse
than an explicit deferral. The implementation plan's **first task** is to obtain
SGO's NFL statID catalog (from SGO's market reference and/or a dry-run probe;
see Verification) and pin the map. The spec fixes the map's **shape and target
stat_types**, not the source strings.

### 2. `GAME_MARKETS['nfl']` — game market → `(statID, statEntityID, periodID)`

Full-game **total (points over/under) only** in #1 — spread/moneyline deferred to
#3 (see Scope note). Same structure as MLB's `GAME_MARKETS['mlb']` (e.g. MLB "1st
Inning O/U" is `statID=points, statEntityID=all, periodID=1i`); the NFL full-game
total is the `points`/`all`/`game` over/under. Confirmed from the working MLB code
that full-game markets use `periodID="game"` and `betTypeID="ou"`.

### 3. Defensive ingestion (mandatory)

An SGO `statID` (or game-market tuple) not present in the maps is **logged and
skipped, never crashes the run**. `collect_prop_rows` already does
`stat_map.get(statID)` (None → skip); preserve and extend that discipline to the
game-market path. Rationale: the maps are built without a fully verified live
feed, so an unexpected/renamed market must degrade gracefully, not fail the
nightly NFL chain (#4) every night.

## Discovery / verification approach (LOCKED — "A")

Build both maps **from SGO's NFL market reference now (free, no API quota)**;
verify against the real feed at preseason. Concretely:

- **`--dry-run` mode**: fetch NFL events and print observed `statID`s, which map /
  don't map, and player/game **match rates**, WITHOUT appending to the DB. This is
  the verification instrument: run it once against the live feed when preseason
  odds appear (~August) to confirm the maps and see name-match quality. It is
  also the safe way to spend a *single* live probe now if we later choose to.
- No API quota is spent by building/testing #1 (unit tests use fixtures). Matches
  the user's "build now, prove at kickoff" decision and the prioritize-free
  principle.

Alternatives considered and rejected: (B) spend a live probe now for early
certainty — the offseason feed is likely sparse, so it may not reveal the full
catalog; (C) stub the maps and fill at preseason — then #1 delivers nothing
runnable. `--dry-run` gives us B's benefit on demand without committing to it.

## Known risk — player-name matching

SGO's NFL player names vs our `nfl_backfill` names (the same fuzzy-match concern
MLB had). Reuse `matching.load_player_index` normalization. **Log unmatched
props** (with the SGO name) rather than dropping silently, so match rate is
visible in `--dry-run` output at preseason and we can add normalization fixes
before Week 1. Team/game matching reuses the existing sport-scoped indexes.

## Verification (offseason — no live odds yet)

- **Pure unit tests**, mirroring `tests/test_odds.py`: fixtured SGO NFL event
  payloads → `collect_prop_rows` / `collect_game_rows` produce the correct
  `prop_lines` / `game_lines` rows; an unmapped `statID` is skipped (not raised);
  unmatched player names are logged and skipped. The pure collectors
  (`collect_prop_rows`/`collect_game_rows`) take `(event, map)` and return rows —
  no DB, no network, ideal for fixture tests. `ingestion.db.get_engine()` is LIVE
  — never write it from tests; test the pure collectors directly (the DB write is
  plain append INSERTs, unchanged from MLB, and not re-tested here).
- **Live `--dry-run` probe**: deferred to preseason (or run once now only if we
  explicitly choose to spend quota).
- Settlement is NOT verified here (it's #2/#3).

## Done criteria

1. `STAT_MAPS['nfl']` and `GAME_MARKETS['nfl']` exist, covering the 12 prop
   stat_types + the full-game total, sourced from SGO's reference.
2. `odds_ingest --sport nfl` runs clean end-to-end on a fixtured event, appending
   `prop_lines` + `game_lines` snapshot rows matched to NFL games/players, and
   skipping unmapped markets / unmatched players with a log line.
3. `--dry-run` prints observed statIDs + map coverage + match rates without
   writing.
4. New pure unit tests green; full suite still green (currently 279).

## Out of scope (later sub-projects)

- The builder tiers and leg-loading generalization (#2).
- **NFL spread + moneyline ingestion** — needs a `game_lines` schema change +
  collector generalization; bundled with their settlement in #3.
- NFL game-market settlement — total/spread/moneyline `leg_status` (#3).
- NFL chain (weekly cadence) + launchd + dashboard/record surface (#4).
- Any change to the prediction model (separate track).
