import argparse
import itertools
import json

import pandas as pd
from sqlalchemy import text

from ingestion import db

DEFAULT_MIN_LEGS = 2
DEFAULT_MAX_LEGS = 6
DEFAULT_TOLERANCE = 0.15
DEFAULT_TOP_N = 10


def american_to_decimal(odds):
    if odds > 0:
        return 1 + odds / 100
    return 1 + 100 / abs(odds)


def load_candidate_legs(engine):
    """Positive-edge legs only, with the American odds for whichever side has the edge."""
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT e.player_id, e.game_id, e.stat_type, e.side, e.model_prob,
                       CASE e.side WHEN 'over' THEN pl.over_odds ELSE pl.under_odds END AS odds
                FROM edges e
                JOIN (
                    SELECT DISTINCT ON (player_id, game_id, stat_type)
                        player_id, game_id, stat_type, over_odds, under_odds
                    FROM prop_lines
                    ORDER BY player_id, game_id, stat_type, pulled_at DESC
                ) pl ON pl.player_id = e.player_id AND pl.game_id = e.game_id AND pl.stat_type = e.stat_type
                WHERE e.edge > 0
                """
            ),
            conn,
        )
    if df.empty:
        return []

    df["decimal_odds"] = df["odds"].apply(american_to_decimal)
    return df.to_dict("records")


def find_combinations(legs, target_payout, min_legs, max_legs, tolerance):
    """Brute-force search over leg combinations, excluding same-game combos entirely
    (no attempt to model same-game correlation — see Phase 5 plan for why).
    """
    matches = []

    for size in range(min_legs, max_legs + 1):
        for combo in itertools.combinations(legs, size):
            game_ids = [leg["game_id"] for leg in combo]
            if len(set(game_ids)) != len(game_ids):
                continue  # two or more legs share a game — skip

            combined_odds = 1.0
            joint_prob = 1.0
            for leg in combo:
                combined_odds *= leg["decimal_odds"]
                joint_prob *= leg["model_prob"]

            if abs(combined_odds - target_payout) / target_payout > tolerance:
                continue

            matches.append({"legs": combo, "combined_odds": combined_odds, "joint_prob": joint_prob})

    matches.sort(key=lambda m: m["joint_prob"], reverse=True)
    return matches


def save_recommendations(engine, target_payout, matches, top_n):
    rows = 0
    with engine.begin() as conn:
        for match in matches[:top_n]:
            legs_json = [
                {
                    "player_id": leg["player_id"],
                    "game_id": leg["game_id"],
                    "stat_type": leg["stat_type"],
                    "side": leg["side"],
                    "model_prob": leg["model_prob"],
                    "odds": leg["odds"],
                }
                for leg in match["legs"]
            ]
            conn.execute(
                text(
                    """
                    INSERT INTO parlay_recommendations (target_payout, legs, joint_prob, combined_odds)
                    VALUES (:target_payout, CAST(:legs AS JSONB), :joint_prob, :combined_odds)
                    """
                ),
                {
                    "target_payout": target_payout,
                    "legs": json.dumps(legs_json),
                    "joint_prob": match["joint_prob"],
                    "combined_odds": match["combined_odds"],
                },
            )
            rows += 1
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-payout", type=float, required=True, help="e.g. 2.0 for a 2x payout")
    parser.add_argument("--min-legs", type=int, default=DEFAULT_MIN_LEGS)
    parser.add_argument("--max-legs", type=int, default=DEFAULT_MAX_LEGS)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    args = parser.parse_args()

    engine = db.get_engine()
    legs = load_candidate_legs(engine)
    print(f"candidate legs (positive edge): {len(legs)}")

    if not legs:
        print("No candidate legs — nothing to search (expected until prop_lines/edges have real data).")
        return

    matches = find_combinations(legs, args.target_payout, args.min_legs, args.max_legs, args.tolerance)
    print(f"combinations near target payout ({args.target_payout}x, tolerance {args.tolerance:.0%}): {len(matches)}")

    saved = save_recommendations(engine, args.target_payout, matches, args.top_n)
    print(f"parlay_recommendations: inserted {saved} rows")


if __name__ == "__main__":
    main()
