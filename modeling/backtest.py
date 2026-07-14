from sklearn.metrics import mean_absolute_error
from sqlalchemy import text

from ingestion import db
from modeling.calibration import check_coverage
from modeling.train import STAT_CONFIG, fit_models, load_dataset, model_version, split_train_test


def run_backtest(engine, stat):
    target_col, feature_cols, _ = STAT_CONFIG[stat]
    df = load_dataset(engine, stat)

    if len(df) < 20:
        print(f"({stat}) too few rows for a meaningful backtest yet.")
        return

    train_df, test_df = split_train_test(df)
    mean_model, _, _, _, _ = fit_models(train_df, stat)

    X_test = test_df[feature_cols]
    y_test = test_df[target_col]
    mae = mean_absolute_error(y_test, mean_model.predict(X_test))

    coverage = check_coverage(engine, stat)
    if coverage is None:
        coverage_16, coverage_84 = None, None
    else:
        coverage_16, coverage_84 = float(coverage[0]), float(coverage[1])

    n_test_games = int(test_df["game_id"].nunique())

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO backtest_runs (stat_type, model_version, n_test_games, mae, coverage_16, coverage_84)
                VALUES (:stat_type, :model_version, :n_test_games, :mae, :coverage_16, :coverage_84)
                """
            ),
            {
                "stat_type": stat,
                "model_version": model_version(stat),
                "n_test_games": n_test_games,
                "mae": float(mae),
                "coverage_16": coverage_16,
                "coverage_84": coverage_84,
            },
        )

    print(f"({stat}) logged backtest run: n_test_games={n_test_games}, mae={mae:.2f}, "
          f"coverage_16={coverage_16}, coverage_84={coverage_84}")


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=["all"] + sorted({s for _, _, s in STAT_CONFIG.values()}),
                        default="all")
    args = parser.parse_args()

    engine = db.get_engine()
    for stat, (_, _, sport) in STAT_CONFIG.items():
        if args.sport in ("all", sport):
            run_backtest(engine, stat)


if __name__ == "__main__":
    main()
