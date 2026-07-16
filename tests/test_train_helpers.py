"""Unit tests for the pure helper functions in modeling/train.py:
_jittered_quantile, stat_family, model_version, stats_for_sport, and
predicted_std_from_quantiles. No DB access, no model training.

fit_discrete/fit_gaussian/fit_models/load_dataset/main are skipped — they
require a DB connection (load_dataset queries player_game_stats/
rolling_player_features) and/or fit an XGBoost model, neither of which
belongs in a fast pure-math suite.

Run with: python -m pytest tests/test_train_helpers.py -q
"""

import numpy as np
import pytest

from modeling.train import (
    STAT_CONFIG,
    _jittered_quantile,
    model_version,
    predicted_std_from_quantiles,
    stat_family,
    stats_for_sport,
)


# --- _jittered_quantile --------------------------------------------------------

def test_jittered_quantile_deterministic_with_fixed_seed():
    residuals = np.array([0.0, 0.0, 0.0, 1.0, -1.0, 2.0, -2.0])
    q1 = _jittered_quantile(residuals, 0.16, seed=0)
    q2 = _jittered_quantile(residuals, 0.16, seed=0)
    assert q1 == pytest.approx(q2)


def test_jittered_quantile_different_seeds_can_differ_slightly():
    residuals = np.array([0.0] * 20 + [5.0] * 5)
    q_seed0 = _jittered_quantile(residuals, 0.16, seed=0)
    q_seed1 = _jittered_quantile(residuals, 0.16, seed=1)
    # Not asserting they differ (they might coincide), just that both are
    # finite, well-formed floats — the real behavioral guarantee is the
    # zero-atom test below.
    assert np.isfinite(q_seed0)
    assert np.isfinite(q_seed1)


def test_jittered_quantile_fixes_zero_inflation_atom():
    # README §8's assists case: a heavy atom of residuals tied at exactly 0
    # swallows the target quantile band, so a plain np.quantile call returns
    # 0 regardless of what correction is actually needed. Reproduce that
    # shape: 70% of residuals exactly 0, the rest spread out below/above.
    rng = np.random.default_rng(42)
    n_zero = 700
    n_other = 300
    other = rng.uniform(-5, 5, size=n_other)
    residuals = np.concatenate([np.zeros(n_zero), other])
    rng.shuffle(residuals)

    plain_q16 = np.quantile(residuals, 0.16)
    assert plain_q16 == pytest.approx(0.0)  # the naive approach returns exactly 0

    jittered_q16 = _jittered_quantile(residuals, 0.16, seed=0)
    # The jittered quantile should NOT be pinned to exactly 0 — it should
    # reflect where the tied mass "should" fall within the jitter interval.
    assert jittered_q16 != pytest.approx(0.0, abs=1e-9)


def test_jittered_quantile_no_atom_case_close_to_plain_quantile():
    # Without ties, jittering with averaged draws should land close to the
    # plain quantile (jitter noise averages out over many draws).
    rng = np.random.default_rng(0)
    residuals = rng.normal(0, 2, size=2000)
    plain = np.quantile(residuals, 0.5)
    jittered = _jittered_quantile(residuals, 0.5, seed=0)
    assert jittered == pytest.approx(plain, abs=0.1)


# --- stat_family / model_version / stats_for_sport -----------------------------

def test_stat_family_mlb_is_discrete():
    assert stat_family("hits") == "discrete"
    assert stat_family("total_bases") == "discrete"
    assert stat_family("outs_recorded") == "discrete"


def test_stat_family_nba_is_gaussian():
    assert stat_family("points") == "gaussian"
    assert stat_family("rebounds") == "gaussian"
    assert stat_family("assists") == "gaussian"


def test_model_version_discrete_stats_are_v2_nbinom():
    assert model_version("hits") == "xgb_nbinom_hits_v2"
    assert model_version("total_bases") == "xgb_nbinom_total_bases_v2"


def test_model_version_gaussian_stats_are_v1_xgboost():
    assert model_version("points") == "xgboost_points_v1"
    assert model_version("assists") == "xgboost_assists_v1"


def test_stats_for_sport_partitions_stat_config():
    nba_stats = stats_for_sport("nba")
    mlb_stats = stats_for_sport("mlb")

    assert set(nba_stats) == {"points", "rebounds", "assists"}
    assert set(nba_stats) | set(mlb_stats) == set(STAT_CONFIG)
    assert set(nba_stats) & set(mlb_stats) == set()

    for stat in nba_stats:
        assert STAT_CONFIG[stat][2] == "nba"
    for stat in mlb_stats:
        assert STAT_CONFIG[stat][2] == "mlb"


def test_stats_for_sport_unknown_sport_returns_empty():
    assert stats_for_sport("nhl") == []


# --- predicted_std_from_quantiles ----------------------------------------------

def test_predicted_std_from_quantiles_basic():
    # corrected interval [10+1, 20-1] = [11, 19], half-width = 4
    std = predicted_std_from_quantiles(q16_pred=10.0, q84_pred=20.0, c16=1.0, c84=-1.0)
    assert std == pytest.approx(4.0)


def test_predicted_std_from_quantiles_guards_against_quantile_crossing():
    # q84_pred < q16_pred after correction -> clamped to 0, not negative
    std = predicted_std_from_quantiles(q16_pred=20.0, q84_pred=10.0, c16=0.0, c84=0.0)
    assert std == 0.0


def test_predicted_std_from_quantiles_no_correction_is_half_the_raw_interval():
    std = predicted_std_from_quantiles(q16_pred=8.0, q84_pred=16.0, c16=0.0, c84=0.0)
    assert std == pytest.approx((16.0 - 8.0) / 2)
