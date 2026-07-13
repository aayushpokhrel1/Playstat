import argparse

from ingestion import db
from modeling.train import STAT_CONFIG, fit_models, load_dataset, split_train_test

# How far empirical coverage can drift from the nominal quantile level before
# we call it out as miscalibrated, rather than just noise from a small test set.
DEVIATION_WARNING_THRESHOLD = 0.10


def check_coverage(engine, stat):
    target_col, feature_cols, _ = STAT_CONFIG[stat]
    df = load_dataset(engine, stat)

    if len(df) < 20:
        print(f"({stat}) too few rows for a meaningful calibration check yet.")
        return None

    train_df, test_df = split_train_test(df)
    _, q16_model, q84_model, c16, c84 = fit_models(train_df, stat)

    X_test = test_df[feature_cols]
    y_test = test_df[target_col]

    # Applying the same calibration correction fit_models computed, so this checks
    # what actually ships in model_predictions, not the raw uncalibrated quantiles.
    q16_preds = q16_model.predict(X_test) + c16
    q84_preds = q84_model.predict(X_test) + c84

    coverage_16 = (y_test.values <= q16_preds).mean()
    coverage_84 = (y_test.values <= q84_preds).mean()

    print(f"({stat}) n={len(test_df)} — empirical coverage at nominal 16%: {coverage_16:.1%}, "
          f"at nominal 84%: {coverage_84:.1%}")

    for label, empirical, nominal in [("16th percentile", coverage_16, 0.16), ("84th percentile", coverage_84, 0.84)]:
        if abs(empirical - nominal) > DEVIATION_WARNING_THRESHOLD:
            print(f"  WARNING: {label} model looks miscalibrated "
                  f"(expected ~{nominal:.0%} of actuals below it, got {empirical:.1%})")

    return coverage_16, coverage_84


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stat", choices=list(STAT_CONFIG) + ["all"], default="all")
    args = parser.parse_args()

    engine = db.get_engine()
    stats = list(STAT_CONFIG) if args.stat == "all" else [args.stat]
    for stat in stats:
        check_coverage(engine, stat)


if __name__ == "__main__":
    main()
