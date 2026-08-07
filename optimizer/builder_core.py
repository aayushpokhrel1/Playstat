"""Pure, DB-free core for the low-risk parlay builder.

MARKET-centric by design: every probability here comes from de-vigging the
book's two-sided price, never from a model. README §15 explains why — the
models lack per-game resolution and overstate heavy-favorite safety, so the
book's de-vigged price is the best-calibrated probability available.
"""

from optimizer.devig import devig
from optimizer.parlay import american_to_decimal

# No single leg may be worse than this to hit (de-vigged market probability).
DEFAULT_FLOOR = 0.55


def favorite_side(over_odds, under_odds):
    """(side, de-vigged probability) for whichever side the market makes the favorite."""
    p_over, p_under = devig(over_odds, under_odds)
    if p_over >= p_under:
        return "over", p_over
    return "under", p_under


# market name -> geometry: "ou" (over/under, e.g. totals) or "homeaway"
# (spread/moneyline). Drives normalize_team_leg's branch (NFL builder #3).
MARKET_GEOMETRY = {
    "first_inning_runs": "ou", "f5_runs": "ou", "full_game_total": "ou",
    "full_game_spread": "homeaway", "full_game_moneyline": "homeaway",
}


def is_home_away_market(market):
    return MARKET_GEOMETRY.get(market) == "homeaway"


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


def _base_leg(game_id, side, market_prob, line_value, american_odds, model_prob, label, book=None):
    return {
        "game_id": int(game_id),
        "label": label,
        "side": side,
        "line_value": None if line_value is None else float(line_value),
        "american_odds": int(american_odds),
        "decimal_odds": american_to_decimal(int(american_odds)),
        "market_prob": float(market_prob),
        "model_prob": _clean_optional(model_prob),
        "book": book,
    }


# side -> (best-odds column, best-book column, consensus-odds column) for the
# shopped payout price. market_prob is ALWAYS the consensus devig (ranking/floor
# unchanged); only the payout price is shopped. A missing best_* (NULL / absent)
# falls back to the consensus price for that side (README §15.9 item 3).
_SHOP_COLS = {
    "over":  ("best_over_odds",  "best_over_book",  "over_odds"),
    "under": ("best_under_odds", "best_under_book", "under_odds"),
    "home":  ("best_home_odds",  "best_home_book",  "home_odds"),
    "away":  ("best_away_odds",  "best_away_book",  "away_odds"),
}


def shopped_odds(row, side):
    """(american_odds, book) for the chosen side: the best single-book price when
    present, else the consensus price (book None)."""
    best_col, book_col, cons_col = _SHOP_COLS[side]
    best = row.get(best_col)
    if best is not None:
        return int(best), row.get(book_col)
    return int(row[cons_col]), None


def normalize_player_leg(row):
    side, prob = favorite_side(row["over_odds"], row["under_odds"])
    odds, book = shopped_odds(row, side)
    label = f"{row.get('player_name', 'player')} {row['stat_type']} {side} {row['line_value']}"
    leg = _base_leg(row["game_id"], side, prob, row["line_value"], odds,
                    row.get("model_prob"), label, book=book)
    leg.update({"kind": "player", "player_id": int(row["player_id"]),
                "stat_type": row["stat_type"], "market": None})
    return leg


def normalize_team_leg(row):
    market = row["market"]
    if is_home_away_market(market):
        raw, prob = favorite_side(row["home_odds"], row["away_odds"])   # "over"->home, "under"->away
        side = "home" if raw == "over" else "away"
        odds, book = shopped_odds(row, side)
        line = row.get("line_value")
        label = f"{market} {side}" if line is None else f"{market} {side} {line}"
    else:
        side, prob = favorite_side(row["over_odds"], row["under_odds"])
        odds, book = shopped_odds(row, side)
        line = row["line_value"]
        label = f"{market} {side} {line}"
    leg = _base_leg(row["game_id"], side, prob, line, odds, row.get("model_prob"), label, book=book)
    leg.update({"kind": "team", "player_id": None, "stat_type": None, "market": market})
    return leg


import bisect
import heapq
import itertools
import math

DEFAULT_TOLERANCE = 0.15
DEFAULT_MIN_LEGS = 2
DEFAULT_MAX_LEGS = 4
# Hard ceiling on search work. The old uncapped player optimizer was OOM-killed
# (SIGKILL) on 2026-07-18 — see README §11/§15. The game-structured search below
# makes blowing this budget very unlikely, but it stays as a guaranteed bound.
MAX_NODES = 5_000_000

# Lagrange multipliers for the floor-aware joint-probability bound on the payout
# axis (see build()). The bound is valid for every lambda >= 0; taking the
# minimum over this small geometric grid gives a tight upper bound cheaply. 0.0
# recovers the plain best_prob_from bound; the positive values bite while the
# payout floor still forces odds (and therefore lost probability) onto the
# remaining legs.
LAMBDA_GRID = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0)


def entity_of(leg):
    """Reuse-limited identity: the player for a player leg, else the game."""
    return leg["player_id"] if leg["kind"] == "player" else leg["game_id"]


def select_diverse(results, n, max_uses, entity_of=entity_of):
    """Greedily pick up to n constructions in the given (rank-descending) order,
    admitting one only if every entity it uses stays within max_uses. Not claimed
    optimal — it removes cross-construction reuse cascade, nothing more."""
    counts, chosen = {}, []
    for con in results:
        ents = {entity_of(l) for l in con["legs"]}
        if all(counts.get(e, 0) < max_uses for e in ents):
            for e in ents:
                counts[e] = counts.get(e, 0) + 1
            chosen.append(con)
            if len(chosen) == n:
                break
    return chosen


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


def _suffix_prod_desc(per_game, n_games):
    """table[gi][r] = product of the r largest values in per_game[gi:] (r>=0)."""
    table = []
    for gi in range(n_games + 1):
        prefix = [1.0]
        for value in sorted(per_game[gi:], reverse=True):
            prefix.append(prefix[-1] * value)
        table.append(prefix)
    return table


def _suffix_sum_desc(per_game, n_games):
    """table[gi][r] = sum of the r largest values in per_game[gi:] (r>=0)."""
    table = []
    for gi in range(n_games + 1):
        prefix = [0.0]
        for value in sorted(per_game[gi:], reverse=True):
            prefix.append(prefix[-1] + value)
        table.append(prefix)
    return table


def build(legs, target_payout=None, tolerance=DEFAULT_TOLERANCE, min_prob=None,
          min_legs=DEFAULT_MIN_LEGS, max_legs=DEFAULT_MAX_LEGS, top_n=10,
          max_nodes=MAX_NODES, stats=None, max_uses=None):
    """Across-game parlay constructions, two-axis filtered and ranked.

    Pin target_payout -> filter to constructions paying AT LEAST that payout,
                          rank by joint probability (safest route that still
                          clears the floor).
    Pin min_prob      -> rank by payout (biggest payout at that safety level).

    target_payout is a FLOOR, not the centre of a tolerance band (user-confirmed
    2026-07-21). Making the two axes exact duals (min_prob -> filter by prob,
    rank by odds; target_payout -> filter by odds, rank by prob) removes the
    "band centre vs. floor" confusion.

    Legs from the same game are never combined: the joint probability is a plain
    product, which is only valid for independent (different-game) legs. That
    constraint is enforced structurally — the search picks a set of GAMES and
    then one leg from each, so same-game pairs are never generated in the first
    place. Enumerating flat leg tuples instead would generate billions of
    same-game combinations only to discard them.

    Search is a SINGLE unbounded pass (no odds ceiling), bounded by EXACT prunes.
    It is provably identical to a pure global brute force on EVERY slate — no
    assumption about odds/probability correlation. (An earlier progressive-widening
    early-stop was exact only when higher payout implied strictly lower joint_prob,
    which holds on uniform-vig book lines but not in general; it missed ~0.5% of
    optima on wide-vig synthetic slates.)

    The two axes are exact DUALS (2026-07-22 work, README §15.10): each ranks by
    one quantity subject to a floor on the other, and each is bounded by the same
    three-part machinery with the roles of odds<->probability and
    target_payout<->min_prob swapped. Legs within a game are visited in DESCENDING
    order of the RANK quantity, so every rank bound is monotone and ends a game's
    leg loop rather than skipping one leg at a time.

    target_payout pinned: rank by joint_prob, floor combined_odds >= lo (visit
    legs by descending market probability):
    (a) best_prob_from prune: prob * best_prob_from[gi][remaining] upper-bounds
        any completion's joint_prob (decimal odds are all >= 1, so joint_prob
        only falls as legs are added). Once it can't beat the N-th best, done.
    (b) floor-aware Lagrangian bound: reaching the payout floor forces the
        remaining legs to carry odds, and high-odds legs carry low probability.
        For any lambda >= 0, a floor-clearing completion's joint_prob is at most
        prob times the product of the `remaining` largest per-game
        max(p * d**lambda), divided by R**lambda where R = lo/odds is the odds
        still needed. The minimum over LAMBDA_GRID is a far tighter bound than (a)
        while the floor binds — the prune that stops a 1.4x/2.0x floor from
        forcing the search millions of nodes deep.
    (c) last-leg odds-constrained bound: the final leg must itself clear the floor
        (decimal_odds >= lo/odds), and among legs that do the best probability is
        exact (suffix_pmax). If even that can't beat the N-th best the whole game's
        last leg is skipped — the single biggest win on the real slate, because
        best_prob_from (a) wrongly assumes the last leg could be a ~1.0x
        near-certainty when the floor forbids it.

    min_prob pinned: rank by combined_odds, floor joint_prob >= min_prob (visit
    legs by descending decimal odds). The exact dual of the above:
    (a') best_from prune: odds * best_from[gi][remaining] upper-bounds any
         completion's combined_odds.
    (b') floor-aware Lagrangian bound: keeping joint_prob >= min_prob limits how
         much odds the remaining legs can add (high odds cost probability). Same
         form with odds<->prob swapped; here R = min_prob/prob is the probability
         still allowed to be spent, and because prod(p) has no >= 1 floor the
         need term is NOT clamped to 0 (it is for the payout dual, where prod(d)>=1).
    (c') last-leg prob-constrained bound: the final leg must keep joint_prob >=
         min_prob (market_prob >= min_prob/prob), and among legs that do the best
         combined_odds is exact (suffix_omax); skip the game if it can't beat the
         N-th best.

    All prunes are verified exact by the pure global brute-force oracle in
    tests/test_builder_search_exactness.py, including its independent-draw and
    favourite-heavy adversarial slates. MAX_NODES stays a hard bound and the
    `truncated` flag is surfaced if it is ever hit.

    tolerance is retained for API/CLI/daily_chain compatibility but no longer
    affects results — the search is exact and unbounded, so there is no band to
    widen. It is accepted and ignored.

    max_uses (docs/superpowers/specs/2026-07-29-builder-independence-design.md):
    None (default) reproduces today's exact top_n, byte-for-byte — the exact
    search itself is untouched either way. When set, the search instead keeps a
    wider ranked pool (pool_n) and a pure post-hoc selection layer
    (select_diverse) greedily picks up to top_n of those, admitting a
    construction only while every player/game it uses stays within max_uses
    across the selected set. Top-1 is always unchanged; only slots 2..N reshuffle.
    """
    if not legs:
        return []

    pool_n = top_n if max_uses is None else max(top_n * 20, 100)

    by_game = {}
    for leg in dedupe_by_price(legs):
        by_game.setdefault(leg["game_id"], []).append(leg)
    games = sorted(by_game.values(), key=lambda gl: min(l["decimal_odds"] for l in gl))
    n_games = len(games)
    lo = target_payout
    rank_by_payout = target_payout is None and min_prob is not None

    # Suffix-maximum look-aheads (independent of leg order within a game).
    game_max = [max(leg["decimal_odds"] for leg in gl) for gl in games]
    best_from = _suffix_prod_desc(game_max, n_games)
    game_prob_max = [max(leg["market_prob"] for leg in gl) for gl in games]
    best_prob_from = _suffix_prod_desc(game_prob_max, n_games)

    heap = []
    counter = itertools.count()
    state = {"nodes": 0, "truncated": False, "matches": 0}

    def keep(result):
        """Retain only the current pool_n (== top_n unless max_uses widens the
        pool for select_diverse). Accumulating every match would recreate the
        memory blow-up this builder exists to replace."""
        state["matches"] += 1
        key = result["combined_odds"] if rank_by_payout else result["joint_prob"]
        entry = (key, next(counter), result)
        if len(heap) < pool_n:
            heapq.heappush(heap, entry)
        elif key > heap[0][0]:
            heapq.heapreplace(heap, entry)

    if rank_by_payout:
        # ---- min_prob axis: filter joint_prob >= min_prob, rank by combined_odds.
        # Exact DUAL of the target_payout axis below — swap the roles of odds<->prob
        # and lo<->min_prob throughout. Legs are visited in DESCENDING decimal odds
        # (the rank key) so every odds bound is monotone and ends a game's leg loop.
        for group in games:
            group.sort(key=lambda leg: leg["decimal_odds"], reverse=True)
        # min_prob <= 0 is a vacuous floor (every construction qualifies): the
        # floor look-aheads below all no-op, and the floor-aware Lagrangian (b')
        # is skipped (log(0) is undefined and there is nothing to bound against).
        prob_floored = min_prob > 0.0
        logmp = math.log(min_prob) if prob_floored else 0.0
        # Probability-ascending copy per game for the last-leg floor bisect, plus a
        # suffix-max of ODDS over it: suffix_omax[gi][k] is the largest decimal_odds
        # among legs in game gi with market_prob >= game_probs[gi][k].
        game_legs_by_prob = [sorted(gl, key=lambda l: l["market_prob"]) for gl in games]
        game_probs = [[l["market_prob"] for l in gl] for gl in game_legs_by_prob]
        suffix_omax = []
        for gl in game_legs_by_prob:
            sm = [0.0] * (len(gl) + 1)
            for k in range(len(gl) - 1, -1, -1):
                o = gl[k]["decimal_odds"]
                sm[k] = o if o > sm[k + 1] else sm[k + 1]
            suffix_omax.append(sm)
        log_o = [[math.log(l["decimal_odds"]) for l in gl] for gl in games]
        log_p = [[math.log(l["market_prob"]) for l in gl] for gl in games]
        smu = []
        if prob_floored:
            for mu in LAMBDA_GRID:
                gw = [max(lo_ + mu * lp_ for lo_, lp_ in zip(log_o[i], log_p[i]))
                      for i in range(n_games)]
                smu.append(_suffix_sum_desc(gw, n_games))

        def odds_upper_log(gi, logodds, logprob, remaining):
            # Dual Lagrangian bound on log(combined_odds) of any floor-clearing
            # completion. need = log(min_prob/prob) <= 0 for feasible branches
            # (prob already clears min_prob), and is NOT clamped: the valid bound
            # is smu - mu*need = smu + mu*|need|. (Unlike the payout dual, where
            # prod(d) >= 1 always holds so need clamps to 0; prod(p) has no such
            # >= 1 floor, so dropping the term would over-prune.)
            need = logmp - logprob
            best = None
            for j, mu in enumerate(LAMBDA_GRID):
                b = logodds + smu[j][gi][remaining] - mu * need
                if best is None or b < best:
                    best = b
            return best

        def descend(start, chosen, odds, prob, logodds, logprob, size):
            if state["truncated"]:
                return
            remaining = size - len(chosen)
            heap_full = len(heap) == pool_n
            thr = heap[0][0] if heap_full else -1.0
            logthr = math.log(thr) if heap_full else 0.0
            for gi in range(start, n_games - remaining + 1):
                # Probability floor look-ahead: best joint_prob reachable from here
                # is prob * best_prob_from[gi][remaining]; if it can't clear
                # min_prob the branch is dead and the suffix max only shrinks.
                if prob * best_prob_from[gi][remaining] < min_prob:
                    break
                if heap_full:
                    # (a') payout bound: max reachable odds is odds*best_from[gi][r];
                    # once it can't beat the N-th best odds no later game can either.
                    if odds * best_from[gi][remaining] <= thr:
                        break
                    # (b') floor-aware Lagrangian odds bound (dual of the payout (b)).
                    if prob_floored and odds_upper_log(gi, logodds, logprob, remaining) <= logthr:
                        break
                if remaining == 1:
                    legs_p = game_legs_by_prob[gi]
                    # Last leg must keep joint_prob >= min_prob alone: market_prob >=
                    # min_prob/prob. Legs prob-ascending, so skip the sub-floor
                    # prefix (a tiny relative guard keeps float rounding from
                    # dropping a leg the leaf check would have kept).
                    lo_idx = bisect.bisect_left(game_probs[gi], (min_prob / prob) * (1 - 1e-9))
                    if lo_idx >= len(legs_p):
                        continue
                    # (c') best qualifying last leg's odds = suffix_omax[gi][lo_idx];
                    # if odds*that can't beat the N-th best, skip the whole game.
                    if heap_full and odds * suffix_omax[gi][lo_idx] <= thr:
                        continue
                    for li in range(lo_idx, len(legs_p)):
                        leg = legs_p[li]
                        state["nodes"] += 1
                        if state["nodes"] > max_nodes:
                            state["truncated"] = True
                            return
                        next_prob = prob * leg["market_prob"]
                        if next_prob < min_prob:
                            continue
                        keep({"legs": chosen + [leg], "combined_odds": odds * leg["decimal_odds"],
                              "joint_prob": next_prob, "n_legs": size})
                else:
                    legs_gi = games[gi]
                    bo = odds * best_from[gi + 1][remaining - 1]
                    bpf = best_prob_from[gi + 1][remaining - 1]
                    lo_gi = log_o[gi]
                    lp_gi = log_p[gi]
                    for li in range(len(legs_gi)):
                        leg = legs_gi[li]
                        if heap_full and bo * leg["decimal_odds"] <= thr:
                            break  # (a'), odds descending -> the rest fail too
                        next_prob = prob * leg["market_prob"]
                        # Floor feasibility: this leg plus the best of the remaining
                        # legs must still reach min_prob. Not monotone in odds order
                        # -> skip this leg, don't end the loop.
                        if next_prob * bpf < min_prob:
                            continue
                        state["nodes"] += 1
                        if state["nodes"] > max_nodes:
                            state["truncated"] = True
                            return
                        chosen.append(leg)
                        descend(gi + 1, chosen, odds * leg["decimal_odds"], next_prob,
                                logodds + lo_gi[li], logprob + lp_gi[li], size)
                        chosen.pop()
                        if state["truncated"]:
                            return
    else:
        # ---- target_payout axis: filter combined_odds >= lo (and joint_prob >= ----
        # min_prob if also pinned), rank by joint_prob. See docstring (a)/(b)/(c).
        # Legs visited in DESCENDING probability so every bound below is monotone.
        for group in games:
            group.sort(key=lambda leg: leg["market_prob"], reverse=True)
        floored = lo is not None
        loglo = math.log(lo) if floored else 0.0
        # Odds-ascending copy per game for the last-leg floor bisect, plus a
        # suffix-max of probability over it: suffix_pmax[gi][k] is the largest
        # market_prob among legs in game gi with decimal_odds >= game_odds[gi][k].
        game_legs_by_odds = [sorted(gl, key=lambda l: l["decimal_odds"]) for gl in games]
        game_odds = [[l["decimal_odds"] for l in gl] for gl in game_legs_by_odds]
        suffix_pmax = []
        for gl in game_legs_by_odds:
            sm = [0.0] * (len(gl) + 1)
            for k in range(len(gl) - 1, -1, -1):
                p = gl[k]["market_prob"]
                sm[k] = p if p > sm[k + 1] else sm[k + 1]
            suffix_pmax.append(sm)
        # Per-game logs parallel to the probability-descending order, so the hot
        # recursion never calls math.log.
        log_d = [[math.log(l["decimal_odds"]) for l in gl] for gl in games]
        log_p = [[math.log(l["market_prob"]) for l in gl] for gl in games]
        # Floor-aware Lagrangian tables (only meaningful when a floor is pinned).
        slam = []
        if floored:
            for lam in LAMBDA_GRID:
                gw = [max(lp + lam * ld for lp, ld in zip(log_p[i], log_d[i]))
                      for i in range(n_games)]
                slam.append(_suffix_sum_desc(gw, n_games))

        def prob_upper_log(gi, logprob, logodds, remaining):
            need = loglo - logodds
            if need < 0.0:
                need = 0.0
            best = None
            for j, lam in enumerate(LAMBDA_GRID):
                b = logprob + slam[j][gi][remaining] - lam * need
                if best is None or b < best:
                    best = b
            return best

        def descend(start, chosen, odds, prob, logodds, logprob, size):
            if state["truncated"]:
                return
            remaining = size - len(chosen)
            heap_full = len(heap) == pool_n
            thr = heap[0][0] if heap_full else -1.0
            logthr = math.log(thr) if heap_full else 0.0
            for gi in range(start, n_games - remaining + 1):
                if floored and odds * best_from[gi][remaining] < lo:
                    break
                if heap_full:
                    if prob * best_prob_from[gi][remaining] <= thr:
                        break  # (a)
                    if floored and prob_upper_log(gi, logprob, logodds, remaining) <= logthr:
                        break  # (b)
                if remaining == 1:
                    legs_o = game_legs_by_odds[gi]
                    lo_idx = 0
                    if floored:
                        # Last leg must clear the floor alone: decimal_odds >=
                        # lo/odds. Legs are odds-ascending, so skip the sub-floor
                        # prefix (a tiny relative guard keeps float rounding from
                        # dropping a leg the leaf check would have kept).
                        lo_idx = bisect.bisect_left(game_odds[gi], (lo / odds) * (1 - 1e-9))
                        if lo_idx >= len(legs_o):
                            continue
                        if heap_full and prob * suffix_pmax[gi][lo_idx] <= thr:
                            continue  # (c)
                    for li in range(lo_idx, len(legs_o)):
                        leg = legs_o[li]
                        state["nodes"] += 1
                        if state["nodes"] > max_nodes:
                            state["truncated"] = True
                            return
                        next_odds = odds * leg["decimal_odds"]
                        if floored and next_odds < lo:
                            continue
                        next_prob = prob * leg["market_prob"]
                        if min_prob is not None and next_prob < min_prob:
                            continue
                        keep({"legs": chosen + [leg], "combined_odds": next_odds,
                              "joint_prob": next_prob, "n_legs": size})
                else:
                    legs_gi = games[gi]
                    bp = prob * best_prob_from[gi + 1][remaining - 1]
                    ld_gi = log_d[gi]
                    lp_gi = log_p[gi]
                    for li in range(len(legs_gi)):
                        leg = legs_gi[li]
                        leg_prob = leg["market_prob"]
                        if heap_full and bp * leg_prob <= thr:
                            break  # (a), probability descending -> the rest fail too
                        next_prob = prob * leg_prob
                        if min_prob is not None and next_prob < min_prob:
                            break
                        state["nodes"] += 1
                        if state["nodes"] > max_nodes:
                            state["truncated"] = True
                            return
                        chosen.append(leg)
                        descend(gi + 1, chosen, odds * leg["decimal_odds"], next_prob,
                                logodds + ld_gi[li], logprob + lp_gi[li], size)
                        chosen.pop()
                        if state["truncated"]:
                            return

    max_reach = min(max_legs, n_games)
    for size in range(min_legs, max_reach + 1):
        descend(0, [], 1.0, 1.0, 0.0, 0.0, size)
        if state["truncated"]:
            break

    # Pinning the probability floor means "how much can I win at this safety
    # level" -> rank by payout. Otherwise rank by safety (joint probability).
    ranked = [entry[2] for entry in sorted(heap, key=lambda e: e[0], reverse=True)]
    results = ranked[:top_n] if max_uses is None else select_diverse(ranked, top_n, max_uses)

    if stats is not None:
        stats.update(state)
        stats["candidate_games"] = n_games
    return results
