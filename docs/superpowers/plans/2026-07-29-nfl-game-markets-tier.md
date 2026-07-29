# NFL game-markets tier + settlement (#3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add NFL full-game total/spread/moneyline to the low-risk builder end-to-end — ingest the odds, build a sport-aware game-market tier, and settle it against final scores.

**Architecture:** A `game_lines` schema gains `home_odds`/`away_odds` for the two home/away markets (spread, moneyline); NFL final scores — currently discarded at ingest — are captured into `team_game_stats` as a `'points'` actual; `collect_game_rows` generalizes to `betTypeID` sp/ml; the builder's team-leg loader becomes per-sport and geometry-aware (over/under vs home/away), with the binding 0.55 floor doing the market filtering; settlement gains pure total/spread/moneyline scoring functions dispatched by market.

**Tech Stack:** Python 3.11, SQLAlchemy Core (`text()`), pandas, pytest. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-07-29-nfl-game-markets-tier-design.md](../specs/2026-07-29-nfl-game-markets-tier-design.md)

## Global Constraints

- **§15.8 guardrails (binding, not re-litigated):** rank on de-vigged MARKET probability only (never `model_prob`); per-leg `market_prob ≥ 0.55` favorite floor; 2–4 legs; across-game only; paper-only; **no "+EV"/"edge"/"value"/"beat the market" language** in code, labels, or JSONB; no signal-green.
- **NO TEST DB. `ingestion.db.get_engine()` is LIVE.** Every test must be pure (no engine) or use the `_FakeEngine`/queue pattern from `tests/test_parlay_recommendations_api.py` / `tests/test_builder_record_api.py`. A test that calls `get_engine()` or hits a real socket is a defect.
- **Additive-only to Budgerr surfaces (§7.1).** Do NOT modify `api/main.py`'s `TIER_TO_CLASS` or `/parlay-builder/saved` behavior in #3 (that's #4). The new `class='game_tier'` is reachable only via `?tier=all&sport=nfl`; no existing consumer sees NFL rows (COALESCE sport default `mlb`).
- **Worktree note:** `graphify-out/` is gitignored and absent in the worktree, so reading source directly is expected. When you do want the graph, query the main checkout: `graphify query "<q>" --graph /Users/aayushpokhrel/dev/playstat/graphify-out/graph.json`. Copy `.env` from the main checkout if a step needs it (none of the pure tests do). Interpreter: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python` run from the worktree cwd.
- **Preseason-verified unknowns (LOCKED — same discipline as #1's statIDs):** the SGO `betTypeID` strings (`"sp"`/`"ml"`), `sideID` (`"home"`/`"away"`), and the **spread-line field name** (assumed `bookSpread`) are pinned in single named constants and confirmed by a `--dry-run` probe at preseason before the first live ingest. Tests use fixtures built on these assumed strings.
- Run the full suite with `.venv/bin/python -m pytest -q` from the worktree root. Baseline is **299 green**.

---

## Architect prerequisites (reserved lane — NOT for the worktree agent)

These are live-DB / launchd operations the architect performs; the agent's tasks are all pure and do not depend on them being done first.

- **P1 — Apply migration 007.** After Task 1's SQL file merges (or from the file directly), the architect runs it against the live `playstat` DB and confirms `\d game_lines` shows the two new nullable columns and existing rows are unaffected.
- **P2 — Backfill NFL final scores.** After Task 2 merges, the architect runs `python -m ingestion.nfl_backfill` (full) to populate `team_game_stats` `'points'` for the 3 existing NFL seasons, then spot-checks a known final (e.g. a 2023 game's home/away points sum to the real total) and confirms idempotent re-run inserts 0 net new.
- **P3 — Preseason (~August):** `python -m ingestion.odds_ingest --sport nfl --dry-run` to confirm sp/ml betTypeIDs + the spread-line field; pin the constants if they differ; then live ingest + a real `--sport nfl --team-only` build + settle.

---

## Task 1: `game_lines` migration + NFL final-score ingestion

**Files:**
- Create: `db/migrations/007_game_lines_home_away_odds.sql`
- Modify: `ingestion/nfl_backfill.py` (`backfill_games`, ~L202-225; add a pure helper)
- Test: `tests/test_nfl_backfill.py` (create, or extend if it exists)

**Interfaces:**
- Produces: `team_points_rows(row: dict, game_id: int, home_team_id: int, away_team_id: int) -> list[dict]` — pure; returns `[]` for an unplayed game, else two dicts `{"team_id","game_id","stat_type":"points","value":int}` (home then away).

- [ ] **Step 1: Write the migration SQL file** (`db/migrations/007_game_lines_home_away_odds.sql`)

```sql
-- Migration 007: home/away odds on game_lines (NFL spread + moneyline, README §16 / NFL #3).
-- game_lines was over/under-only (line_value, over_odds, under_odds). Spread and
-- moneyline are home/away markets. Additive: existing MLB (first_inning_runs, f5_runs)
-- and the NFL full_game_total rows keep using over/under and read NULL here. No backfill.
BEGIN;
ALTER TABLE game_lines
    ADD COLUMN IF NOT EXISTS home_odds INTEGER,
    ADD COLUMN IF NOT EXISTS away_odds INTEGER;
COMMIT;
```

(Architect applies live — prerequisite P1. Not applied by the agent.)

- [ ] **Step 2: Write the failing test for `team_points_rows`**

```python
# tests/test_nfl_backfill.py
from ingestion.nfl_backfill import team_points_rows

def test_team_points_rows_played_game_yields_home_and_away():
    row = {"home_score": "27", "away_score": "17"}
    rows = team_points_rows(row, game_id=200_2023_01_5, home_team_id=10, away_team_id=20)
    assert rows == [
        {"team_id": 10, "game_id": 200_2023_01_5, "stat_type": "points", "value": 27},
        {"team_id": 20, "game_id": 200_2023_01_5, "stat_type": "points", "value": 17},
    ]

def test_team_points_rows_unplayed_game_yields_nothing():
    assert team_points_rows({"home_score": "", "away_score": ""}, 1, 10, 20) == []
    assert team_points_rows({}, 1, 10, 20) == []
```

- [ ] **Step 3: Run it, verify it fails** — `.venv/bin/python -m pytest tests/test_nfl_backfill.py -v` → FAIL (ImportError / not defined).

- [ ] **Step 4: Implement `team_points_rows`** (add near `backfill_games` in `ingestion/nfl_backfill.py`)

```python
def team_points_rows(row, game_id, home_team_id, away_team_id):
    """Pure: final-score rows for team_game_stats. Empty for an unplayed game.
    NFL final scores are otherwise discarded at ingest (see README §16 / #3);
    stored as a 'points' actual so settlement reads them like MLB runs_inning_1."""
    home, away = row.get("home_score"), row.get("away_score")
    if home in (None, "") or away in (None, ""):
        return []
    return [
        {"team_id": home_team_id, "game_id": game_id, "stat_type": "points", "value": int(home)},
        {"team_id": away_team_id, "game_id": game_id, "stat_type": "points", "value": int(away)},
    ]
```

- [ ] **Step 5: Run it, verify it passes.**

- [ ] **Step 6: Wire the helper into `backfill_games`** — inside the existing `for row in rows:` loop (after the `db.upsert(... "games" ...)` call), upsert the points rows in the same transaction:

```python
            for pr in team_points_rows(row, game_id, team_ids[row["home_team"]], team_ids[row["away_team"]]):
                db.upsert(conn, "team_game_stats", ["team_id", "game_id", "stat_type"], pr)
```

(`db.upsert` on the `(team_id, game_id, stat_type)` PK is idempotent — a re-run overwrites, never duplicates.)

- [ ] **Step 7: Run the full suite** — `.venv/bin/python -m pytest -q` → all green.

- [ ] **Step 8: Commit** — `git add db/migrations/007_game_lines_home_away_odds.sql ingestion/nfl_backfill.py tests/test_nfl_backfill.py && git commit -m "feat(nfl): game_lines home/away odds migration + final-score ingestion (#3)"`

---

## Task 2: NFL spread + moneyline odds ingestion

**Files:**
- Modify: `ingestion/odds_ingest.py` (`GAME_MARKETS`, `collect_game_rows` ~L120-136, `observed_statid_summary` ~L139-153, the `game_lines` INSERT ~L216-230)
- Test: `tests/test_odds.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `bettype_for_market(market: str) -> str` (returns `"sp"`/`"ml"`/`"ou"`); `collect_game_rows(event, game_markets)` now emits rows carrying `home_odds`/`away_odds` for sp/ml markets and `line_value`=home spread for sp, `None` for ml.

- [ ] **Step 1: Update `GAME_MARKETS` and add constants** (`ingestion/odds_ingest.py`)

```python
# Field on an SGO odd carrying the spread line for the home/away side.
# PRESEASON-VERIFY via --dry-run before first live ingest (README #3 spec).
SPREAD_LINE_FIELD = "bookSpread"

GAME_MARKETS = {
    "mlb": {
        "first_inning_runs": ("points", "all", "1i"),
        "f5_runs": ("points", "all", "1ix5"),
    },
    "nfl": {
        "full_game_total":     ("points", "all", "game"),  # betTypeID ou
        "full_game_spread":    ("points", "all", "game"),  # betTypeID sp
        "full_game_moneyline": ("points", "all", "game"),  # betTypeID ml
    },
}

# market name -> SGO betTypeID. Home/away markets use sp/ml; everything else ou.
_MARKET_BETTYPE = {"full_game_spread": "sp", "full_game_moneyline": "ml"}

def bettype_for_market(market):
    return _MARKET_BETTYPE.get(market, "ou")
```

> Renaming `game_total`→`full_game_total`: `grep -rn game_total .` and update **every** hit, including #1's fixture tests in `tests/test_odds.py`. The grep must come back clean afterward.

- [ ] **Step 2: Write failing tests for the generalized collector** (`tests/test_odds.py`)

```python
from ingestion.odds_ingest import collect_game_rows, bettype_for_market, GAME_MARKETS

NFL_MARKETS = GAME_MARKETS["nfl"]

def _odd(stat="points", entity="all", period="game", bt="ou", side="over", price="-110", **extra):
    return {"statID": stat, "statEntityID": entity, "periodID": period,
            "betTypeID": bt, "sideID": side, "bookOdds": price,
            "bookOverUnder": extra.get("ou_line"), "bookSpread": extra.get("spread")}

def test_bettype_for_market():
    assert bettype_for_market("full_game_spread") == "sp"
    assert bettype_for_market("full_game_moneyline") == "ml"
    assert bettype_for_market("full_game_total") == "ou"

def test_collect_game_rows_total_ou_unchanged():
    ev = {"odds": {"a": _odd(bt="ou", side="over", price="-105", ou_line="44.5"),
                   "b": _odd(bt="ou", side="under", price="-115", ou_line="44.5")}}
    rows = {r["market"]: r for r in collect_game_rows(ev, NFL_MARKETS)}
    r = rows["full_game_total"]
    assert r["line_value"] == "44.5" and r["over_odds"] == -105 and r["under_odds"] == -115
    assert r.get("home_odds") is None and r.get("away_odds") is None

def test_collect_game_rows_spread_home_away():
    ev = {"odds": {"a": _odd(bt="sp", side="home", price="-110", spread="-3.5"),
                   "b": _odd(bt="sp", side="away", price="-110", spread="3.5")}}
    r = {x["market"]: x for x in collect_game_rows(ev, NFL_MARKETS)}["full_game_spread"]
    assert r["home_odds"] == -110 and r["away_odds"] == -110
    assert r["line_value"] == "-3.5"   # HOME spread
    assert r.get("over_odds") is None and r.get("under_odds") is None

def test_collect_game_rows_moneyline_no_line():
    ev = {"odds": {"a": _odd(bt="ml", side="home", price="-160"),
                   "b": _odd(bt="ml", side="away", price="+140")}}
    r = {x["market"]: x for x in collect_game_rows(ev, NFL_MARKETS)}["full_game_moneyline"]
    assert r["home_odds"] == -160 and r["away_odds"] == 140 and r.get("line_value") is None

def test_collect_game_rows_skips_unmapped_bettype():
    ev = {"odds": {"a": _odd(bt="xx", side="home", price="-110")}}
    assert collect_game_rows(ev, NFL_MARKETS) == []
```

- [ ] **Step 3: Run, verify they fail.**

- [ ] **Step 4: Rewrite `collect_game_rows`** to dispatch on betTypeID:

```python
def collect_game_rows(event, game_markets):
    """Game-level lines. over/under markets (totals) fill over_odds/under_odds +
    line_value; home/away markets (spread/moneyline) fill home_odds/away_odds,
    spread carries the HOME line, moneyline has none. Unmapped markets/betTypeIDs
    are skipped, never raised (defensive ingest, README #1)."""
    rows = {}
    for odd in event.get("odds", {}).values():
        for market, (stat_id, entity_id, period_id) in game_markets.items():
            if (odd.get("statID"), odd.get("statEntityID"), odd.get("periodID")) != (stat_id, entity_id, period_id):
                continue
            want_bt = bettype_for_market(market)
            if odd.get("betTypeID") != want_bt:
                continue
            side = odd.get("sideID")
            price = parse_american_odds(odd.get("bookOdds"))
            if want_bt == "ou":
                if side not in ("over", "under"):
                    continue
                row = rows.setdefault(market, {"market": market})
                row["line_value"] = odd.get("bookOverUnder")
                row[f"{side}_odds"] = price
            else:  # sp / ml — home/away
                if side not in ("home", "away"):
                    continue
                row = rows.setdefault(market, {"market": market})
                row[f"{side}_odds"] = price
                if want_bt == "sp" and side == "home":
                    row["line_value"] = odd.get(SPREAD_LINE_FIELD)
    return list(rows.values())
```

- [ ] **Step 5: Run the new tests, verify they pass.**

- [ ] **Step 6: Extend the `game_lines` INSERT** (in `ingest_odds`) to carry the new columns:

```python
                conn.execute(
                    text(
                        "INSERT INTO game_lines (game_id, market, line_value, over_odds, under_odds, home_odds, away_odds) "
                        "VALUES (:game_id, :market, :line_value, :over_odds, :under_odds, :home_odds, :away_odds)"
                    ),
                    {
                        "game_id": game_id, "market": row["market"],
                        "line_value": row.get("line_value"),
                        "over_odds": row.get("over_odds"), "under_odds": row.get("under_odds"),
                        "home_odds": row.get("home_odds"), "away_odds": row.get("away_odds"),
                    },
                )
```

- [ ] **Step 7: Extend `observed_statid_summary` to also count betTypeIDs per game market** — add a `bettypes` dict keyed by `(market_statID, betTypeID)` so `--dry-run` reveals sp/ml coverage. Update the print in `ingest_odds`'s dry-run branch to show it.

```python
def observed_statid_summary(events, stat_map, game_markets):
    game_stat_ids = {stat_id for (stat_id, _e, _p) in game_markets.values()}
    mapped, unmapped, bettypes = {}, {}, {}
    for event in events:
        for odd in event.get("odds", {}).values():
            stat_id, bt = odd.get("statID"), odd.get("betTypeID")
            if stat_id in game_stat_ids:
                bettypes[(stat_id, bt)] = bettypes.get((stat_id, bt), 0) + 1
            if bt != "ou":
                continue
            bucket = mapped if (stat_id in stat_map or stat_id in game_stat_ids) else unmapped
            bucket[stat_id] = bucket.get(stat_id, 0) + 1
    return {"mapped": mapped, "unmapped": unmapped, "bettypes": bettypes}
```

Add a matching assertion test (fixtured events → `bettypes` counts sp/ml/ou) and a `print(f"  game-market betTypeIDs: {summary['bettypes']}")` in the dry-run branch.

- [ ] **Step 8: Run the full suite** → green. Confirm `grep -rn "game_total" .` is clean (only `full_game_total` remains).

- [ ] **Step 9: Commit** — `git commit -am "feat(ingestion): NFL spread+moneyline into game_lines home/away odds (#3)"`

---

## Task 3: Builder game-market tier (per-sport, geometry-aware)

**Files:**
- Modify: `optimizer/builder_core.py` (`MARKET_GEOMETRY`, `normalize_team_leg`, `_base_leg`)
- Modify: `optimizer/builder.py` (`TEAM_MARKETS`, `load_team_legs`, `_normalize`, `main` save-class)
- Test: `tests/test_builder_core.py`, `tests/test_builder.py` (extend)

**Interfaces:**
- Produces: `MARKET_GEOMETRY: dict[str, str]` (market → `"ou"`|`"homeaway"`) and `is_home_away_market(market) -> bool` in `builder_core`; `normalize_team_leg(row)` handles both geometries; `TEAM_MARKETS: dict[str, tuple[str, ...]]` in `builder`.

- [ ] **Step 1: Write failing tests for geometry + normalization** (`tests/test_builder_core.py`)

```python
from optimizer.builder_core import (
    MARKET_GEOMETRY, is_home_away_market, normalize_team_leg, passes_floor,
)

def test_market_geometry():
    assert MARKET_GEOMETRY["first_inning_runs"] == "ou"
    assert MARKET_GEOMETRY["full_game_total"] == "ou"
    assert MARKET_GEOMETRY["full_game_spread"] == "homeaway"
    assert MARKET_GEOMETRY["full_game_moneyline"] == "homeaway"
    assert is_home_away_market("full_game_moneyline") and not is_home_away_market("full_game_total")

def test_normalize_team_leg_ou_unchanged():
    # -200/+170 favors OVER; existing behavior
    leg = normalize_team_leg({"game_id": 1, "market": "full_game_total", "line_value": 44.5,
                              "over_odds": -200, "under_odds": 170, "home_odds": None,
                              "away_odds": None, "model_prob": None})
    assert leg["kind"] == "team" and leg["side"] == "over" and leg["market_prob"] > 0.6

def test_normalize_team_leg_moneyline_home_favorite_null_line():
    # home -250 vs away +200 -> home favorite, no line
    leg = normalize_team_leg({"game_id": 5, "market": "full_game_moneyline", "line_value": None,
                              "over_odds": None, "under_odds": None, "home_odds": -250,
                              "away_odds": 200, "model_prob": None})
    assert leg["side"] == "home" and leg["market"] == "full_game_moneyline"
    assert leg["line_value"] is None and leg["american_odds"] == -250
    assert leg["market_prob"] > 0.55 and "moneyline" in leg["label"]

def test_normalize_team_leg_spread_away_favorite_uses_home_line():
    # home +130 / away -150 -> away favorite; line stored is the HOME spread
    leg = normalize_team_leg({"game_id": 7, "market": "full_game_spread", "line_value": 3.5,
                              "over_odds": None, "under_odds": None, "home_odds": 130,
                              "away_odds": -150, "model_prob": None})
    assert leg["side"] == "away" and leg["american_odds"] == -150 and leg["line_value"] == 3.5
```

- [ ] **Step 2: Run, verify they fail.**

- [ ] **Step 3: Add geometry + generalize `normalize_team_leg` / `_base_leg`** (`optimizer/builder_core.py`)

```python
MARKET_GEOMETRY = {
    "first_inning_runs": "ou", "f5_runs": "ou", "full_game_total": "ou",
    "full_game_spread": "homeaway", "full_game_moneyline": "homeaway",
}

def is_home_away_market(market):
    return MARKET_GEOMETRY.get(market) == "homeaway"
```

Change `_base_leg` to tolerate a null line (moneyline):

```python
        "line_value": None if line_value is None else float(line_value),
```

Rewrite `normalize_team_leg`:

```python
def normalize_team_leg(row):
    market = row["market"]
    if is_home_away_market(market):
        raw, prob = favorite_side(row["home_odds"], row["away_odds"])   # "over"->home, "under"->away
        side = "home" if raw == "over" else "away"
        odds = row["home_odds"] if side == "home" else row["away_odds"]
        line = row.get("line_value")
        label = f"{market} {side}" if line is None else f"{market} {side} {line}"
    else:
        side, prob = favorite_side(row["over_odds"], row["under_odds"])
        odds = row["over_odds"] if side == "over" else row["under_odds"]
        line = row["line_value"]
        label = f"{market} {side} {line}"
    leg = _base_leg(row["game_id"], side, prob, line, odds, row.get("model_prob"), label)
    leg.update({"kind": "team", "player_id": None, "stat_type": None, "market": market})
    return leg
```

(`favorite_side` is price-only; feeding it home/away odds returns the home/away favorite. Verified in the spec's finding #3.)

- [ ] **Step 4: Run the builder_core tests, verify they pass.**

- [ ] **Step 5: Write failing tests for the loader + save class** (`tests/test_builder.py`)

```python
import optimizer.builder as B
from optimizer.builder import TEAM_MARKETS, _normalize
from optimizer.builder_core import normalize_team_leg

def test_team_markets_per_sport():
    assert TEAM_MARKETS["mlb"] == ("first_inning_runs", "f5_runs")
    assert TEAM_MARKETS["nfl"] == ("full_game_total", "full_game_spread", "full_game_moneyline")

def test_normalize_keeps_homeaway_and_applies_floor():
    import pandas as pd
    df = pd.DataFrame([
        # moneyline home favorite (~0.71) -> kept
        {"game_id": 1, "market": "full_game_moneyline", "line_value": None,
         "over_odds": None, "under_odds": None, "home_odds": -250, "away_odds": 200, "model_prob": None},
        # coin-flip total (~0.52) -> filtered by the 0.55 floor
        {"game_id": 2, "market": "full_game_total", "line_value": 44.5,
         "over_odds": -105, "under_odds": -105, "home_odds": None, "away_odds": None, "model_prob": None},
    ])
    legs = _normalize(df, normalize_team_leg, floor=0.55)
    assert [l["market"] for l in legs] == ["full_game_moneyline"]
```

- [ ] **Step 6: Run, verify they fail.**

- [ ] **Step 7: Make `TEAM_MARKETS` per-sport + generalize `load_team_legs` and `_normalize`** (`optimizer/builder.py`)

```python
TEAM_MARKETS = {
    "mlb": ("first_inning_runs", "f5_runs"),
    "nfl": ("full_game_total", "full_game_spread", "full_game_moneyline"),
}
```

In `load_team_legs`, select the new columns, use the per-sport market set, and default an unknown sport to no markets:

```python
def load_team_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb"):
    markets = list(TEAM_MARKETS.get(sport, ()))
    if not markets:
        return []
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT gl.game_id, gl.market, gl.line_value,
                       gl.over_odds, gl.under_odds, gl.home_odds, gl.away_odds,
                       ge.model_prob
                FROM (
                    SELECT DISTINCT ON (game_id, market)
                        game_id, market, line_value, over_odds, under_odds, home_odds, away_odds
                    FROM game_lines
                    WHERE market = ANY(:markets)
                    ORDER BY game_id, market, pulled_at DESC
                ) gl
                JOIN games g ON g.game_id = gl.game_id AND g.status != 'FT'
                    AND g.date = COALESCE(:slate_date, CURRENT_DATE)
                    AND g.sport = :sport
                LEFT JOIN game_edges ge ON ge.game_id = gl.game_id AND ge.market = gl.market
                """
            ),
            conn, params={"markets": markets, "slate_date": slate_date, "sport": sport},
        )
    return _normalize(df, normalize_team_leg, floor)
```

Make `_normalize`'s validity filter geometry-aware (over/under rows need over/under/line; home/away rows need both side odds, spread needs a line, moneyline does not):

```python
def _normalize(df, normalizer, floor):
    if df.empty:
        return []
    from optimizer.builder_core import is_home_away_market
    def _valid(r):
        m = r.get("market")
        if m is not None and is_home_away_market(m):
            if r.get("home_odds") is None or r.get("away_odds") is None:
                return False
            if m == "full_game_spread" and r.get("line_value") is None:
                return False
            return True
        # player props + over/under game markets
        return not (r.get("over_odds") is None or r.get("under_odds") is None or r.get("line_value") is None)
    records = [r for r in df.where(pd.notna(df), None).to_dict("records") if _valid(r)]
    legs = [normalizer(r) for r in records]
    return [leg for leg in legs if passes_floor(leg, floor)]
```

> Note: player rows have no `market` key → `_valid` takes the over/under branch (correct). Keep `favorite_side`/devig unchanged.

- [ ] **Step 8: Update the save-class in `main`** so `--team-only --sport nfl` writes `game_tier`:

```python
    if args.save:
        if args.team_only:
            parlay_class = "game_tier" if args.sport == "nfl" else "team_tier"
        else:
            parlay_class = "across_game"
        saved = save_builds(engine, args.target_payout or 0.0, results, parlay_class, args.sport)
```

Add a test asserting the class mapping (pure — call the branch logic, or assert via a small helper if you extract one; simplest: a `_team_class(sport)` pure helper returning `"game_tier"`/`"team_tier"`, unit-tested, used in `main`).

- [ ] **Step 9: Run the full suite** → green.

- [ ] **Step 10: Commit** — `git commit -am "feat(builder): per-sport geometry-aware game-market tier (NFL total/spread/ML) (#3)"`

---

## Task 4: Game-market settlement against final scores

**Files:**
- Modify: `modeling/settle.py` (pure scoring fns; `settle_builder_parlays` ~L396-505 team branch + the fetch block)
- Test: `tests/test_settle_builder.py` (extend), `tests/test_settle.py` (pure fns)

**Interfaces:**
- Consumes: `MARKET_GEOMETRY`, `is_home_away_market` from `optimizer.builder_core`; `settle_leg` (existing).
- Produces: `game_total(home_pts, away_pts) -> float`; `settle_spread_leg(side, home_pts, away_pts, home_line) -> "won"|"lost"|"void"`; `settle_moneyline_leg(side, home_pts, away_pts) -> "won"|"lost"|"void"`.

- [ ] **Step 1: Write failing tests for the pure scoring fns** (`tests/test_settle.py`)

```python
from modeling.settle import game_total, settle_spread_leg, settle_moneyline_leg

def test_game_total():
    assert game_total(27, 17) == 44

def test_settle_spread_home_covers_pushes_loses():
    # home -3.5 (home_line=-3.5), margin 27-17=+10 -> home covers
    assert settle_spread_leg("home", 27, 17, -3.5) == "won"
    assert settle_spread_leg("away", 27, 17, -3.5) == "lost"
    # exact push: home_line=-10, margin 10 -> 0
    assert settle_spread_leg("home", 27, 17, -10) == "void"
    assert settle_spread_leg("away", 27, 17, -10) == "void"
    # away covers: home +3 (home_line=+3), margin -7 -> home -4 -> away wins
    assert settle_spread_leg("away", 20, 27, 3) == "won"

def test_settle_moneyline_winner_and_tie():
    assert settle_moneyline_leg("home", 27, 17) == "won"
    assert settle_moneyline_leg("away", 27, 17) == "lost"
    assert settle_moneyline_leg("away", 17, 27) == "won"
    assert settle_moneyline_leg("home", 20, 20) == "void"   # tie -> push
    assert settle_moneyline_leg("away", 20, 20) == "void"
```

- [ ] **Step 2: Run, verify they fail.**

- [ ] **Step 3: Implement the pure fns** (`modeling/settle.py`, near `settle_leg`)

```python
def game_total(home_pts, away_pts):
    return float(home_pts) + float(away_pts)

def settle_spread_leg(side, home_pts, away_pts, home_line):
    """home_line is the HOME spread; away's is its negation. Push -> void."""
    margin = float(home_pts) - float(away_pts)
    covered = margin + float(home_line) if side == "home" else -(margin + float(home_line))
    if covered == 0:
        return "void"
    return "won" if covered > 0 else "lost"

def settle_moneyline_leg(side, home_pts, away_pts):
    if home_pts == away_pts:
        return "void"   # tie -> push
    winner = "home" if home_pts > away_pts else "away"
    return "won" if side == winner else "lost"
```

- [ ] **Step 4: Run, verify they pass.**

- [ ] **Step 5: Write a failing end-to-end test** — a fixtured NFL game-market builder parlay settling via the fake-engine (mirror the existing NFL player-parlay test in `tests/test_settle_builder.py`). Include a moneyline leg (no line), a spread leg, and a total leg; assert the parlay `result`/`pnl`. Use the `_FakeEngine` queue pattern; queue results for: candidates (one `kind='builder'`, `sport='nfl'`, `class='game_tier'` blob), `games` (status FT + home/away team ids), `team_game_stats` points rows, `game_lines` snapshots (spread line; total line; moneyline null line). Assert the moneyline leg settles with NO line lookup.

```python
# sketch — fill in the fake-engine queue exactly like test_settle_builder.py's NFL player test
def test_nfl_game_market_parlay_settles(monkeypatch):
    legs = [
        {"kind": "team", "game_id": 111, "market": "full_game_moneyline", "side": "home",
         "line": None, "odds": -160, "player_id": None, "stat_type": None},
        {"kind": "team", "game_id": 222, "market": "full_game_total", "side": "over",
         "line": 44.5, "odds": -110, "player_id": None, "stat_type": None},
    ]
    # game 111: home 27 away 17 -> home ML wins; game 222 total 45 > 44.5 -> over wins
    # -> parlay 'won'. Assert inserted outcome result == 'won' and pnl > 0.
    ...
```

- [ ] **Step 6: Run, verify it fails.**

- [ ] **Step 7: Generalize the `settle_builder_parlays` team branch.** Add an NFL game-points fetch and dispatch by market. In the fetch block, add per-team points + home/away ids:

```python
        tpoints = pd.read_sql(
            text("""SELECT game_id, team_id, value FROM team_game_stats
                    WHERE game_id = ANY(:g) AND stat_type = 'points'"""),
            conn, params={"g": game_ids})
        ghome = pd.read_sql(
            text("SELECT game_id, home_team_id, away_team_id FROM games WHERE game_id = ANY(:g)"),
            conn, params={"g": game_ids})
```

Build lookups after the existing ones:

```python
    pts = {(int(r.game_id), int(r.team_id)): float(r.value) for r in tpoints.itertuples()}
    ha = {int(r.game_id): (int(r.home_team_id), int(r.away_team_id)) for r in ghome.itertuples()}

    def _game_scores(gid):
        ht, at = ha.get(gid, (None, None))
        return pts.get((gid, ht)), pts.get((gid, at))
```

In the per-leg loop, replace the single team branch with a market-aware one. For a `kind='team'` leg on a home/away market (`is_home_away_market(market)`) settle from scores; for over/under team markets keep the existing `tstats_lookup` path but extend `stat_to_market`/the IN-list so `full_game_total` resolves to `SUM(points)`:

```python
                else:  # team leg
                    _, _, market = key
                    if is_home_away_market(market):
                        hp, ap = _game_scores(gid)
                        state = leg_status(gstatus, hp if hp is not None else None)
                        if state == "pending":
                            ready = False; break
                        if state == "void":
                            results.append("void"); odds_list.append(american_to_decimal(leg["odds"]))
                            audit.append({"market": market, "kind": "team", "game_id": gid,
                                          "side": leg["side"], "odds": int(leg["odds"]),
                                          "result": "void", "dnp": True})
                            continue
                        if market == "full_game_spread":
                            snaps = glines_grp.get_group((gid, market))  # KeyError -> not ready
                            line = _rec_snapshot(snaps, created_at)["line_value"]
                            if line is None or pd.isna(line): ready = False; break
                            res = settle_spread_leg(leg["side"], hp, ap, float(line))
                        else:  # moneyline — NO line lookup
                            res = settle_moneyline_leg(leg["side"], hp, ap)
                        results.append(res); odds_list.append(american_to_decimal(leg["odds"]))
                        audit.append({"market": market, "kind": "team", "game_id": gid,
                                      "side": leg["side"], "home_pts": hp, "away_pts": ap,
                                      "odds": int(leg["odds"]), "result": res})
                        continue
                    # over/under team market (MLB inning runs + NFL full_game_total) -> existing path below
                    actual = tstats_lookup.get((gid, market))
                    audit_id = {"market": market}
```

Extend the SUM query + map so `full_game_total` works via the existing over/under path:

```python
        tstats = pd.read_sql(
            text("""SELECT game_id, stat_type, SUM(value) AS total
                    FROM team_game_stats
                    WHERE game_id = ANY(:g) AND stat_type IN ('runs_inning_1','runs_f5','points')
                    GROUP BY game_id, stat_type"""),
            conn, params={"g": game_ids})
    ...
    stat_to_market = {"runs_inning_1": "first_inning_runs", "runs_f5": "f5_runs", "points": "full_game_total"}
```

> Import `from optimizer.builder_core import is_home_away_market` at the top of `settle.py`. The existing over/under team branch (`settle_leg(side, actual, line)`) then handles `full_game_total` unchanged — its line comes from the `full_game_total` `game_lines` snapshot.

- [ ] **Step 8: Run the end-to-end test + full suite**, verify green.

- [ ] **Step 9: Commit** — `git commit -am "feat(settle): NFL game-market settlement (total/spread/moneyline vs final scores) (#3)"`

---

## Self-review (completed by plan author)

- **Spec coverage:** A→Task 1 (migration) + P1; B→Task 1 (score ingestion) + P2; C→Task 2; D→Task 3; E→Task 4. Preseason `--dry-run` → P3. All spec sections mapped.
- **Type consistency:** `settle_spread_leg`/`settle_moneyline_leg`/`game_total` signatures identical across Task 4 def + tests; `MARKET_GEOMETRY`/`is_home_away_market` defined Task 3, consumed Tasks 3 & 4; `TEAM_MARKETS` dict shape consistent; `bettype_for_market` def/use consistent.
- **No live-DB from agent:** all tests pure or fake-engine; migration apply + score backfill + odds ingest are architect prerequisites P1–P3.
- **Budgerr-safe:** no `api/main.py` change; `class='game_tier'` additive; no kickstart needed for #3.

## Execution handoff

Architect executes via delegation: dispatch a fresh worktree subagent per task (Tasks 1–4), review the actual diff between tasks, then perform P1–P2 live and merge. P3 waits for preseason.
