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


def _favourite_slate(rng, n_games, legs_per_game):
    """Favourite-heavy slate mirroring the REAL MLB structure (README §15.10).

    The builder takes market-prob>=0.55 FAVOURITES, so every leg is a favourite
    priced from ~1.0x up. Crucially it reproduces the property that makes the
    real slate hard where a naive synthetic one is easy (memory: validate perf on
    real data): decimal_odds and market_prob are CORRELATED the way de-vigging
    produces them (decimal_odds = vig / market_prob), which packs a dense frontier
    of near-equal joint-probabilities against the payout floor — exactly what the
    tighter payout-axis prunes have to cut through. The per-leg vig VARIES, so
    this stays adversarial for exactness (no uniform-vig assumption, the thing the
    Stage-2 rewrite removed). The i*1e-5 / i*1e-6 jitter keeps every key tie-free."""
    legs, i = [], 0
    for g in range(n_games):
        for _ in range(legs_per_game):
            prob = 0.55 + rng.random() * 0.40
            vig = 0.90 + rng.random() * 0.09  # per-leg overround, non-uniform
            odds = vig / prob
            legs.append(_leg(g, round(odds + i * 1e-5, 6),
                             round(min(0.985, prob - i * 1e-6), 6), i))
            i += 1
    return legs


def test_build_matches_pure_brute_force_favourite_heavy():
    """The payout axis on favourite-heavy inputs is where the tighter prunes live
    (README §15.10). Verify they stay exact against the brute-force oracle. Legs
    cluster near 1.0x, so dedupe_by_price can legitimately collapse same-price
    legs; the oracle is run on the deduped legs to compare the SAME candidate set
    (dedupe losslessness is covered separately by dedupe_by_price's own docstring
    contract), isolating the search's exactness."""
    rng = random.Random(4242)
    for _ in range(60):
        legs = _favourite_slate(rng, n_games=rng.randint(3, 6), legs_per_game=rng.randint(1, 5))
        candidates = builder_core.dedupe_by_price(legs)
        for params in ({"target_payout": 1.2}, {"target_payout": 1.4},
                       {"target_payout": 1.6}, {"target_payout": 2.0},
                       {"target_payout": 2.5}, {"min_prob": 0.7}):
            top_n = rng.choice([1, 3, 5, 10])
            rank_by_payout = params.get("target_payout") is None
            got = builder_core.build(legs, top_n=top_n, **params)
            exp = brute_force_build(candidates, top_n=top_n, **params)
            assert search_key_seq(got, rank_by_payout) == search_key_seq(exp, rank_by_payout), (params, top_n)


def test_baseline_node_counts_are_captured():
    legs = _big_slate()
    for params in ({"target_payout": 1.4}, {"target_payout": 2.0}, {"min_prob": 0.75}):
        stats = {}
        builder_core.build(legs, top_n=5, stats=stats, **params)
        print(params, "nodes=", stats["nodes"], "truncated=", stats["truncated"])
        assert stats["nodes"] <= builder_core.MAX_NODES + 1


def test_both_axes_exhaustive_on_favourite_heavy_slate():
    """Regression guard for README §15.10's core fix: on a REAL-shaped
    favourite-heavy slate (20 games x ~100 legs, priced like live MLB), BOTH axes
    must finish EXHAUSTIVELY — not merely under the node budget, but with
    truncated=False — at the production default top_n=10. Before the floor-aware
    Lagrangian + last-leg constraint prunes both truncated at the 5M budget
    (payout ~25s, min_prob ~7s, partial results); they now complete in at most a
    few hundred thousand nodes."""
    rng = random.Random(7)
    legs = _favourite_slate(rng, n_games=20, legs_per_game=110)
    pins = [{"target_payout": 1.4}, {"target_payout": 2.0},
            {"min_prob": 0.6}, {"min_prob": 0.75}]
    for pin in pins:
        stats = {}
        builder_core.build(legs, top_n=10, stats=stats, **pin)
        print(f"{pin} nodes={stats['nodes']} truncated={stats['truncated']}")
        assert not stats["truncated"], f"axis truncated at {pin}"
        assert stats["nodes"] < builder_core.MAX_NODES
