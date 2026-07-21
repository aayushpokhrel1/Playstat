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


def _clean_optional(value):
    """None for missing/NaN. model_prob comes from a LEFT JOIN, so it is absent
    whenever no edges row exists — and pandas represents that as NaN in a float
    column, not None. Left alone, json.dumps writes a bare NaN, which is invalid
    JSON and PostgreSQL rejects it (caught 2026-07-21: it broke every --save).
    NaN is the only value not equal to itself, so this needs no pandas import.
    """
    if value is None or value != value:
        return None
    return float(value)


def _base_leg(game_id, side, market_prob, line_value, american_odds, model_prob, label):
    return {
        "game_id": int(game_id),
        "label": label,
        "side": side,
        "line_value": float(line_value),
        "american_odds": int(american_odds),
        "decimal_odds": american_to_decimal(int(american_odds)),
        "market_prob": float(market_prob),
        "model_prob": _clean_optional(model_prob),
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


import heapq
import itertools

DEFAULT_TOLERANCE = 0.15
DEFAULT_MIN_LEGS = 2
DEFAULT_MAX_LEGS = 4
# Hard ceiling on search work. The old uncapped player optimizer was OOM-killed
# (SIGKILL) on 2026-07-18 — see README §11/§15. The game-structured search below
# makes blowing this budget very unlikely, but it stays as a guaranteed bound.
MAX_NODES = 5_000_000


def dedupe_by_price(legs):
    """Per game, keep only the highest-probability leg at each distinct price.

    Two legs in the same game at the same decimal odds contribute identically to
    the payout, so the less probable one can never be part of a better parlay.
    Lossless (to 3-decimal price granularity) — unlike a global "keep the top-N
    most probable legs" cap, which silently collapses the odds ceiling and can
    make the requested payout unreachable.
    """
    best = {}
    for leg in legs:
        key = (leg["game_id"], round(leg["decimal_odds"], 3))
        current = best.get(key)
        if current is None or leg["market_prob"] > current["market_prob"]:
            best[key] = leg
    return list(best.values())


def build(legs, target_payout=None, tolerance=DEFAULT_TOLERANCE, min_prob=None,
          min_legs=DEFAULT_MIN_LEGS, max_legs=DEFAULT_MAX_LEGS, top_n=10,
          max_nodes=MAX_NODES, stats=None):
    """Across-game parlay constructions, two-axis filtered and ranked.

    Pin target_payout -> filter to constructions paying AT LEAST that payout,
                          rank by joint probability (safest route that still
                          clears the floor).
    Pin min_prob      -> rank by payout (biggest payout at that safety level).

    target_payout is a FLOOR, not the centre of a tolerance band (user-confirmed
    2026-07-21). Joint probability falls monotonically as payout rises, so
    ranking-by-joint-prob inside a symmetric band always returns the bottom of
    the band — the band's lower edge was silently the real target. Making the
    two axes exact duals (min_prob -> filter by prob, rank by odds; target_payout
    -> filter by odds, rank by prob) removes that confusion.

    Legs from the same game are never combined: the joint probability is a plain
    product, which is only valid for independent (different-game) legs. That
    constraint is enforced structurally — the search picks a set of GAMES and
    then one leg from each, so same-game pairs are never generated in the first
    place. Enumerating flat leg tuples instead would generate billions of
    same-game combinations only to discard them.

    Pruning is exact, not heuristic: decimal odds are all >= 1, so a partial
    product only grows, and any branch already past the internal search
    ceiling is dead. Legs within a game are ordered by price so that test can
    stop a whole run — this is a primary performance lever (see the
    progressive-widening comment below for why removing the ceiling outright
    is not the fix).
    """
    if not legs:
        return []

    by_game = {}
    for leg in dedupe_by_price(legs):
        by_game.setdefault(leg["game_id"], []).append(leg)
    games = sorted(by_game.values(), key=lambda gl: min(l["decimal_odds"] for l in gl))
    for group in games:
        group.sort(key=lambda leg: leg["decimal_odds"])

    # target_payout is now a floor: any qualifying construction must have
    # combined_odds >= lo. (When only min_prob is pinned, lo stays None and
    # the probability floor below does all the filtering, unchanged.)
    lo = target_payout

    n_games = len(games)
    game_max = [max(leg["decimal_odds"] for leg in gl) for gl in games]
    # best_from[gi][r] = the largest payout obtainable by taking r legs from
    # games[gi:]. Lets a branch that can never reach the payout floor die early,
    # which matters because most legs are heavy favourites priced near 1.0x.
    best_from = []
    for gi in range(n_games + 1):
        prefix = [1.0]
        for value in sorted(game_max[gi:], reverse=True):
            prefix.append(prefix[-1] * value)
        best_from.append(prefix)

    rank_by_payout = target_payout is None and min_prob is not None

    # Progressive widening (the fix for the "floor, not band centre" bug).
    #
    # Naively dropping the internal ceiling (hi=None) would delete the
    # `next_odds > hi: break` prune in descend(), which is a primary
    # performance lever — legs within a game are sorted price-ascending
    # precisely so that prune can kill a whole run. Losing it explodes the
    # search space and makes max_nodes truncate far sooner.
    #
    # Instead: when ranking by joint_prob subject to combined_odds >= lo, the
    # optimum is always the qualifying construction with the LOWEST payout
    # (decimal odds are all >= 1, so more/pricier legs only ever shrink joint
    # probability further). That means an internal search ceiling can never
    # exclude the optimum as long as at least one qualifying construction
    # exists below it. So: search with a narrow ceiling first; only widen if
    # that pass finds nothing at all. Widening only ever *adds* candidates
    # with strictly higher payout, and higher payout means strictly lower
    # joint probability, so a later, wider pass can never displace a result
    # an earlier, narrower pass already found. Stopping at the first
    # non-empty pass is therefore exact, not a heuristic.
    if target_payout is not None:
        ceilings = [target_payout * (1 + tolerance), target_payout * 1.5,
                    target_payout * 3.0, None]
    else:
        ceilings = [None]

    heap = []
    state = {"nodes": 0, "truncated": False, "matches": 0}

    for hi in ceilings:
        heap = []
        counter = itertools.count()
        state = {"nodes": 0, "truncated": False, "matches": 0}

        def keep(result):
            """Retain only the current top_n. Accumulating every match would
            recreate the memory blow-up this builder exists to replace."""
            state["matches"] += 1
            key = result["combined_odds"] if rank_by_payout else result["joint_prob"]
            entry = (key, next(counter), result)
            if len(heap) < top_n:
                heapq.heappush(heap, entry)
            elif key > heap[0][0]:
                heapq.heapreplace(heap, entry)

        def descend(start, chosen, odds, prob, size):
            if state["truncated"]:
                return
            if len(chosen) == size:
                if lo is not None and odds < lo:
                    return
                if hi is not None and odds > hi:
                    return
                if min_prob is not None and prob < min_prob:
                    return
                keep({
                    "legs": list(chosen),
                    "combined_odds": odds,
                    "joint_prob": prob,
                    "n_legs": size,
                })
                return
            remaining = size - len(chosen)
            for gi in range(start, n_games - remaining + 1):
                # Even the best legs left cannot reach the floor; suffix maxima only
                # shrink as gi advances, so no later game can rescue this branch.
                if lo is not None and odds * best_from[gi][remaining] < lo:
                    break
                for leg in games[gi]:
                    state["nodes"] += 1
                    if state["nodes"] > max_nodes:
                        state["truncated"] = True
                        return
                    next_odds = odds * leg["decimal_odds"]
                    # Within a game legs are price-ascending, so once one overshoots
                    # this pass's ceiling every later one does too.
                    if hi is not None and next_odds > hi:
                        break
                    next_prob = prob * leg["market_prob"]
                    # Joint probability only falls as legs are added.
                    if min_prob is not None and next_prob < min_prob:
                        continue
                    chosen.append(leg)
                    descend(gi + 1, chosen, next_odds, next_prob, size)
                    chosen.pop()
                    if state["truncated"]:
                        return

        for size in range(min_legs, min(max_legs, n_games) + 1):
            descend(0, [], 1.0, 1.0, size)
            if state["truncated"]:
                break

        # Stop at the first pass that finds anything (see the widening
        # comment above for why this is exact) — or once hi is None, since
        # that is the unbounded last resort and there is nowhere left to widen.
        if state["matches"] > 0 or hi is None:
            break

    # Pinning the probability floor means the user asked "how much can I win at
    # this safety level" -> rank by payout. Otherwise rank by safety.
    results = [entry[2] for entry in sorted(heap, key=lambda e: e[0], reverse=True)]

    if stats is not None:
        stats.update(state)
        stats["candidate_games"] = n_games
    return results
