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


from optimizer.builder_core import build, cap_candidates, MAX_COMBOS


def _leg(game_id, prob, dec_odds, kind="player"):
    return {
        "game_id": game_id, "kind": kind, "label": f"g{game_id}",
        "player_id": 1 if kind == "player" else None,
        "stat_type": "hits" if kind == "player" else None,
        "market": None if kind == "player" else "f5_runs",
        "side": "over", "line_value": 0.5, "american_odds": -150,
        "decimal_odds": dec_odds, "market_prob": prob, "model_prob": None,
    }


def test_build_excludes_same_game_combos():
    legs = [_leg(1, 0.7, 1.4), _leg(1, 0.7, 1.4)]
    assert build(legs, target_payout=1.96, tolerance=0.5) == []


def test_build_joint_prob_is_product_and_odds_is_product():
    legs = [_leg(1, 0.8, 1.25), _leg(2, 0.8, 1.25)]
    out = build(legs, target_payout=1.5625, tolerance=0.01)
    assert len(out) == 1
    assert out[0]["joint_prob"] == pytest.approx(0.64)
    assert out[0]["combined_odds"] == pytest.approx(1.5625)
    assert out[0]["n_legs"] == 2


def test_build_pin_payout_ranks_by_joint_prob_desc():
    legs = [_leg(1, 0.9, 1.25), _leg(2, 0.9, 1.25), _leg(3, 0.5, 1.25), _leg(4, 0.5, 1.25)]
    out = build(legs, target_payout=1.5625, tolerance=0.01)
    probs = [r["joint_prob"] for r in out]
    assert probs == sorted(probs, reverse=True)
    assert probs[0] == pytest.approx(0.81)


def test_build_pin_min_prob_ranks_by_payout_desc():
    legs = [_leg(1, 0.9, 1.2), _leg(2, 0.9, 1.2), _leg(3, 0.8, 2.0), _leg(4, 0.8, 2.0)]
    out = build(legs, min_prob=0.6)
    assert all(r["joint_prob"] >= 0.6 for r in out)
    odds = [r["combined_odds"] for r in out]
    assert odds == sorted(odds, reverse=True)


def test_build_respects_leg_bounds():
    legs = [_leg(i, 0.9, 1.1) for i in range(1, 7)]
    out = build(legs, min_prob=0.0, min_legs=2, max_legs=3)
    assert out
    assert all(2 <= r["n_legs"] <= 3 for r in out)


def test_build_both_axes_pinned_filters_both():
    legs = [_leg(1, 0.9, 1.25), _leg(2, 0.9, 1.25), _leg(3, 0.5, 1.25), _leg(4, 0.5, 1.25)]
    out = build(legs, target_payout=1.5625, tolerance=0.01, min_prob=0.7)
    assert len(out) == 1
    assert out[0]["joint_prob"] == pytest.approx(0.81)


def test_build_respects_top_n():
    legs = [_leg(i, 0.9, 1.25) for i in range(1, 8)]
    out = build(legs, target_payout=1.5625, tolerance=0.01, top_n=3)
    assert len(out) == 3


def test_build_no_legs_returns_empty():
    assert build([], target_payout=2.0) == []


def test_cap_candidates_bounds_combination_count():
    legs = [_leg(i, 0.5 + (i % 50) / 200, 1.3) for i in range(1, 501)]
    capped = cap_candidates(legs, max_legs=4, max_combos=100_000)
    from math import comb
    assert comb(len(capped), 4) <= 100_000
    assert len(capped) < len(legs)


def test_cap_candidates_keeps_highest_market_prob():
    legs = [_leg(1, 0.60, 1.3), _leg(2, 0.90, 1.3), _leg(3, 0.70, 1.3)]
    capped = cap_candidates(legs, max_legs=2, max_combos=1)
    assert capped[0]["market_prob"] == 0.90


def test_cap_candidates_noop_when_already_small():
    legs = [_leg(1, 0.6, 1.3), _leg(2, 0.6, 1.3)]
    assert len(cap_candidates(legs, max_legs=2, max_combos=MAX_COMBOS)) == 2
