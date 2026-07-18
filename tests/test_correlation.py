import pytest
from modeling.correlation import pair_joint_prob, empirical_lift


def test_lift_independence_is_one():
    # both-under = 0.25, marginals 0.5 and 0.5 over n=100 -> exactly independent
    assert empirical_lift(both=25, a=50, b=50, n=100) == pytest.approx(1.0)


def test_lift_positive_dependence_above_one():
    # both-under observed 40 vs expected 25 -> lift 1.6
    assert empirical_lift(both=40, a=50, b=50, n=100) == pytest.approx(1.6)


def test_lift_empty_marginal_defaults_to_one():
    assert empirical_lift(both=0, a=0, b=10, n=100) == 1.0


def test_pair_joint_applies_lift():
    assert pair_joint_prob(0.6, 0.5, lift=1.5) == pytest.approx(0.45)


def test_pair_joint_clamped_to_min_marginal():
    # 0.9*0.9*2.0 = 1.62 -> clamped to min(0.9,0.9)=0.9
    assert pair_joint_prob(0.9, 0.9, lift=2.0) == 0.9


def test_pair_joint_clamped_nonnegative():
    assert pair_joint_prob(0.3, 0.3, lift=0.0) == 0.0
