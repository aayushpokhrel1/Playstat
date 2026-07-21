"""Regression tests for GET /parlay-recommendations (README §15.10 bug #5).

Once optimizer.builder started writing kind='builder' rows into
parlay_recommendations, this endpoint's unfiltered "most recent N rows" query
would eventually put a builder row inside its LIMIT window. Builder legs are
stored under a {"class", "legs": [...]} JSONB wrapper (psycopg2 hands JSONB
back already parsed, so it arrives as a dict) and `json.loads(a_dict)` raises
TypeError -- exactly the bug already fixed once for modeling/settle.py
(README §15.10 bug #4) but never fixed here. The dormant kind='team' shape
(optimizer/team_parlay.py) shares the same wrapper and has no `player_id`,
which the old code accessed unconditionally.

DB-free by design, matching tests/test_settle.py's conventions: no httpx/
TestClient is installed in this environment, so DB access is faked with a
minimal in-memory stand-in for the SQLAlchemy engine rather than exercised
over HTTP.
"""

import inspect

import api.main as api_main


# --- _as_legs_list: the unwrap fix (README §15.10 bug #5) --------------------

def test_as_legs_list_passes_through_bare_list():
    # The live, legacy kind='player' shape: legs is already a bare list.
    legs = [{"player_id": 1, "game_id": 2, "stat_type": "hits", "side": "over",
             "model_prob": 0.6, "odds": -120}]
    assert api_main._as_legs_list(legs) is legs


def test_as_legs_list_parses_json_string():
    assert api_main._as_legs_list('[{"player_id": 1}]') == [{"player_id": 1}]


def test_as_legs_list_unwraps_builder_dict_wrapper():
    """Regression: this previously raised TypeError, because psycopg2 hands
    JSONB back already parsed (a dict), and json.loads(dict) is invalid."""
    blob = {"class": "across_game", "legs": [{"kind": "player", "player_id": 9}]}
    assert api_main._as_legs_list(blob) == blob["legs"]


def test_as_legs_list_unwraps_team_dict_wrapper():
    # optimizer/team_parlay.py's wrapper additionally carries an "ev" key.
    blob = {"class": "team_pair", "ev": 0.1, "legs": [{"game_id": 1, "market": "f5_runs"}]}
    assert api_main._as_legs_list(blob) == blob["legs"]


# --- the SQL-level fix: builder rows never reach Python ----------------------

def test_query_restricts_to_legacy_kinds_and_excludes_builder():
    source = inspect.getsource(api_main.list_parlay_recommendations)
    assert "WHERE kind IN ('player', 'team')" in source
    # Guard against someone widening the IN-list to include 'builder' again.
    where_clause = source.split("WHERE kind IN")[1].split("ORDER BY")[0]
    assert "builder" not in where_clause


# --- fake DB plumbing (no httpx/TestClient in this environment) -------------

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, queue):
        self._queue = queue

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return _FakeResult(self._queue.pop(0))


class _FakeEngine:
    """Stands in for the SQLAlchemy engine: each .begin() call gets a fresh
    connection wrapper, all pulling from one shared queue of canned results
    -- one entry per query the endpoint issues, in call order."""

    def __init__(self, results_sequence):
        self._queue = list(results_sequence)

    def begin(self):
        return _FakeConn(self._queue)


# --- end-to-end: the endpoint function itself must not raise -----------------

def test_endpoint_survives_dormant_team_shape_and_serves_legacy_rows(monkeypatch):
    """A fixture standing in for what `WHERE kind IN ('player', 'team')`
    would actually hand back: one live kind='player' row (bare list of legs)
    and one dormant kind='team' row (dict-wrapped, no player_id on its leg).
    Neither should raise, and both should come back as valid
    ParlayRecommendationOut objects -- this is the acceptance bar for the
    fix, not just the isolated _as_legs_list unit tests above."""
    player_row = (
        1, "2026-07-21T00:00:00", 1.4, 0.7, 1.4,
        [{"player_id": 5, "game_id": 10, "stat_type": "hits", "side": "under",
          "model_prob": 0.7, "odds": -150}],
    )
    team_row = (
        2, "2026-07-20T00:00:00", 1.4, 0.6, 1.4,
        {"class": "team_pair", "ev": 0.0,
         "legs": [{"game_id": 11, "market": "first_inning_runs", "side": "over",
                   "odds": 150, "model_prob": 0.55}]},
    )
    fake_engine = _FakeEngine([[player_row, team_row], [(5, "Test Player")]])
    monkeypatch.setattr(api_main, "engine", fake_engine)

    results = api_main.list_parlay_recommendations(limit=10)

    assert len(results) == 2
    by_id = {r.parlay_id: r for r in results}

    player_leg = by_id[1].legs[0]
    assert player_leg.player_id == 5
    assert player_leg.player_name == "Test Player"
    assert player_leg.stat_type == "hits"

    team_leg = by_id[2].legs[0]
    assert team_leg.player_id is None
    assert team_leg.stat_type is None
    assert team_leg.side == "over"
    assert team_leg.model_prob == 0.55


def test_endpoint_unchanged_shape_for_player_only_rows(monkeypatch):
    """Byte-for-byte check: with only legacy kind='player' rows (the only
    shape this endpoint ever actually served in production so far), the
    response fields and values are exactly what the pre-fix code produced."""
    player_row = (
        7, "2026-07-19T00:00:00", 2.0, 0.5, 2.0,
        [{"player_id": 3, "game_id": 20, "stat_type": "total_bases", "side": "over",
          "model_prob": 0.62, "odds": 110}],
    )
    fake_engine = _FakeEngine([[player_row], [(3, "Aaron Judge")]])
    monkeypatch.setattr(api_main, "engine", fake_engine)

    results = api_main.list_parlay_recommendations(limit=10)
    assert len(results) == 1
    r = results[0]
    assert (r.parlay_id, r.created_at, r.target_payout, r.joint_prob, r.combined_odds) == (
        7, "2026-07-19T00:00:00", 2.0, 0.5, 2.0,
    )
    leg = r.legs[0]
    assert (leg.player_id, leg.player_name, leg.game_id, leg.stat_type, leg.side,
            leg.model_prob, leg.odds) == (3, "Aaron Judge", 20, "total_bases", "over", 0.62, 110)
