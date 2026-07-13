import argparse

from sqlalchemy import text

from ingestion import db
from modeling.train import STAT_CONFIG, fit_models, load_dataset, model_version, predicted_std_from_quantiles


def predict_for_games(engine, game_ids, stat):
    """Predicts the given stat for every player row in the given games.

    Trains on all historical rows *except* the target games, so this is leakage-safe
    whether game_ids are a held-out backtest slice or (once the current season is
    loaded) genuinely future games — either way the target rows aren't in training.
    """
    _, feature_cols = STAT_CONFIG[stat]
    df = load_dataset(engine, stat)
    predict_df = df[df["game_id"].isin(game_ids)]
    train_df = df[~df["game_id"].isin(game_ids)]

    if predict_df.empty:
        print("No rows found for the given game_ids — do they have features computed? (see modeling/features.py)")
        return

    mean_model, q16_model, q84_model, c16, c84 = fit_models(train_df, stat)

    X = predict_df[feature_cols]
    predicted_mean = mean_model.predict(X)
    q16 = q16_model.predict(X)
    q84 = q84_model.predict(X)
    predicted_std = [predicted_std_from_quantiles(lo, hi, c16, c84) for lo, hi in zip(q16, q84)]

    rows = 0
    with engine.begin() as conn:
        for (_, record), mean, std in zip(predict_df.iterrows(), predicted_mean, predicted_std):
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

    print(f"model_predictions: upserted {rows} rows for {len(game_ids)} game(s), stat={stat}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stat", choices=list(STAT_CONFIG), default="points")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--game-ids", help="comma-separated game_id list")
    group.add_argument("--after-date", help="predict for all games with date > this (YYYY-MM-DD)")
    args = parser.parse_args()

    engine = db.get_engine()

    if args.game_ids:
        game_ids = [int(g) for g in args.game_ids.split(",")]
    else:
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT game_id FROM games WHERE date > :after"), {"after": args.after_date}
            ).fetchall()
        game_ids = [r[0] for r in rows]

    predict_for_games(engine, game_ids, args.stat)


if __name__ == "__main__":
    main()
