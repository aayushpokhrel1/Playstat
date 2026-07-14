"""Closing-line-value (CLV) tracking — the referee for edge quality.

For every edge whose game has finished, compare the de-vigged implied
probability of our side at the line when the edge was first flagged
(edges.created_at) against the same at the closing line (last snapshot).
Positive CLV = the market moved toward our position. A model whose edges
average positive CLV is finding real value even before enough bets settle
for win/loss records to mean anything; one that averages negative CLV is
being picked off, no matter how the first few bets land.

Run daily after box scores finalize games (the com.playstat.mlb chain).
Each (player, game, stat) is recorded once, when its game is first seen
finished; edges on games with a single line snapshot record CLV 0 with
n_snapshots=1 so they can be excluded from averages.
"""

import pandas as pd
from sqlalchemy import text

from ingestion import db
from modeling.edges import devig


def _implied_prob(side, over_odds, under_odds):
    p_over, p_under = devig(over_odds, under_odds)
    return p_over if side == "over" else p_under


def compute_clv(engine):
    with engine.begin() as conn:
        edges = pd.read_sql(
            text(
                """
                SELECT e.player_id, e.game_id, e.stat_type, e.side, e.created_at
                FROM edges e
                JOIN games g ON g.game_id = e.game_id
                WHERE g.status = 'FT'
                  AND NOT EXISTS (
                      SELECT 1 FROM clv_records c
                      WHERE c.player_id = e.player_id AND c.game_id = e.game_id
                        AND c.stat_type = e.stat_type
                  )
                """
            ),
            conn,
        )
        if edges.empty:
            print("clv: no newly-finished games with edges to score.")
            return
        lines = pd.read_sql(
            text(
                """
                SELECT player_id, game_id, stat_type, over_odds, under_odds, pulled_at
                FROM prop_lines
                WHERE game_id = ANY(:game_ids)
                  AND over_odds IS NOT NULL AND under_odds IS NOT NULL
                ORDER BY pulled_at
                """
            ),
            conn,
            params={"game_ids": [int(g) for g in edges["game_id"].unique()]},
        )

    grouped = lines.groupby(["player_id", "game_id", "stat_type"])
    rows = 0
    with engine.begin() as conn:
        for rec in edges.to_dict("records"):
            key = (rec["player_id"], rec["game_id"], rec["stat_type"])
            try:
                snaps = grouped.get_group(key)
            except KeyError:
                continue  # edge without a two-sided line history

            # Line in effect when the edge was flagged: the last pull at or
            # before created_at, else the earliest pull we have.
            at_flag = snaps[snaps["pulled_at"] <= rec["created_at"]]
            rec_snap = (at_flag.iloc[-1] if not at_flag.empty else snaps.iloc[0])
            closing_snap = snaps.iloc[-1]

            rec_prob = _implied_prob(rec["side"], rec_snap["over_odds"], rec_snap["under_odds"])
            closing_prob = _implied_prob(rec["side"], closing_snap["over_odds"], closing_snap["under_odds"])
            rec_odds = rec_snap["over_odds"] if rec["side"] == "over" else rec_snap["under_odds"]
            closing_odds = closing_snap["over_odds"] if rec["side"] == "over" else closing_snap["under_odds"]

            db.upsert(
                conn,
                "clv_records",
                ["player_id", "game_id", "stat_type"],
                {
                    "player_id": rec["player_id"],
                    "game_id": rec["game_id"],
                    "stat_type": rec["stat_type"],
                    "side": rec["side"],
                    "rec_odds": int(rec_odds),
                    "rec_implied_prob": float(rec_prob),
                    "closing_odds": int(closing_odds),
                    "closing_implied_prob": float(closing_prob),
                    "clv": float(closing_prob - rec_prob),
                    "n_snapshots": int(len(snaps)),
                    "rec_at": rec_snap["pulled_at"],
                    "closing_pulled_at": closing_snap["pulled_at"],
                },
            )
            rows += 1

    print(f"clv: recorded {rows} edges from newly-finished games")
    with engine.begin() as conn:
        summary = conn.execute(
            text(
                """
                SELECT COUNT(*) AS n, ROUND(AVG(clv), 4) AS avg_clv,
                       ROUND(AVG((clv > 0)::int), 3) AS pct_positive
                FROM clv_records WHERE n_snapshots > 1
                """
            )
        ).fetchone()
    if summary and summary[0]:
        print(f"clv (all-time, multi-snapshot only): n={summary[0]}, "
              f"avg CLV {summary[1]}, {float(summary[2]):.0%} positive")


if __name__ == "__main__":
    compute_clv(db.get_engine())
