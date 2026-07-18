import pytest
from optimizer.team_parlay import recommendation_ev, same_game_pairs


def test_ev_positive_when_model_beats_price():
    # joint 0.55 at combined decimal 2.0 -> 0.55*2 - 1 = 0.10
    assert recommendation_ev(0.55, 2.0) == pytest.approx(0.10)


def test_ev_negative_when_price_beats_model():
    assert recommendation_ev(0.45, 2.0) == pytest.approx(-0.10)


def test_same_game_pairs_uses_lift_not_naive_product():
    legs = [
        {"game_id": 1, "market": "first_inning_runs", "side": "under", "model_prob": 0.7, "decimal_odds": 1.4},
        {"game_id": 1, "market": "f5_runs", "side": "under", "model_prob": 0.55, "decimal_odds": 1.5},
    ]
    # lift 1.3 -> joint = 0.7*0.55*1.3 = 0.5005, NOT the naive 0.385
    out = same_game_pairs(legs, lift_fn=lambda sn, sf: (1.3, 500), target_payout=2.0, tolerance=0.15)
    assert len(out) == 1
    assert out[0]["joint_prob"] == pytest.approx(0.5005)
    assert out[0]["class"] == "same_game_pair"


def test_same_game_pairs_excludes_cross_game():
    legs = [
        {"game_id": 1, "market": "first_inning_runs", "side": "under", "model_prob": 0.7, "decimal_odds": 1.4},
        {"game_id": 2, "market": "f5_runs", "side": "under", "model_prob": 0.55, "decimal_odds": 1.5},
    ]
    assert same_game_pairs(legs, lift_fn=lambda sn, sf: (1.3, 500), target_payout=2.0, tolerance=0.15) == []
