import argparse

import numpy as np

from ingestion import db
from modeling.distributions import cdf_array, ppf_array, randomized_pit
from modeling.train import STAT_CONFIG, fit_models, load_dataset, split_train_test

# How far empirical coverage can drift from the nominal quantile level before
# we call it out as miscalibrated, rather than just noise from a small test set.
# Only applied to the Gaussian (NBA) family — for discrete counts the nominal
# levels are structurally unreachable (see check_coverage_discrete), so the
# comparison target there is the distribution's own expected coverage instead.
DEVIATION_WARNING_THRESHOLD = 0.10


def check_coverage_discrete(model, X_test, y_test, stat):
    """Calibration for the discrete (MLB) family.

    coverage_16/84 here are the empirical rates P(X <= ppf(q)) at the discrete
    distribution's 16th/84th percentiles. For lumpy counts these are expected to
    OVERSHOOT nominal (ppf jumps to the next integer, which carries extra mass),
    so each is printed next to its model-expected coverage E[F(ppf(q))] — a
    calibrated model matches the expected number, not the nominal one.

    The uniformity check is a randomized PIT: u = F(x-1) + V*(F(x)-F(x-1)),
    V~U(0,1), which is exactly Uniform(0,1) when the predictive law is right.
    Reported as the mean/std of u (ideal 0.5 / 0.289) plus a coarse KS distance.
    """
    mu = np.maximum(model.predict_mean(X_test), 1e-6)
    std = model.predict_std(X_test, mean=mu)
    y = y_test.values

    cov, exp_cov = {}, {}
    for q in (0.16, 0.84):
        ppfs = ppf_array(mu, std, q)
        cov[q] = float((y <= ppfs).mean())
        exp_cov[q] = float(cdf_array(mu, std, ppfs).mean())

    u = randomized_pit(mu, std, y)
    grid = np.linspace(0, 1, 101)
    ks = float(np.max(np.abs(np.searchsorted(np.sort(u), grid, side="right") / len(u) - grid)))

    print(f"({stat}) n={len(y)} [discrete] coverage at q16: {cov[0.16]:.1%} "
          f"(model-expected {exp_cov[0.16]:.1%}), q84: {cov[0.84]:.1%} (model-expected {exp_cov[0.84]:.1%})")
    print(f"  randomized PIT: mean={u.mean():.3f} (ideal 0.500), std={u.std():.3f} (ideal 0.289), "
          f"KS distance vs U(0,1)={ks:.3f}")

    for q in (0.16, 0.84):
        if abs(cov[q] - exp_cov[q]) > DEVIATION_WARNING_THRESHOLD:
            print(f"  WARNING: empirical coverage at q{int(q*100)} ({cov[q]:.1%}) is far from the "
                  f"distribution's own expected coverage ({exp_cov[q]:.1%}) — the discrete model "
                  "looks miscalibrated.")

    return cov[0.16], cov[0.84]


def check_coverage_gaussian(model, X_test, y_test, stat):
    """NBA path, unchanged: empirical coverage of the conformal-corrected
    q16/q84 quantile predictions against their nominal levels."""
    q16_preds = model.q16_model.predict(X_test) + model.c16
    q84_preds = model.q84_model.predict(X_test) + model.c84

    coverage_16 = (y_test.values <= q16_preds).mean()
    coverage_84 = (y_test.values <= q84_preds).mean()

    print(f"({stat}) n={len(y_test)} — empirical coverage at nominal 16%: {coverage_16:.1%}, "
          f"at nominal 84%: {coverage_84:.1%}")

    for label, empirical, nominal in [("16th percentile", coverage_16, 0.16), ("84th percentile", coverage_84, 0.84)]:
        if abs(empirical - nominal) > DEVIATION_WARNING_THRESHOLD:
            print(f"  WARNING: {label} model looks miscalibrated "
                  f"(expected ~{nominal:.0%} of actuals below it, got {empirical:.1%})")

    return coverage_16, coverage_84


def check_coverage(engine, stat):
    target_col, feature_cols, _ = STAT_CONFIG[stat]
    df = load_dataset(engine, stat)

    if len(df) < 20:
        print(f"({stat}) too few rows for a meaningful calibration check yet.")
        return None

    train_df, test_df = split_train_test(df)
    model = fit_models(train_df, stat)

    X_test = test_df[feature_cols]
    y_test = test_df[target_col]

    # Same fitted models that ship in model_predictions, so this checks what
    # actually runs, not a variant.
    if model.family == "discrete":
        return check_coverage_discrete(model, X_test, y_test, stat)
    return check_coverage_gaussian(model, X_test, y_test, stat)


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
