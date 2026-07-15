"""Head-to-head validation of the discrete (Poisson/negative-binomial) MLB models
against the machinery they replaced — the acceptance gate for the v2 bump.

For each MLB stat, on the held-out time-based test slice (same split_train_test
used everywhere):

  OLD: reg:squarederror mean + conformal-corrected quantile std, with edges.py's
       former stopgap applied verbatim — Poisson at the predicted mean when
       mean < 5 (ignoring std), Gaussian otherwise.
  NEW: count:poisson mean + per-stat NB2 dispersion r from the calibration
       slice; P(over) from the exact discrete CDF via modeling/distributions.py.

Scored on P(over line) vs the realized over/under outcome: Brier score and
log-loss per stat, plus a reliability table (predicted P(over) bins vs empirical
over-rate) and the randomized-PIT uniformity summary for the new model.

Lines: every prop_lines row in the DB is for a still-scheduled game, so none
join to realized outcomes — ALL lines here are SYNTHESIZED. Each test row gets
line = floor(its primary rolling-average feature) + 0.5 (mimicking how books
anchor a line at the player's recent form), clamped to the range of real book
lines observed for that stat where we have them; rows without the feature fall
back to the stat's median real book line. Pushes are impossible at half-integer
lines, so every row scores.

Read-only against the DB; writes nothing.

Run: python -m modeling.eval_discrete [--stat hits]
"""

import argparse
import math

import numpy as np
from scipy.stats import norm, poisson

from ingestion import db
from modeling.distributions import prob_over_discrete, randomized_pit
from modeling.train import (
    STAT_CONFIG,
    fit_discrete,
    fit_gaussian,
    load_dataset,
    split_train_test,
    stats_for_sport,
)

# Range of real book lines seen in prop_lines per stat (2026-07 snapshot),
# used only to clamp synthesized lines to realistic values.
REAL_LINE_RANGE = {
    "hits": (0.5, 1.5), "total_bases": (0.5, 1.5), "home_runs": (0.5, 0.5),
    "rbis": (0.5, 1.5), "runs": (0.5, 0.5), "batter_strikeouts": (0.5, 1.5),
    "walks": (0.5, 0.5), "stolen_bases": (0.5, 0.5),
    "pitcher_strikeouts": (2.5, 6.5), "earned_runs": (1.5, 2.5),
    "hits_allowed": (4.5, 6.5), "walks_allowed": (1.5, 2.5),
    "outs_recorded": (15.5, 17.5),
}
OLD_POISSON_MEAN_CUTOFF = 5  # edges.py's former stopgap threshold


def synthesize_lines(test_df, stat, median_line):
    """Per-row synthetic line anchored at the player's primary rolling average."""
    lo, hi = REAL_LINE_RANGE[stat]
    primary = test_df[STAT_CONFIG[stat][1][0]].values
    lines = np.where(np.isnan(primary), median_line, np.floor(np.nan_to_num(primary)) + 0.5)
    return np.clip(lines, lo, hi)


def old_prob_over(mean, std, line):
    """edges.py's pre-v2 behavior, verbatim."""
    if mean < OLD_POISSON_MEAN_CUTOFF:
        return float(1 - poisson.cdf(math.floor(line), max(float(mean), 0.01)))
    return float(1 - norm.cdf(line, loc=mean, scale=std))


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def log_loss(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def reliability(p, y, n_bins=5):
    """(bin_range, n, mean predicted P(over), empirical over-rate) rows."""
    edges = np.linspace(0, 1, n_bins + 1)
    rows = []
    for i in range(n_bins):
        mask = (p >= edges[i]) & (p < edges[i + 1] if i < n_bins - 1 else p <= edges[i + 1])
        if mask.sum() == 0:
            continue
        rows.append((f"[{edges[i]:.1f},{edges[i+1]:.1f})", int(mask.sum()),
                     float(p[mask].mean()), float(y[mask].mean())))
    return rows


def evaluate_stat(engine, stat):
    target_col, feature_cols, _ = STAT_CONFIG[stat]
    df = load_dataset(engine, stat)
    if len(df) < 100:
        print(f"({stat}) only {len(df)} rows — skipping.")
        return None

    train_df, test_df = split_train_test(df)
    X_test = test_df[feature_cols]
    y = test_df[target_col].values

    old = fit_gaussian(train_df, stat)
    new = fit_discrete(train_df, stat)

    old_mean = old.predict_mean(X_test)
    old_std = old.predict_std(X_test)
    new_mean = np.maximum(new.predict_mean(X_test), 1e-6)
    new_std = new.predict_std(X_test, mean=new_mean)

    lines = synthesize_lines(test_df, stat, median_line=np.floor(np.median(y)) + 0.5)
    over = (y > lines).astype(float)

    p_old = np.array([old_prob_over(m, s, l) for m, s, l in zip(old_mean, old_std, lines)])
    p_new = np.array([prob_over_discrete(m, s, l) for m, s, l in zip(new_mean, new_std, lines)])

    u = randomized_pit(new_mean, new_std, y)

    res = {
        "stat": stat, "n": len(y),
        "family": "poisson" if np.isinf(new.r) else "nbinom", "r": new.r,
        "mae_old": float(np.mean(np.abs(y - old_mean))),
        "mae_new": float(np.mean(np.abs(y - new_mean))),
        "brier_old": brier(p_old, over), "brier_new": brier(p_new, over),
        "ll_old": log_loss(p_old, over), "ll_new": log_loss(p_new, over),
        "pit_mean": float(u.mean()), "pit_std": float(u.std()),
        "reliability_new": reliability(p_new, over),
        "reliability_old": reliability(p_old, over),
        "over_rate": float(over.mean()),
    }

    fam = "Poisson" if np.isinf(new.r) else f"NB(r={new.r:.2f})"
    print(f"\n=== {stat} (n={res['n']}, lines synthesized, family={fam}, base over-rate {res['over_rate']:.1%}) ===")
    print(f"  MAE      old {res['mae_old']:.3f}  new {res['mae_new']:.3f}")
    print(f"  Brier    old {res['brier_old']:.4f}  new {res['brier_new']:.4f}"
          f"  ({'new better' if res['brier_new'] <= res['brier_old'] else 'OLD BETTER'})")
    print(f"  log-loss old {res['ll_old']:.4f}  new {res['ll_new']:.4f}"
          f"  ({'new better' if res['ll_new'] <= res['ll_old'] else 'OLD BETTER'})")
    print(f"  randomized PIT: mean={res['pit_mean']:.3f} (ideal 0.500), std={res['pit_std']:.3f} (ideal 0.289)")
    print("  reliability (new): bin, n, mean p_over, empirical over-rate")
    for b, n, mp, er in res["reliability_new"]:
        print(f"    {b}  n={n:5d}  pred={mp:.3f}  actual={er:.3f}")
    return res


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stat", choices=stats_for_sport("mlb") + ["all"], default="all")
    args = parser.parse_args()

    engine = db.get_engine()
    stats = stats_for_sport("mlb") if args.stat == "all" else [args.stat]
    results = [r for s in stats if (r := evaluate_stat(engine, s)) is not None]

    if len(results) > 1:
        print("\n================ SUMMARY ================")
        print(f"{'stat':22s} {'family':14s} {'MAE old':>8s} {'MAE new':>8s} {'Brier old':>10s} {'Brier new':>10s} {'LL old':>8s} {'LL new':>8s}")
        for r in results:
            fam = "poisson" if np.isinf(r["r"]) else f"nb r={r['r']:.1f}"
            print(f"{r['stat']:22s} {fam:14s} {r['mae_old']:8.3f} {r['mae_new']:8.3f} "
                  f"{r['brier_old']:10.4f} {r['brier_new']:10.4f} {r['ll_old']:8.4f} {r['ll_new']:8.4f}")
        w = lambda k: float(np.mean([r[k] for r in results]))
        print(f"{'MEAN':22s} {'':14s} {w('mae_old'):8.3f} {w('mae_new'):8.3f} "
              f"{w('brier_old'):10.4f} {w('brier_new'):10.4f} {w('ll_old'):8.4f} {w('ll_new'):8.4f}")


if __name__ == "__main__":
    main()
