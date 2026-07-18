import pytest
from modeling.settle import team_leg_actual, settle_leg, parlay_result


def test_team_leg_actual_maps_market_to_total():
    totals = {(1, "first_inning_runs"): 0.0, (1, "f5_runs"): 3.0}
    assert team_leg_actual(totals, 1, "first_inning_runs") == 0.0
    assert team_leg_actual(totals, 1, "f5_runs") == 3.0
    assert team_leg_actual(totals, 2, "f5_runs") is None


def test_team_pair_under_under_wins():
    # NRFI under 1.5 actual 0 -> hit; F5 under 4.5 actual 3 -> hit
    r1 = settle_leg("under", 0.0, 1.5)
    r2 = settle_leg("under", 3.0, 4.5)
    result, _, pnl = parlay_result([r1, r2], [1.4, 1.5])
    assert result == "win"
    assert pnl == pytest.approx(1.4 * 1.5 - 1)


def test_team_pair_one_miss_loses():
    r1 = settle_leg("under", 2.0, 1.5)   # miss
    r2 = settle_leg("under", 3.0, 4.5)   # hit
    result, _, pnl = parlay_result([r1, r2], [1.4, 1.5])
    assert result == "loss"
    assert pnl == -1.0
