"""Game-market edges (NRFI, F5): game_lines x game_predictions -> game_edges.

The team-market analogue of modeling/edges.py. game-level markets were served
(game_predictions) but never turned into edges/parlays before this. Same devig
math as edges.py; keyed by (game_id, market) instead of (player, game, stat).
"""

import pandas as pd
from sqlalchemy import text

from ingestion import db
from modeling.edges import devig
from modeling.f5 import MODEL_VERSION as F5_VERSION
from modeling.first_inning import MODEL_VERSION as FI_VERSION

# The current model version per game market. game_predictions accumulates old
# versions (e.g. xgb_poisson_fi_v1) across model bumps; without this pin the merge
# would non-deterministically mix versions for the same (game_id, market).
CURRENT_VERSIONS = {"first_inning_runs": FI_VERSION, "f5_runs": F5_VERSION}


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
                SELECT game_id, market, model_version,
                       line_value AS model_line, prob_over, prob_under
                FROM game_predictions
                WHERE prob_over IS NOT NULL AND prob_under IS NOT NULL
                """
            ),
            conn,
        )

    # Keep only the current model version per market (drops stale predictions).
    preds = preds[preds.apply(
        lambda p: CURRENT_VERSIONS.get(p["market"]) == p["model_version"], axis=1
    )]

    merged = lines.merge(preds, on=["game_id", "market"], how="inner")
    rows, skipped, line_mismatch = 0, 0, 0
    fresh = []
    with engine.begin() as conn:
        for r in merged.to_dict("records"):
            if pd.isna(r["over_odds"]) or pd.isna(r["under_odds"]):
                skipped += 1
                continue
            # The model's probability is only comparable to the book's odds when
            # both refer to the SAME line. The first-inning model predicts at 1.5
            # (P<=1 run) while books quote NRFI at 0.5 (P=0 runs) — comparing those
            # manufactures huge fake edges. Require the lines to match; F5 derives
            # its prob at the book line so it matches, NRFI-at-0.5 is skipped until
            # the first-inning model predicts at the book's line.
            if pd.isna(r["model_line"]) or abs(float(r["model_line"]) - float(r["line_value"])) > 1e-6:
                line_mismatch += 1
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
          + (f" (skipped {line_mismatch} model/market line mismatch)" if line_mismatch else "")
          + (f" (pruned {stale} stale)" if stale else ""))


if __name__ == "__main__":
    compute_team_edges(db.get_engine())
