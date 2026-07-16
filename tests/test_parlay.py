"""Unit tests for optimizer/parlay.py's pure combinatorics functions:
american_to_decimal and find_combinations. No DB access — legs are built as
plain dicts matching the keys load_candidate_legs would produce
(player_id, game_id, stat_type, side, model_prob, odds, decimal_odds).

Run with: python -m pytest tests/test_parlay.py -q
"""

import pytest

from optimizer.parlay import american_to_decimal, find_combinations


# --- american_to_decimal ------------------------------------------------------

def test_american_to_decimal_plus_100():
    assert american_to_decimal(100) == pytest.approx(2.0)


def test_american_to_decimal_minus_110():
    assert american_to_decimal(-110) == pytest.approx(1.9091, rel=1e-3)


def test_american_to_decimal_plus_150():
    assert american_to_decimal(150) == pytest.approx(2.5)


def test_american_to_decimal_minus_200():
    assert american_to_decimal(-200) == pytest.approx(1.5)


# --- find_combinations ---------------------------------------------------------

def _leg(player_id, game_id, stat_type, side, model_prob, odds):
    return {
        "player_id": player_id,
        "game_id": game_id,
        "stat_type": stat_type,
        "side": side,
        "model_prob": model_prob,
        "odds": odds,
        "decimal_odds": american_to_decimal(odds),
    }


def test_find_combinations_excludes_same_game_combos():
    legs = [
        _leg(1, 100, "hits", "over", 0.6, -110),
        _leg(2, 100, "runs", "over", 0.6, -110),  # same game_id as leg 1
        _leg(3, 200, "hits", "over", 0.6, -110),
    ]
    # target payout ~ (1.9091)^2 ~= 3.647, generous tolerance to catch any 2-leg combo
    matches = find_combinations(legs, target_payout=3.647, min_legs=2, max_legs=2, tolerance=0.5)
    game_id_sets = [tuple(sorted(leg["game_id"] for leg in m["legs"])) for m in matches]
    for gids in game_id_sets:
        assert len(set(gids)) == len(gids)  # no repeated game_id within a combo
    # specifically: legs 1 & 2 (same game) must never appear together
    for m in matches:
        combo_game_ids = [leg["game_id"] for leg in m["legs"]]
        assert not (100 in combo_game_ids and combo_game_ids.count(100) > 1)


def test_find_combinations_only_within_tolerance_survive():
    # Two legs at decimal 2.0 each -> combined 4.0.
    legs = [
        _leg(1, 100, "hits", "over", 0.5, 100),  # decimal 2.0
        _leg(2, 200, "hits", "over", 0.5, 100),  # decimal 2.0
    ]
    # target 4.0, tight tolerance -> matches
    matches = find_combinations(legs, target_payout=4.0, min_legs=2, max_legs=2, tolerance=0.01)
    assert len(matches) == 1
    assert matches[0]["combined_odds"] == pytest.approx(4.0)

    # target far away, tight tolerance -> no matches
    matches_far = find_combinations(legs, target_payout=10.0, min_legs=2, max_legs=2, tolerance=0.01)
    assert matches_far == []


def test_find_combinations_joint_prob_is_product_of_leg_probs():
    legs = [
        _leg(1, 100, "hits", "over", 0.6, -110),
        _leg(2, 200, "runs", "over", 0.55, -110),
        _leg(3, 300, "rbis", "over", 0.7, -110),
    ]
    matches = find_combinations(legs, target_payout=american_to_decimal(-110) ** 3, min_legs=3, max_legs=3, tolerance=0.5)
    assert len(matches) == 1
    match = matches[0]
    expected_joint = 0.6 * 0.55 * 0.7
    assert match["joint_prob"] == pytest.approx(expected_joint)


def test_find_combinations_combined_odds_is_product_of_decimal_odds():
    legs = [
        _leg(1, 100, "hits", "over", 0.6, 150),   # decimal 2.5
        _leg(2, 200, "runs", "over", 0.55, -200),  # decimal 1.5
    ]
    matches = find_combinations(legs, target_payout=2.5 * 1.5, min_legs=2, max_legs=2, tolerance=0.01)
    assert len(matches) == 1
    assert matches[0]["combined_odds"] == pytest.approx(2.5 * 1.5)


def test_find_combinations_sorted_by_joint_prob_descending():
    legs = [
        _leg(1, 100, "hits", "over", 0.9, -110),
        _leg(2, 200, "runs", "over", 0.9, -110),
        _leg(3, 300, "rbis", "over", 0.1, -110),
        _leg(4, 400, "walks", "over", 0.1, -110),
    ]
    target = american_to_decimal(-110) ** 2
    matches = find_combinations(legs, target_payout=target, min_legs=2, max_legs=2, tolerance=0.01)
    joint_probs = [m["joint_prob"] for m in matches]
    assert joint_probs == sorted(joint_probs, reverse=True)
    # the highest-probability combo should be legs 1 & 2 (both p=0.9)
    top_game_ids = sorted(leg["game_id"] for leg in matches[0]["legs"])
    assert top_game_ids == [100, 200]


def test_find_combinations_respects_min_max_leg_bounds():
    legs = [
        _leg(i, i * 100, "hits", "over", 0.5, 100)  # decimal 2.0 each
        for i in range(1, 6)  # 5 legs, all different games
    ]
    # min_legs=3, max_legs=4 -> only sizes 3 and 4 should ever appear
    matches = find_combinations(legs, target_payout=1e9, min_legs=3, max_legs=4, tolerance=1e12)
    sizes = {len(m["legs"]) for m in matches}
    assert sizes <= {3, 4}
    assert sizes  # sanity: we actually generated some combos

    # min_legs=max_legs=2 -> only size-2 combos
    matches_2 = find_combinations(legs, target_payout=1e9, min_legs=2, max_legs=2, tolerance=1e12)
    sizes_2 = {len(m["legs"]) for m in matches_2}
    assert sizes_2 == {2}


def test_find_combinations_no_legs_returns_empty():
    assert find_combinations([], target_payout=2.0, min_legs=2, max_legs=4, tolerance=0.1) == []
