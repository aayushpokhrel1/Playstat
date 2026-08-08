import pytest
from optimizer.builder_core import (
    favorite_side, normalize_player_leg, normalize_team_leg,
    passes_floor, DEFAULT_FLOOR, MARKET_GEOMETRY, is_home_away_market,
)


def test_market_geometry():
    assert MARKET_GEOMETRY["first_inning_runs"] == "ou"
    assert MARKET_GEOMETRY["full_game_total"] == "ou"
    assert MARKET_GEOMETRY["full_game_spread"] == "homeaway"
    assert MARKET_GEOMETRY["full_game_moneyline"] == "homeaway"
    assert is_home_away_market("full_game_moneyline") and not is_home_away_market("full_game_total")


def test_normalize_team_leg_ou_unchanged():
    # -200/+170 favors OVER; existing behavior
    leg = normalize_team_leg({"game_id": 1, "market": "full_game_total", "line_value": 44.5,
                              "over_odds": -200, "under_odds": 170, "home_odds": None,
                              "away_odds": None, "model_prob": None})
    assert leg["kind"] == "team" and leg["side"] == "over" and leg["market_prob"] > 0.6


def test_normalize_team_leg_moneyline_home_favorite_null_line():
    # home -250 vs away +200 -> home favorite, no line
    leg = normalize_team_leg({"game_id": 5, "market": "full_game_moneyline", "line_value": None,
                              "over_odds": None, "under_odds": None, "home_odds": -250,
                              "away_odds": 200, "model_prob": None})
    assert leg["side"] == "home" and leg["market"] == "full_game_moneyline"
    assert leg["line_value"] is None and leg["american_odds"] == -250
    assert leg["market_prob"] > 0.55 and "moneyline" in leg["label"]


def test_normalize_team_leg_spread_away_favorite_uses_home_line():
    # home +130 / away -150 -> away favorite; line stored is the HOME spread
    leg = normalize_team_leg({"game_id": 7, "market": "full_game_spread", "line_value": 3.5,
                              "over_odds": None, "under_odds": None, "home_odds": 130,
                              "away_odds": -150, "model_prob": None})
    assert leg["side"] == "away" and leg["american_odds"] == -150 and leg["line_value"] == 3.5


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


# --- builder independence: diverse top-N (docs/superpowers/specs/
# 2026-07-29-builder-independence-design.md) -----------------------------

from optimizer.builder_core import build, entity_of, select_diverse


def _con(legs, jp):
    return {"legs": legs, "joint_prob": jp, "combined_odds": 1.0 + jp}


def _pl(pid, gid): return {"kind": "player", "player_id": pid, "game_id": gid}
def _tm(gid):      return {"kind": "team", "player_id": None, "game_id": gid}


def test_entity_of_player_vs_team():
    assert entity_of(_pl(500, 9)) == 500
    assert entity_of(_tm(9)) == 9


def test_select_diverse_caps_player_reuse():
    # player 1 in every construction; cap m=2 -> at most 2 selected use player 1
    results = [_con([_pl(1, g), _pl(2 + i, g + 100)], 0.9 - i * 0.01)
               for i, g in enumerate([10, 20, 30, 40, 50])]
    out = select_diverse(results, n=5, max_uses=2)
    used = sum(1 for c in out if any(entity_of(l) == 1 for l in c["legs"]))
    assert used == 2
    assert out[0] is results[0]         # rank-1 always kept


def test_select_diverse_m1_is_strict_disjoint():
    results = [_con([_pl(1, 10), _pl(2, 20)], 0.9),
               _con([_pl(1, 30), _pl(3, 40)], 0.8),   # reuses player 1 -> excluded
               _con([_pl(4, 50), _pl(5, 60)], 0.7)]
    out = select_diverse(results, n=5, max_uses=1)
    assert [c["joint_prob"] for c in out] == [0.9, 0.7]


def test_select_diverse_team_legs_key_on_game():
    results = [_con([_tm(10)], 0.9), _con([_tm(10)], 0.8), _con([_tm(11)], 0.7)]
    out = select_diverse(results, n=5, max_uses=1)
    assert [c["joint_prob"] for c in out] == [0.9, 0.7]


def test_select_diverse_returns_fewer_than_n_gracefully():
    results = [_con([_pl(1, 10)], 0.9), _con([_pl(1, 20)], 0.8)]
    assert len(select_diverse(results, n=5, max_uses=1)) == 1


def _leg(gid, pid, prob, odds):
    return {"game_id": gid, "player_id": pid, "kind": "player", "stat_type": "x",
            "side": "over", "line_value": 0.5, "american_odds": odds,
            "decimal_odds": 1 + prob, "market_prob": prob, "model_prob": None, "market": None}


def test_build_max_uses_none_matches_today():
    legs = [_leg(g, g, 0.8, -150) for g in range(1, 6)]
    a = build(legs, target_payout=1.4, top_n=5)
    b = build(legs, target_payout=1.4, top_n=5, max_uses=None)
    assert [c["joint_prob"] for c in a] == [c["joint_prob"] for c in b]


def test_build_cap_reduces_player_reuse():
    # a dominant favourite (player 99) appears in many top constructions
    legs = [_leg(1, 99, 0.95, -400)] + [_leg(g, g, 0.75, -120) for g in range(2, 8)]
    capped = build(legs, target_payout=1.4, top_n=5, max_uses=2)
    uses99 = sum(1 for c in capped for l in c["legs"] if l["player_id"] == 99)
    assert uses99 <= 2


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


def test_player_leg_uses_best_price_for_chosen_side_prob_stays_consensus():
    # consensus -200/+170 -> favorite OVER, market_prob from consensus devig.
    # best over price is -150 at fanduel -> payout uses -150, prob unchanged.
    consensus = normalize_player_leg({
        "game_id": 1, "player_id": 9, "stat_type": "hits", "line_value": 0.5,
        "player_name": "P", "over_odds": -200, "under_odds": 170,
        "best_over_odds": None, "best_over_book": None,
        "best_under_odds": None, "best_under_book": None, "model_prob": None,
    })
    shopped = normalize_player_leg({
        "game_id": 1, "player_id": 9, "stat_type": "hits", "line_value": 0.5,
        "player_name": "P", "over_odds": -200, "under_odds": 170,
        "best_over_odds": -150, "best_over_book": "fanduel",
        "best_under_odds": None, "best_under_book": None, "model_prob": None,
    })
    assert shopped["side"] == "over" == consensus["side"]
    assert shopped["market_prob"] == consensus["market_prob"]        # ranking unchanged
    assert shopped["american_odds"] == -150 and shopped["book"] == "fanduel"
    assert shopped["decimal_odds"] > consensus["decimal_odds"]       # bigger payout
    assert consensus["book"] is None                                 # fallback


def test_player_leg_falls_back_to_consensus_when_no_best_for_chosen_side():
    # favorite is UNDER (+? ); only best_over present -> under uses consensus.
    leg = normalize_player_leg({
        "game_id": 2, "player_id": 3, "stat_type": "hits", "line_value": 0.5,
        "player_name": "Q", "over_odds": 170, "under_odds": -200,
        "best_over_odds": -150, "best_over_book": "dk",   # wrong side, ignored
        "best_under_odds": None, "best_under_book": None, "model_prob": None,
    })
    assert leg["side"] == "under" and leg["american_odds"] == -200 and leg["book"] is None


def test_team_ou_leg_uses_best_price():
    leg = normalize_team_leg({
        "game_id": 4, "market": "first_inning_runs", "line_value": 0.5,
        "over_odds": None, "under_odds": None, "home_odds": None, "away_odds": None,
        # NRFI is under-favored here:
        "over_odds": 150, "under_odds": -180,
        "best_over_odds": None, "best_over_book": None,
        "best_under_odds": -160, "best_under_book": "betmgm", "model_prob": None,
    })
    assert leg["side"] == "under" and leg["american_odds"] == -160 and leg["book"] == "betmgm"


def test_team_homeaway_leg_has_book_none_in_v1():
    leg = normalize_team_leg({
        "game_id": 5, "market": "full_game_moneyline", "line_value": None,
        "over_odds": None, "under_odds": None, "home_odds": -250, "away_odds": 200,
        "model_prob": None,
    })
    assert leg["side"] == "home" and leg["american_odds"] == -250 and leg["book"] is None


from optimizer.builder_core import same_game_pairs


def _tleg(game_id, market, side, prob, dec, line=0.5):
    return {
        "game_id": game_id, "market": market, "side": side, "market_prob": prob,
        "decimal_odds": dec, "american_odds": -120, "line_value": line,
        "label": f"{market} {side} {line}", "book": None, "kind": "team",
    }


def _lift_fn_stub(lift=1.30, n=2000, both=1000):
    return lambda sn, sf, nl, fl: (lift, n, both)


def test_same_game_pairs_one_card_per_game_with_both_markets():
    legs = [
        _tleg(1, "first_inning_runs", "under", 0.56, 1.8, 0.5),
        _tleg(1, "f5_runs", "under", 0.57, 1.9, 4.5),
        _tleg(2, "first_inning_runs", "under", 0.55, 1.7, 0.5),  # game 2 missing f5
    ]
    cards = same_game_pairs(legs, _lift_fn_stub(), top_n=10)
    assert len(cards) == 1
    c = cards[0]
    assert c["n_legs"] == 2
    assert c["combined_odds"] == 1.8 * 1.9
    assert c["lift"] == 1.30 and c["lift_n"] == 2000 and c["both_n"] == 1000
    # joint = 0.56*0.57*1.30 clamped to <= min(0.56,0.57)
    assert abs(c["joint_prob"] - 0.56 * 0.57 * 1.30) < 1e-9
    assert c["small_sample"] is False


def test_same_game_pairs_gates_low_sample():
    legs = [
        _tleg(1, "first_inning_runs", "under", 0.56, 1.8),
        _tleg(1, "f5_runs", "under", 0.57, 1.9, 4.5),
    ]
    # both_n below floor -> dropped entirely
    assert same_game_pairs(legs, _lift_fn_stub(n=1000, both=40), min_both=50) == []
    # n_games below floor -> dropped
    assert same_game_pairs(legs, _lift_fn_stub(n=400, both=300), min_games=500) == []


def test_same_game_pairs_flags_small_sample_but_still_shows():
    legs = [
        _tleg(1, "first_inning_runs", "under", 0.56, 1.8),
        _tleg(1, "f5_runs", "under", 0.57, 1.9, 4.5),
    ]
    cards = same_game_pairs(legs, _lift_fn_stub(n=1500, both=700), warn_below=2000)
    assert len(cards) == 1 and cards[0]["small_sample"] is True


def test_same_game_pairs_ranks_by_joint_and_caps_top_n():
    legs = []
    for g, (p, dec) in enumerate([(0.56, 1.8), (0.60, 1.8), (0.58, 1.8)], start=1):
        legs.append(_tleg(g, "first_inning_runs", "under", p, dec))
        legs.append(_tleg(g, "f5_runs", "under", p, dec, 4.5))
    cards = same_game_pairs(legs, _lift_fn_stub(), top_n=2)
    assert len(cards) == 2
    # highest joint first: game 2 (0.60) then game 3 (0.58)
    assert cards[0]["legs"][0]["game_id"] == 2
    assert cards[1]["legs"][0]["game_id"] == 3


def test_same_game_pairs_empty_input():
    assert same_game_pairs([], _lift_fn_stub()) == []
