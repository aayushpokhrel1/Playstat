import pytest
from optimizer.builder_core import (
    favorite_side, normalize_player_leg, normalize_team_leg,
    passes_floor, DEFAULT_FLOOR,
)


def test_favorite_side_picks_higher_devigged_side():
    # -200 over / +170 under: over is the favorite
    side, prob = favorite_side(-200, 170)
    assert side == "over"
    assert 0.5 < prob < 1.0


def test_favorite_side_picks_under_when_under_is_favorite():
    side, prob = favorite_side(170, -200)
    assert side == "under"
    assert 0.5 < prob < 1.0


def test_favorite_side_devigs_so_probability_is_below_raw_implied():
    # raw implied for -200 is 0.6667; devigged must be lower (vig removed)
    _, prob = favorite_side(-200, 170)
    assert prob < 200 / 300


def test_favorite_side_even_market_is_half():
    side, prob = favorite_side(100, -100)
    assert prob == pytest.approx(0.5, abs=0.02)


def test_passes_floor_rejects_below_floor():
    assert not passes_floor({"market_prob": 0.54}, 0.55)
    assert passes_floor({"market_prob": 0.55}, 0.55)
    assert passes_floor({"market_prob": 0.80}, 0.55)


def test_default_floor_is_055():
    assert DEFAULT_FLOOR == 0.55


def test_normalize_player_leg_shape():
    leg = normalize_player_leg({
        "player_id": 7, "game_id": 100, "stat_type": "total_bases",
        "line_value": 1.5, "over_odds": -200, "under_odds": 170,
        "player_name": "Judge", "model_prob": 0.61,
    })
    assert leg["kind"] == "player"
    assert leg["game_id"] == 100
    assert leg["player_id"] == 7
    assert leg["stat_type"] == "total_bases"
    assert leg["market"] is None
    assert leg["side"] == "over"
    assert leg["decimal_odds"] == pytest.approx(1.5)
    assert 0.5 < leg["market_prob"] < 1.0
    assert leg["model_prob"] == 0.61
    assert "Judge" in leg["label"]


def test_normalize_team_leg_shape():
    leg = normalize_team_leg({
        "game_id": 200, "market": "first_inning_runs",
        "line_value": 0.5, "over_odds": 150, "under_odds": -180,
        "model_prob": None,
    })
    assert leg["kind"] == "team"
    assert leg["player_id"] is None
    assert leg["stat_type"] is None
    assert leg["market"] == "first_inning_runs"
    assert leg["side"] == "under"
    assert leg["model_prob"] is None


def test_normalize_player_leg_keeps_model_prob_optional():
    leg = normalize_player_leg({
        "player_id": 1, "game_id": 2, "stat_type": "hits",
        "line_value": 0.5, "over_odds": -300, "under_odds": 240,
        "player_name": "X", "model_prob": None,
    })
    assert leg["model_prob"] is None
