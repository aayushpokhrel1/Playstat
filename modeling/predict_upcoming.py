"""Predictions for games that haven't been played yet — the piece live edges
need (modeling/predict.py can only predict games that already have stats rows,
which made it backtest-only).

Requires modeling/features.py to have run with --upcoming-days > 0 first, so
rolling_player_features has as-of rows at the upcoming games' dates. The daily
job chains them.
"""

import argparse
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import text

from ingestion import db
from modeling.train import (
    STAT_CONFIG,
    fit_models,
    load_dataset,
    model_version,
    predicted_std_from_quantiles,
    stats_for_sport,
)


def _load_upcoming_frame(conn, sport, days):
    """One row per (player, upcoming game) with pivoted as-of features."""
    today = date.today()
    df = pd.read_sql(
        text(
            """
            SELECT p.player_id, g.game_id, g.date, rpf.feature, rpf.value
            FROM games g
            JOIN players p ON p.team_id IN (g.home_team_id, g.away_team_id)
            JOIN rolling_player_features rpf
              ON rpf.player_id = p.player_id AND rpf.as_of_date = g.date
            WHERE g.sport = :sport AND g.status != 'FT'
              AND g.date >= :today AND g.date <= :horizon
            """
        ),
        conn,
        params={"sport": sport, "today": today, "horizon": today + timedelta(days=days)},
    )
    if df.empty:
        return df
    wide = df.pivot_table(
        index=["player_id", "game_id"], columns="feature", values="value", aggfunc="first"
    ).reset_index()
    return wide


def predict_upcoming(engine, sport, days=2):
    with engine.begin() as conn:
        upcoming = _load_upcoming_frame(conn, sport, days)

    if upcoming.empty:
        print(f"({sport}) no upcoming games with features in the next {days} day(s) — "
              "run modeling.features --upcoming-days first, or there are no games scheduled.")
        return

    n_games = upcoming["game_id"].nunique()
    for stat in stats_for_sport(sport):
        _, feature_cols, _ = STAT_CONFIG[stat]

        history = load_dataset(engine, stat)
        if len(history) < 100:
            print(f"({stat}) only {len(history)} historical rows — skipping until more data exists.")
            continue
        mean_model, q16_model, q84_model, c16, c84 = fit_models(history, stat)

        X = upcoming.copy()
        for col in feature_cols:
            if col not in X.columns:
                X[col] = float("nan")
            X[col] = X[col].astype("float64")

        # Require the stat's own primary rolling average — a player with no
        # history for this stat (e.g. a batter for pitcher stats) gets skipped
        # rather than predicted off nothing but the opponent rating.
        X = X.dropna(subset=[feature_cols[0]])
        if X.empty:
            continue

        preds = mean_model.predict(X[feature_cols])
        q16 = q16_model.predict(X[feature_cols])
        q84 = q84_model.predict(X[feature_cols])

        rows = 0
        with engine.begin() as conn:
            for (_, record), mean, lo, hi in zip(X.iterrows(), preds, q16, q84):
                std = predicted_std_from_quantiles(lo, hi, c16, c84)
                conn.execute(
                    text(
                        """
                        INSERT INTO model_predictions
                            (player_id, game_id, stat_type, predicted_mean, predicted_std, model_version)
                        VALUES (:player_id, :game_id, :stat_type, :predicted_mean, :predicted_std, :model_version)
                        ON CONFLICT (player_id, game_id, stat_type, model_version)
                        DO UPDATE SET predicted_mean = EXCLUDED.predicted_mean, predicted_std = EXCLUDED.predicted_std
                        """
                    ),
                    {
                        "player_id": int(record["player_id"]),
                        "game_id": int(record["game_id"]),
                        "stat_type": stat,
                        "predicted_mean": float(mean),
                        "predicted_std": float(std),
                        "model_version": model_version(stat),
                    },
                )
                rows += 1
        print(f"({stat}) model_predictions: upserted {rows} rows across {n_games} upcoming game(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, choices=sorted({s for _, _, s in STAT_CONFIG.values()}))
    parser.add_argument("--days", type=int, default=2)
    args = parser.parse_args()
    predict_upcoming(db.get_engine(), args.sport, args.days)
