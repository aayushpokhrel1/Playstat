# tests/test_builder_search_exactness.py
import itertools
import random
from optimizer import builder_core


def _leg(game_id, decimal_odds, market_prob, i):
    return {
        "game_id": game_id, "kind": "player", "label": f"L{i}",
        "side": "under", "decimal_odds": decimal_odds,
        "american_odds": -100, "market_prob": market_prob,
        "model_prob": None, "line_value": 0.5, "player_id": i,
        "stat_type": "hits", "market": None,
    }


def brute_force_build(legs, target_payout=None, min_prob=None,
                      min_legs=2, max_legs=4, top_n=10):
    """PURE global brute force — the ground truth for build()'s exactness.

    Enumerates EVERY across-game construction of size min_legs..max_legs
    (same-game combinations excluded, matching build()'s structural rule),
    filters by the pinned floor, ranks by the pinned axis, and takes top_n.

    This makes NO assumption about odds/prob correlation. It is the definition
    of the correct answer on every slate, including the adversarial
    independent-draw slates where the old progressive-widening search diverged.

    - target_payout pinned -> keep combined_odds >= target_payout, rank by
      joint_prob (highest first).
    - min_prob pinned       -> keep joint_prob >= min_prob, rank by combined_odds
      (highest first).
    """
    rank_by_payout = target_payout is None and min_prob is not None

    qualifying = []
    for size in range(min_legs, max_legs + 1):
        for combo in itertools.combinations(legs, size):
            game_ids = [l["game_id"] for l in combo]
            if len(set(game_ids)) != len(game_ids):
                continue  # same-game excluded
            odds = 1.0
            prob = 1.0
            for l in combo:
                odds *= l["decimal_odds"]
                prob *= l["market_prob"]
            if target_payout is not None and odds < target_payout:
                continue
            if min_prob is not None and prob < min_prob:
                continue
            qualifying.append({"combined_odds": odds, "joint_prob": prob, "n_legs": size})

    key = "combined_odds" if rank_by_payout else "joint_prob"
    qualifying.sort(key=lambda r: r[key], reverse=True)
    return qualifying[:top_n]


def search_key_seq(results, rank_by_payout):
    key = "combined_odds" if rank_by_payout else "joint_prob"
    return [round(r[key], 12) for r in results]


def _random_slate(rng, n_games, legs_per_game):
    legs, i = [], 0
    for g in range(n_games):
        for _ in range(legs_per_game):
            # distinct odds/probs keep the top-N boundary tie-free; odds and
            # prob are drawn INDEPENDENTLY, so vig varies leg-to-leg and a
            # higher-payout construction can carry a higher joint_prob.
            odds = round(1.0 + rng.random() * 1.5 + i * 1e-4, 6)
            prob = round(min(0.99, 0.55 + rng.random() * 0.44 - i * 1e-5), 6)
            legs.append(_leg(g, odds, prob, i))
            i += 1
    return legs


def test_build_matches_pure_brute_force_including_independent_draws():
    rng = random.Random(99)
    for _ in range(60):
        legs = _random_slate(rng, n_games=rng.randint(3, 6), legs_per_game=rng.randint(1, 4))
        for params in ({"target_payout": 1.6}, {"target_payout": 2.0}, {"target_payout": 1.3},
                       {"min_prob": 0.5}, {"min_prob": 0.75}):
            top_n = rng.choice([1, 3, 5])
            rank_by_payout = params.get("target_payout") is None
            got = builder_core.build(legs, top_n=top_n, **params)
            exp = brute_force_build(legs, top_n=top_n, **params)
            assert search_key_seq(got, rank_by_payout) == search_key_seq(exp, rank_by_payout), (params, top_n)


def _big_slate():
    rng = random.Random(1)
    return _random_slate(rng, n_games=19, legs_per_game=128)


def test_baseline_node_counts_are_captured():
    legs = _big_slate()
    for params in ({"target_payout": 1.4}, {"target_payout": 2.0}, {"min_prob": 0.75}):
        stats = {}
        builder_core.build(legs, top_n=5, stats=stats, **params)
        print(params, "nodes=", stats["nodes"], "truncated=", stats["truncated"])
        assert stats["nodes"] <= builder_core.MAX_NODES + 1
