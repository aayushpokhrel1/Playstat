"""Team-market parlay builder (NRFI + F5). Two output classes, kept separate:
  * across_game: legs from DIFFERENT games -> independent joint = product.
  * same_game_pair: NRFI + F5 on the SAME game -> empirical lift-adjusted joint
    (the innings are nested; naive product is wrong).
Each recommendation carries an honest model-vs-market EV (README §14.1 layered goal).
"""

import argparse
import itertools
import json

import pandas as pd
from sqlalchemy import text

from ingestion import db
from optimizer.parlay import american_to_decimal, find_combinations
from modeling.correlation import pair_joint_prob, nrfi_f5_lift

TARGET_PAYOUT = 2.0
TOLERANCE = 0.15
MIN_LEGS, MAX_LEGS = 2, 3
TOP_N = 10


def recommendation_ev(joint_prob, combined_odds):
    return joint_prob * combined_odds - 1


def same_game_pairs(legs, lift_fn, target_payout, tolerance):
    """One 2-leg pair per game that has both a NRFI and an F5 leg."""
    out = []
    by_game = {}
    for leg in legs:
        by_game.setdefault(leg["game_id"], []).append(leg)
    for game_id, glegs in by_game.items():
        nrfi = next((l for l in glegs if l["market"] == "first_inning_runs"), None)
        f5 = next((l for l in glegs if l["market"] == "f5_runs"), None)
        if not nrfi or not f5:
            continue
        combined = nrfi["decimal_odds"] * f5["decimal_odds"]
        if abs(combined - target_payout) / target_payout > tolerance:
            continue
        lift, n = lift_fn(nrfi["side"], f5["side"])
        joint = pair_joint_prob(nrfi["model_prob"], f5["model_prob"], lift)
        out.append({"legs": [nrfi, f5], "combined_odds": combined, "joint_prob": joint,
                    "class": "same_game_pair", "lift": lift, "lift_n": n})
    out.sort(key=lambda m: m["joint_prob"], reverse=True)
    return out


def load_team_legs(engine, min_edge=0.0):
    """Candidate legs from game_edges joined to the latest game_lines odds and
    the model prob for the edge's side. min_edge=0 keeps the builder populated
    even without a real edge (layered goal); EV is tagged per recommendation."""
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT ge.game_id, ge.market, ge.side, ge.model_prob, ge.edge,
                       CASE ge.side WHEN 'over' THEN gl.over_odds ELSE gl.under_odds END AS odds
                FROM game_edges ge
                JOIN (
                    SELECT DISTINCT ON (game_id, market) game_id, market, over_odds, under_odds
                    FROM game_lines ORDER BY game_id, market, pulled_at DESC
                ) gl ON gl.game_id = ge.game_id AND gl.market = ge.market
                JOIN games g ON g.game_id = ge.game_id AND g.status != 'FT'
                WHERE ge.edge >= :min_edge
                """
            ),
            conn, params={"min_edge": min_edge},
        )
    if df.empty:
        return []
    df = df.dropna(subset=["odds"])
    df["decimal_odds"] = df["odds"].apply(american_to_decimal)
    return df.to_dict("records")


def save_team_recommendations(engine, matches, top_n):
    rows = 0
    with engine.begin() as conn:
        for m in matches[:top_n]:
            legs_json = [
                {"game_id": int(l["game_id"]), "market": l["market"], "side": l["side"],
                 "odds": int(l["odds"]), "model_prob": float(l["model_prob"])}
                for l in m["legs"]
            ]
            conn.execute(
                text(
                    """
                    INSERT INTO parlay_recommendations
                        (kind, target_payout, legs, joint_prob, combined_odds)
                    VALUES ('team', :tp, CAST(:legs AS JSONB), :jp, :co)
                    """
                ),
                {"tp": TARGET_PAYOUT, "legs": json.dumps(
                    {"class": m["class"], "ev": recommendation_ev(m["joint_prob"], m["combined_odds"]),
                     "legs": legs_json}),
                 "jp": m["joint_prob"], "co": m["combined_odds"]},
            )
            rows += 1
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-payout", type=float, default=TARGET_PAYOUT)
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    parser.add_argument("--max-legs", type=int, default=MAX_LEGS)
    parser.add_argument("--top-n", type=int, default=TOP_N)
    args = parser.parse_args()

    engine = db.get_engine()
    legs = load_team_legs(engine)
    print(f"team legs: {len(legs)}")
    if not legs:
        print("no team legs yet (expected until game_edges + game_lines have data).")
        return

    across = find_combinations(legs, args.target_payout, MIN_LEGS, args.max_legs, args.tolerance)
    for m in across:
        m["class"] = "across_game"

    lift_cache = {}
    def lift_fn(side_nrfi, side_f5):
        key = (side_nrfi, side_f5)
        if key not in lift_cache:
            lift_cache[key] = nrfi_f5_lift(engine, side_nrfi, side_f5)
        return lift_cache[key]

    pairs = same_game_pairs(legs, lift_fn, args.target_payout, args.tolerance)

    print(f"across-game combos: {len(across)}, same-game pairs: {len(pairs)}")
    saved = save_team_recommendations(engine, across, args.top_n) \
        + save_team_recommendations(engine, pairs, args.top_n)
    print(f"parlay_recommendations (kind=team): inserted {saved} rows")


if __name__ == "__main__":
    main()
