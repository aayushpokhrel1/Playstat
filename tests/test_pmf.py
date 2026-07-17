"""Unit tests for modeling/distributions.pmf_list (README §14.5 — per-edge PMF
bar chart). Pure math, DB-free — scipy.stats is used as an oracle wherever
possible, matching the convention in tests/test_distributions.py.

Run with: python -m pytest tests/test_pmf.py -q
"""

import math

import pytest
from scipy.stats import nbinom, poisson

from modeling.distributions import discrete_dist, pmf_list, prob_over_discrete


# --- basic shape / probability sanity -----------------------------------------

@pytest.mark.parametrize(
    "mean,std",
    [(5.0, 5.0 ** 0.5), (3.0, 3.0), (10.0, 5.0), (2.5, 4.0), (0.3, 0.5)],
)
def test_pmf_list_probabilities_in_unit_interval(mean, std):
    pairs = pmf_list(mean, std)
    for k, p in pairs:
        assert 0.0 <= p <= 1.0
        assert isinstance(k, int)


@pytest.mark.parametrize(
    "mean,std",
    [(5.0, 5.0 ** 0.5), (3.0, 3.0), (10.0, 5.0)],
)
def test_pmf_list_ks_are_contiguous_from_zero(mean, std):
    pairs = pmf_list(mean, std)
    ks = [k for k, _ in pairs]
    assert ks[0] == 0
    assert ks == list(range(ks[-1] + 1))


def test_pmf_list_sums_to_at_least_cover_for_nondegenerate_case():
    mean, std = 5.0, 5.0 ** 0.5  # Poisson, healthy mean -> no degenerate guard
    pairs = pmf_list(mean, std, cover=0.999)
    total = sum(p for _, p in pairs)
    assert total >= 0.999 - 1e-6


def test_pmf_list_sums_close_to_one_when_tail_negligible():
    # k_cap generous, cover high -> almost all mass captured.
    mean, std = 4.0, 2.0
    pairs = pmf_list(mean, std, cover=0.9999, k_cap=60)
    total = sum(p for _, p in pairs)
    assert total == pytest.approx(1.0, abs=1e-3)


# --- matches scipy oracle per family -------------------------------------------

def test_pmf_list_matches_poisson_pmf_when_equidispersed():
    mean, std = 6.0, 6.0 ** 0.5  # var == mean -> Poisson path
    pairs = pmf_list(mean, std)
    dist = poisson(mean)
    for k, p in pairs:
        assert p == pytest.approx(dist.pmf(k))


def test_pmf_list_matches_nbinom_pmf_when_overdispersed():
    mean, std = 3.0, 3.0  # var = 9 > mean = 3 -> NB2 path
    var = std ** 2
    r = mean * mean / (var - mean)
    p_param = r / (r + mean)
    expected = nbinom(r, p_param)
    pairs = pmf_list(mean, std)
    for k, p in pairs:
        assert p == pytest.approx(expected.pmf(k))


def test_pmf_list_matches_discrete_dist_pmf_directly():
    # Cross-check against discrete_dist itself (the single source of truth),
    # independent of manually re-deriving Poisson/NB params.
    mean, std = 8.0, 10.0
    dist = discrete_dist(mean, std)
    pairs = pmf_list(mean, std)
    for k, p in pairs:
        assert p == pytest.approx(dist.pmf(k))


# --- k_cap respected ------------------------------------------------------------

def test_pmf_list_respects_k_cap_even_with_huge_mean():
    mean, std = 500.0, 50.0
    pairs = pmf_list(mean, std, cover=0.999, k_cap=60)
    ks = [k for k, _ in pairs]
    assert max(ks) <= 60


def test_pmf_list_k_cap_smaller_than_default_bounds_length():
    mean, std = 20.0, 6.0
    pairs = pmf_list(mean, std, cover=0.999, k_cap=10)
    ks = [k for k, _ in pairs]
    assert max(ks) <= 10


# --- degenerate small-mean case --------------------------------------------------

def test_pmf_list_degenerate_tiny_mean_returns_at_least_k0_and_k1():
    # A tiny mean can make discrete_ppf return 0 (all mass on k=0), but callers
    # (a bar chart) always want at least two bars to draw something meaningful.
    mean, std = 0.01, 0.05
    pairs = pmf_list(mean, std)
    ks = [k for k, _ in pairs]
    assert ks[:2] == [0, 1]


def test_pmf_list_zero_mean_like_input_does_not_crash():
    # mean=0 gets floored to EPS inside discrete_dist; pmf_list must still
    # return a well-formed, non-empty list.
    pairs = pmf_list(0.0, 0.01)
    assert len(pairs) >= 2
    assert pairs[0][0] == 0


# --- consistency with prob_over_discrete (the exact number /edges reports) ------

@pytest.mark.parametrize(
    "mean,std,line",
    [
        (5.0, 5.0 ** 0.5, 2.5),   # Poisson
        (3.0, 3.0, 1.5),          # NB2
        (10.0, 5.0, 8.5),         # NB2, higher mean
        (4.0, 4.0, 3.0),          # NB2, integer line
    ],
)
def test_pmf_list_over_mass_matches_prob_over_discrete(mean, std, line):
    pairs = pmf_list(mean, std, cover=0.999999, k_cap=200)
    over_mass = sum(p for k, p in pairs if k > math.floor(line))
    expected = prob_over_discrete(mean, std, line)
    assert over_mass == pytest.approx(expected, abs=1e-4)
