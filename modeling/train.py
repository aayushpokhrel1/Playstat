import argparse

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error
from sqlalchemy import text

from ingestion import db

# stat -> (target_col, feature_cols). Feature sets differ per stat because the
# schema only has a 10-game rolling average for points (no reb_avg_10/ast_avg_10).
STAT_CONFIG = {
    "points": ("points", ["pts_avg_5", "pts_avg_10", "opp_def_rating", "rest_days", "is_home", "is_back_to_back"]),
    "rebounds": ("rebounds", ["reb_avg_5", "opp_def_rating", "rest_days", "is_home", "is_back_to_back"]),
    "assists": ("assists", ["ast_avg_5", "opp_def_rating", "rest_days", "is_home", "is_back_to_back"]),
}


def model_version(stat):
    return f"xgboost_{stat}_v1"


def load_dataset(engine, stat):
    """Historical rows with both a known outcome and computed features for the given stat.
    Only covers games that already have player_game_stats — see modeling/features.py.
    """
    target_col, feature_cols = STAT_CONFIG[stat]
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT pgs.player_id, pgs.game_id, pgs.points, pgs.rebounds, pgs.assists, g.date,
                       rpf.pts_avg_5, rpf.pts_avg_10, rpf.reb_avg_5, rpf.ast_avg_5,
                       rpf.opp_def_rating, rpf.rest_days, rpf.is_home, rpf.is_back_to_back
                FROM player_game_stats pgs
                JOIN games g ON g.game_id = pgs.game_id
                JOIN rolling_player_features rpf
                  ON rpf.player_id = pgs.player_id AND rpf.as_of_date = g.date
                """
            ),
            conn,
        )
    df["date"] = pd.to_datetime(df["date"])
    for col in feature_cols:
        df[col] = df[col].astype("float64")
    # Only require the target — feature columns (e.g. pts_avg_10) are often NaN
    # this early in the data, and XGBoost handles missing feature values natively
    # via learned default-direction splits, so there's no need to drop those rows.
    return df.dropna(subset=[target_col]).sort_values("date")


def split_train_test(df, test_frac=0.2):
    """Date-based split — train on earlier games, test on the most recent slice."""
    cutoff_idx = int(len(df) * (1 - test_frac))
    cutoff_date = df.iloc[cutoff_idx]["date"]
    train_df = df[df["date"] < cutoff_date]
    test_df = df[df["date"] >= cutoff_date]
    return train_df, test_df


def _jittered_quantile(residuals, quantile, n_draws=200, seed=0):
    """Quantile of residuals, averaged over many U(-0.5, 0.5) jitters.

    points/rebounds/assists are all discrete counts, so identical predicted
    values against nearby integer actuals pile residuals into an exact tie
    (assists is the worst case: 267/1134 calibration residuals landed on
    precisely 0, an atom that swallowed the whole 10th-30th percentile band).
    A plain np.quantile call on ties like that returns 0 regardless of which
    correction is actually needed. Jittering breaks the ties so the quantile
    reflects where the tied mass should fall within the interval; averaging
    many draws keeps a single unlucky jitter from swinging the correction.
    """
    rng = np.random.default_rng(seed)
    draws = [np.quantile(residuals + rng.uniform(-0.5, 0.5, size=len(residuals)), quantile) for _ in range(n_draws)]
    return float(np.mean(draws))


def fit_models(train_df, stat):
    """Fits the mean model on all of train_df, but the quantile models get a
    split-conformal calibration correction — raw XGBoost quantile regression on
    this dataset is meaningfully miscalibrated (see modeling/calibration.py findings:
    q16 empirical coverage runs 25-33% vs. the nominal 16%), and hyperparameter
    tuning alone doesn't fix it (tried several configs, coverage barely moved).
    Holding out a calibration slice and correcting for the measured residual
    quantile is the standard fix for this failure mode.
    """
    target_col, feature_cols = STAT_CONFIG[stat]
    X = train_df[feature_cols]
    y = train_df[target_col]

    mean_model = xgb.XGBRegressor(objective="reg:squarederror", n_estimators=100, max_depth=4)
    mean_model.fit(X, y)

    proper_train, cal_df = split_train_test(train_df)
    X_pt, y_pt = proper_train[feature_cols], proper_train[target_col]
    X_cal, y_cal = cal_df[feature_cols], cal_df[target_col]

    q16_model = xgb.XGBRegressor(
        objective="reg:quantileerror", quantile_alpha=0.16, n_estimators=100, max_depth=4
    )
    q16_model.fit(X_pt, y_pt)

    q84_model = xgb.XGBRegressor(
        objective="reg:quantileerror", quantile_alpha=0.84, n_estimators=100, max_depth=4
    )
    q84_model.fit(X_pt, y_pt)

    c16 = _jittered_quantile(y_cal.values - q16_model.predict(X_cal), 0.16)
    c84 = _jittered_quantile(y_cal.values - q84_model.predict(X_cal), 0.84)

    return mean_model, q16_model, q84_model, c16, c84


def predicted_std_from_quantiles(q16_pred, q84_pred, c16, c84):
    """Applies the calibration correction, then derives predicted_std from the
    corrected ~68% interval. max(...,0) guards against quantile crossing —
    q84_pred isn't structurally guaranteed to exceed q16_pred since the two
    quantile models are trained independently.
    """
    corrected_lo = q16_pred + c16
    corrected_hi = q84_pred + c84
    return max(corrected_hi - corrected_lo, 0) / 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stat", choices=list(STAT_CONFIG), default="points")
    args = parser.parse_args()
    stat = args.stat
    target_col, feature_cols = STAT_CONFIG[stat]

    engine = db.get_engine()
    df = load_dataset(engine, stat)
    print(f"dataset ({stat}): {len(df)} rows with complete features")

    if len(df) < 20:
        print("Too few rows for a meaningful train/test split yet — re-run once more data is loaded.")
        return

    train_df, test_df = split_train_test(df)
    print(f"train: {len(train_df)} rows, test: {len(test_df)} rows (cutoff date: {test_df['date'].min().date()})")

    mean_model, q16_model, q84_model, c16, c84 = fit_models(train_df, stat)
    print(f"calibration correction ({stat}): c16={c16:.2f}, c84={c84:.2f}")

    X_test = test_df[feature_cols]
    y_test = test_df[target_col]
    preds = mean_model.predict(X_test)
    mae = mean_absolute_error(y_test, preds)
    print(f"held-out MAE ({stat}): {mae:.2f}")

    q16_preds = q16_model.predict(X_test) + c16
    q84_preds = q84_model.predict(X_test) + c84
    crossed = (q84_preds < q16_preds).sum()
    print(f"quantile crossing (q84 < q16, post-calibration) on {crossed}/{len(X_test)} test rows")

    print(f"held-out test game_ids: {sorted(test_df['game_id'].unique().tolist())}")


if __name__ == "__main__":
    main()
