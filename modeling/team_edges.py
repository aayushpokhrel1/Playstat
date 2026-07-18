"""Game-market edges (NRFI, F5): game_lines x game_predictions -> game_edges.

The team-market analogue of modeling/edges.py. game-level markets were served
(game_predictions) but never turned into edges/parlays before this. Same devig
math as edges.py; keyed by (game_id, market) instead of (player, game, stat).
"""

import pandas as pd
from sqlalchemy import text

from ingestion import db
from modeling.edges import devig


def best_side(model_p_over, model_p_under, implied_over, implied_under):
    edge_over = model_p_over - implied_over
    edge_under = model_p_under - implied_under
    if edge_over >= edge_under:
        return "over", model_p_over, implied_over, edge_over
    return "under", model_p_under, implied_under, edge_under


def _latest_game_lines(conn):
    return pd.read_sql(
        text(
            """
            SELECT DISTINCT ON (game_id, market)
                game_id, market, line_value, over_odds, under_odds
            FROM game_lines
            ORDER BY game_id, market, pulled_at DESC
            """
        ),
        conn,
    )


def compute_team_edges(engine):
    with engine.begin() as conn:
        lines = _latest_game_lines(conn)
        if lines.empty:
            print("team_edges: game_lines empty — nothing to compute yet.")
            return
        preds = pd.read_sql(
            text(
                """
                SELECT game_id, market, prob_over, prob_under
                FROM game_predictions
                WHERE prob_over IS NOT NULL AND prob_under IS NOT NULL
                """
            ),
            conn,
        )

    merged = lines.merge(preds, on=["game_id", "market"], how="inner")
    rows, skipped = 0, 0
    fresh = []
    with engine.begin() as conn:
        for r in merged.to_dict("records"):
            if pd.isna(r["over_odds"]) or pd.isna(r["under_odds"]):
                skipped += 1
                continue
            implied_over, implied_under = devig(r["over_odds"], r["under_odds"])
            side, mp, ip, edge = best_side(
                float(r["prob_over"]), float(r["prob_under"]), implied_over, implied_under
            )
            db.upsert(
                conn, "game_edges", ["game_id", "market"],
                {"game_id": int(r["game_id"]), "market": r["market"],
                 "side": side, "model_prob": float(mp),
                 "implied_prob": float(ip), "edge": float(edge)},
            )
            rows += 1
            fresh.append((int(r["game_id"]), r["market"]))

        # Prune stale unplayed-game edges (same rationale as edges.py).
        stale = conn.execute(
            text(
                """
                DELETE FROM game_edges ge USING games g
                WHERE g.game_id = ge.game_id AND g.status != 'FT'
                  AND NOT EXISTS (
                      SELECT 1 FROM unnest(CAST(:gids AS bigint[]), CAST(:mkts AS text[])) AS f(game_id, market)
                      WHERE f.game_id = ge.game_id AND f.market = ge.market)
                """
            ),
            {"gids": [k[0] for k in fresh], "mkts": [k[1] for k in fresh]},
        ).rowcount if fresh else 0

    print(f"team_edges: upserted {rows} rows"
          + (f" (skipped {skipped} one-sided)" if skipped else "")
          + (f" (pruned {stale} stale)" if stale else ""))


if __name__ == "__main__":
    compute_team_edges(db.get_engine())
