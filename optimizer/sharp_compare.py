"""Booked builder legs vs the sharp reference close (README §15.9 item 14e).

The kill test the sharp snapshots exist for, in two numbers per leg:

  fair_ratio    = fair_prob x booked_decimal. >1 means the price we booked beats
                  the de-vigged Pinnacle close — the standard top-down test.
  fair_delta_pp = fair_prob - build_prob, how far our (soft-consensus) ranking
                  probability sits from sharp fair.

HONESTY RULES, inherited from optimizer/line_movement.py:
  - a sharp row counts only if pulled STRICTLY LATER than the card was built;
  - a moved line_value is a DIFFERENT BET and is excluded, not compared;
  - a one-sided row cannot be de-vigged and is excluded;
  - coverage is first-class output — a low comparable rate is itself a finding.

CLI REPORT ONLY (§15.8 #2 by construction): this module feeds no API endpoint,
no dashboard, and writes nothing. The verdict is for the operator.
"""

import argparse
import json
from collections import defaultdict

from optimizer.devig import devig
from optimizer.parlay import american_to_decimal

SHARP_BOOK = "pinnacle"

_SIDE_COLUMNS = {
    "over": ("over_odds", "under_odds", 0),
    "under": ("over_odds", "under_odds", 1),
    "home": ("home_odds", "away_odds", 0),
    "away": ("home_odds", "away_odds", 1),
}


def sharp_prob_for_side(row, side):
    """De-vigged sharp probability of `side`, or None for unknown/one-sided."""
    columns = _SIDE_COLUMNS.get(side)
    if columns is None:
        return None
    first, second, index = columns
    a, b = row.get(first), row.get(second)
    if a is None or b is None:
        return None
    return devig(int(a), int(b))[index]


def leg_vs_sharp(build_leg, sharp_row):
    """Compare one booked leg to one sharp row, or None if not comparable."""
    if not sharp_row or build_leg.get("odds") is None:
        return None
    build_line = build_leg.get("line")
    sharp_line = sharp_row.get("line_value")
    if (build_line is None) != (sharp_line is None):
        return None
    if build_line is not None and float(sharp_line) != float(build_line):
        return None
    fair = sharp_prob_for_side(sharp_row, build_leg.get("side"))
    if fair is None:
        return None
    booked_decimal = american_to_decimal(int(build_leg["odds"]))
    build_prob = build_leg.get("market_prob")
    return {
        "player_id": build_leg.get("player_id"),
        "game_id": build_leg.get("game_id"),
        "market": build_leg.get("stat_type") or build_leg.get("market"),
        "side": build_leg.get("side"),
        "line": float(build_line) if build_line is not None else None,
        "book": build_leg.get("book"),
        "booked_odds": int(build_leg["odds"]),
        "booked_decimal": booked_decimal,
        "fair_prob": float(fair),
        "fair_ratio": float(fair) * booked_decimal,
        "build_prob": float(build_prob) if build_prob is not None else None,
        "fair_delta_pp": ((float(fair) - float(build_prob)) * 100.0
                          if build_prob is not None else None),
    }


def summarize(pairs):
    """Aggregate (build_leg, sharp_row) pairs; coverage is first-class."""
    legs = []
    for build_leg, sharp_row in pairs:
        row = leg_vs_sharp(build_leg, sharp_row)
        if row is not None:
            legs.append(row)
    ratios = [l["fair_ratio"] for l in legs]
    deltas = [l["fair_delta_pp"] for l in legs if l["fair_delta_pp"] is not None]
    return {
        "n_legs": len(pairs),
        "n_compared": len(legs),
        "coverage": (len(legs) / len(pairs)) if pairs else 0.0,
        "mean_fair_ratio": (sum(ratios) / len(ratios)) if ratios else None,
        "n_above_fair": sum(1 for r in ratios if r > 1.0),
        "n_below_fair": sum(1 for r in ratios if r < 1.0),
        "mean_fair_delta_pp": (sum(deltas) / len(deltas)) if deltas else None,
        "legs": legs,
    }


def _parse_wrapper(raw):
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw or {}


def load_pairs(conn, sport="mlb", days=1):
    """(build_leg, sharp_row) pairs for saved cards in the window.

    Sharp row: the newest SHARP_BOOK row per (game, player, market) pulled
    STRICTLY LATER than the card was built (line_movement.py's rule — without
    it a same-pull row would compare a price to itself).
    """
    from sqlalchemy import text  # DB import kept out of module import path

    cards = conn.execute(text(
        """
        SELECT pr.created_at, pr.legs
        FROM parlay_recommendations pr
        WHERE pr.kind = 'builder'
          AND COALESCE(pr.legs->>'sport', 'mlb') = :sport
          AND pr.created_at::date >= CURRENT_DATE - :days
        ORDER BY pr.created_at
        """
    ), {"sport": sport, "days": days}).fetchall()

    sharp = {}
    for r in conn.execute(text(
        """
        SELECT DISTINCT ON (game_id, player_id, market)
               game_id, player_id, market, line_value,
               over_odds, under_odds, home_odds, away_odds, pulled_at
        FROM sharp_lines
        WHERE book = :book AND pulled_at >= CURRENT_DATE - :days
        ORDER BY game_id, player_id, market, pulled_at DESC
        """
    ), {"book": SHARP_BOOK, "days": days}).fetchall():
        sharp[(r.game_id, r.player_id, r.market)] = {
            "line_value": r.line_value, "over_odds": r.over_odds,
            "under_odds": r.under_odds, "home_odds": r.home_odds,
            "away_odds": r.away_odds, "pulled_at": r.pulled_at,
        }

    pairs = []
    for built_at, wrapper in cards:
        for leg in _parse_wrapper(wrapper).get("legs", []):
            key = (leg.get("game_id"), leg.get("player_id"),
                   leg.get("stat_type") or leg.get("market"))
            row = sharp.get(key)
            if row is not None and built_at is not None:
                if row["pulled_at"] is None or row["pulled_at"] <= built_at:
                    row = None
            pairs.append((leg, row))
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sport", default="mlb")
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()

    from ingestion.db import get_engine

    with get_engine().begin() as conn:
        pairs = load_pairs(conn, sport=args.sport, days=args.days)
    summary = summarize(pairs)

    print(f"sharp comparison — {args.sport}, last {args.days} day(s), "
          f"book={SHARP_BOOK}")
    print(f"legs {summary['n_legs']}, comparable {summary['n_compared']} "
          f"(coverage {summary['coverage']:.1%})")
    if not summary["n_compared"]:
        print("nothing comparable — no sharp snapshot later than a card yet")
        return
    print(f"mean fair_ratio {summary['mean_fair_ratio']:.4f}  "
          f"above/below fair {summary['n_above_fair']}/{summary['n_below_fair']}")
    if summary["mean_fair_delta_pp"] is not None:
        print(f"mean fair_delta {summary['mean_fair_delta_pp']:+.2f}pp "
              f"(sharp fair prob minus build consensus prob, our side)")

    by_market = defaultdict(list)
    for leg in summary["legs"]:
        by_market[leg["market"]].append(leg)
    print(f"\n{'market':20} {'n':>3} {'mean ratio':>10} {'>1':>3}")
    for market, legs in sorted(by_market.items(), key=lambda kv: -len(kv[1])):
        ratios = [l["fair_ratio"] for l in legs]
        print(f"{market:20} {len(legs):3d} {sum(ratios)/len(ratios):10.4f} "
              f"{sum(1 for r in ratios if r > 1):3d}")

    print(f"\n{'market':20} {'side':6} {'line':>5} {'booked':>7} "
          f"{'fair_p':>7} {'ratio':>7}")
    for leg in sorted(summary["legs"], key=lambda l: -l["fair_ratio"]):
        line = f"{leg['line']:.1f}" if leg["line"] is not None else "-"
        print(f"{leg['market']:20} {leg['side']:6} {line:>5} "
              f"{leg['booked_odds']:>7d} {leg['fair_prob']:7.3f} "
              f"{leg['fair_ratio']:7.4f}")


if __name__ == "__main__":
    main()
