import inspect
from datetime import datetime

import pandas as pd
import pytest
from modeling.settle import (
    builder_leg_key, leg_status, parlay_result, settle_builder_parlays,
    settle_leg, settle_parlays, settle_team_parlays,
)


def test_builder_leg_key_player():
    leg = {"kind": "player", "game_id": 10, "player_id": 3, "stat_type": "hits", "market": None}
    assert builder_leg_key(leg) == ("player", 3, 10, "hits")


def test_builder_leg_key_team():
    leg = {"kind": "team", "game_id": 11, "player_id": None, "stat_type": None,
           "market": "f5_runs"}
    assert builder_leg_key(leg) == ("team", 11, "f5_runs")


def test_builder_leg_key_rejects_unknown_kind():
    with pytest.raises(ValueError):
        builder_leg_key({"kind": "spaceship", "game_id": 1})


def test_mixed_parlay_all_hit_wins():
    results = [settle_leg("over", 2.0, 1.5), settle_leg("under", 0.0, 0.5)]
    assert results == ["hit", "hit"]
    result, odds, pnl = parlay_result(results, [1.5, 1.4])
    assert result == "win"
    assert odds == pytest.approx(2.1)
    assert pnl == pytest.approx(1.1)


def test_mixed_parlay_one_miss_loses():
    results = [settle_leg("over", 2.0, 1.5), settle_leg("under", 3.0, 0.5)]
    assert results == ["hit", "miss"]
    result, _, pnl = parlay_result(results, [1.5, 1.4])
    assert result == "loss"
    assert pnl == pytest.approx(-1.0)


def test_as_legs_list_passes_through_parsed_jsonb():
    """psycopg2 returns JSONB already parsed. The team/builder wrapper arrives as
    a dict, which json.loads cannot take — this crashed settlement the first time
    real builder rows existed (2026-07-21)."""
    from modeling.settle import _as_legs_list
    wrapper = {"class": "across_game", "legs": [{"kind": "player"}]}
    assert _as_legs_list(wrapper) is wrapper
    assert _as_legs_list([{"kind": "team"}]) == [{"kind": "team"}]
    assert _as_legs_list('[{"kind": "player"}]') == [{"kind": "player"}]


# --- DNP void handling (README §15.10 KNOWN ISSUE / §15.9 item 6) -----------
# A scratched/DNP player (or a team-market game with no team_game_stats
# aggregate) leaves a game FT with no stat row. Standard book rule: void the
# leg like a push instead of stranding the whole parlay "not ready" forever.

def test_leg_status_pending_when_game_not_ft():
    assert leg_status("S", None) == "pending"
    assert leg_status("S", 3.0) == "pending"  # game state always wins first


def test_leg_status_void_when_ft_and_no_stat_row():
    assert leg_status("FT", None) == "void"
    assert leg_status("FT", float("nan")) == "void"


def test_leg_status_ready_when_ft_and_stat_present():
    assert leg_status("FT", 0.0) == "ready"  # 0 is a real value, not missing
    assert leg_status("FT", 3.0) == "ready"


def test_parlay_result_hit_plus_void_settles_as_the_hit_legs_odds():
    """A void leg is dropped exactly like a pushed leg — parlay_result already
    filters to hit/miss, so 'void' needs no special-casing there (verified here)."""
    result, odds, pnl = parlay_result(["hit", "void"], [1.5, 1.4])
    assert result == "win"
    assert odds == pytest.approx(1.5)  # recomputed over the hit leg only
    assert pnl == pytest.approx(0.5)


def test_parlay_result_all_void_is_a_no_action_push():
    result, odds, pnl = parlay_result(["void", "void"], [1.5, 1.4])
    assert result == "push"
    assert odds == pytest.approx(1.0)
    assert pnl == pytest.approx(0.0)


def test_parlay_result_miss_plus_void_is_a_loss():
    result, _, pnl = parlay_result(["miss", "void"], [1.5, 1.4])
    assert result == "loss"
    assert pnl == pytest.approx(-1.0)


# --- regression guards: all three settle_* paths share the void rule --------
# (README §15.10: "Both gaps are shared by the settle_parlays/settle_team_parlays
# paths.") No live DB is available to exercise these end to end (ingestion.db.
# get_engine() points at production), so this asserts the void branch is wired
# into each function's source rather than running it.

@pytest.mark.parametrize("fn", [settle_parlays, settle_team_parlays, settle_builder_parlays])
def test_settle_function_voids_dnp_legs_via_leg_status(fn):
    source = inspect.getsource(fn)
    assert "leg_status(" in source
    assert '"void"' in source
    assert '"dnp": True' in source


# --- NFL player legs settle through the same sport-agnostic path (tier #2) ---
# settle_builder_parlays looks up player_game_stats[(player_id, game_id,
# stat_type)] and scores over/under vs the line -- nothing MLB-specific. These
# assert the pure scoring path handles NFL stat_types/lines identically.

def test_builder_leg_key_handles_an_nfl_player_leg():
    leg = {"kind": "player", "game_id": 200000123, "player_id": 200000045,
           "stat_type": "passing_yards", "market": None}
    assert builder_leg_key(leg) == ("player", 200000045, 200000123, "passing_yards")


def test_nfl_passing_yards_over_scores_by_the_same_rule():
    # 290 passing yards clears an over 274.5; 12 rushing yards clears an under 45.5
    assert settle_leg("over", 290.0, 274.5) == "hit"
    assert settle_leg("under", 12.0, 45.5) == "hit"
    assert settle_leg("over", 250.0, 274.5) == "miss"


def test_nfl_two_leg_parlay_all_hit_wins():
    results = [settle_leg("over", 290.0, 274.5), settle_leg("under", 3.0, 6.5)]
    assert results == ["hit", "hit"]
    result, _, pnl = parlay_result(results, [1.8, 1.9])
    assert result == "win"
    assert pnl == pytest.approx(1.8 * 1.9 - 1.0)


def test_nfl_dnp_receiver_leg_voids_like_any_other():
    # A receiver who didn't play -> FT game, no stat row -> void (dropped).
    assert leg_status("FT", None) == "void"


# --- NFL game-market end-to-end settlement (#3) -------------------------------
# CRITICAL SAFETY: ingestion.db.get_engine() is LIVE (production). This is a
# pure/fake-engine test: no real socket. conn.execute() is faked for the
# candidates SELECT + the recommendation_outcomes INSERT (dispatched on SQL
# substring, mirroring test_parlay_recommendations_api.py's _FakeEngine); every
# pd.read_sql() call is faked by matching a substring of the query text against
# a dict of canned DataFrames, since settle_builder_parlays makes several
# distinct read_sql calls per run.

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, candidate_rows, insert_calls):
        self._candidate_rows = candidate_rows
        self._insert_calls = insert_calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM parlay_recommendations" in sql:
            return _FakeResult(self._candidate_rows)
        if "INSERT INTO recommendation_outcomes" in sql:
            self._insert_calls.append(params)
            return None
        raise AssertionError(f"unexpected conn.execute in fake: {sql}")


class _FakeEngine:
    """Records begin() calls; hands back a _FakeConn wired to the same
    candidate rows / insert-call list every time (settle_builder_parlays opens
    two separate `with engine.begin()` blocks: one to read, one to write)."""

    def __init__(self, candidate_rows):
        self.insert_calls = []
        self._candidate_rows = candidate_rows

    def begin(self):
        return _FakeConn(self._candidate_rows, self.insert_calls)


def _fake_read_sql(dataframes):
    """Returns a pd.read_sql replacement that matches the query text against
    `dataframes`' keys (substrings) and returns the first matching DataFrame."""
    def _read_sql(stmt, conn, params=None):
        sql = str(stmt)
        for marker, df in dataframes.items():
            if marker in sql:
                return df
        raise AssertionError(f"unexpected pd.read_sql in fake: {sql}")
    return _read_sql


def test_nfl_game_market_parlay_settles(monkeypatch):
    """A 3-leg NFL game-market builder parlay (moneyline, full-game total,
    spread) settles end-to-end against final scores in team_game_stats
    ('points') via the fake-engine queue pattern (no live DB)."""
    created_at = datetime(2026, 9, 10, 12, 0, 0)

    legs = [
        {"kind": "team", "game_id": 111, "market": "full_game_moneyline", "side": "home",
         "line": None, "odds": -160, "player_id": None, "stat_type": None},
        {"kind": "team", "game_id": 222, "market": "full_game_total", "side": "over",
         "line": 44.5, "odds": -110, "player_id": None, "stat_type": None},
        {"kind": "team", "game_id": 333, "market": "full_game_spread", "side": "home",
         "line": -3.5, "odds": -110, "player_id": None, "stat_type": None},
    ]
    # game 111: home 27 away 17 -> home ML wins (no line lookup needed).
    # game 222: total 30+20=50 > 44.5 -> over wins (existing SUM/over-under path).
    # game 333: home 27 away 17, home line -3.5 -> margin 10 covers -> home wins.
    blob = {"class": "game_tier", "sport": "nfl", "legs": legs}
    candidate_rows = [(1, created_at, blob)]

    games_df = pd.DataFrame([
        {"game_id": 111, "status": "FT"},
        {"game_id": 222, "status": "FT"},
        {"game_id": 333, "status": "FT"},
    ])
    ghome_df = pd.DataFrame([
        {"game_id": 111, "home_team_id": 10, "away_team_id": 20},
        {"game_id": 222, "home_team_id": 30, "away_team_id": 40},
        {"game_id": 333, "home_team_id": 50, "away_team_id": 60},
    ])
    tpoints_df = pd.DataFrame([
        {"game_id": 111, "team_id": 10, "value": 27.0},
        {"game_id": 111, "team_id": 20, "value": 17.0},
        {"game_id": 222, "team_id": 30, "value": 30.0},
        {"game_id": 222, "team_id": 40, "value": 20.0},
        {"game_id": 333, "team_id": 50, "value": 27.0},
        {"game_id": 333, "team_id": 60, "value": 17.0},
    ])
    tstats_df = pd.DataFrame([
        {"game_id": 222, "stat_type": "points", "total": 50.0},
    ])
    pstats_df = pd.DataFrame(columns=["player_id", "game_id", "stat_type", "value"])
    plines_df = pd.DataFrame(columns=["player_id", "game_id", "stat_type", "line_value", "pulled_at"])
    glines_df = pd.DataFrame([
        {"game_id": 222, "market": "full_game_total", "line_value": 44.5, "pulled_at": created_at},
        {"game_id": 333, "market": "full_game_spread", "line_value": -3.5, "pulled_at": created_at},
    ])

    dataframes = {
        "home_team_id, away_team_id FROM games": ghome_df,
        "SELECT game_id, status FROM games": games_df,
        "stat_type = 'points'": tpoints_df,
        "SUM(value) AS total": tstats_df,
        "FROM player_game_stats": pstats_df,
        "FROM prop_lines": plines_df,
        "FROM game_lines": glines_df,
    }
    monkeypatch.setattr("modeling.settle.pd.read_sql", _fake_read_sql(dataframes))

    engine = _FakeEngine(candidate_rows)
    inserted = settle_builder_parlays(engine)

    assert inserted == 1
    assert len(engine.insert_calls) == 1
    params = engine.insert_calls[0]
    assert params["res"] == "win"
    assert params["pnl"] > 0
    assert params["n"] == 3

    audit = params["legs"]
    import json
    audit = json.loads(audit) if isinstance(audit, str) else audit
    by_market = {a["market"]: a for a in audit}
    # Moneyline settled with NO line lookup -- no "line" key in its audit row.
    assert "line" not in by_market["full_game_moneyline"]
    assert by_market["full_game_moneyline"]["result"] == "won"
    assert by_market["full_game_spread"]["result"] == "won"
    assert by_market["full_game_total"]["result"] == "hit"  # existing settle_leg vocabulary
