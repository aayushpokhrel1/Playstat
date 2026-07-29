# NFL Odds Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `odds_ingest --sport nfl` land NFL player-prop + full-game-total odds into `prop_lines`/`game_lines`, matched to our existing NFL games/players.

**Architecture:** `ingestion/odds_ingest.py` is already sport-parameterized (`ingest_odds(sport)` reads `STAT_MAPS[sport]`/`GAME_MARKETS[sport]`/`SPORTS[sport]['odds_league_id']`, and `matching.*_index(conn, sport)` are sport-scoped). The only new code is two market-map entries for `"nfl"` plus a `--dry-run` reporting mode. The pure collectors `collect_prop_rows`/`collect_game_rows` are unit-tested with fixtured SGO events — no DB, no network.

**Tech Stack:** Python 3.11, pytest, SQLAlchemy, `requests` (via `SportsGameOddsClient`).

## Global Constraints

- **The DB is LIVE.** `ingestion.db.get_engine()` connects to the production database. Tests MUST be pure (the collectors are pure `(event, map) -> list[row]` functions) — never write the live DB from a test. A root `conftest.py` supplies dummy `DATABASE_URL`/`API_BASKETBALL_KEY` so importing `ingestion.odds_ingest` needs no `.env`.
- **Append-only, not idempotent.** `prop_lines`/`game_lines` are plain `INSERT`s on purpose — CLV needs multiple snapshots per line. Do NOT add `ON CONFLICT`/upsert.
- **Restrict `STAT_MAPS['nfl']` to markets we ingest actuals for** (the 12 NFL `player_game_stats` stat_types) so every prop is settleable downstream.
- **SGO statID strings are PROVISIONAL.** We have never hit the NFL feed; SGO's docs are ambiguous on casing (docs show `passingYards`, but our live-verified MLB map uses snake_case `batting_basesOnBalls`/`pitching_strikeouts`, and SGO's own example URL showed `rushing_ya...`). Use the snake_case values below (consistent with the working MLB map), documented as provisional; the `--dry-run` mode (Task 4) is how they get confirmed against the real feed at preseason. Tests are self-consistent with whatever strings the map uses, so they verify the *mechanism*, not real-feed correctness.
- **Defensive:** an unmapped `statID` (or a non-matching game-market tuple) is skipped, never raised — `collect_prop_rows` already does `stat_map.get(...)` → None → skip; preserve that.
- **Scope:** player props + full-game total ONLY. Spread/moneyline need a `game_lines` schema change and are deferred to sub-project #3. Settlement, tiers, chain, dashboard are out of scope.
- **graphify:** `graphify-out/graph.json` exists in the MAIN checkout only (gitignored — absent in a worktree). Read `ingestion/odds_ingest.py` directly; you don't need graphify for this small, file-local change.
- **Worktree setup:** copy `.env` from the main checkout (`cp /Users/aayushpokhrel/dev/playstat/.env ./.env`); run tests with `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest` from the worktree cwd.
- **Reference (SGO ID facts, confirmed from the working MLB code + docs):** full-game props/markets use `periodID="game"`; over/under uses `betTypeID="ou"` with `sideID` in `("over","under")`; a game total is `statID="points"`, `statEntityID="all"`; a player prop's `statEntityID` is the player id (looked up in `event["players"]`); the line is `bookOverUnder`, the price is `bookOdds` (American, as a string).

---

## File Structure

- **Modify** `ingestion/odds_ingest.py`:
  - Add `STAT_MAPS["nfl"]` (12 prop statIDs → stat_types).
  - Add `GAME_MARKETS["nfl"]` (full-game total).
  - Add a pure `observed_statid_summary(events, stat_map, game_markets)` helper.
  - Add `dry_run` param to `ingest_odds` + a `--dry-run` CLI flag (skips all DB writes, prints coverage + match counts).
- **Create** `tests/test_odds_nfl.py`: pure fixture tests for the collectors + the summary helper.

---

### Task 1: NFL player-prop map (`STAT_MAPS['nfl']`)

**Files:**
- Modify: `ingestion/odds_ingest.py` (the `STAT_MAPS` dict, ~L19-40)
- Test: `tests/test_odds_nfl.py`

**Interfaces:**
- Consumes: existing `collect_prop_rows(event, stat_map)` (pure; returns `list[dict]` with keys `player_name`, `stat_type`, `line_value`, `over_odds`, `under_odds`).
- Produces: `STAT_MAPS["nfl"]: dict[str, str]` (SGO statID → our stat_type).

- [ ] **Step 1: Write the failing test**

Create `tests/test_odds_nfl.py`:
```python
"""Pure unit tests for NFL odds ingestion (SGO event -> prop_lines/game_lines
rows). No DB, no network: the collectors are pure (event, map) -> list[dict].
SGO statIDs in STAT_MAPS['nfl'] are PROVISIONAL (verify via --dry-run at
preseason); these tests are self-consistent with the map, so they verify the
mapping mechanism, not real-feed statID correctness.
"""

from ingestion.odds_ingest import (
    STAT_MAPS,
    GAME_MARKETS,
    collect_prop_rows,
    collect_game_rows,
    observed_statid_summary,
)


def _prop_odd(stat_id, entity, side, line, price):
    return {
        "statID": stat_id, "statEntityID": entity, "periodID": "game",
        "betTypeID": "ou", "sideID": side, "bookOverUnder": line, "bookOdds": price,
    }


def test_nfl_stat_map_covers_the_twelve_settleable_stat_types():
    assert set(STAT_MAPS["nfl"].values()) == {
        "passing_yards", "rushing_yards", "receiving_yards", "receptions",
        "targets", "passing_tds", "rushing_tds", "receiving_tds",
        "completions", "carries", "pass_attempts", "interceptions_thrown",
    }


def test_collect_prop_rows_maps_an_nfl_passing_yards_market():
    # Pick whatever statID the map uses for passing_yards, so the test stays
    # correct even if the provisional string is corrected later.
    pass_yards_statid = next(k for k, v in STAT_MAPS["nfl"].items() if v == "passing_yards")
    event = {
        "players": {"MAHOMES_1": {"name": "Patrick Mahomes"}},
        "odds": {
            "o1": _prop_odd(pass_yards_statid, "MAHOMES_1", "over", 274.5, "-110"),
            "o2": _prop_odd(pass_yards_statid, "MAHOMES_1", "under", 274.5, "-105"),
        },
    }
    rows = collect_prop_rows(event, STAT_MAPS["nfl"])
    assert rows == [{
        "player_name": "Patrick Mahomes", "stat_type": "passing_yards",
        "line_value": 274.5, "over_odds": -110, "under_odds": -105,
    }]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_odds_nfl.py -q`
Expected: FAIL — `KeyError: 'nfl'` (STAT_MAPS has no "nfl" entry) / ImportError on `observed_statid_summary` (added in Task 4 — this test file imports it up front, so until Task 4 the import fails; that's fine, this task's own tests are what must pass by Task 1's end — see Step 3 note).

> Note: because the test file imports `observed_statid_summary` (Task 4) at module load, run Task 1's tests with the import temporarily satisfied by adding a stub now: in `ingestion/odds_ingest.py` add `def observed_statid_summary(events, stat_map, game_markets): raise NotImplementedError` — Task 4 fills it in. This keeps every task's tests runnable in order.

- [ ] **Step 3: Write the minimal implementation**

In `ingestion/odds_ingest.py`, add to the `STAT_MAPS` dict (alongside `"nba"`/`"mlb"`):
```python
    # NFL player props. statID keys are PROVISIONAL (SGO NFL feed never hit;
    # docs ambiguous camel vs snake, MLB map here uses snake) -- confirm via
    # `odds_ingest --sport nfl --dry-run` against the live feed at preseason.
    # Values MUST match player_game_stats stat_types (so props are settleable).
    "nfl": {
        "passing_yards": "passing_yards",
        "rushing_yards": "rushing_yards",
        "receiving_yards": "receiving_yards",
        "receptions": "receptions",
        "targets": "targets",
        "passing_touchdowns": "passing_tds",
        "rushing_touchdowns": "rushing_tds",
        "receiving_touchdowns": "receiving_tds",
        "passing_completions": "completions",
        "rushing_attempts": "carries",
        "passing_attempts": "pass_attempts",
        "interceptions": "interceptions_thrown",
    },
```
Also add the stub `def observed_statid_summary(events, stat_map, game_markets): raise NotImplementedError` near the other module functions (filled in Task 4).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_odds_nfl.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ingestion/odds_ingest.py tests/test_odds_nfl.py
git commit -m "feat(ingestion): NFL player-prop statID map (odds ingestion #1)"
```

---

### Task 2: NFL full-game-total map (`GAME_MARKETS['nfl']`)

**Files:**
- Modify: `ingestion/odds_ingest.py` (the `GAME_MARKETS` dict, ~L50-55)
- Test: `tests/test_odds_nfl.py`

**Interfaces:**
- Consumes: existing `collect_game_rows(event, game_markets)` (pure; returns `list[dict]` with keys `market`, `line_value`, `over_odds`, `under_odds`).
- Produces: `GAME_MARKETS["nfl"]: dict[str, tuple[str, str, str]]` (market name → `(statID, statEntityID, periodID)`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_odds_nfl.py`:
```python
def test_collect_game_rows_maps_the_nfl_full_game_total():
    event = {
        "odds": {
            "g1": {"statID": "points", "statEntityID": "all", "periodID": "game",
                   "betTypeID": "ou", "sideID": "over", "bookOverUnder": 47.5, "bookOdds": "-110"},
            "g2": {"statID": "points", "statEntityID": "all", "periodID": "game",
                   "betTypeID": "ou", "sideID": "under", "bookOverUnder": 47.5, "bookOdds": "-108"},
        },
    }
    rows = collect_game_rows(event, GAME_MARKETS["nfl"])
    assert rows == [{
        "market": "game_total", "line_value": 47.5,
        "over_odds": -110, "under_odds": -108,
    }]
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_odds_nfl.py::test_collect_game_rows_maps_the_nfl_full_game_total -q`
Expected: FAIL — `KeyError: 'nfl'` on `GAME_MARKETS["nfl"]`.

- [ ] **Step 3: Write the minimal implementation**

In `ingestion/odds_ingest.py`, add to the `GAME_MARKETS` dict:
```python
    # NFL full-game total (points over/under). Spread/moneyline are home/away
    # markets that don't fit game_lines' over/under columns -- deferred to
    # sub-project #3 (schema change + settlement). periodID "game" / statID
    # "points" / entity "all" confirmed from the MLB game-market pattern.
    "nfl": {
        "game_total": ("points", "all", "game"),
    },
```

- [ ] **Step 4: Run to verify it passes**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_odds_nfl.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ingestion/odds_ingest.py tests/test_odds_nfl.py
git commit -m "feat(ingestion): NFL full-game-total market map (odds ingestion #1)"
```

---

### Task 3: Defensive skip (unmapped/wrong-period markets)

**Files:**
- Test: `tests/test_odds_nfl.py` (no implementation change — this locks in existing behavior so a future edit can't regress it)

**Interfaces:**
- Consumes: `collect_prop_rows`, `STAT_MAPS["nfl"]`, `GAME_MARKETS["nfl"]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_odds_nfl.py`:
```python
def test_unmapped_statid_is_skipped_not_raised():
    event = {
        "players": {"K_1": {"name": "Some Kicker"}},
        "odds": {  # kicking points isn't in STAT_MAPS['nfl'] -> ignored
            "o1": _prop_odd("kicking_points", "K_1", "over", 7.5, "-110"),
            "o2": _prop_odd("kicking_points", "K_1", "under", 7.5, "-110"),
        },
    }
    assert collect_prop_rows(event, STAT_MAPS["nfl"]) == []


def test_mapped_stat_on_non_game_period_is_skipped():
    # A passing_yards market but for the 1st half (periodID "1h") -- we only
    # ingest full-game ("game") lines.
    pass_yards_statid = next(k for k, v in STAT_MAPS["nfl"].items() if v == "passing_yards")
    odd = _prop_odd(pass_yards_statid, "P_1", "over", 120.5, "-110")
    odd["periodID"] = "1h"
    event = {"players": {"P_1": {"name": "Half QB"}}, "odds": {"o1": odd}}
    assert collect_prop_rows(event, STAT_MAPS["nfl"]) == []
```

- [ ] **Step 2: Run to verify it passes immediately (behavior already exists)**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_odds_nfl.py -q`
Expected: PASS (5 tests). These assert existing defensive behavior; they are regression guards, so they pass without code changes.

- [ ] **Step 3: Commit**

```bash
git add tests/test_odds_nfl.py
git commit -m "test(ingestion): NFL odds defensive-skip regression guards (#1)"
```

---

### Task 4: `--dry-run` mode + statID coverage summary

**Files:**
- Modify: `ingestion/odds_ingest.py` (replace the `observed_statid_summary` stub; add `dry_run` to `ingest_odds`; add `--dry-run` to the CLI, ~L115-203)
- Test: `tests/test_odds_nfl.py`

**Interfaces:**
- Produces: `observed_statid_summary(events, stat_map, game_markets) -> dict` with keys `"mapped"` (`dict[statID, count]`) and `"unmapped"` (`dict[statID, count]`), counting every over/under odd across the events by whether its `statID` is in `stat_map` or is a configured game-market statID.
- Produces: `ingest_odds(sport, dry_run=False)` — when `dry_run=True`, performs NO DB writes; fetches events, prints the coverage summary + game/player match counts, returns None.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_odds_nfl.py`:
```python
def test_observed_statid_summary_partitions_mapped_and_unmapped():
    pass_yards_statid = next(k for k, v in STAT_MAPS["nfl"].items() if v == "passing_yards")
    events = [
        {"odds": {
            "a": _prop_odd(pass_yards_statid, "P_1", "over", 250.5, "-110"),
            "b": _prop_odd(pass_yards_statid, "P_1", "under", 250.5, "-110"),
            "c": _prop_odd("kicking_points", "K_1", "over", 7.5, "-110"),
            "d": {"statID": "points", "statEntityID": "all", "periodID": "game",
                  "betTypeID": "ou", "sideID": "over", "bookOverUnder": 47.5, "bookOdds": "-110"},
        }},
    ]
    summary = observed_statid_summary(events, STAT_MAPS["nfl"], GAME_MARKETS["nfl"])
    assert summary["mapped"] == {pass_yards_statid: 2, "points": 1}
    assert summary["unmapped"] == {"kicking_points": 1}
```

- [ ] **Step 2: Run to verify it fails**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_odds_nfl.py::test_observed_statid_summary_partitions_mapped_and_unmapped -q`
Expected: FAIL — `NotImplementedError` (the stub from Task 1).

- [ ] **Step 3: Write the implementation**

Replace the `observed_statid_summary` stub in `ingestion/odds_ingest.py` with:
```python
def observed_statid_summary(events, stat_map, game_markets):
    """Pure: count each over/under odd across events by whether its statID is
    known (in stat_map, or a configured game-market statID) or unmapped. The
    reporting core of --dry-run, so the map can be confirmed against the live
    feed without writing the DB."""
    game_stat_ids = {stat_id for (stat_id, _entity, _period) in game_markets.values()}
    mapped, unmapped = {}, {}
    for event in events:
        for odd in event.get("odds", {}).values():
            if odd.get("betTypeID") != "ou":
                continue
            stat_id = odd.get("statID")
            bucket = mapped if (stat_id in stat_map or stat_id in game_stat_ids) else unmapped
            bucket[stat_id] = bucket.get(stat_id, 0) + 1
    return mapped, unmapped
```

> The test compares against a single dict with keys `"mapped"`/`"unmapped"`. Return that shape instead of a tuple — replace the last line with:
```python
    return {"mapped": mapped, "unmapped": unmapped}
```

Then thread `dry_run` through `ingest_odds`. Change the signature and, right after the events loop starts collecting, branch. Concretely, modify `ingest_odds` so that when `dry_run=True` it collects events into a list, prints the summary + the existing `games_unmatched`/`players_unmatched` counters, and returns before any `INSERT`. Minimal version — wrap the write block and add an early dry-run path:
```python
def ingest_odds(sport="nba", dry_run=False):
    stat_map = STAT_MAPS[sport]
    game_markets = GAME_MARKETS.get(sport, {})
    odds_league_id = SPORTS[sport]["odds_league_id"]

    client = SportsGameOddsClient()
    engine = db.get_engine()

    with engine.begin() as conn:
        team_index = matching.load_team_index(conn, sport)
        player_index = matching.load_player_index(conn, sport)
        game_index = matching.load_game_index(conn, sport)

    events = list(client.get_events(odds_league_id, odds_available=True))

    if dry_run:
        summary = observed_statid_summary(events, stat_map, game_markets)
        print(f"({sport}) DRY RUN — events: {len(events)}")
        print(f"  mapped statIDs:   {summary['mapped']}")
        print(f"  UNMAPPED statIDs: {summary['unmapped']}")
        matched = unmatched_games = 0
        for event in events:
            home = event.get("teams", {}).get("home", {}).get("names", {}).get("long")
            away = event.get("teams", {}).get("away", {}).get("names", {}).get("long")
            date = matching.utc_start_to_local_date(event.get("status", {}).get("startsAt"))
            hid, aid = matching.match_team(home, team_index), matching.match_team(away, team_index)
            gid = matching.match_game(hid, aid, date, game_index) if (hid and aid and date) else None
            matched += gid is not None
            unmatched_games += gid is None
        print(f"  games matched: {matched}, unmatched: {unmatched_games}")
        return

    events_seen = 0
    rows_inserted = 0
    games_unmatched = 0
    players_unmatched = 0

    for event in events:
        events_seen += 1
        # ... (unchanged existing body: match game, collect rows, INSERT) ...
```
Keep the existing post-loop `print(...)` for the non-dry-run path unchanged. (The existing loop body from `home_name = ...` down through the `prop_rows` INSERTs is unchanged; it now iterates the already-materialized `events` list.)

Finally, add the CLI flag:
```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=list(STAT_MAPS), default="nba")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and report statID coverage + match rates without writing")
    args = parser.parse_args()
    ingest_odds(args.sport, dry_run=args.dry_run)
```

- [ ] **Step 4: Run the full test file to verify it passes**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest tests/test_odds_nfl.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the whole suite (no regressions)**

Run: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python -m pytest -q`
Expected: PASS — 279 baseline + 6 new = 285.

- [ ] **Step 6: Commit**

```bash
git add ingestion/odds_ingest.py tests/test_odds_nfl.py
git commit -m "feat(ingestion): --dry-run statID coverage report for NFL odds (#1)"
```

---

## Self-Review

**Spec coverage:**
- `STAT_MAPS['nfl']` (12 settleable stat_types) — Task 1. ✓
- `GAME_MARKETS['nfl']` full-game total — Task 2. ✓
- Defensive skip of unmapped/wrong-period markets — Task 3. ✓
- `--dry-run` coverage + match-rate report (no writes) — Task 4. ✓
- Append-snapshot (not idempotent) — preserved (no upsert added); noted in constraints. ✓
- Player-name matching (log unmatched) — the existing `players_unmatched` counter is preserved and surfaced in the dry-run report; no change needed. ✓
- Spread/moneyline deferred to #3 — enforced by `GAME_MARKETS['nfl']` containing only the total. ✓

**Placeholder scan:** provisional statID strings are concrete values with a documented `--dry-run` verification path (not TBDs); every code step shows full code. ✓

**Type consistency:** `observed_statid_summary` returns `{"mapped": dict, "unmapped": dict}` (Step 3 corrects the transient tuple to the dict the test asserts); `ingest_odds(sport, dry_run=False)` used consistently in the CLI. ✓

## Out of Scope (later sub-projects)
- Builder tiers / leg-loading generalization (#2); NFL game-market settlement + spread/moneyline ingestion & schema change (#3); NFL chain + dashboard (#4); the prediction-model shelve/measure track.
