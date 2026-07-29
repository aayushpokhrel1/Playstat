import inspect

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
