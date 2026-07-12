import pandas as pd
from scipy.stats import norm
from sqlalchemy import text

from ingestion import db
from modeling.train import model_version


def odds_to_probability(american_odds):
    if american_odds > 0:
        return 100 / (american_odds + 100)
    return abs(american_odds) / (abs(american_odds) + 100)


def devig(over_odds, under_odds):
    """Removes the sportsbook's overround so the two implied probabilities sum to 1."""
    p_over_raw = odds_to_probability(over_odds)
    p_under_raw = odds_to_probability(under_odds)
    overround = p_over_raw + p_under_raw
    return p_over_raw / overround, p_under_raw / overround


def latest_prop_lines(conn):
    """One row per (player, game, stat) — the most recent pull, since prop_lines
    is a snapshot-per-pull table and lines move over time.
    """
    return pd.read_sql(
        text(
            """
            SELECT DISTINCT ON (player_id, game_id, stat_type)
                player_id, game_id, stat_type, line_value, over_odds, under_odds
            FROM prop_lines
            ORDER BY player_id, game_id, stat_type, pulled_at DESC
            """
        ),
        conn,
    )


def compute_edges(engine):
    with engine.begin() as conn:
        lines = latest_prop_lines(conn)

        if lines.empty:
            print("edges: prop_lines is empty — nothing to compute yet (expected until live odds exist).")
            return

        predictions = pd.read_sql(
            text(
                """
                SELECT player_id, game_id, stat_type, predicted_mean, predicted_std, model_version
                FROM model_predictions
                """
            ),
            conn,
        )

    merged = lines.merge(predictions, on=["player_id", "game_id", "stat_type"], how="inner")
    merged = merged[merged["model_version"] == merged["stat_type"].map(model_version)]

    rows = 0
    with engine.begin() as conn:
        for record in merged.to_dict("records"):
            model_prob_over = 1 - norm.cdf(
                record["line_value"], loc=record["predicted_mean"], scale=record["predicted_std"]
            )
            model_prob_under = 1 - model_prob_over

            implied_over, implied_under = devig(record["over_odds"], record["under_odds"])

            edge_over = model_prob_over - implied_over
            edge_under = model_prob_under - implied_under

            if edge_over >= edge_under:
                side, model_prob, implied_prob, edge = "over", model_prob_over, implied_over, edge_over
            else:
                side, model_prob, implied_prob, edge = "under", model_prob_under, implied_under, edge_under

            db.upsert(
                conn,
                "edges",
                ["player_id", "game_id", "stat_type"],
                {
                    "player_id": record["player_id"],
                    "game_id": record["game_id"],
                    "stat_type": record["stat_type"],
                    "model_prob": float(model_prob),
                    "implied_prob": float(implied_prob),
                    "edge": float(edge),
                    "side": side,
                },
            )

            conn.execute(
                text(
                    """
                    UPDATE model_predictions
                    SET prob_over = :prob_over, prob_under = :prob_under
                    WHERE player_id = :player_id AND game_id = :game_id
                      AND stat_type = :stat_type AND model_version = :model_version
                    """
                ),
                {
                    "prob_over": float(model_prob_over),
                    "prob_under": float(model_prob_under),
                    "player_id": record["player_id"],
                    "game_id": record["game_id"],
                    "stat_type": record["stat_type"],
                    "model_version": record["model_version"],
                },
            )
            rows += 1

    print(f"edges: upserted {rows} rows")


if __name__ == "__main__":
    compute_edges(db.get_engine())
