import argparse

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error
from sqlalchemy import text

from ingestion import db

# stat -> (target_col, feature_cols, sport). Feature names come from
# modeling/features.py's SPORT_CONFIG windows plus the schedule/opponent
# features. rest_days/is_back_to_back are NBA-only: MLB teams play near-daily,
# so those carry no signal there. at_bats/outs_recorded averages act as the
# playing-time exposure features (the MLB analogue of minutes).
STAT_CONFIG = {
    "points": ("points", ["pts_avg_5", "pts_avg_10", "opp_def_rating", "rest_days", "is_home", "is_back_to_back"], "nba"),
    "rebounds": ("rebounds", ["reb_avg_5", "opp_def_rating", "rest_days", "is_home", "is_back_to_back"], "nba"),
    "assists": ("assists", ["ast_avg_5", "opp_def_rating", "rest_days", "is_home", "is_back_to_back"], "nba"),
    # MLB batters
    "hits": ("hits", ["hits_avg_5", "hits_avg_10", "ab_avg_5", "opp_def_rating", "is_home"], "mlb"),
    "total_bases": ("total_bases", ["tb_avg_5", "tb_avg_10", "hr_avg_10", "ab_avg_5", "opp_def_rating", "is_home"], "mlb"),
    "home_runs": ("home_runs", ["hr_avg_10", "tb_avg_10", "ab_avg_5", "opp_def_rating", "is_home"], "mlb"),
    "rbis": ("rbis", ["rbis_avg_5", "tb_avg_5", "ab_avg_5", "opp_def_rating", "is_home"], "mlb"),
    "runs": ("runs", ["runs_avg_5", "hits_avg_5", "walks_avg_5", "ab_avg_5", "opp_def_rating", "is_home"], "mlb"),
    "batter_strikeouts": ("batter_strikeouts", ["bso_avg_5", "ab_avg_5", "opp_def_rating", "is_home"], "mlb"),
    "walks": ("walks", ["walks_avg_5", "ab_avg_5", "opp_def_rating", "is_home"], "mlb"),
    "stolen_bases": ("stolen_bases", ["sb_avg_10", "ab_avg_5", "opp_def_rating", "is_home"], "mlb"),
    # MLB pitchers
    "pitcher_strikeouts": ("pitcher_strikeouts", ["pso_avg_3", "outs_avg_3", "opp_def_rating", "is_home"], "mlb"),
    "earned_runs": ("earned_runs", ["er_avg_3", "outs_avg_3", "ha_avg_3", "opp_def_rating", "is_home"], "mlb"),
    "hits_allowed": ("hits_allowed", ["ha_avg_3", "outs_avg_3", "opp_def_rating", "is_home"], "mlb"),
    "walks_allowed": ("walks_allowed", ["wa_avg_3", "outs_avg_3", "opp_def_rating", "is_home"], "mlb"),
    "outs_recorded": ("outs_recorded", ["outs_avg_3", "er_avg_3", "opp_def_rating", "is_home"], "mlb"),
}


def stats_for_sport(sport):
    return [stat for stat, (_, _, s) in STAT_CONFIG.items() if s == sport]


def model_version(stat):
    return f"xgboost_{stat}_v1"


def load_dataset(engine, stat):
    """Historical rows with both a known outcome and computed features for the given stat.
    Only covers games that already have player_game_stats — see modeling/features.py.

    player_game_stats and rolling_player_features are long format (multi-sport,
    see db/migrations/001_multi_sport.sql); this pivots both back to the wide
    frame the models train on.
    """
    target_col, feature_cols, sport = STAT_CONFIG[stat]
    with engine.begin() as conn:
        stats_df = pd.read_sql(
            text(
                """
                SELECT pgs.player_id, pgs.game_id, pgs.stat_type, pgs.value, g.date
                FROM player_game_stats pgs
                JOIN games g ON g.game_id = pgs.game_id
                WHERE g.sport = :sport AND pgs.stat_type = :stat_type
                """
            ),
            conn,
            params={"sport": sport, "stat_type": target_col},
        )
        feats_df = pd.read_sql(
            text(
                """
                SELECT rpf.player_id, rpf.as_of_date, rpf.feature, rpf.value
                FROM rolling_player_features rpf
                WHERE rpf.feature = ANY(:features)
                """
            ),
            conn,
            params={"features": list(feature_cols)},
        )

    stats_df = stats_df.rename(columns={"value": target_col})
    feats_wide = feats_df.pivot_table(
        index=["player_id", "as_of_date"], columns="feature", values="value", aggfunc="first"
    ).reset_index()

    stats_df["date"] = pd.to_datetime(stats_df["date"])
    feats_wide["as_of_date"] = pd.to_datetime(feats_wide["as_of_date"])

    df = stats_df.merge(
        feats_wide,
        left_on=["player_id", "date"],
        right_on=["player_id", "as_of_date"],
        how="inner",
    )
    for col in feature_cols:
        if col not in df.columns:
            df[col] = float("nan")
        df[col] = df[col].astype("float64")
    df[target_col] = df[target_col].astype("float64")
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
    target_col, feature_cols, _ = STAT_CONFIG[stat]
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
    target_col, feature_cols, _ = STAT_CONFIG[stat]

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
