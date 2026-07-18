import pytest
from scipy.stats import poisson

from modeling.f5 import prob_under_line_nb


def test_nb_large_dispersion_collapses_to_poisson():
    # As r -> inf the NB2 variance -> mean, so discrete_dist falls back to Poisson.
    # P(X < 4.5) = P(X <= 4) = poisson.cdf(4, mean).
    assert prob_under_line_nb(4.5, 1e9, 4.5) == pytest.approx(poisson.cdf(4, 4.5), abs=1e-4)


def test_nb_half_integer_line_collapses_to_poisson():
    assert prob_under_line_nb(5.0, 1e9, 3.5) == pytest.approx(poisson.cdf(3, 5.0), abs=1e-4)


def test_nb_overdispersion_raises_under_tail_above_poisson():
    # Real F5 overdispersion (r~4.8) fattens the low tail, so P(under 4.5) is
    # HIGHER than the equidispersed Poisson value at the same mean — this is the
    # ~5-point correction that fixed the holdout calibration bias.
    mean = 5.0
    poisson_under = poisson.cdf(4, mean)          # P(X < 4.5) under Poisson(5)
    nb_under = prob_under_line_nb(mean, 4.8, 4.5)
    assert nb_under > poisson_under


def test_nb_monotonic_decreasing_in_mean():
    assert prob_under_line_nb(3.0, 4.8, 4.5) > prob_under_line_nb(6.0, 4.8, 4.5)
