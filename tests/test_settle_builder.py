import pytest
from modeling.settle import builder_leg_key, settle_leg, parlay_result


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
