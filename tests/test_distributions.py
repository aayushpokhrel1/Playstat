"""Unit tests for modeling/distributions.py — the shared moment-based
reconstruction of discrete predictive distributions (README §8).

Rule under test: var = std**2; var <= mean -> Poisson(mean); else NB2 with
r = mean**2 / (var - mean). scipy.stats is used as an oracle wherever
possible so these tests don't just re-derive the implementation's own math.

Run with: python -m pytest tests/test_distributions.py -q
"""

import numpy as np
import pytest
from scipy.stats import nbinom, poisson

from modeling.distributions import (
    cdf_array,
    discrete_dist,
    ppf_array,
    prob_over,
    prob_over_discrete,
    prob_over_gaussian,
    randomized_pit,
)


# --- discrete_dist: family selection + moment round-trip ---------------------

def test_discrete_dist_underdispersed_selects_poisson():
    # var < mean -> Poisson
    dist = discrete_dist(mean=5.0, std=1.0)
    assert isinstance(dist.dist, type(poisson(1).dist))


def test_discrete_dist_equidispersed_boundary_selects_poisson():
    # var == mean exactly -> the <= boundary picks Poisson, not NB
    mean = 4.0
    std = mean ** 0.5
    dist = discrete_dist(mean=mean, std=std)
    assert isinstance(dist.dist, type(poisson(1).dist))
    assert dist.mean() == pytest.approx(mean)
    assert dist.var() == pytest.approx(mean)


def test_discrete_dist_overdispersed_selects_negative_binomial():
    mean, std = 3.0, 3.0  # var = 9 > mean = 3
    dist = discrete_dist(mean=mean, std=std)
    assert isinstance(dist.dist, type(nbinom(1, 0.5).dist))


def test_discrete_dist_poisson_mean_variance_round_trip():
    mean = 6.0
    dist = discrete_dist(mean=mean, std=mean ** 0.5)
    assert dist.mean() == pytest.approx(mean)
    assert dist.var() == pytest.approx(mean)


@pytest.mark.parametrize("mean,std", [(3.0, 3.0), (10.0, 5.0), (2.5, 4.0)])
def test_discrete_dist_nb_mean_variance_round_trip(mean, std):
    dist = discrete_dist(mean=mean, std=std)
    assert dist.mean() == pytest.approx(mean, rel=1e-6)
    assert dist.var() == pytest.approx(std ** 2, rel=1e-6)


def test_discrete_dist_nb_matches_manual_r_p():
    mean, std = 4.0, 6.0
    var = std ** 2
    r = mean * mean / (var - mean)
    p = r / (r + mean)
    expected = nbinom(r, p)
    got = discrete_dist(mean, std)
    for k in (0, 1, 2, 5, 10):
        assert got.cdf(k) == pytest.approx(expected.cdf(k))


# --- prob_over_discrete / prob_over / prob_over_gaussian ---------------------

def test_prob_over_discrete_matches_poisson_survival_half_integer_line():
    mean, std = 5.0, 5.0 ** 0.5
    # half-integer line 2.5 -> over means X >= 3 -> 1 - CDF(2)
    got = prob_over_discrete(mean, std, 2.5)
    expected = 1.0 - poisson(mean).cdf(2)
    assert got == pytest.approx(expected)


def test_prob_over_discrete_matches_nbinom_survival():
    mean, std = 3.0, 3.0
    var = std ** 2
    r = mean * mean / (var - mean)
    p = r / (r + mean)
    line = 1.5
    got = prob_over_discrete(mean, std, line)
    expected = 1.0 - nbinom(r, p).cdf(1)
    assert got == pytest.approx(expected)


def test_prob_over_discrete_integer_line_excludes_push_value():
    # An integer line of 3 means "over" = X > 3 = X >= 4, i.e. 1 - CDF(3),
    # matching floor(3) = 3 (the push value X == 3 is correctly excluded).
    mean, std = 5.0, 5.0 ** 0.5
    got = prob_over_discrete(mean, std, 3)
    expected = 1.0 - poisson(mean).cdf(3)
    assert got == pytest.approx(expected)


def test_prob_over_discrete_half_vs_integer_line_consistency():
    # over 2.5 and over 3 (integer) should give the same probability, since
    # floor(2.5) == floor(3) - 1... actually floor(2.5) = 2, floor(3) = 3.
    # over an integer line of 3 excludes X==3; over 2.5 includes X==3.
    mean, std = 5.0, 5.0 ** 0.5
    over_2_5 = prob_over_discrete(mean, std, 2.5)
    over_3 = prob_over_discrete(mean, std, 3)
    dist = discrete_dist(mean, std)
    assert over_2_5 == pytest.approx(1.0 - dist.cdf(2))
    assert over_3 == pytest.approx(1.0 - dist.cdf(3))
    assert over_2_5 > over_3  # over_2_5 includes X==3, over_3 does not


def test_prob_over_dispatches_by_family():
    mean, std, line = 5.0, 5.0 ** 0.5, 2.5
    assert prob_over(mean, std, line, "discrete") == pytest.approx(
        prob_over_discrete(mean, std, line)
    )
    assert prob_over(mean, std, line, "gaussian") == pytest.approx(
        prob_over_gaussian(mean, std, line)
    )


def test_prob_over_gaussian_matches_norm_survival():
    from scipy.stats import norm

    mean, std, line = 20.0, 5.0, 22.5
    assert prob_over_gaussian(mean, std, line) == pytest.approx(
        1.0 - norm.cdf(line, loc=mean, scale=std)
    )


def test_prob_over_discrete_monotonic_increasing_in_mean():
    std = 3.0
    line = 5.5
    probs = [prob_over_discrete(mean, std, line) for mean in (2.0, 4.0, 6.0, 8.0, 10.0)]
    assert probs == sorted(probs)
    assert probs[0] < probs[-1]


def test_prob_over_gaussian_monotonic_increasing_in_mean():
    std = 3.0
    line = 5.5
    probs = [prob_over_gaussian(mean, std, line) for mean in (2.0, 4.0, 6.0, 8.0, 10.0)]
    assert probs == sorted(probs)
    assert probs[0] < probs[-1]


# --- cdf_array / ppf_array: mechanical properties ----------------------------

def test_cdf_array_in_unit_interval():
    means = np.array([1.0, 5.0, 10.0, 3.0])
    stds = np.array([1.0, 2.0, 3.0, 3.0])  # last one is overdispersed
    for k in (0, 1, 5, 20):
        vals = cdf_array(means, stds, k)
        assert np.all(vals >= 0.0)
        assert np.all(vals <= 1.0)


def test_cdf_array_non_decreasing_in_k():
    mean, std = 4.0, 4.0  # overdispersed -> NB path
    ks = [0, 1, 2, 3, 5, 10, 20]
    vals = [float(np.asarray(cdf_array(mean, std, k)).reshape(-1)[0]) for k in ks]
    assert vals == sorted(vals)


def test_cdf_array_matches_discrete_dist():
    mean, std = 6.0, 8.0  # NB path
    dist = discrete_dist(mean, std)
    for k in (0, 1, 3, 7, 15):
        assert float(np.asarray(cdf_array(mean, std, k)).reshape(-1)[0]) == pytest.approx(dist.cdf(k))

    mean2, std2 = 6.0, 2.0  # Poisson path
    dist2 = discrete_dist(mean2, std2)
    for k in (0, 1, 3, 7, 15):
        assert float(np.asarray(cdf_array(mean2, std2, k)).reshape(-1)[0]) == pytest.approx(dist2.cdf(k))


def test_ppf_array_inverts_cdf_at_grid_points():
    mean, std = 5.0, 6.0  # NB path
    for q in (0.1, 0.25, 0.5, 0.75, 0.9):
        k = float(np.asarray(ppf_array(mean, std, q)).reshape(-1)[0])
        # ppf(q) is the smallest integer with CDF >= q, so CDF at that point
        # must be >= q, and CDF just below it must be < q (when k > 0).
        assert float(np.asarray(cdf_array(mean, std, k)).reshape(-1)[0]) >= q - 1e-9
        if k > 0:
            assert float(np.asarray(cdf_array(mean, std, k - 1)).reshape(-1)[0]) < q + 1e-9


# --- randomized_pit -----------------------------------------------------------

def test_randomized_pit_in_unit_interval():
    mean, std = 5.0, 5.0 ** 0.5
    x = np.arange(0, 20)
    u = randomized_pit(mean, std, x, seed=42)
    assert np.all(u >= 0.0)
    assert np.all(u <= 1.0)


def test_randomized_pit_deterministic_with_fixed_seed():
    mean, std = 5.0, 6.0
    x = np.array([0, 1, 2, 3, 10])
    u1 = randomized_pit(mean, std, x, seed=7)
    u2 = randomized_pit(mean, std, x, seed=7)
    np.testing.assert_array_equal(u1, u2)


def test_randomized_pit_approximately_uniform_for_correct_distribution():
    # Draw many samples from the *true* Poisson(mean) law, then PIT-transform
    # them under that same (mean, std) — for a correctly specified predictive
    # distribution, the PIT values should be ~Uniform(0,1). Loose tolerance
    # since this is a Monte Carlo check.
    mean = 8.0
    std = mean ** 0.5  # Poisson
    rng = np.random.default_rng(123)
    samples = rng.poisson(mean, size=20000)
    u = randomized_pit(mean, std, samples, seed=999)
    assert u.mean() == pytest.approx(0.5, abs=0.02)
    # KS-style loose check: fraction below 0.5 should be close to 0.5
    assert np.mean(u < 0.5) == pytest.approx(0.5, abs=0.03)
