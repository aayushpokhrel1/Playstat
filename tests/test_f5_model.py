import pytest
from modeling.f5 import prob_under_line_poisson


def test_prob_under_line_poisson_matches_scipy():
    from scipy.stats import poisson
    mean, line = 4.5, 4.5
    # P(X < 4.5) = P(X <= 4) = poisson.cdf(4, 4.5)
    assert prob_under_line_poisson(mean, line) == pytest.approx(poisson.cdf(4, mean))


def test_prob_under_line_poisson_half_integer_line():
    from scipy.stats import poisson
    assert prob_under_line_poisson(5.0, 3.5) == pytest.approx(poisson.cdf(3, 5.0))


def test_prob_under_line_poisson_monotonic_in_mean():
    assert prob_under_line_poisson(3.0, 4.5) > prob_under_line_poisson(6.0, 4.5)
