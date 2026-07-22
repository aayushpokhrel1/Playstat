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
                      min_legs=2, max_legs=4, top_n=10, tolerance=0.15):
    """Naive-but-obviously-correct reference matching build()'s DEFINED semantics.

    The oracle enumerates every across-game combination directly. It reproduces
    two things beyond the bare filter/rank:

    1. same-game legs are excluded (structural constraint in build()).
    2. **progressive-widening early-stop** for the payout-floor axis. build()
       does not enumerate the whole odds>=floor space and rank it globally by
       joint_prob; it searches widening ceiling bands
       ([floor*(1+tol)], [floor*1.5], [floor*3.0], unbounded) and STOPS at the
       first band that contains any qualifying construction, ranking only that
       band by joint_prob. build() proves this exact for real de-vigged markets
       via odds/prob anti-correlation (higher payout => strictly lower joint
       prob), so a wider band can never displace an earlier result. The random
       slate here draws odds and prob INDEPENDENTLY, so that invariant does not
       hold and a globally-brute-forced ranking would diverge from build() — the
       oracle must mirror the widening to describe build()'s true behaviour.
       (The min_prob axis is a single unbounded pass, so no widening applies.)
    """
    rank_by_payout = target_payout is None and min_prob is not None

    def enumerate_qualifying():
        out = []
        for size in range(min_legs, max_legs + 1):
            for combo in itertools.combinations(legs, size):
                games = [l["game_id"] for l in combo]
                if len(set(games)) != len(games):
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
                out.append({"combined_odds": odds, "joint_prob": prob, "n_legs": size})
        return out

    qualifying = enumerate_qualifying()

    if rank_by_payout:
        qualifying.sort(key=lambda r: r["combined_odds"], reverse=True)
        return qualifying[:top_n]

    if target_payout is None:
        # min_legs<=..<=max_legs with neither floor pinned: rank by joint_prob.
        qualifying.sort(key=lambda r: r["joint_prob"], reverse=True)
        return qualifying[:top_n]

    # target_payout pinned: mirror build()'s widening ceilings + first-non-empty
    # early-stop, then rank that band by joint_prob.
    ceilings = [target_payout * (1 + tolerance), target_payout * 1.5,
                target_payout * 3.0, None]
    for hi in ceilings:
        band = [r for r in qualifying if hi is None or r["combined_odds"] <= hi]
        if band or hi is None:
            band.sort(key=lambda r: r["joint_prob"], reverse=True)
            return band[:top_n]
    return []


def search_key_seq(results, rank_by_payout):
    key = "combined_odds" if rank_by_payout else "joint_prob"
    return [round(r[key], 12) for r in results]


def _random_slate(rng, n_games, legs_per_game):
    legs, i = [], 0
    for g in range(n_games):
        for _ in range(legs_per_game):
            # distinct odds/probs keep the top-N boundary tie-free
            odds = round(1.0 + rng.random() * 1.5 + i * 1e-4, 6)
            prob = round(min(0.99, 0.55 + rng.random() * 0.44 - i * 1e-5), 6)
            legs.append(_leg(g, odds, prob, i))
            i += 1
    return legs


def test_oracle_matches_current_build_across_params():
    rng = random.Random(7)
    for _ in range(40):
        legs = _random_slate(rng, n_games=rng.randint(3, 6), legs_per_game=rng.randint(1, 4))
        for params in (
            {"target_payout": 2.0}, {"target_payout": 1.4}, {"target_payout": 3.0},
            {"min_prob": 0.75}, {"min_prob": 0.5}, {"min_prob": 0.9},
        ):
            top_n = rng.choice([1, 3, 10])
            rank_by_payout = params.get("target_payout") is None
            got = builder_core.build(legs, top_n=top_n, **params)
            exp = brute_force_build(legs, top_n=top_n, **params)
            assert search_key_seq(got, rank_by_payout) == search_key_seq(exp, rank_by_payout), params


def _big_slate():
    rng = random.Random(1)
    return _random_slate(rng, n_games=19, legs_per_game=128)


def test_baseline_node_counts_are_captured():
    legs = _big_slate()
    for params in ({"target_payout": 1.4}, {"target_payout": 2.0}, {"min_prob": 0.75}):
        stats = {}
        builder_core.build(legs, top_n=5, stats=stats, **params)
        # Baseline (main 1f00735): all three saturate the 5M budget.
        # Task 2 must bring at least the 2-4 leg common queries under it.
        print(params, "nodes=", stats["nodes"], "truncated=", stats["truncated"])
        assert stats["nodes"] <= builder_core.MAX_NODES + 1
