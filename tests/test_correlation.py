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


import modeling.correlation as corr


def test_nrfi_f5_lift_reads_given_lines_and_returns_cells(monkeypatch):
    import pandas as pd
    # 4 games: (fi, f5) totals. under 0.5 fi => fi==0; under 4.5 f5 => f5<4.5
    frame = pd.DataFrame(
        {"game_id": [1, 2, 3, 4], "fi": [0, 0, 2, 3], "f5": [3, 6, 2, 8]}
    )
    monkeypatch.setattr(corr, "_game_totals", lambda engine, f5_line: frame)
    lift, n, both = corr.nrfi_f5_lift(
        object(), "under", "under", nrfi_line=0.5, f5_line=4.5
    )
    # under/under: fi<0.5 -> games 1,2 (a=2); f5<4.5 -> games 1,3 (b=2); both -> game 1 (=1)
    # expected = (2/4)*(2/4)=0.25 ; observed = 1/4=0.25 ; lift=1.0
    assert n == 4
    assert both == 1
    assert lift == 1.0


def test_nrfi_f5_lift_over_side(monkeypatch):
    import pandas as pd
    frame = pd.DataFrame(
        {"game_id": [1, 2, 3, 4], "fi": [0, 0, 2, 3], "f5": [3, 6, 2, 8]}
    )
    monkeypatch.setattr(corr, "_game_totals", lambda engine, f5_line: frame)
    lift, n, both = corr.nrfi_f5_lift(
        object(), "over", "over", nrfi_line=0.5, f5_line=4.5
    )
    # over/over: fi>0.5 -> games 3,4 (a=2); f5>4.5 -> games 2,4 (b=2); both -> game 4 (=1)
    assert (n, both) == (4, 1)
    assert lift == 1.0


def test_empirical_lift_empty_marginal_returns_one():
    assert corr.empirical_lift(0, 0, 5, 10) == 1.0
    assert corr.empirical_lift(0, 5, 0, 10) == 1.0
    assert corr.empirical_lift(1, 2, 3, 0) == 1.0


def test_pair_joint_prob_clamps_to_min_marginal():
    # huge lift can't push joint above the smaller marginal
    assert corr.pair_joint_prob(0.6, 0.55, 10.0) == 0.55
    # negative-direction lift lowers the joint below the product
    assert corr.pair_joint_prob(0.6, 0.6, 0.5) == 0.6 * 0.6 * 0.5
