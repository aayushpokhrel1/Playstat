# Late-Afternoon Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 17:30 ET confirmed-lineup builder card and 17:30/19:45 ET odds snapshots that make closing-line movement measurable, while *reducing* total SGO quota consumption.

**Architecture:** Three additive layers. (1) Date-narrow the SGO event pull so all three daily pulls together cost less than today's single pull. (2) A new statsapi lineup fetcher feeding a `--require-confirmed-lineup` builder mode that saves a new `confirmed_lineup` class. (3) A pure line-movement module comparing each saved leg's build price against its last pre-start snapshot, surfaced via a new dashboard-only endpoint. Every library default is OFF so existing behaviour is byte-identical; the chain and new scripts opt in.

**Tech Stack:** Python 3.11 (`/Users/aayushpokhrel/dev/playstat/.venv/bin/python`), SQLAlchemy Core + `text()`, pandas, pytest, FastAPI + Pydantic, Next.js 16 (`web/`), bash + launchd.

**Spec:** [`docs/superpowers/specs/2026-08-08-late-afternoon-job-design.md`](../specs/2026-08-08-late-afternoon-job-design.md)

## Global Constraints

- **§15.8 guardrails, binding:** rank only on devig `market_prob`; `market_prob >= 0.55`; 2–4 legs; favourite side only; across-game only (except the existing `same_game_pair` class); paper-only. **No "+EV" / "edge" / "value" / "beat the market" language** in UI, API payloads, or recommendation JSONB. This binds hardest on the line-movement surface.
- **Additive-only + mlb-default everywhere.** `/parlay-builder/saved`, `/box-scores`, `/games` response shapes are the Budgerr contract (§7.1) and must stay **byte-identical**.
- **Every new library parameter defaults to OFF/None**, so behaviour without it is byte-identical. Precedent: `min_start_rate=0.0` in `optimizer/builder.py`.
- **No test database.** `ingestion.db.get_engine()` is **LIVE**. Every new test is pure or uses an existing fake: `tests/test_builder.py:_CapturingEngine`, `tests/test_parlay_builder_api.py:_fake_engine`, `tests/test_ingestion_retry.py` (HTTP mocking). **Never** let a test call `db.get_engine()`.
- **Timezone:** always `zoneinfo.ZoneInfo("America/New_York")`. **Never** a hardcoded `-04:00`.
- **Repo rule:** run `graphify query "<question>"` before reading/grepping source. **In a worktree `graphify-out/` does not exist (gitignored) — read source directly there; do not burn turns failing this rule.**
- **Baseline:** 374 pytest green before this plan starts.
- **Reserved for the architect, never done by a worker:** `git push`, launchd job creation/loading, live DB writes, browser verification, and `launchctl kickstart -k gui/$(id -u)/com.playstat.api`.
- Run all Python from the repo root as `.venv/bin/python -m <module>`.

---

### Task 1: Date-narrowed SGO event pull

**Files:**
- Modify: `ingestion/odds_client.py:64-80` (`get_events`)
- Create: `ingestion/slate_window.py`
- Modify: `ingestion/odds_ingest.py:277-290` (`ingest_odds` signature + call), `ingestion/odds_ingest.py:388-394` (`main`)
- Test: `tests/test_slate_window.py`, `tests/test_odds_window.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ingestion.slate_window.slate_window(now, sport="mlb", not_before_now=False) -> tuple[str, str]` returning `(starts_after_iso, starts_before_iso)`, both UTC ISO-8601 ending in `Z`.
  - `SportsGameOddsClient.get_events(league_id, odds_available=True, starts_after=None, starts_before=None, limit=None)`
  - `ingest_odds(sport="nba", dry_run=False, starts_after=None, starts_before=None)`

- [ ] **Step 1: Write the failing tests for `slate_window`**

Create `tests/test_slate_window.py`:

```python
from datetime import datetime, timezone

from ingestion.slate_window import slate_window


def _dt(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_mlb_window_is_one_et_day_in_summer():
    # 2026-08-08 14:00Z == 10:00 ET (EDT, -04:00).
    after, before = slate_window(_dt(2026, 8, 8, 14), "mlb")
    assert after == "2026-08-08T10:00:00Z"   # 06:00 EDT
    assert before == "2026-08-09T10:00:00Z"


def test_mlb_window_shifts_with_est_in_winter():
    # 2026-11-15 15:00Z == 10:00 ET (EST, -05:00). The window must move an hour,
    # which a hardcoded -04:00 offset would get wrong.
    after, before = slate_window(_dt(2026, 11, 15, 15), "mlb")
    assert after == "2026-11-15T11:00:00Z"   # 06:00 EST
    assert before == "2026-11-16T11:00:00Z"


def test_before_06_et_still_belongs_to_the_previous_slate():
    # 2026-08-08 08:00Z == 04:00 ET, i.e. still the 08-07 slate's late games.
    after, before = slate_window(_dt(2026, 8, 8, 8), "mlb")
    assert after == "2026-08-07T10:00:00Z"
    assert before == "2026-08-08T10:00:00Z"


def test_nfl_window_spans_its_weekly_slate():
    # SLATE_WINDOW_DAYS['nfl'] == 4 -> Thu..Mon, so the window is 5 ET days.
    after, before = slate_window(_dt(2026, 8, 8, 14), "nfl")
    assert after == "2026-08-08T10:00:00Z"
    assert before == "2026-08-13T10:00:00Z"


def test_not_before_now_clamps_the_lower_bound():
    # At 21:30Z (17:30 ET) the lower bound becomes now, excluding started games.
    after, before = slate_window(_dt(2026, 8, 8, 21, 30), "mlb", not_before_now=True)
    assert after == "2026-08-08T21:30:00Z"
    assert before == "2026-08-09T10:00:00Z"


def test_not_before_now_never_moves_the_bound_backwards():
    # At 08:00Z the slate start (previous day 10:00Z) is already in the past;
    # clamping must take the LATER of the two, i.e. now.
    after, _ = slate_window(_dt(2026, 8, 8, 8), "mlb", not_before_now=True)
    assert after == "2026-08-08T08:00:00Z"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_slate_window.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingestion.slate_window'`

- [ ] **Step 3: Implement `ingestion/slate_window.py`**

```python
"""ET slate window -> UTC ISO bounds for the SGO /events pull.

README §15.9 item 11 Option B. The SGO free tier meters ENTITIES (2,500/month,
1 returned event = 1 entity), and an unfiltered MLB pull returns ~51 events —
~3-4 days of schedule — of which the builder uses only today's slate
(load_player_legs filters `g.date` to the slate). Narrowing the pull to the
slate window cuts it to ~15 and is what makes a second and third daily pull
affordable (measured: 3 narrowed pulls ~27 entities/day vs 51 for today's
single unfiltered pull).

The 06:00 ET boundary is deliberate: no MLB game starts near it (earliest
observed ~12:00 ET), and it is clear of both the UTC date rollover and the
02:00 ET DST transition instant, so a window never splits a slate and never
straddles a clock change.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Hour (ET) at which one slate ends and the next begins.
SLATE_BOUNDARY_HOUR = 6


def _to_utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slate_window(now, sport="mlb", not_before_now=False):
    """(starts_after, starts_before) as UTC ISO-8601 strings for `sport`'s slate.

    `now` must be timezone-aware. The window covers the ET slate date containing
    `now` through + SLATE_WINDOW_DAYS[sport] further ET days (NFL bets a Thu..Mon
    card, window_days=4; every other sport is 0 = a single day).

    not_before_now=True raises the lower bound to `now`, so an already-started
    game is neither fetched nor billed — used by the 17:30 and 19:45 pulls. It
    also keeps in-play prices out of a snapshot meant to represent a pre-game
    line. max() is used rather than assignment so the bound never moves backwards.
    """
    # Imported here: optimizer.builder imports pandas, and ingestion callers
    # should not pay that cost at module import.
    from optimizer.builder import SLATE_WINDOW_DAYS

    et_now = now.astimezone(ET)
    slate_date = et_now.date()
    if et_now.hour < SLATE_BOUNDARY_HOUR:
        slate_date -= timedelta(days=1)

    boundary = time(SLATE_BOUNDARY_HOUR, 0)
    start = datetime.combine(slate_date, boundary, tzinfo=ET)
    end = datetime.combine(
        slate_date + timedelta(days=SLATE_WINDOW_DAYS.get(sport, 0) + 1),
        boundary, tzinfo=ET,
    )

    if not_before_now:
        start = max(start, now)

    return _to_utc_iso(start), _to_utc_iso(end)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_slate_window.py -v`
Expected: 6 passed

- [ ] **Step 5: Write the failing test for the client + ingest wiring**

Create `tests/test_odds_window.py`:

```python
from ingestion.odds_client import SportsGameOddsClient


class _RecordingClient(SportsGameOddsClient):
    """Captures the params of each .get() instead of hitting the network."""

    def __init__(self, pages):
        self.calls = []
        self._pages = list(pages)

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        return self._pages.pop(0)


def test_get_events_omits_window_params_by_default():
    client = _RecordingClient([{"data": [{"eventID": "a"}]}])
    assert len(list(client.get_events("MLB"))) == 1
    _, params = client.calls[0]
    assert "startsAfter" not in params
    assert "startsBefore" not in params
    assert "limit" not in params


def test_get_events_passes_window_and_limit_through():
    client = _RecordingClient([{"data": []}])
    list(client.get_events(
        "MLB",
        starts_after="2026-08-08T10:00:00Z",
        starts_before="2026-08-09T10:00:00Z",
        limit=100,
    ))
    _, params = client.calls[0]
    assert params["startsAfter"] == "2026-08-08T10:00:00Z"
    assert params["startsBefore"] == "2026-08-09T10:00:00Z"
    assert params["limit"] == 100


def test_get_events_keeps_window_params_on_every_page():
    client = _RecordingClient([
        {"data": [{"eventID": "a"}], "nextCursor": "c1"},
        {"data": [{"eventID": "b"}]},
    ])
    events = list(client.get_events("MLB", starts_after="2026-08-08T10:00:00Z", limit=100))
    assert [e["eventID"] for e in events] == ["a", "b"]
    assert len(client.calls) == 2
    # A dropped filter on page 2 would silently re-bill the full unfiltered slate.
    assert client.calls[1][1]["startsAfter"] == "2026-08-08T10:00:00Z"
    assert client.calls[1][1]["limit"] == 100
    assert client.calls[1][1]["cursor"] == "c1"
```

- [ ] **Step 6: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_odds_window.py -v`
Expected: FAIL — `TypeError: get_events() got an unexpected keyword argument 'starts_after'`

- [ ] **Step 7: Implement the client change**

In `ingestion/odds_client.py`, replace `get_events` (currently lines 64-80) with:

```python
    def get_events(self, league_id, odds_available=True, starts_after=None,
                   starts_before=None, limit=None):
        """Yields events across all pages for a league.

        starts_after/starts_before (UTC ISO-8601) narrow the pull to one slate
        window. This is a QUOTA control, not a convenience: the free tier meters
        entities (1 returned event = 1 entity, 2,500/month) and an unfiltered MLB
        pull returns ~51 events, ~70% of them future-dated games the builder
        discards. See ingestion/slate_window.py and README §15.9 item 11.

        limit sets the page size; limit=100 makes a narrowed slate fit in ONE
        request instead of paging at 6.5s/request (the 2026-08-08 odds step took
        885s largely for this reason).

        Defaults are all None/unset, so the unfiltered call is byte-identical.
        """
        cursor = None
        while True:
            params = {"leagueID": league_id}
            if odds_available:
                params["oddsAvailable"] = "true"
            if starts_after:
                params["startsAfter"] = starts_after
            if starts_before:
                params["startsBefore"] = starts_before
            if limit:
                params["limit"] = limit
            if cursor:
                params["cursor"] = cursor

            payload = self.get("/events/", params=params)
            for event in payload.get("data", []):
                yield event

            cursor = payload.get("nextCursor")
            if not cursor:
                break
```

- [ ] **Step 8: Implement the `odds_ingest` wiring**

In `ingestion/odds_ingest.py`, change the `ingest_odds` signature (line 277) to:

```python
def ingest_odds(sport="nba", dry_run=False, starts_after=None, starts_before=None):
```

and the `get_events` call (line 290) to:

```python
    # limit=100 only when a window is set: a narrowed slate then fits in ONE
    # request. Unfiltered calls keep the old paging so behaviour is unchanged.
    events = list(client.get_events(
        odds_league_id, odds_available=True,
        starts_after=starts_after, starts_before=starts_before,
        limit=100 if (starts_after or starts_before) else None,
    ))
```

In `main()` (line 389), add before `args = parser.parse_args()`:

```python
    parser.add_argument("--starts-after", default=None,
                        help="UTC ISO-8601 lower bound on event start (quota control, "
                             "README §15.9 item 11); default None = unfiltered")
    parser.add_argument("--starts-before", default=None,
                        help="UTC ISO-8601 upper bound on event start")
    parser.add_argument("--slate-window", action="store_true",
                        help="derive --starts-after/--starts-before from the sport's "
                             "ET slate window (ingestion/slate_window.py)")
    parser.add_argument("--not-before-now", action="store_true",
                        help="with --slate-window, exclude already-started games")
```

and replace the final `ingest_odds(...)` call with:

```python
    starts_after, starts_before = args.starts_after, args.starts_before
    if args.slate_window:
        from datetime import datetime, timezone

        from ingestion.slate_window import slate_window
        starts_after, starts_before = slate_window(
            datetime.now(timezone.utc), args.sport, args.not_before_now
        )
        print(f"({args.sport}) slate window: {starts_after} .. {starts_before}")
    ingest_odds(args.sport, dry_run=args.dry_run,
                starts_after=starts_after, starts_before=starts_before)
```

- [ ] **Step 9: Run the new + full test suites**

Run: `.venv/bin/python -m pytest tests/test_slate_window.py tests/test_odds_window.py -v`
Expected: 9 passed

Run: `.venv/bin/python -m pytest -q`
Expected: 383 passed (374 baseline + 9)

- [ ] **Step 10: Commit**

```bash
git add ingestion/slate_window.py ingestion/odds_client.py ingestion/odds_ingest.py tests/test_slate_window.py tests/test_odds_window.py
git commit -m "feat(ingestion): date-narrowed SGO event pull (README §15.9 item 11 Option B)"
```

---

### Task 2: Confirmed-lineup fetcher

**Files:**
- Create: `ingestion/mlb_lineups.py`
- Test: `tests/test_mlb_lineups.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `lineup_player_ids(payload) -> set[int]` — playstat `player_id`s (raw statsapi id + 100_000_000).
  - `game_start_times(payload) -> dict[int, datetime]` — playstat `game_id` -> tz-aware UTC first pitch.
  - `fetch_lineups(date_str, session=None) -> tuple[set[int], dict[int, datetime]]` — one network call returning both.

**Background the implementer needs:** `statsapi.mlb.com` is free, key-less, and already used by `ingestion/mlb_backfill.py`. MLB ids map into playstat's id space by the mlb `id_offset` of **+100,000,000** (verified live: 270/270 distinct lineup player ids resolve against `players`). The schedule payload nests as `dates[].games[]`, each game carrying `gamePk`, `gameDate`, and — under `hydrate=lineups` — `lineups.homePlayers[]` / `lineups.awayPlayers[]`, each entry having an `id`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mlb_lineups.py`:

```python
from datetime import timezone

import pytest

from ingestion.mlb_lineups import game_start_times, lineup_player_ids

# Trimmed shape of statsapi /api/v1/schedule?hydrate=lineups.
PAYLOAD = {
    "dates": [
        {
            "date": "2026-08-08",
            "games": [
                {
                    "gamePk": 824085,
                    "gameDate": "2026-08-08T22:40:00Z",
                    "lineups": {
                        "homePlayers": [{"id": 11}, {"id": 12}],
                        "awayPlayers": [{"id": 13}],
                    },
                },
                {
                    # Lineups not posted yet: the key is absent entirely.
                    "gamePk": 824086,
                    "gameDate": "2026-08-09T01:50:00Z",
                },
            ],
        }
    ]
}


def test_lineup_player_ids_applies_the_mlb_offset():
    assert lineup_player_ids(PAYLOAD) == {100_000_011, 100_000_012, 100_000_013}


def test_lineup_player_ids_tolerates_games_without_posted_lineups():
    # Must not raise; the unposted game simply contributes nothing.
    assert 824086 + 100_000_000 not in lineup_player_ids(PAYLOAD)


def test_lineup_player_ids_empty_payload():
    assert lineup_player_ids({}) == set()


def test_game_start_times_applies_offset_and_returns_utc():
    times = game_start_times(PAYLOAD)
    assert set(times) == {100_824_085, 100_824_086}
    assert times[100_824_085].tzinfo is not None
    assert times[100_824_085].astimezone(timezone.utc).hour == 22


def test_game_start_times_skips_games_missing_a_date():
    payload = {"dates": [{"games": [{"gamePk": 1}]}]}
    assert game_start_times(payload) == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mlb_lineups.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingestion.mlb_lineups'`

- [ ] **Step 3: Implement `ingestion/mlb_lineups.py`**

```python
"""Confirmed MLB lineups + first-pitch times from statsapi (free, no key).

README §15.9 item 11 Option B. The morning chain builds at ~08:39 ET but MLB
lineups post ~2-3h before first pitch, so ~16.2% of player legs still void even
after the Option A start-rate filter. This module supplies the posted lineup so a
17:30 ET pass can build a higher-confidence card.

It returns start times as well as lineups because `games` has NO start-time
column (game_id, date, home_team_id, away_team_id, status, sport) — so "games not
yet started" has no other source, and the same call already carries it.

Verified live 2026-08-08: 15/15 games populate (9 players/side) and 270/270
distinct lineup player ids map to `players` at 100% via the mlb offset.
"""

from datetime import datetime

import requests

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_ID_OFFSET = 100_000_000
TIMEOUT = (10, 30)  # (connect, read) — bare timeouts stalled the chain, §15.9 item 8


def _games(payload):
    for date_block in (payload or {}).get("dates", []):
        for game in date_block.get("games", []):
            yield game


def lineup_player_ids(payload):
    """Set of playstat player_ids appearing in any posted lineup.

    A game whose lineup has not posted has no "lineups" key at all; it simply
    contributes nothing rather than raising.
    """
    ids = set()
    for game in _games(payload):
        lineups = game.get("lineups") or {}
        for side in ("homePlayers", "awayPlayers"):
            for player in lineups.get(side) or []:
                pid = player.get("id")
                if pid is not None:
                    ids.add(pid + MLB_ID_OFFSET)
    return ids


def game_start_times(payload):
    """{playstat game_id: tz-aware UTC first pitch}. Games with no gameDate are skipped."""
    times = {}
    for game in _games(payload):
        pk, game_date = game.get("gamePk"), game.get("gameDate")
        if pk is None or not game_date:
            continue
        times[pk + MLB_ID_OFFSET] = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
    return times


def fetch_lineups(date_str, session=None):
    """(player_ids, start_times) for an ET slate date 'YYYY-MM-DD'. One network call."""
    getter = session or requests
    response = getter.get(
        SCHEDULE_URL,
        params={"sportId": 1, "startDate": date_str, "endDate": date_str,
                "hydrate": "lineups"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return lineup_player_ids(payload), game_start_times(payload)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mlb_lineups.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add ingestion/mlb_lineups.py tests/test_mlb_lineups.py
git commit -m "feat(ingestion): confirmed-lineup + first-pitch fetcher (README §15.9 item 11 Option B)"
```

---

### Task 3: `--require-confirmed-lineup` builder mode

**Files:**
- Modify: `optimizer/builder.py` — `load_player_legs` (line 129), `load_legs` (line 252), `main` (lines 443-460, 480-516)
- Test: `tests/test_builder_confirmed_lineup.py`

**Interfaces:**
- Consumes: `ingestion.mlb_lineups.fetch_lineups`, `ingestion.slate_window` (indirectly, via the chain script).
- Produces:
  - `filter_by_confirmed_lineup(legs, confirmed_ids, started_game_ids) -> list` (pure).
  - `load_player_legs(..., confirmed_ids=None, started_game_ids=None)`
  - `load_legs(..., confirmed_ids=None, started_game_ids=None)`
  - CLI flag `--require-confirmed-lineup`; save class string `"confirmed_lineup"`.

**Design note the implementer must respect:** the confirmed build passes the lineup filter **only** — it must **not** also apply `--min-start-rate 0.65`. A posted lineup is direct evidence of starting; the start-rate filter is a *proxy* for the same thing, and stacking them would drop confirmed starters who happen to have thin history.

- [ ] **Step 1: Write the failing test**

Create `tests/test_builder_confirmed_lineup.py`:

```python
from optimizer.builder import filter_by_confirmed_lineup


def _player(pid, gid=1):
    return {"kind": "player", "player_id": pid, "game_id": gid}


def _team(gid=1):
    return {"kind": "team", "player_id": None, "game_id": gid}


def test_keeps_only_players_in_the_posted_lineup():
    legs = [_player(1), _player(2), _player(3)]
    kept = filter_by_confirmed_lineup(legs, {1, 3}, set())
    assert [leg["player_id"] for leg in kept] == [1, 3]


def test_drops_legs_from_games_already_started():
    legs = [_player(1, gid=10), _player(2, gid=20)]
    kept = filter_by_confirmed_lineup(legs, {1, 2}, {20})
    assert [leg["player_id"] for leg in kept] == [1]


def test_team_legs_survive_the_lineup_filter_but_not_the_started_filter():
    # Team markets have no player, so a lineup can never confirm them; they are
    # never lineup-filtered. A started game is still excluded.
    legs = [_team(gid=10), _team(gid=20)]
    kept = filter_by_confirmed_lineup(legs, set(), {20})
    assert [leg["game_id"] for leg in kept] == [10]


def test_none_confirmed_ids_is_off_and_returns_input_unchanged():
    legs = [_player(1), _player(2), _team(3)]
    assert filter_by_confirmed_lineup(legs, None, None) == legs


def test_empty_confirmed_set_drops_every_player_leg():
    # Distinct from None: an empty posted set means "no lineups yet", and the
    # caller (not this pure helper) decides that is a no-build.
    legs = [_player(1), _team(2)]
    kept = filter_by_confirmed_lineup(legs, set(), set())
    assert kept == [_team(2)]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_builder_confirmed_lineup.py -v`
Expected: FAIL — `ImportError: cannot import name 'filter_by_confirmed_lineup'`

- [ ] **Step 3: Implement the pure filter**

In `optimizer/builder.py`, add immediately after `filter_by_start_rate` (i.e. after line 126):

```python
def filter_by_confirmed_lineup(legs, confirmed_ids, started_game_ids):
    """Restrict legs to CONFIRMED starters in games that have not started.

    README §15.9 item 11 Option B. Pure — the caller fetches the lineup.

    confirmed_ids=None is OFF and returns `legs` unchanged (byte-identical
    default). An EMPTY set is meaningfully different: it means no lineup has
    posted, and every player leg is correctly dropped. Team legs carry no player
    so a lineup can never confirm them — they are never lineup-filtered, only
    excluded when their game has already started.
    """
    if confirmed_ids is None:
        return legs
    started = started_game_ids or set()
    return [
        leg for leg in legs
        if leg["game_id"] not in started
        and (leg["kind"] != "player" or leg["player_id"] in confirmed_ids)
    ]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_builder_confirmed_lineup.py -v`
Expected: 5 passed

- [ ] **Step 5: Thread it through the loaders**

In `optimizer/builder.py`, change `load_player_legs`'s signature (line 129) to:

```python
def load_player_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb", window_days=0,
                     min_start_rate=0.0, confirmed_ids=None, started_game_ids=None):
```

and replace its trailing filter block (currently lines 177-181) with:

```python
    # Start-probability filter (README §15.9 item 11 Option A). Default 0.0 = OFF,
    # so the library default stays byte-identical; the chain/CLI opt in explicitly.
    if min_start_rate:
        legs = filter_by_start_rate(legs, load_start_rates(engine, slate_date, sport), min_start_rate)
    # Confirmed-lineup filter (Option B). Default None = OFF. Deliberately NOT
    # combined with min_start_rate by the chain: a posted lineup is direct
    # evidence of starting, and the start-rate proxy would drop confirmed
    # starters with thin history for no reason.
    legs = filter_by_confirmed_lineup(legs, confirmed_ids, started_game_ids)
    return legs
```

Change `load_team_legs`'s signature (line 184) to accept and apply the started-game filter:

```python
def load_team_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb", window_days=0,
                   started_game_ids=None):
```

and immediately before its `return`, add:

```python
    # Team legs are never lineup-filtered (no player), but a started game is
    # still out of scope for a confirmed-lineup card. confirmed_ids is passed as
    # an empty set purely to switch the helper on; it is not consulted for team legs.
    if started_game_ids is not None:
        legs = filter_by_confirmed_lineup(legs, set(), started_game_ids)
    return legs
```

Change `load_legs` (line 252) to:

```python
def load_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb", window_days=0,
              min_start_rate=0.0, confirmed_ids=None, started_game_ids=None):
    return (load_player_legs(engine, floor, slate_date, sport, window_days, min_start_rate,
                             confirmed_ids, started_game_ids)
            + load_team_legs(engine, floor, slate_date, sport, window_days, started_game_ids))
```

> Verify against the current file: `load_legs` at line 252 concatenates the two loaders. Preserve its existing concatenation order and any surrounding lines exactly; only the parameters change.

- [ ] **Step 6: Add the CLI flag and save class**

In `optimizer/builder.py:main()`, add after the `--min-start-rate` argument (line 443):

```python
    parser.add_argument("--require-confirmed-lineup", action="store_true",
                        help="MLB only: restrict player legs to players in a POSTED "
                             "lineup and to games not yet started; saves class "
                             "'confirmed_lineup' (README §15.9 item 11 Option B)")
```

Replace the leg-loading block (currently lines 480-484) with:

```python
    confirmed_ids = started_game_ids = None
    if args.require_confirmed_lineup:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        from ingestion.mlb_lineups import fetch_lineups
        now = datetime.now(timezone.utc)
        slate = args.slate_date or now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        confirmed_ids, start_times = fetch_lineups(slate)
        started_game_ids = {gid for gid, start in start_times.items() if start <= now}
        print(f"confirmed lineups: {len(confirmed_ids)} players, "
              f"{len(started_game_ids)} games already started")
        if not confirmed_ids:
            print("no posted lineups yet — nothing to build.")
            return

    if args.team_only:
        legs = load_team_legs(engine, args.floor, args.slate_date, args.sport, window_days,
                              started_game_ids)
    else:
        legs = load_legs(engine, args.floor, args.slate_date, args.sport, window_days,
                         args.min_start_rate, confirmed_ids, started_game_ids)
```

Replace the save-class selection (currently lines 512-514) with:

```python
        if args.require_confirmed_lineup:
            parlay_class = "confirmed_lineup"
        elif args.team_only:
            parlay_class = _team_class(args.sport)
        else:
            parlay_class = "across_game"
```

- [ ] **Step 7: Verify the default path is byte-identical**

Run: `.venv/bin/python -m pytest -q`
Expected: 388 passed (383 + 5). **Zero failures in `tests/test_builder.py` — any failure there means a default changed.**

- [ ] **Step 8: Commit**

```bash
git add optimizer/builder.py tests/test_builder_confirmed_lineup.py
git commit -m "feat(builder): --require-confirmed-lineup mode, class confirmed_lineup (§15.9 item 11 Option B)"
```

---

### Task 4: Line-movement measurement

**Files:**
- Create: `optimizer/line_movement.py`
- Test: `tests/test_line_movement.py`

**Interfaces:**
- Consumes: `optimizer.devig` (already exists — `devig`/`odds_to_probability`, extracted in §16 #3B).
- Produces: `close_prob_for_side(row, side) -> float | None`, `leg_movement(build_leg, close_row) -> dict | None`, `summarize_movement(pairs) -> dict`.

**Honesty rules that are part of the deliverable (§15.8 #2):** the output must never contain the words "edge", "value", "+EV", or "beat". Field names are `movement_pp`, `coverage`, `median_lead_minutes`. This measures whether the market moved toward or away from our price — nothing more.

**Line-movement policy:** compare **only** when `line_value` is unchanged. A moved line (0.5 -> 1.5) is a *different bet*, and comparing across it would fabricate movement. Excluded pairs are counted in `coverage`, which is part of the output rather than a footnote.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_line_movement.py`:

```python
import pytest

from optimizer.line_movement import leg_movement, summarize_movement


def _build(prob=0.60, line=0.5, side="under"):
    return {"market_prob": prob, "line": line, "side": side,
            "player_id": 1, "game_id": 10, "stat_type": "home_runs"}


def test_movement_is_positive_when_the_market_moves_toward_our_side():
    # We took it at 0.60; by the close the market prices our side at 0.65.
    out = leg_movement(_build(prob=0.60), {"market_prob": 0.65, "line_value": 0.5})
    assert out["movement_pp"] == pytest.approx(5.0)


def test_movement_is_negative_when_the_market_moves_against_us():
    out = leg_movement(_build(prob=0.60), {"market_prob": 0.55, "line_value": 0.5})
    assert out["movement_pp"] == pytest.approx(-5.0)


def test_a_moved_line_is_excluded_rather_than_compared():
    # 0.5 -> 1.5 is a DIFFERENT bet. Returning a number here would be fabrication.
    assert leg_movement(_build(line=0.5), {"market_prob": 0.9, "line_value": 1.5}) is None


def test_a_missing_close_row_is_excluded():
    assert leg_movement(_build(), None) is None


def test_summary_reports_coverage_and_excludes_uncompared_legs():
    pairs = [
        (_build(prob=0.60), {"market_prob": 0.65, "line_value": 0.5}),
        (_build(prob=0.60), {"market_prob": 0.55, "line_value": 0.5}),
        (_build(prob=0.60, line=0.5), {"market_prob": 0.99, "line_value": 1.5}),  # moved
        (_build(prob=0.60), None),                                                # missing
    ]
    summary = summarize_movement(pairs)
    assert summary["n_legs"] == 4
    assert summary["n_compared"] == 2
    assert summary["coverage"] == pytest.approx(0.5)
    assert summary["mean_movement_pp"] == pytest.approx(0.0)


def test_summary_of_nothing_comparable_is_zero_coverage_not_a_crash():
    summary = summarize_movement([(_build(), None)])
    assert summary["n_compared"] == 0
    assert summary["coverage"] == 0.0
    assert summary["mean_movement_pp"] is None
```

Append the de-vig tests to the same file:

```python
from optimizer.line_movement import close_prob_for_side


def test_close_prob_devigs_the_over_side():
    # -120/+100 -> raw .5455/.5000, overround 1.0455 -> over .5218
    row = {"over_odds": -120, "under_odds": 100}
    assert close_prob_for_side(row, "over") == pytest.approx(0.5218, abs=1e-4)


def test_close_prob_devigs_the_under_side_to_the_complement():
    row = {"over_odds": -120, "under_odds": 100}
    over = close_prob_for_side(row, "over")
    under = close_prob_for_side(row, "under")
    assert over + under == pytest.approx(1.0)


def test_close_prob_uses_home_away_columns_for_team_markets():
    row = {"home_odds": -200, "away_odds": 170, "over_odds": None, "under_odds": None}
    assert close_prob_for_side(row, "home") == pytest.approx(0.642857, abs=1e-4)


def test_close_prob_is_none_when_one_sided():
    # A one-sided row cannot be de-vigged; it must be excluded, not guessed.
    assert close_prob_for_side({"over_odds": -120, "under_odds": None}, "over") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_line_movement.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'optimizer.line_movement'`

- [ ] **Step 3: Implement `optimizer/line_movement.py`**

```python
"""Line movement between a card's build price and its last pre-start price.

README §15.9 item 12's MANDATORY validation gate. The builder's apparent "+EV"
is measured against a consensus of six SOFT books with no sharp reference, so
best-of-six beating consensus-of-six may be a genuine stale line OR an artifact
of one outlier dragging the average. The industry-standard discriminator is
closing-line value.

This is a MEASUREMENT-ONLY module. `modeling/clv.py` was DELETED in §16 #3B and
this is deliberately NOT a revival of it: it lives beside optimizer/devig.py and
optimizer/stake.py because it measures the builder, not a model.

HONESTY (§15.8 #2). This is NOT the true closing line — the last snapshot lands
a median ~100 minutes (worst case ~150) before first pitch. It must be presented
as "line movement, build -> last pre-start snapshot" with the lead time stated,
and never as edge/value/+EV.

Both sides of the comparison already exist in the DB — prop_lines/game_lines
carry `pulled_at` and inserts are append-only — so this needs no migration.
"""


from optimizer.devig import devig

# Which (over, under)-shaped column pair a side is priced from. Team home/away
# markets store home_odds/away_odds; everything else is over/under.
_SIDE_COLUMNS = {
    "over": ("over_odds", "under_odds", 0),
    "under": ("over_odds", "under_odds", 1),
    "home": ("home_odds", "away_odds", 0),
    "away": ("home_odds", "away_odds", 1),
}


def close_prob_for_side(row, side):
    """De-vigged probability of `side` in a raw prop_lines/game_lines row.

    Returns None for an unknown side or a ONE-SIDED row: a single price cannot be
    de-vigged, and inventing a probability from it would manufacture movement.
    Excluded rows are counted in `coverage` by summarize_movement.
    """
    columns = _SIDE_COLUMNS.get(side)
    if columns is None:
        return None
    first, second, index = columns
    a, b = row.get(first), row.get(second)
    if a is None or b is None:
        return None
    return devig(int(a), int(b))[index]


def leg_movement(build_leg, close_row):
    """Movement in percentage points for one leg, or None if not comparable.

    Positive means the market moved TOWARD the side we took (our side became more
    probable, i.e. the price we got was better than the later one).

    Returns None when there is no later snapshot, or when `line_value` moved —
    a different line is a DIFFERENT BET, and comparing across it would invent
    movement that is really a change of market. Excluded legs are counted in
    `coverage` by summarize_movement rather than silently dropped.
    """
    if not close_row or build_leg.get("market_prob") is None:
        return None
    if float(close_row["line_value"]) != float(build_leg["line"]):
        return None
    # Accept either a pre-computed market_prob (unit tests, callers that already
    # de-vigged) or a raw odds row, which we de-vig for the side we actually took.
    close_prob = close_row.get("market_prob")
    if close_prob is None:
        close_prob = close_prob_for_side(close_row, build_leg.get("side"))
    if close_prob is None:
        return None
    close_row = {**close_row, "market_prob": close_prob}
    return {
        "player_id": build_leg.get("player_id"),
        "game_id": build_leg.get("game_id"),
        "stat_type": build_leg.get("stat_type"),
        "side": build_leg.get("side"),
        "line": float(build_leg["line"]),
        "build_prob": float(build_leg["market_prob"]),
        "close_prob": float(close_row["market_prob"]),
        "movement_pp": (float(close_row["market_prob"]) - float(build_leg["market_prob"])) * 100.0,
    }


def summarize_movement(pairs):
    """Aggregate (build_leg, close_row) pairs.

    coverage is first-class output, not a footnote: it is the share of legs that
    could honestly be compared, and a low value is itself the finding.
    """
    moves = []
    for build_leg, close_row in pairs:
        move = leg_movement(build_leg, close_row)
        if move is not None:
            moves.append(move)

    n_legs = len(pairs)
    n_compared = len(moves)
    values = [m["movement_pp"] for m in moves]
    return {
        "n_legs": n_legs,
        "n_compared": n_compared,
        "coverage": (n_compared / n_legs) if n_legs else 0.0,
        "mean_movement_pp": (sum(values) / len(values)) if values else None,
        "n_toward": sum(1 for v in values if v > 0),
        "n_against": sum(1 for v in values if v < 0),
        "legs": moves,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_line_movement.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add optimizer/line_movement.py tests/test_line_movement.py
git commit -m "feat(optimizer): line-movement measurement for the §15.9 item 12 CLV gate"
```

---

### Task 5: `GET /parlay-builder/line-movement`

**Files:**
- Modify: `api/schemas.py` (append new models), `api/main.py` (after `builder_record`, ~line 560)
- Test: `tests/test_line_movement_api.py`

**Interfaces:**
- Consumes: `optimizer.line_movement.summarize_movement`.
- Produces: `_shape_line_movement(saved_rows, close_rows) -> LineMovementOut`; endpoint `GET /parlay-builder/line-movement?sport=mlb&days=14`.

**Contract rule:** this is **dashboard-only and additive**. `/parlay-builder/saved`, `/box-scores`, `/games` are untouched. Follow the `_shape_builder_record` pattern exactly: a **pure, DB-free** shaping function plus a thin endpoint that only runs SQL.

- [ ] **Step 1: Write the failing test**

Create `tests/test_line_movement_api.py`:

```python
import pytest

from api.main import _shape_line_movement


def _saved(parlay_id, legs):
    return (parlay_id, "2026-08-08", {"class": "across_game", "legs": legs})


def _leg(pid, prob, line=0.5):
    return {"kind": "player", "player_id": pid, "game_id": 10,
            "stat_type": "home_runs", "side": "under", "line": line,
            "market_prob": prob}


def test_shapes_movement_against_the_close_rows():
    saved = [_saved(1, [_leg(11, 0.60), _leg(12, 0.70)])]
    close = {(11, 10, "home_runs"): {"market_prob": 0.65, "line_value": 0.5},
             (12, 10, "home_runs"): {"market_prob": 0.68, "line_value": 0.5}}
    out = _shape_line_movement(saved, close)
    assert out.n_compared == 2
    assert out.coverage == pytest.approx(1.0)
    assert out.mean_movement_pp == pytest.approx(1.5)


def test_missing_close_rows_lower_coverage_without_crashing():
    saved = [_saved(1, [_leg(11, 0.60), _leg(12, 0.70)])]
    close = {(11, 10, "home_runs"): {"market_prob": 0.65, "line_value": 0.5}}
    out = _shape_line_movement(saved, close)
    assert out.n_legs == 2
    assert out.n_compared == 1
    assert out.coverage == pytest.approx(0.5)


def test_no_saved_rows_is_an_empty_honest_result():
    out = _shape_line_movement([], {})
    assert out.n_legs == 0
    assert out.coverage == 0.0
    assert out.mean_movement_pp is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_line_movement_api.py -v`
Expected: FAIL — `ImportError: cannot import name '_shape_line_movement'`

- [ ] **Step 3: Add the schema**

Append to `api/schemas.py`:

```python
class LineMovementLegOut(BaseModel):
    player_id: int | None = None
    game_id: int | None = None
    stat_type: str | None = None
    side: str | None = None
    line: float
    build_prob: float
    close_prob: float
    movement_pp: float


class LineMovementOut(BaseModel):
    """Line movement between a card's build price and its last pre-start price.

    NOT the closing line and NOT an edge/value claim (README §15.8 #2): the last
    snapshot lands a median ~100 min before first pitch. `coverage` is the share
    of legs comparable at an UNCHANGED line — a low value is itself the finding.
    """
    n_legs: int
    n_compared: int
    coverage: float
    mean_movement_pp: float | None = None
    n_toward: int = 0
    n_against: int = 0
    legs: list[LineMovementLegOut] = []
```

- [ ] **Step 4: Implement the shaping helper + endpoint**

In `api/main.py`, add `LineMovementLegOut` and `LineMovementOut` to the existing `from api.schemas import (...)` block, and add a module-level import:

```python
from optimizer.line_movement import summarize_movement
```

Then add after `builder_record` (~line 560):

```python
def _shape_line_movement(saved_rows, close_rows):
    """Pure: saved_rows are (parlay_id, slate_date, legs_wrapper) and close_rows
    maps (player_id, game_id, stat_type) -> the last pre-start line row. DB-free
    and unit-testable without a database, mirroring _shape_builder_record.

    Team legs have no player_id and are keyed (None, game_id, market); they are
    looked up the same way and simply miss when absent, lowering coverage.
    """
    pairs = []
    for _parlay_id, _slate_date, wrapper in saved_rows:
        for leg in (wrapper or {}).get("legs", []):
            key = (leg.get("player_id"), leg.get("game_id"),
                   leg.get("stat_type") or leg.get("market"))
            pairs.append((leg, close_rows.get(key)))

    summary = summarize_movement(pairs)
    return LineMovementOut(
        n_legs=summary["n_legs"],
        n_compared=summary["n_compared"],
        coverage=summary["coverage"],
        mean_movement_pp=summary["mean_movement_pp"],
        n_toward=summary["n_toward"],
        n_against=summary["n_against"],
        legs=[LineMovementLegOut(**leg) for leg in summary["legs"]],
    )


@app.get("/parlay-builder/line-movement", response_model=LineMovementOut)
def builder_line_movement(sport: str = "mlb", days: int = 14):
    """Line movement from build price to last pre-start price (README §15.9 item 12).

    Dashboard-only and ADDITIVE — /parlay-builder/saved, /box-scores and /games
    are untouched (Budgerr contract, §7.1). NOT a closing line and NOT an edge
    claim: see LineMovementOut's docstring.
    """
    with engine.begin() as conn:
        saved_rows = conn.execute(text(
            """
            SELECT pr.parlay_id, pr.created_at::date, pr.legs
            FROM parlay_recommendations pr
            WHERE pr.kind = 'builder'
              AND COALESCE(pr.legs->>'sport', 'mlb') = :sport
              AND pr.created_at::date >= CURRENT_DATE - :days
            ORDER BY pr.created_at DESC
            """
        ), {"sport": sport, "days": days}).fetchall()

        # The last snapshot per (player, game, stat). With --not-before-now on the
        # late pulls, a game only appears in pulls preceding its first pitch, so
        # its newest row IS its last pre-start price. Raw odds are returned and
        # de-vigged in Python by close_prob_for_side, because the correct side
        # is known only from the saved leg.
        close_rows = {
            (r[0], r[1], r[2]): {"line_value": r[3],
                                 "over_odds": r[4], "under_odds": r[5]}
            for r in conn.execute(text(
                """
                SELECT DISTINCT ON (player_id, game_id, stat_type)
                    player_id, game_id, stat_type, line_value, over_odds, under_odds
                FROM prop_lines
                ORDER BY player_id, game_id, stat_type, pulled_at DESC
                """
            )).fetchall()
        }
        # Team legs: keyed (None, game_id, market) to match the saved leg's shape,
        # which has player_id=None and stat_type=None but carries `market`.
        close_rows.update({
            (None, r[0], r[1]): {"line_value": r[2],
                                 "over_odds": r[3], "under_odds": r[4],
                                 "home_odds": r[5], "away_odds": r[6]}
            for r in conn.execute(text(
                """
                SELECT DISTINCT ON (game_id, market)
                    game_id, market, line_value, over_odds, under_odds,
                    home_odds, away_odds
                FROM game_lines
                ORDER BY game_id, market, pulled_at DESC
                """
            )).fetchall()
        })

    return _shape_line_movement(saved_rows, close_rows)
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_line_movement_api.py -v`
Expected: 3 passed

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 401 passed (374 baseline + 9 + 5 + 5 + 10 + 3). **`tests/test_parlay_builder_api.py` must be green — a failure there means a Budgerr surface moved.**

- [ ] **Step 7: Commit**

```bash
git add api/schemas.py api/main.py tests/test_line_movement_api.py
git commit -m "feat(api): additive dashboard-only GET /parlay-builder/line-movement"
```

---

### Task 6: The two launchd job scripts

**Files:**
- Create: `scripts/late_afternoon.sh`
- Create: `~/Library/LaunchAgents/com.playstat.mlb.late.plist` — **ARCHITECT ONLY, do not create or load**
- Create: `~/Library/LaunchAgents/com.playstat.mlb.close.plist` — **ARCHITECT ONLY, do not create or load**

**Interfaces:**
- Consumes: everything above, via CLI.
- Produces: `scripts/late_afternoon.sh [--odds-only]`.

**Worker scope:** write and `bash -n`-check the script only. **Do not create, load, or modify any launchd job** — that is a reserved architect lane. The plists are specified here for the architect.

- [ ] **Step 1: Write `scripts/late_afternoon.sh`**

```bash
#!/bin/bash
# Playstat late-afternoon job — README §15.9 item 11 Option B + item 12.
#
# Two launchd triggers share this script:
#   com.playstat.mlb.late  17:30 ET  (full: odds -> confirmed-lineup builds)
#   com.playstat.mlb.close 19:45 ET  (--odds-only: the closing snapshot)
#
# BEST-EFFORT BY DESIGN: it must never page. The morning card has already landed
# and IS the product; a missed confirmed card is a missed improvement, not an
# outage. Failures go to logs/mlb.log and the script exits non-zero quietly.
#
# QUOTA: every pull is --slate-window --not-before-now, so it fetches only
# not-yet-started games in today's ET slate. Measured ~9.6 entities at 17:30 and
# ~3.7 at 19:45 against a 2,500/month cap; all three daily pulls together cost
# ~27/day, roughly HALF what today's single unfiltered morning pull costs.
# --not-before-now also keeps in-play prices out of a pre-game snapshot.
#
# 17:30 sits inside a measured structural gap: across 193 games / 14 days, NO
# MLB game starts between 16:10 and 18:05 ET, so the trigger costs zero coverage
# versus the originally scoped 16:45 while landing 45 min closer to the close.

set -uo pipefail

REPO="${PLAYSTAT_REPO:-/Users/aayushpokhrel/dev/playstat}"
PY="$REPO/.venv/bin/python"

cd "$REPO" || exit 1

# launchd does not read .env; the python modules load it via dotenv themselves,
# but this wrapper's own conditionals need it too.
if [ -f "$REPO/.env" ]; then
	set -a
	# shellcheck disable=SC1091
	. "$REPO/.env"
	set +a
fi

ODDS_ONLY=0
[ "${1:-}" = "--odds-only" ] && ODDS_ONLY=1

_step() { local n="$1"; shift; local s; s=$(date +%s); "$@"; local r=$?; echo "=== step $n: $(( $(date +%s) - s ))s rc=$r ==="; return $r; }

echo "=== late-afternoon start $(date '+%F %H:%M:%S') (odds_only=$ODDS_ONLY) ==="

# Hold off idle sleep for the run; released when this script exits.
command -v caffeinate >/dev/null && caffeinate -i -m -w $$ 2>/dev/null &

_step odds_late "$PY" -m ingestion.odds_ingest --sport mlb --slate-window --not-before-now
rc=$?

if [ "$ODDS_ONLY" = 1 ]; then
	echo "=== late-afternoon done (odds only) rc=$rc $(date '+%F %H:%M:%S') ==="
	exit $rc
fi

if [ "$rc" -ne 0 ]; then
	echo "=== late-afternoon: odds failed rc=$rc — skipping builds (non-fatal) ==="
	exit $rc
fi

# Confirmed-lineup builds. NOTE: deliberately NO --min-start-rate here. A posted
# lineup is direct evidence of starting; the 0.65 start-rate proxy the morning
# chain uses would drop confirmed starters with thin history for no reason.
_step confirmed_1.4 "$PY" -m optimizer.builder --require-confirmed-lineup \
	--target-payout 1.4 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save ||
	echo "=== confirmed_1.4: FAILED (non-fatal) ==="

_step confirmed_2.0 "$PY" -m optimizer.builder --require-confirmed-lineup \
	--target-payout 2.0 --tolerance 0.10 --top-n 5 --max-leg-reuse 2 --save ||
	echo "=== confirmed_2.0: FAILED (non-fatal) ==="

echo "=== late-afternoon done $(date '+%F %H:%M:%S') ==="
```

- [ ] **Step 2: Syntax-check and make executable**

```bash
bash -n scripts/late_afternoon.sh && chmod +x scripts/late_afternoon.sh
```
Expected: no output (clean), exit 0.

- [ ] **Step 3: Smoke-test the odds-only path without spending quota**

Run: `bash -n scripts/late_afternoon.sh && echo SYNTAX_OK`
Expected: `SYNTAX_OK`

Then verify the CLI wiring resolves a window **without** calling SGO:

```bash
.venv/bin/python -c "
from datetime import datetime, timezone
from ingestion.slate_window import slate_window
print(slate_window(datetime.now(timezone.utc), 'mlb', True))
"
```
Expected: a `('...Z', '...Z')` tuple whose first element is now-ish and second is tomorrow 10:00Z (summer).

- [ ] **Step 4: Commit**

```bash
git add scripts/late_afternoon.sh
git commit -m "feat(chain): late-afternoon job script (17:30 confirmed build + 19:45 closing snapshot)"
```

- [ ] **Step 5: ARCHITECT — narrow the morning pull**

In `scripts/daily_chain.sh`, change the MLB `odds` step (line 200) from:

```bash
		_step_retry odds        "$PY" -m ingestion.odds_ingest --sport mlb &&
```

to:

```bash
		_step_retry odds        "$PY" -m ingestion.odds_ingest --sport mlb --slate-window &&
```

No `--not-before-now`: the morning run wants the whole slate. Then `bash -n scripts/daily_chain.sh`.

- [ ] **Step 6: ARCHITECT — create the plists**

`~/Library/LaunchAgents/com.playstat.mlb.late.plist` — `ProgramArguments` = `/bin/bash /Users/aayushpokhrel/dev/playstat/scripts/late_afternoon.sh`, `StartCalendarInterval` Hour 17 Minute 30, `StandardOutPath`/`StandardErrorPath` = `/Users/aayushpokhrel/dev/playstat/logs/mlb.log`.

`~/Library/LaunchAgents/com.playstat.mlb.close.plist` — same, plus the `--odds-only` argument, Hour 19 Minute 45.

Load with `launchctl bootstrap gui/$(id -u) <plist>`. **Architect only.**

---

### Task 7: Dashboard surface

**Files:**
- Modify: `web/app/lib/api.ts` (add fetcher + type), `web/app/builder/page.tsx` (server-fetch + pass down)
- Create: `web/app/builder/LineMovementPanel.tsx`

**Interfaces:**
- Consumes: `GET /parlay-builder/line-movement`.
- Produces: a `LineMovementPanel` section on the builder page.

**Before writing any UI:** read `PRODUCT.md` and `DESIGN.md` (required by `CLAUDE.md`) and match `web/app/builder/` conventions. `apiGet` is **server-only**, so `page.tsx` server-fetches and passes props down — follow how `RecordPanel.tsx` receives its data.

**Copy rules (§15.8 #2, non-negotiable):** no "edge", "value", "+EV", or "beat the market". Use monochrome glyphs — **no signal-green** (reserved for the >=75% joint-prob rule). The caption must state that this is **not** the closing line and give the median lead time.

- [ ] **Step 1: Add the type and fetcher to `web/app/lib/api.ts`**

```ts
export type LineMovementLeg = {
  player_id: number | null;
  game_id: number | null;
  stat_type: string | null;
  side: string | null;
  line: number;
  build_prob: number;
  close_prob: number;
  movement_pp: number;
};

export type LineMovement = {
  n_legs: number;
  n_compared: number;
  coverage: number;
  mean_movement_pp: number | null;
  n_toward: number;
  n_against: number;
  legs: LineMovementLeg[];
};

export async function fetchLineMovement(sport = "mlb"): Promise<LineMovement | null> {
  try {
    return await apiGet<LineMovement>(`/parlay-builder/line-movement?sport=${sport}`);
  } catch {
    // A missing measurement must never break the builder page.
    return null;
  }
}
```

- [ ] **Step 2: Create `web/app/builder/LineMovementPanel.tsx`**

A `"use client"` component taking `{ data }: { data: LineMovement | null }`. Render:
- Heading: `Line movement (paper)`.
- If `data` is null or `n_compared === 0`: the honest empty state — `"Not enough matched lines yet."` Nothing else.
- Otherwise: `mean_movement_pp` to one decimal with an explicit sign, `n_toward`/`n_against`, and `coverage` as a percentage.
- Caption, verbatim: `Build price vs the last snapshot before first pitch — median ~100 min out, not the closing line. Legs whose line moved are excluded; coverage shows how many were comparable.`

Match the markup/classes of the existing `RecordPanel.tsx` sibling.

- [ ] **Step 3: Wire it into `web/app/builder/page.tsx`**

Server-fetch alongside the existing calls and render `<LineMovementPanel data={lineMovement} />` beneath the record region:

```tsx
const lineMovement = await fetchLineMovement(sport);
```

- [ ] **Step 4: Verify the build**

```bash
cd web && npx tsc --noEmit && npm run build
```
Expected: both clean, no type errors.

- [ ] **Step 5: Commit**

```bash
git add web/app/lib/api.ts web/app/builder/LineMovementPanel.tsx web/app/builder/page.tsx
git commit -m "feat(web): line-movement panel on the builder page (paper-framed, no value language)"
```

---

### Task 8: README

**Files:**
- Modify: `README.md` §15.9 item 11 (line 474, Option B), item 12 (line 486), §15.10, §7.1

- [ ] **Step 1: Update §15.9 item 11 Option B**

Mark **BUILT & DEPLOYED 2026-08-08**, linking the spec and this plan. Record, because they correct what is written there now:
- Trigger is **17:30 ET, not 16:45** — a measured structural gap (16:10–18:05 ET, 0 of 193 games) makes it free.
- **"79% by 19:00 ET" was WRONG — measured 45.1%.** "~77% start 18:00+" is really **69.4%**, which is the true coverage ceiling.
- The quota result: SGO meters **entities** (1 event = 1 entity, 2,500/month); the old unfiltered pull cost **51/day with ~70% waste**; three narrowed pulls cost **~27/day ≈ 840/month = 34% of cap**, i.e. **less than the single pull it replaces**.
- The odds step should fall from **885s** toward one request.

- [ ] **Step 2: Update §15.9 item 12**

Record that the CLV gate is now **measurable but not yet answered**: metric is line movement build -> last pre-start snapshot, median lead ~100 min / worst ~150 min, **explicitly not the closing line**; moved lines are excluded and coverage is reported. State plainly that if selections do not beat that line, the "+EV" is noise.

- [ ] **Step 3: Update §15.10 and §7.1**

§15.10: the new `confirmed_lineup` class, and **the deliberate two-classes-two-ledger-entries behaviour** — a construction saved under both `across_game` and `confirmed_lineup` books two paper bets, which is INTENDED (it is what makes the morning-vs-confirmed void/ROI comparison possible) and must not be "fixed" as a duplicate-save bug. Note the expectation from §15.9 item 10 that **reported ROI should FALL** as voids drop.

§7.1: `GET /parlay-builder/line-movement` is dashboard-only and additive; `/parlay-builder/saved`, `/box-scores`, `/games` are unchanged.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(README §15.9 items 11-12, §15.10, §7.1): late-afternoon job BUILT"
```

---

## Architect verification checklist (not delegated)

1. `.venv/bin/python -m pytest -q` — expect 401 green, and specifically `tests/test_builder.py` + `tests/test_parlay_builder_api.py` unchanged.
2. **Review the real `git diff`**, never a worker's summary. Look for: a hardcoded `-04:00` anywhere; a default that is not OFF; window params dropped on page 2 of pagination; a de-vig applied to the wrong side of a team leg; any "+EV"/edge/value wording.
3. Confirm `/parlay-builder/saved` is byte-identical against the live API before and after.
4. `launchctl kickstart -k gui/$(id -u)/com.playstat.api` — **`optimizer/builder.py` and `api/main.py` are API-imported**.
5. Verify the narrowed morning pull live: one `odds_ingest --sport mlb --slate-window` run should report ~15 events, and `/v2/account/usage` should rise by ~15, not ~51.
6. Browser-verify the builder page (login-gated; `SESSION_SECRET=` empty in a dev preview).
7. Create + load the two plists; confirm the 17:30 run writes to `logs/mlb.log`.
8. `git push` after review.
