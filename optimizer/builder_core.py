"""Pure, DB-free core for the low-risk parlay builder.

MARKET-centric by design: every probability here comes from de-vigging the
book's two-sided price, never from a model. README §15 explains why — the
models lack per-game resolution and overstate heavy-favorite safety, so the
book's de-vigged price is the best-calibrated probability available.
"""

from modeling.edges import devig
from optimizer.parlay import american_to_decimal

# No single leg may be worse than this to hit (de-vigged market probability).
DEFAULT_FLOOR = 0.55


def favorite_side(over_odds, under_odds):
    """(side, de-vigged probability) for whichever side the market makes the favorite."""
    p_over, p_under = devig(over_odds, under_odds)
    if p_over >= p_under:
        return "over", p_over
    return "under", p_under


def passes_floor(leg, floor=DEFAULT_FLOOR):
    return leg["market_prob"] >= floor


def _base_leg(game_id, side, market_prob, line_value, american_odds, model_prob, label):
    return {
        "game_id": int(game_id),
        "label": label,
        "side": side,
        "line_value": float(line_value),
        "american_odds": int(american_odds),
        "decimal_odds": american_to_decimal(int(american_odds)),
        "market_prob": float(market_prob),
        "model_prob": None if model_prob is None else float(model_prob),
    }


def normalize_player_leg(row):
    side, prob = favorite_side(row["over_odds"], row["under_odds"])
    odds = row["over_odds"] if side == "over" else row["under_odds"]
    label = f"{row.get('player_name', 'player')} {row['stat_type']} {side} {row['line_value']}"
    leg = _base_leg(row["game_id"], side, prob, row["line_value"], odds,
                    row.get("model_prob"), label)
    leg.update({"kind": "player", "player_id": int(row["player_id"]),
                "stat_type": row["stat_type"], "market": None})
    return leg


def normalize_team_leg(row):
    side, prob = favorite_side(row["over_odds"], row["under_odds"])
    odds = row["over_odds"] if side == "over" else row["under_odds"]
    label = f"{row['market']} {side} {row['line_value']}"
    leg = _base_leg(row["game_id"], side, prob, row["line_value"], odds,
                    row.get("model_prob"), label)
    leg.update({"kind": "team", "player_id": None, "stat_type": None,
                "market": row["market"]})
    return leg


import itertools
from math import comb

DEFAULT_TOLERANCE = 0.15
DEFAULT_MIN_LEGS = 2
DEFAULT_MAX_LEGS = 4
# Bounds the brute-force search. The uncapped player optimizer was OOM-killed
# (SIGKILL) on 2026-07-18 at ~198M combinations — see README §11/§15.
MAX_COMBOS = 5_000_000


def cap_candidates(legs, max_legs=DEFAULT_MAX_LEGS, max_combos=MAX_COMBOS):
    """Keep the highest-market-probability legs such that C(n, max_legs) <= max_combos."""
    legs = sorted(legs, key=lambda leg: leg["market_prob"], reverse=True)
    if len(legs) <= max_legs:
        return legs
    n = len(legs)
    while n > max_legs and comb(n, max_legs) > max_combos:
        n -= 1
    return legs[:n]


def build(legs, target_payout=None, tolerance=DEFAULT_TOLERANCE, min_prob=None,
          min_legs=DEFAULT_MIN_LEGS, max_legs=DEFAULT_MAX_LEGS, top_n=10):
    """Across-game parlay constructions, two-axis filtered and ranked.

    Pin target_payout -> rank by joint probability (safest route to that payout).
    Pin min_prob      -> rank by payout (biggest payout at that safety level).
    Legs from the same game are never combined: the joint probability is a plain
    product, which is only valid for independent (different-game) legs.
    """
    if not legs:
        return []

    results = []
    for size in range(min_legs, max_legs + 1):
        for combo in itertools.combinations(legs, size):
            game_ids = [leg["game_id"] for leg in combo]
            if len(set(game_ids)) != len(game_ids):
                continue

            combined_odds = 1.0
            joint_prob = 1.0
            for leg in combo:
                combined_odds *= leg["decimal_odds"]
                joint_prob *= leg["market_prob"]

            if target_payout is not None and \
                    abs(combined_odds - target_payout) / target_payout > tolerance:
                continue
            if min_prob is not None and joint_prob < min_prob:
                continue

            results.append({
                "legs": list(combo),
                "combined_odds": combined_odds,
                "joint_prob": joint_prob,
                "n_legs": size,
            })

    # Pinning the probability floor means the user asked "how much can I win at
    # this safety level" -> rank by payout. Otherwise rank by safety.
    if target_payout is None and min_prob is not None:
        results.sort(key=lambda r: r["combined_odds"], reverse=True)
    else:
        results.sort(key=lambda r: r["joint_prob"], reverse=True)
    return results[:top_n]
