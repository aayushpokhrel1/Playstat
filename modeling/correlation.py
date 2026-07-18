"""Empirical NRFI x F5 co-occurrence for same-game team-market pairs.

The 1st inning is nested inside innings 1-5 and positively correlated, so a
same-game (NRFI, F5) pair must NOT use naive P_a * P_b. v1 corrects the product
by a global observed/expected "lift" measured from box-score history — auditable,
no new modeling family. Known bias: assumes constant dependence and is noisy
until ~a season of shared history exists (README §14.2 correlation notes).
"""

import pandas as pd
from sqlalchemy import text


def empirical_lift(both, a, b, n):
    """observed P(both) / (P(a) * P(b)) from raw counts; 1.0 if either marginal empty."""
    if n == 0 or a == 0 or b == 0:
        return 1.0
    expected = (a / n) * (b / n)
    return (both / n) / expected


def pair_joint_prob(p_a, p_b, lift):
    """Dependence-adjusted joint prob of two same-game legs, clamped to a valid range."""
    joint = p_a * p_b * lift
    return max(0.0, min(joint, min(p_a, p_b)))


def _game_totals(engine, f5_line):
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT game_id,
                       SUM(value) FILTER (WHERE stat_type='runs_inning_1') AS fi,
                       SUM(value) FILTER (WHERE stat_type='runs_f5')       AS f5
                FROM team_game_stats
                GROUP BY game_id
                """
            ),
            conn,
        )
    return df.dropna(subset=["fi", "f5"])


def nrfi_f5_lift(engine, side_nrfi, side_f5, f5_line=4.5, nrfi_line=1.5):
    """(lift, n_games) for a same-game NRFI-side x F5-side pair from history."""
    df = _game_totals(engine, f5_line)
    n = len(df)
    nrfi_hit = (df["fi"] < nrfi_line) if side_nrfi == "under" else (df["fi"] > nrfi_line)
    f5_hit = (df["f5"] < f5_line) if side_f5 == "under" else (df["f5"] > f5_line)
    return empirical_lift(int((nrfi_hit & f5_hit).sum()), int(nrfi_hit.sum()), int(f5_hit.sum()), n), n
