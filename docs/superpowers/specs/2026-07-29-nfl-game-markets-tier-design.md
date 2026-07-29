# NFL game-markets tier + settlement — design spec

Sub-project **#3 of 4** in the NFL parlay-builder decomposition (user-confirmed
2026-07-28). Build order: #1 odds ingestion (BUILT) → #2 builder + player-prop
tier (BUILT) → **#3 game-markets tier + settlement (this doc)** → #4 NFL chain +
dashboard. Depends on #1's ingestion plumbing and #2's sport-aware builder. Built
now (offseason); verified structurally + by `--dry-run` at preseason (~August);
produces real parlays once NFL game odds land.

Strategy context: inherits the MLB builder's proven honest core — rank on
DE-VIGGED MARKET probability, `model_prob` context-only (null for NFL), the §15.8
guardrails — and needs no prediction model.

## Goal

`optimizer.builder --sport nfl --team-only --save` builds and saves NFL
across-game **game-market** parlays (total / spread / moneyline; `kind='builder'`,
`sport='nfl'`, `class='game_tier'` in the legs blob) that **settle automatically
against final scores**. Full-game spread + moneyline odds are ingested into
`game_lines` (requiring a schema change), and NFL final scores — currently stored
nowhere — are captured so settlement has data to score against.

## Key findings (from reading the code, 2026-07-29)

1. **`game_lines` is over/under-only.** Schema:
   `(line_id, game_id, market, line_value, over_odds, under_odds, pulled_at)`;
   append-snapshot INSERT (`ingestion/odds_ingest.py`). Spread + moneyline are
   home/away markets that don't fit — hence the schema change (below).
2. **`collect_game_rows` handles `betTypeID=="ou"` only** (sideID over/under,
   reads `bookOverUnder` + `bookOdds`). `GAME_MARKETS['nfl']` today = `game_total`
   only (`points`/`all`/`game`).
3. **`favorite_side(a, b)` is side-label-agnostic** (`optimizer/builder_core.py`):
   it de-vigs two American prices and returns `(side, prob)` for the more probable
   one. The `"over"/"under"` labels are cosmetic — the *same math* selects a
   home/away favorite. So spread/moneyline normalization reuses it directly.
4. **`load_team_legs` (`optimizer/builder.py`)** filters `game_lines.market =
   ANY(TEAM_MARKETS)` with a module constant `TEAM_MARKETS =
   ("first_inning_runs","f5_runs")`, SELECTs `over_odds/under_odds`, and
   `_normalize` drops rows with null over/under/line. This is MLB-shaped and must
   become sport-aware + geometry-aware.
5. **🔴 NFL final scores are stored NOWHERE.** `ingestion/nfl_backfill.py`
   `backfill_games` reads `row["home_score"]` from the nflverse schedule CSV only
   as a boolean to set `games.status='FT'`, then discards it. `games` has no score
   columns (by design — results live in long-format `*_game_stats` tables), and
   `team_game_stats` receives nothing from NFL. **Settlement has no score data to
   work against.** This is a 5th, load-bearing piece #3 must include (see [[nfl-final-scores-not-stored]]).
6. **`settle_builder_parlays` team path** SUMs `team_game_stats` over
   `('runs_inning_1','runs_f5')` GROUP BY `(game_id, stat_type)` →
   `settle_leg(side, actual, line)`. NFL total fits this SUM pattern; spread +
   moneyline need the per-team home/away split, which SUM discards.

## Decisions (user-confirmed 2026-07-29)

- **Schema:** add nullable `home_odds`, `away_odds` columns to `game_lines`
  (not a bet-type/side dimension). Additive; existing rows read NULL; the `market`
  string names the geometry.
- **Market scope:** ingest + load **all three** NFL game markets (total, spread,
  moneyline) and let the **binding §15.8 0.55 favorite floor do the filtering** —
  no market-type special-casing. Moneyline favorites clear it; totals/spreads
  price near coin-flip and usually don't. Honest by construction, exactly like MLB
  NRFI/F5 (which almost never surface).
- **Final scores:** store each team's final points in
  `team_game_stats(team_id, game_id, 'points', score)` for home + away (mirrors
  MLB `runs_inning_1`); settlement reads it identically.

## Guardrails (inherited from §15.8, unchanged, NOT re-litigated)

Rank on de-vigged MARKET probability (never `model_prob`); per-leg
`market_prob ≥ 0.55` favorite floor; 2–4 legs; across-game legs only; paper-only;
no "+EV"/"edge"/"value" language; no signal-green. Additive-only to
`/parlay-builder/saved` (Budgerr contract, §7.1).

## Components

### A. Migration — `db/migrations/007_game_lines_home_away_odds.sql` (architect's reserved lane)

```sql
ALTER TABLE game_lines
    ADD COLUMN IF NOT EXISTS home_odds INTEGER,
    ADD COLUMN IF NOT EXISTS away_odds INTEGER;
```

Fully additive. Existing MLB over/under rows (`first_inning_runs`, `f5_runs`) and
the NFL `full_game_total` keep using `over_odds`/`under_odds` untouched; the new
columns stay NULL for them. No backfill. Applied to the live DB by the architect.

**Geometry-per-market (the invariant the rest of the design relies on):**

| market                | geometry   | line_value        | odds columns used   |
|-----------------------|------------|-------------------|---------------------|
| `first_inning_runs`   | over/under | total (0.5…)      | over_odds/under_odds |
| `f5_runs`             | over/under | total (~3.5)      | over_odds/under_odds |
| `full_game_total`     | over/under | total (~44.5)     | over_odds/under_odds |
| `full_game_spread`    | home/away  | **home** spread   | home_odds/away_odds |
| `full_game_moneyline` | home/away  | **NULL**          | home_odds/away_odds |

Spread `line_value` is always the **home** spread by convention (away = its
negation); moneyline has no line.

### B. NFL final-score ingestion — `ingestion/nfl_backfill.py`

In `backfill_games`, when a game is played (`home_score` non-blank), also write
two `team_game_stats` rows in the same transaction:
`(home_team_id, game_id, 'points', home_score)` and
`(away_team_id, game_id, 'points', away_score)`. The scores are already present in
`row` — no new fetch, no new network source. Idempotent via `db.upsert` on
`(team_id, game_id, stat_type)` (the table's PK), so a re-run overwrites rather
than duplicates. Guard against NULL/blank scores (unplayed games write no points
row). The architect runs the live backfill (`ingestion.nfl_backfill`) to populate
the 3 existing seasons after the code merges.

### C. NFL spread + moneyline ingestion — `ingestion/odds_ingest.py`

1. `GAME_MARKETS['nfl']` gains two entries alongside `game_total`:
   ```python
   "full_game_spread":    ("points", "all", "game"),   # betTypeID sp
   "full_game_moneyline": ("points", "all", "game"),   # betTypeID ml
   ```
   (Rename the existing `game_total` key to `full_game_total` for a consistent
   `full_game_*` namespace — it has never been ingested live, so no data migration.
   The agent must sweep **all** references, including #1's `game_total` fixture
   tests, and update them — a `grep -rn game_total` must come back clean.)
2. Generalize `collect_game_rows` to dispatch on `betTypeID`:
   - `"ou"` → today's path (sideID over/under, `line_value` from `bookOverUnder`,
     `over_odds`/`under_odds` from `bookOdds`). Unchanged.
   - `"sp"` → sideID home/away; `line_value` = the **home** side's spread; write
     `home_odds`/`away_odds`.
   - `"ml"` → sideID home/away; `line_value` NULL; write `home_odds`/`away_odds`.

   The market key is now `(statID, statEntityID, periodID, betTypeID)` since total,
   spread and moneyline share `points/all/game` and differ only by `betTypeID`. The
   `GAME_MARKETS` tuple stays `(statID, entity, period)`; `betTypeID` is derived
   from the market **name** (`full_game_spread`→`sp`, `full_game_moneyline`→`ml`,
   else `ou`) via a small pure helper, so the config shape is unchanged.
3. The `game_lines` INSERT gains `home_odds`/`away_odds` (default None for
   over/under rows) — additive to the existing statement.
4. **Defensive ingestion preserved (#1's discipline):** an unmapped market tuple /
   `betTypeID` is logged and skipped, never crashes the run.

**PRESEASON-VERIFIED UNKNOWNS (LOCKED — same discipline as #1's statIDs).** We have
never hit the NFL feed for spread/moneyline. The following are the *intended*
values, to be confirmed by a single `--dry-run` probe when preseason odds appear
(~August) **before** the first live ingest — not guessed into production:
  - `betTypeID` strings `"sp"` / `"ml"` and `sideID` `"home"`/`"away"`.
  - The **exact SGO field carrying the spread line** (analogous to `bookOverUnder`
    for totals — likely `bookSpread`; confirm the field name and its sign
    convention so `line_value` is stored as the home spread).
  - `--dry-run` (from #1) already reports observed statIDs; extend its summary to
    also count observed `betTypeID`s per game market so the probe reveals sp/ml
    coverage + the spread field. The collector reads the spread-line field name
    from a single named constant so pinning it at preseason is a one-line change.

### D. Builder game-market tier — `optimizer/builder.py` + `builder_core.py`

1. `TEAM_MARKETS` becomes a **per-sport dict**:
   ```python
   TEAM_MARKETS = {
       "mlb": ("first_inning_runs", "f5_runs"),
       "nfl": ("full_game_total", "full_game_spread", "full_game_moneyline"),
   }
   ```
   `load_team_legs` selects `TEAM_MARKETS[sport]` (empty tuple / no-op for a sport
   without game markets). MLB default byte-identical.
2. `load_team_legs` SELECT adds `home_odds, away_odds`; the DISTINCT-ON latest-pull
   join is unchanged.
3. **Geometry-aware normalization.** Split `normalize_team_leg` (or branch inside
   it) on market geometry:
   - **over/under markets** (all MLB + `full_game_total`): unchanged —
     `favorite_side(over_odds, under_odds)`, sides over/under, `line_value` required.
   - **home/away markets** (`full_game_spread`, `full_game_moneyline`):
     `raw, prob = favorite_side(home_odds, away_odds)`; map `over→home`,
     `under→away`; `side ∈ {home, away}`; odds = the favored side's odds; label
     e.g. `"{market} {side} {line}"` / `"{market} {side}"` for moneyline.
   - Geometry is decided by market name (a pure `MARKET_GEOMETRY` map:
     name → `"ou"|"homeaway"`), DB-free and unit-testable.
4. **`_normalize` validity filter becomes geometry-aware:** over/under rows still
   require non-null `over/under/line`; home/away rows require non-null
   `home_odds/away_odds` and (spread only) `line_value`; **moneyline requires no
   line**. `_base_leg` tolerates `line_value=None` (store None, not `float(None)`)
   for moneyline.
5. **Save:** `--team-only --sport nfl` writes `class='game_tier'` (new, additive —
   distinct from MLB's `team_tier`) so #4's dashboard + the record endpoints can
   name the NFL game tier. The `sport='nfl'` blob field (from #2) is threaded
   through. `--team-only` for MLB still writes `team_tier` (unchanged).

   > Class naming: MLB team tier = `team_tier`; NFL game tier = `game_tier`. Both
   > are `kind='builder'`, `--team-only` builds. Keeping them distinct classes (not
   > reusing `team_tier`) lets the record/dashboard label NFL vs MLB game markets
   > and keeps the guardrail-honest "may be empty / higher variance" caption
   > sport-specific.

   **`class='game_tier'` is additive — verified against `api/main.py`.** The saved
   endpoint's `TIER_TO_CLASS = {"player":"across_game","team":"team_tier"}` filters
   `legs->>'class' = :cls`. #3 does **NOT** modify `TIER_TO_CLASS` (a `game →
   game_tier` tier mapping is #4's dashboard concern). Consequences, all safe:
   - `?tier=team` filters `team_tier` → NFL `game_tier` rows are **excluded** (not
     mis-served under the MLB team tier).
   - Budgerr (`?tier=all&limit=100`, no `sport`) and the MLB dashboard (no `sport`)
     resolve `COALESCE(legs->>'sport','mlb')='mlb'`, which **excludes** every
     `sport='nfl'` row regardless of class. No existing consumer sees NFL rows.
   - In #3, NFL game-tier rows are reachable only via `?tier=all&sport=nfl` — which
     is sufficient for structural verification. A dedicated `?tier=game` name is #4.
   - `/parlay-builder/record*` GROUP-BY-class already maps an unknown class via
     `_CLASS_TO_TIER.get(cls, cls)` (sorts last) and has no sport filter yet; that
     only matters once NFL parlays *settle* (preseason), which #4 precedes by adding
     the record endpoints' sport filter. Not a #3 concern.

### E. Game-market settlement — `modeling/settle.py`

`settle_builder_parlays`' team-leg branch becomes market-aware. **Pure,
unit-tested scoring functions** (DB-free), mirroring `settle_leg`:

```
game_total(home_pts, away_pts)                 -> home_pts + away_pts
settle_total_leg(side, total, line)            -> settle_leg(side, total, line)  # reuse
settle_spread_leg(side, home_pts, away_pts, home_line)
    margin = home_pts - away_pts
    covered = margin + home_line  (home)  |  -(margin) - home_line (away)  [away covers by away_line = -home_line]
    -> 'won' if covered > 0, 'lost' if < 0, 'void' (push) if == 0
settle_moneyline_leg(side, home_pts, away_pts)
    -> 'won'/'lost' by which side scored more; tie -> 'void' (push)
```

Settlement data fetch (NFL game legs only; MLB path untouched):
- Fetch per-team points: `SELECT team_id, game_id, value FROM team_game_stats
  WHERE stat_type='points' AND game_id = ANY(...)`, plus
  `games.home_team_id/away_team_id`, to build `home_pts`/`away_pts` per game.
- **Dispatch by market name** (self-contained, no sport lookup needed):
  - over/under markets → existing `tstats_lookup` SUM path (extend the stat→market
    map / IN-list so `full_game_total` resolves to `SUM(points)`), `settle_leg`.
  - `full_game_spread` → `settle_spread_leg` with the snapshot's `line_value`
    (home spread) from `game_lines`.
  - `full_game_moneyline` → `settle_moneyline_leg`; **no line lookup / no null-line
    "not ready"** (the existing `line_value is None → ready=False` must be skipped
    for moneyline).
- **DNP/void reuse:** the existing `leg_status(gstatus, actual)` void path (FT game
  but no stat row) still applies — an FT game missing its `points` rows voids the
  game leg like a push, consistent with the player DNP rule.

## Verification (offseason — no live NFL game odds yet)

CRITICAL SAFETY: NO test DB; `ingestion.db.get_engine()` is LIVE. Tests are pure
or use `_FakeEngine` isolation (`tests/test_parlay_recommendations_api.py` /
`test_builder_record_api.py`) — never write the live DB. Worktree agents read
source directly (`graphify-out/` is gitignored; not present in the worktree) but
must still run `graphify query` against the main checkout's graph
(`--graph /Users/aayushpokhrel/dev/playstat/graphify-out/graph.json`) when
exploring, per the repo rule.

- **Ingestion (pure):** fixtured SGO NFL event payloads with `sp`/`ml`/`ou` odds →
  `collect_game_rows` produces correct `game_lines` rows (spread → home/away odds +
  home line; moneyline → home/away odds, null line; total unchanged); an unmapped
  `betTypeID` is skipped, not raised. Extend `observed_statid_summary` /
  `--dry-run` to report betTypeID coverage.
- **Score ingestion (pure/fixture):** a fixtured played schedule row yields two
  `team_game_stats` `'points'` rows (home+away); an unplayed row yields none;
  re-run is idempotent.
- **Builder tier (pure/DataFrame):** `MARKET_GEOMETRY` classification;
  `normalize_team_leg` on a home/away spread row picks the devig favorite side
  (home or away) and the right odds; moneyline row normalizes with null line;
  `_normalize` keeps home/away rows and applies the floor; `load_team_legs` SQL
  uses `TEAM_MARKETS[sport]` and threads `sport`. A fixture proving an NFL
  moneyline favorite (≥0.55) survives and a coin-flip total (<0.55) is filtered.
- **Settlement (pure):** `settle_spread_leg` (home cover, away cover, push),
  `settle_moneyline_leg` (home win, away win, tie→void), `game_total`, and a
  fixtured NFL game-market builder parlay settling end-to-end via `_FakeEngine`
  (win/loss/void), including the moneyline no-line path.
- **Live `--dry-run` probe:** deferred to preseason (or one explicit quota spend
  now) — confirms sp/ml IDs, the spread field, and match rates before Week 1.
- Architect: applies the migration, runs the live NFL score backfill, kickstarts
  `:8000` after any `api/` change (none expected here — the endpoint already has
  `?sport`/`tier`), and confirms MLB is unaffected.
- Full suite stays green (currently 299).

## Done criteria

1. Migration adds `home_odds`/`away_odds`; existing rows unaffected (live-applied).
2. `nfl_backfill` writes `team_game_stats` `'points'` for played games; 3 seasons
   backfilled live.
3. `odds_ingest --sport nfl` ingests total + spread + moneyline into `game_lines`
   (spread/ML using home/away odds), skipping unmapped markets; `--dry-run` reports
   betTypeID coverage. Exact sp/ml/spread-field values pinned at preseason.
4. `optimizer.builder --sport nfl --team-only --save` loads NFL game legs, applies
   the 0.55 floor, saves `class='game_tier'`/`sport='nfl'` rows. Default MLB
   behavior byte-identical.
5. `settle_builder_parlays` settles NFL total/spread/moneyline against final scores
   (pure fns unit-tested; end-to-end via fake-engine), moneyline needs no line, DNP
   voids.
6. New pure/fake-engine tests green; full suite green.

## Out of scope (sub-project #4)

- NFL nightly chain (weekly cadence) + launchd + dashboard surface for the game
  tier + sport-filtering the `/parlay-builder/record*` endpoints.
- Any change to the prediction model (separate track — being shelved, README §16).
- Same-game NFL correlation (deferred, §15.9 item 1).
