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


def test_normalize_coerces_nan_model_prob_to_none():
    """Regression: model_prob arrives as NaN from the LEFT JOIN when no edges row
    exists. Left as NaN, json.dumps emits bare NaN and Postgres rejects the
    insert — this broke every --save until 2026-07-21."""
    import json
    leg = normalize_player_leg({
        "player_id": 1, "game_id": 2, "stat_type": "hits",
        "line_value": 0.5, "over_odds": -300, "under_odds": 240,
        "player_name": "X", "model_prob": float("nan"),
    })
    assert leg["model_prob"] is None
    # Must survive strict JSON encoding, which is what the DB write does.
    json.dumps(leg, allow_nan=False)


def test_normalize_player_leg_keeps_model_prob_optional():
    leg = normalize_player_leg({
        "player_id": 1, "game_id": 2, "stat_type": "hits",
        "line_value": 0.5, "over_odds": -300, "under_odds": 240,
        "player_name": "X", "model_prob": None,
    })
    assert leg["model_prob"] is None


from optimizer.builder_core import build, dedupe_by_price


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


def test_dedupe_keeps_best_probability_at_each_price():
    # Same game, same price: only the more probable leg can matter.
    legs = [_leg(1, 0.60, 1.3), _leg(1, 0.75, 1.3)]
    out = dedupe_by_price(legs)
    assert len(out) == 1
    assert out[0]["market_prob"] == 0.75


def test_dedupe_keeps_distinct_prices_within_a_game():
    legs = [_leg(1, 0.75, 1.3), _leg(1, 0.60, 1.7)]
    assert len(dedupe_by_price(legs)) == 2


def test_dedupe_does_not_merge_across_games():
    legs = [_leg(1, 0.60, 1.3), _leg(2, 0.75, 1.3)]
    assert len(dedupe_by_price(legs)) == 2


def test_build_reaches_high_payout_when_odds_support_it():
    """Regression: a top-N-by-probability cap collapsed the odds ceiling and made
    the 2x target unreachable even though the legs supported ~3x (2026-07-21).
    Also exercises progressive widening: no combo of these legs pays in
    [2.0, 2.3] (the initial ceiling), only the two 1.70x legs together at
    2.89x, so this only succeeds if the search widens past its first pass."""
    # Many cheap near-certain legs plus a few genuinely priced ones.
    legs = [_leg(i, 0.90, 1.05) for i in range(1, 40)]
    legs += [_leg(100, 0.60, 1.70), _leg(101, 0.60, 1.70)]
    out = build(legs, target_payout=2.0, tolerance=0.15)
    assert out, "expected a >=2x construction to be reachable"
    assert all(r["combined_odds"] >= 2.0 for r in out)


def test_build_target_payout_is_a_floor_never_below_it():
    """Task 1 core fix: a pinned target_payout must never return a
    construction cheaper than the target — it is a floor, not the centre of
    a tolerance band."""
    legs = [_leg(1, 0.9, 1.2), _leg(2, 0.9, 1.2), _leg(3, 0.8, 1.5), _leg(4, 0.8, 1.5)]
    out = build(legs, target_payout=1.4, tolerance=0.5, top_n=50)
    assert out
    assert all(r["combined_odds"] >= 1.4 for r in out)


def test_build_target_payout_excludes_below_floor_even_if_safer():
    """The below-floor pair here is *more* probable than the qualifying pair
    (0.95*0.95=.9025 vs 0.6*0.6=.36) but pays only 1.21x against a 2.0x floor
    — it must never appear, no matter how safe it looks."""
    legs = [_leg(1, 0.95, 1.1), _leg(2, 0.95, 1.1),   # payout 1.21x — below floor
            _leg(3, 0.60, 1.5), _leg(4, 0.60, 1.5)]    # payout 2.25x — above floor
    out = build(legs, target_payout=2.0, tolerance=0.3, top_n=50)
    assert out
    assert all(r["combined_odds"] >= 2.0 for r in out)
    assert not any(r["combined_odds"] == pytest.approx(1.21) for r in out)


def test_build_target_payout_ranks_highest_joint_prob_at_or_above_floor():
    """Among qualifying (>= floor) constructions, the top-ranked result must be
    the one with the highest joint_prob, not simply the cheapest payout."""
    legs = [_leg(1, 0.95, 1.5), _leg(2, 0.95, 1.5),   # payout 2.25x, joint .9025
            _leg(3, 0.60, 1.5), _leg(4, 0.60, 1.5)]    # payout 2.25x, joint .36
    out = build(legs, target_payout=2.0, tolerance=0.3, top_n=50)
    assert out
    assert out[0]["joint_prob"] == pytest.approx(0.9025)
    assert out[0]["joint_prob"] == max(r["joint_prob"] for r in out)


def test_build_progressive_widening_finds_result_beyond_unbounded_pass():
    """No combo pays within any of the bounded ceilings (target*1.1, *1.5,
    *3.0) — only the fully unbounded final pass can find the one qualifying
    (9.0x) construction. Exercises every widening step, including the
    fallback to hi=None."""
    legs = [_leg(1, 0.5, 3.0), _leg(2, 0.5, 3.0)]
    stats = {}
    out = build(legs, target_payout=2.0, tolerance=0.1, stats=stats)
    assert out
    assert out[0]["combined_odds"] == pytest.approx(9.0)
    assert stats["matches"] == 1
    assert stats["truncated"] is False


def test_build_prefers_fewest_legs_at_a_given_payout():
    # A 2-leg route and a 3-leg route to the same payout: fewer legs wins on
    # joint probability, so it must rank first.
    legs = [_leg(1, 0.70, 1.45), _leg(2, 0.70, 1.45),
            _leg(3, 0.80, 1.28), _leg(4, 0.80, 1.28), _leg(5, 0.80, 1.28)]
    out = build(legs, target_payout=2.1, tolerance=0.15)
    assert out
    assert out[0]["n_legs"] == 2


def test_build_respects_node_budget_and_reports_truncation():
    # This scenario deliberately DEFEATS the heap-aware prune so the MAX_NODES
    # guard is what stops the search. top_n is set absurdly high: the heap can
    # never accumulate top_n entries within the 500-node budget, so the
    # heap-full precondition of the prune never holds and no subtree is ever
    # short-circuited. (Distinct odds also keep the top-N boundary tie-free.)
    # Contrast the common case, where a full heap lets the prune finish early
    # well under budget — there the guard is never reached.
    legs = [_leg(i, 0.9, 1.1 + i * 0.001) for i in range(1, 60)]
    stats = {}
    build(legs, min_prob=0.0, max_legs=4, max_nodes=500, top_n=100000, stats=stats)
    assert stats["truncated"] is True
    assert stats["nodes"] <= 500 + 1


def test_build_stats_reports_candidate_games():
    legs = [_leg(1, 0.8, 1.25), _leg(2, 0.8, 1.25), _leg(3, 0.8, 1.25)]
    stats = {}
    build(legs, target_payout=1.5625, tolerance=0.01, stats=stats)
    assert stats["candidate_games"] == 3
    assert stats["truncated"] is False


def test_build_never_returns_two_legs_from_one_game():
    legs = [_leg(1, 0.8, 1.25), _leg(1, 0.8, 1.30), _leg(2, 0.8, 1.25),
            _leg(2, 0.8, 1.30), _leg(3, 0.8, 1.25)]
    for r in build(legs, min_prob=0.0, max_legs=3, top_n=50):
        ids = [leg["game_id"] for leg in r["legs"]]
        assert len(set(ids)) == len(ids)
