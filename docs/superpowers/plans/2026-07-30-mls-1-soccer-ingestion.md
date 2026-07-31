# MLS #1 — Soccer Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution note (this project):** bulk mechanical edits are delegated to cheap-model
> workers (`~/.claude/bin/delegate deepseek`, or `free` if OmniRoute is up) that read/edit files
> but **cannot run shell, tests, or git**. The architect runs graphify, pytest, the live
> backfill, and all commits. "Run test / commit" steps are the architect's.
>
> **graphify before reading source** (`graphify query "<q>"`) — repo rule. In the main checkout
> the graph exists; prefer it, else read the exact files named here.

**Goal:** Land MLS 2022–2024 games / players / player-stats / final-scores into the existing
multi-sport schema via a new `ingestion/soccer_backfill.py` (API-Sports **football**), so the
MLS builder + settlement (#2) have data to work against.

**Architecture:** Reuse the generic `APISportsClient` (it already parameterizes host per sport
via `SPORTS[sport]["base_url"]` + shared `x-apisports-key`). A new `soccer_backfill.py` handles
the football-specific ENDPOINTS (`/fixtures`, `/fixtures/players`) and RESPONSE SHAPE (nested
`statistics[0]`, `goals.home/away`), deriving teams+players from the fixture payloads to
minimise API calls. Mirrors `backfill.py` / `nfl_backfill.py` patterns. No schema change.

**Tech Stack:** Python 3.11 (`/Users/aayushpokhrel/dev/playstat/.venv`), SQLAlchemy + Postgres
(LIVE — no test DB), pytest, API-Sports football (`https://v3.football.api-sports.io`).

## Global Constraints

- **No live DB in tests.** `ingestion.db.get_engine()` is LIVE. New tests are pure or use the
  fake-engine isolation pattern (`tests/test_builder_record_api.py`). A test hitting the live DB
  is a defect. Pure extraction/final-status/score-row helpers need no DB at all.
- **API budget: 100 req/day (football free tier, separate from basketball).** `/fixtures?league=
  253&season=Y` returns a whole season in ONE call (fixtures + `goals.home/away` scores) — cheap,
  gets ALL match-total settlement data. `/fixtures/players?fixture=ID` is ONE call PER fixture
  (~500/season) — expensive; make it **resumable** (skip already-loaded fixtures) so it accretes
  across days, and it is NOT required in full for structural verification (a few dozen fixtures'
  player stats prove the player-prop path).
- **Free tier = seasons 2022–2024 only** (2025/26 → "Free plans do not have access to this
  season"). Historical build only; live is paid (spec §paid-gate).
- **Additive-only, MLB/NBA/NFL byte-unchanged.** New `sport='mls'` key + new module; touch no
  existing sport's code path.
- Every landed change updates README §11/§13/§16 in the same commit and pushes.

## File Structure
- `ingestion/config.py` — MODIFY: add `SPORTS['mls']`. (Task 1)
- `ingestion/soccer_backfill.py` — CREATE: football fixtures/players ingestion + CLI. (Tasks 1–3)
- `tests/test_soccer_ingest.py` — CREATE: pure helper tests. (Tasks 1–3)

**Schema note (verified — no migration):** `games`(game_id, sport, date, home_team_id,
away_team_id, status), `teams`(team_id, sport, name), `players`(player_id, sport, name, team_id,
position), `team_game_stats`(team_id, game_id, stat_type, value), `player_game_stats`(player_id,
game_id, stat_type, value) all already exist and are what `backfill.py` upserts into.
`db.upsert(conn, table, conflict_cols, row_dict)` is the upsert helper.

---

### Task 1: config + pure soccer helpers

**Files:**
- Modify: `ingestion/config.py` (`SPORTS` dict, after the `nfl` entry)
- Create: `ingestion/soccer_backfill.py` (pure helpers only, this task)
- Test: `tests/test_soccer_ingest.py` (create)

**Interfaces (produced):**
- `SPORTS['mls']` = `{base_url, league_id:253, odds_league_id:"MLS", id_offset:300_000_000}`
- `is_soccer_final(status) -> bool` (final set `{"FT","AET","PEN"}`)
- `soccer_team_points_rows(fixture, game_id, home_team_id, away_team_id) -> list[dict]`
  (final goals per team as `team_game_stats` 'points' rows; empty if a goal count is None)
- `extract_soccer_player_stats(stat_block) -> dict` (`{stat_type: value}` from a
  `statistics[0]` dict; keys `shots`/`shots_on_goal`/`tackles`; None values dropped)

- [ ] **Step 1: Add `SPORTS['mls']`** to `ingestion/config.py`, after the `nfl` block:

```python
    # MLS (soccer) — API-Sports FOOTBALL API (different host/endpoints than
    # basketball; same key). League 253. Free tier: seasons 2022-2024 only
    # (current season is paid). Ingested by ingestion/soccer_backfill.py.
    "mls": {
        "base_url": "https://v3.football.api-sports.io",
        "league_id": 253,
        "odds_league_id": "MLS",
        "id_offset": 300_000_000,
    },
```

- [ ] **Step 2: Write the failing tests** — `tests/test_soccer_ingest.py`:

```python
from ingestion.soccer_backfill import (
    is_soccer_final, soccer_team_points_rows, extract_soccer_player_stats,
)


def test_is_soccer_final():
    assert is_soccer_final("FT") is True
    assert is_soccer_final("AET") is True   # after extra time
    assert is_soccer_final("PEN") is True   # penalty shootout
    assert is_soccer_final("NS") is False   # not started
    assert is_soccer_final("1H") is False   # in play
    assert is_soccer_final(None) is False


def test_soccer_team_points_rows_scored():
    fixture = {"goals": {"home": 2, "away": 1}}
    rows = soccer_team_points_rows(fixture, game_id=900, home_team_id=50, away_team_id=60)
    assert rows == [
        {"team_id": 50, "game_id": 900, "stat_type": "points", "value": 2},
        {"team_id": 60, "game_id": 900, "stat_type": "points", "value": 1},
    ]


def test_soccer_team_points_rows_missing_returns_empty():
    assert soccer_team_points_rows({"goals": {"home": None, "away": 1}}, 1, 1, 2) == []
    assert soccer_team_points_rows({}, 1, 1, 2) == []


def test_extract_soccer_player_stats():
    block = {
        "games": {"minutes": 90},
        "shots": {"total": 3, "on": 1},
        "tackles": {"total": 4, "blocks": 0, "interceptions": 2},
        "passes": {"total": 55, "key": 2, "accuracy": "88"},
    }
    assert extract_soccer_player_stats(block) == {
        "shots": 3, "shots_on_goal": 1, "tackles": 4,
    }


def test_extract_soccer_player_stats_drops_none():
    # a goalkeeper: shots/tackles null
    block = {"shots": {"total": None, "on": None}, "tackles": {"total": None}}
    assert extract_soccer_player_stats(block) == {}
```

- [ ] **Step 3 (architect): run — expect FAIL** (ImportError):
  `/Users/aayushpokhrel/dev/playstat/.venv/bin/pytest tests/test_soccer_ingest.py -q`

- [ ] **Step 4: Implement the pure helpers** in `ingestion/soccer_backfill.py`:

```python
"""Ingest MLS (soccer) data from API-Sports FOOTBALL (v3.football.api-sports.io)
into the shared multi-sport schema. Different endpoints/shape than the basketball
backfill (ingestion/backfill.py): /fixtures + /fixtures/players, nested statistics,
goals.home/away for scores. Free tier = seasons 2022-2024 only (current is paid).
Reuses the generic APISportsClient (host comes from SPORTS['mls']['base_url'])."""
import argparse

from ingestion import db
from ingestion.api_client import APISportsClient, QuotaExhaustedError
from ingestion.config import SPORTS

SPORT = "mls"
SEASONS = [2022, 2023, 2024]  # free-tier accessible seasons

# API-Sports football final statuses: FT (regulation), AET (after extra time),
# PEN (penalty shootout). All are final and must settle.
SOCCER_FINAL_STATUSES = {"FT", "AET", "PEN"}


def is_soccer_final(status):
    return status in SOCCER_FINAL_STATUSES


def soccer_team_points_rows(fixture, game_id, home_team_id, away_team_id):
    """Final goals per team as team_game_stats 'points' rows (match-total
    settlement reads home+away like MLB runs / NFL/NBA points). Empty if a goal
    count is missing/None."""
    goals = fixture.get("goals") or {}
    home, away = goals.get("home"), goals.get("away")
    if home is None or away is None:
        return []
    return [
        {"team_id": home_team_id, "game_id": game_id, "stat_type": "points", "value": int(home)},
        {"team_id": away_team_id, "game_id": game_id, "stat_type": "points", "value": int(away)},
    ]


def extract_soccer_player_stats(stat_block):
    """{stat_type: value} from a /fixtures/players statistics[0] dict. Keys match
    STAT_MAPS['mls'] values so SGO props settle. None values dropped."""
    shots = stat_block.get("shots") or {}
    tackles = stat_block.get("tackles") or {}
    out = {
        "shots": shots.get("total"),
        "shots_on_goal": shots.get("on"),
        "tackles": tackles.get("total"),
    }
    return {k: v for k, v in out.items() if v is not None}
```

- [ ] **Step 5 (architect): run — expect PASS.**
- [ ] **Step 6 (architect): commit** `feat(ingestion): MLS config + pure soccer helpers (§16)`.

---

### Task 2: fixtures + player-stats backfill + CLI

**Files:**
- Modify: `ingestion/soccer_backfill.py` (add DB functions + `main()`)
- Test: `tests/test_soccer_ingest.py` (append — fake-engine, no live API/DB)

**Interfaces (produced):**
- `backfill_fixtures(client, engine, season) -> list[dict]` — upserts teams (from fixture
  home/away), games, and `team_game_stats('points')` for finals; returns the finished fixtures.
- `backfill_player_stats(client, engine, finished_fixtures) -> int` — per fixture, `/fixtures/
  players` → upsert players + `player_game_stats`; skips fixtures already loaded (resumable);
  returns count loaded this run.
- `main()` — CLI `--season {2022,2023,2024,all}` (default all), `--only {fixtures,stats,all}`.

**Consumes:** `is_soccer_final`, `soccer_team_points_rows`, `extract_soccer_player_stats`,
`db.upsert`, `db.game_ids_with_stats` (exists — used by backfill.py for resumability;
graphify-confirm its signature before use).

- [ ] **Step 1: Append fake-engine tests** to `tests/test_soccer_ingest.py`. These fake the
  client + engine so NO live API/DB is touched (mirror `backfill.py`'s call shape):

```python
class _FakeConn:
    def __init__(self): self.upserts = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): return []

class _FakeEngine:
    def __init__(self): self.conn = _FakeConn()
    def begin(self): return self.conn

class _FakeClient:
    """Returns queued /fixtures then /fixtures/players payloads."""
    def __init__(self, fixtures, players): self._fx, self._pl = fixtures, players
    def get(self, path, params=None):
        return self._fx if path == "/fixtures" else self._pl


def test_backfill_fixtures_upserts_games_teams_scores(monkeypatch):
    import ingestion.soccer_backfill as sb
    calls = []
    monkeypatch.setattr(sb.db, "upsert", lambda conn, t, c, r: calls.append((t, r)))
    fixtures = [{
        "fixture": {"id": 7, "date": "2024-07-04T00:00:00+00:00", "status": {"short": "FT"}},
        "teams": {"home": {"id": 11, "name": "Columbus Crew"},
                  "away": {"id": 22, "name": "Nashville SC"}},
        "goals": {"home": 2, "away": 0},
    }]
    client = _FakeClient(fixtures, [])
    finished = sb.backfill_fixtures(client, _FakeEngine(), 2024)
    off = sb.SPORTS["mls"]["id_offset"]
    tables = [t for t, _ in calls]
    assert tables.count("teams") == 2
    assert tables.count("games") == 1
    assert tables.count("team_game_stats") == 2  # final -> two score rows
    game_row = next(r for t, r in calls if t == "games")
    assert game_row["game_id"] == 7 + off and game_row["sport"] == "mls"
    assert len(finished) == 1


def test_backfill_fixtures_skips_scores_for_unfinished(monkeypatch):
    import ingestion.soccer_backfill as sb
    calls = []
    monkeypatch.setattr(sb.db, "upsert", lambda conn, t, c, r: calls.append((t, r)))
    fixtures = [{
        "fixture": {"id": 8, "date": "2024-08-01T00:00:00+00:00", "status": {"short": "NS"}},
        "teams": {"home": {"id": 11, "name": "A"}, "away": {"id": 22, "name": "B"}},
        "goals": {"home": None, "away": None},
    }]
    finished = sb.backfill_fixtures(_FakeClient(fixtures, []), _FakeEngine(), 2024)
    assert [t for t, _ in calls].count("team_game_stats") == 0
    assert finished == []
```

- [ ] **Step 2 (architect): run — expect FAIL** (`backfill_fixtures` undefined).

- [ ] **Step 3: Implement** the DB functions + CLI in `ingestion/soccer_backfill.py`:

```python
def backfill_teams_and_games(conn, fixtures, offset):
    """Upsert teams (from fixture home/away) + games; write scores for finals."""
    for fx in fixtures:
        f = fx["fixture"]; teams = fx["teams"]
        for side in ("home", "away"):
            t = teams[side]
            db.upsert(conn, "teams", ["team_id"],
                      {"team_id": t["id"] + offset, "sport": SPORT, "name": t["name"]})
        game_id = f["id"] + offset
        status = (f.get("status") or {}).get("short")
        db.upsert(conn, "games", ["game_id"], {
            "game_id": game_id, "sport": SPORT, "date": f["date"][:10],
            "home_team_id": teams["home"]["id"] + offset,
            "away_team_id": teams["away"]["id"] + offset,
            "status": status,
        })
        if is_soccer_final(status):
            for pr in soccer_team_points_rows(
                fx, game_id, teams["home"]["id"] + offset, teams["away"]["id"] + offset
            ):
                db.upsert(conn, "team_game_stats", ["team_id", "game_id", "stat_type"], pr)


def backfill_fixtures(client, engine, season):
    offset = SPORTS[SPORT]["id_offset"]
    fixtures = client.get("/fixtures", params={"league": SPORTS[SPORT]["league_id"], "season": season})
    with engine.begin() as conn:
        backfill_teams_and_games(conn, fixtures, offset)
    finished = [fx for fx in fixtures if is_soccer_final((fx["fixture"].get("status") or {}).get("short"))]
    print(f"fixtures {season}: upserted {len(fixtures)} ({len(finished)} finished)")
    return finished


def backfill_player_stats(client, engine, finished_fixtures):
    offset = SPORTS[SPORT]["id_offset"]
    with engine.begin() as conn:
        already = db.game_ids_with_stats(conn)
    remaining = [fx for fx in finished_fixtures if fx["fixture"]["id"] + offset not in already]
    print(f"player_stats: {len(already)} games already loaded, {len(remaining)} remaining")
    loaded = 0
    for fx in remaining:
        game_id = fx["fixture"]["id"] + offset
        teams = client.get("/fixtures/players", params={"fixture": fx["fixture"]["id"]})
        with engine.begin() as conn:
            for team_block in teams:
                team_id = team_block["team"]["id"] + offset
                for p in team_block.get("players", []):
                    pid = p["player"]["id"] + offset
                    db.upsert(conn, "players", ["player_id"], {
                        "player_id": pid, "sport": SPORT, "name": p["player"]["name"],
                        "team_id": team_id, "position": None,
                    })
                    stats = (p.get("statistics") or [{}])[0]
                    for stat_type, value in extract_soccer_player_stats(stats).items():
                        db.upsert(conn, "player_game_stats",
                                  ["player_id", "game_id", "stat_type"],
                                  {"player_id": pid, "game_id": game_id,
                                   "stat_type": stat_type, "value": value})
        loaded += 1
    print(f"player_stats: loaded {loaded} games this run")
    return loaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default="all")
    parser.add_argument("--only", choices=["fixtures", "stats", "all"], default="all")
    args = parser.parse_args()
    seasons = SEASONS if args.season == "all" else [int(args.season)]
    client = APISportsClient(SPORT)
    engine = db.get_engine()
    try:
        for season in seasons:
            finished = backfill_fixtures(client, engine, season)
            if args.only in ("stats", "all"):
                backfill_player_stats(client, engine, finished)
    except QuotaExhaustedError as e:
        print(f"Stopping: {e}\nRe-run later to resume — loaded games are skipped.")


if __name__ == "__main__":
    main()
```

  **graphify-confirm `db.game_ids_with_stats(conn)` exists + returns a container of game_ids**
  (`backfill.py:125` uses it). If its shape differs, adjust the resumability check in review.

- [ ] **Step 4 (architect): run — expect PASS.**
- [ ] **Step 5 (architect): commit** `feat(ingestion): MLS soccer_backfill fixtures+stats+CLI (§16)`.

---

### Task 3: live backfill + verification (ARCHITECT ONLY — not delegated)

**Files:** none (live DB writes + live API; architect reserved lane).

- [ ] **Step 1: Backfill fixtures + scores for all 3 seasons (cheap, ~3 calls):**
  `python -m ingestion.soccer_backfill --only fixtures` → expect ~1500 fixtures across 2022–24,
  each season's finished count printed. This populates `games` + `team_game_stats('points')` for
  ALL match-total settlement.
- [ ] **Step 2: Verify fixtures/scores:** `games` has ~1500 `sport='mls'` rows;
  `team_game_stats` has ~2×(finished) `points` rows; spot-check one fixture's goals vs a known
  result; AET/PEN games carry scores; no FK orphans (team_ids in `teams`); idempotent re-run.
- [ ] **Step 3: Backfill player stats incrementally (resumable, quota-bounded):**
  `python -m ingestion.soccer_backfill --season 2024 --only stats` — runs until the 100/day quota
  exits cleanly; re-run across days to accrete. For structural verification a few dozen fixtures
  suffice; full multi-season stats are optional/background.
- [ ] **Step 4: Verify player stats:** `player_game_stats` has `sport='mls'` rows with
  stat_types `shots`/`shots_on_goal`/`tackles`; a spot-checked fixture's player shots match the
  API; values are ints; players upserted with `sport='mls'`.
- [ ] **Step 5: Confirm isolation:** MLB/NBA/NFL row counts in `games`/`player_game_stats`
  unchanged (the backfill only wrote `sport='mls'`); no existing test regressed.

---

## Wrap-up (architect)
- [ ] Full suite: `/Users/aayushpokhrel/dev/playstat/.venv/bin/pytest -q` → prior + new soccer tests green.
- [ ] `graphify update .` (AST-only, free).
- [ ] README §16.4: MLS #1 (ingestion) BUILT — data loaded, counts recorded; note #2 (builder
  wiring) is next. Same commit, push.
- [ ] No API kickstart (soccer_backfill is a CLI the API does not import).
- [ ] Hand off to MLS #2 (builder wiring) — its own spec §MLS #2 → plan.

## Self-Review
- **Spec coverage (§MLS #1):** config `SPORTS['mls']` ✓ (T1); `soccer_backfill.py` fixtures/
  players/scores ✓ (T1 helpers, T2 DB fns); `is_soccer_final` incl AET/PEN ✓ (T1); score rows
  ✓ (T1/T2); player-stat extractor matching `STAT_MAPS['mls']` values (shots/shots_on_goal/
  tackles) ✓ (T1); resumable player backfill ✓ (T2); 2022–24 live backfill + verify ✓ (T3);
  isolation ✓ (T3.5); pure/fake-engine tests only ✓.
- **Placeholder scan:** none — all code shown; the two graphify-confirm items (`db.game_ids_
  with_stats` shape) are explicit architect checks, not code placeholders.
- **Type consistency:** `is_soccer_final`/`soccer_team_points_rows`/`extract_soccer_player_stats`/
  `backfill_fixtures`/`backfill_player_stats`/`SEASONS`/`SPORT` identical across tasks + tests.
  stat_type names (`shots`,`shots_on_goal`,`tackles`) are the values #2's `STAT_MAPS['mls']` maps
  SGO statIDs onto — locked here so odds and actuals line up.
