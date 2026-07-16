"""Unit tests for the pure scoring functions in modeling/settle.py — no DB.

Run with: python -m pytest tests/test_settle.py -q
"""

import pytest

from modeling.settle import parlay_result, settle_leg, single_pnl
from modeling.edges import devig, odds_to_probability
from optimizer.parlay import american_to_decimal


# --- settle_leg -------------------------------------------------------------

def test_settle_leg_over_hit():
    assert settle_leg("over", 10, 8.5) == "hit"


def test_settle_leg_over_miss():
    assert settle_leg("over", 7, 8.5) == "miss"


def test_settle_leg_over_push():
    assert settle_leg("over", 8, 8) == "push"


def test_settle_leg_under_hit():
    assert settle_leg("under", 6, 8.5) == "hit"


def test_settle_leg_under_miss():
    assert settle_leg("under", 10, 8.5) == "miss"


def test_settle_leg_under_push():
    assert settle_leg("under", 8, 8) == "push"


def test_settle_leg_unknown_side_raises():
    with pytest.raises(ValueError):
        settle_leg("sideways", 1, 1)


# --- parlay_result -----------------------------------------------------------

def test_parlay_result_all_hit_win():
    # two legs at decimal 2.0 each -> combined 4.0, stake 1 -> pnl 3.0
    result, decimal_odds, pnl = parlay_result(["hit", "hit"], [2.0, 2.0])
    assert result == "win"
    assert decimal_odds == pytest.approx(4.0)
    assert pnl == pytest.approx(3.0)


def test_parlay_result_one_miss_loses():
    result, decimal_odds, pnl = parlay_result(["hit", "miss", "hit"], [2.0, 1.5, 3.0])
    assert result == "loss"
    # combined_over_all is informational — product over every leg, including
    # the one that missed.
    assert decimal_odds == pytest.approx(2.0 * 1.5 * 3.0)
    assert pnl == pytest.approx(-1.0)


def test_parlay_result_pushed_leg_dropped_and_recomputed():
    # push leg dropped entirely; combined odds recomputed over the hit legs only
    result, decimal_odds, pnl = parlay_result(["hit", "push", "hit"], [2.0, 5.0, 3.0])
    assert result == "win"
    assert decimal_odds == pytest.approx(2.0 * 3.0)
    assert pnl == pytest.approx(2.0 * 3.0 - 1.0)


def test_parlay_result_all_push():
    result, decimal_odds, pnl = parlay_result(["push", "push"], [2.0, 3.0])
    assert result == "push"
    assert decimal_odds == pytest.approx(1.0)
    assert pnl == pytest.approx(0.0)


def test_parlay_result_respects_stake():
    result, decimal_odds, pnl = parlay_result(["hit"], [2.5], stake=10.0)
    assert result == "win"
    assert pnl == pytest.approx(15.0)

    result, decimal_odds, pnl = parlay_result(["miss"], [2.5], stake=10.0)
    assert result == "loss"
    assert pnl == pytest.approx(-10.0)


# --- single_pnl ---------------------------------------------------------------

def test_single_pnl_win():
    assert single_pnl("win", 2.0) == pytest.approx(1.0)


def test_single_pnl_loss():
    assert single_pnl("loss", 2.0) == pytest.approx(-1.0)


def test_single_pnl_push():
    assert single_pnl("push", 2.0) == pytest.approx(0.0)


def test_single_pnl_respects_stake():
    assert single_pnl("win", 3.0, stake=5.0) == pytest.approx(10.0)
    assert single_pnl("loss", 3.0, stake=5.0) == pytest.approx(-5.0)


def test_single_pnl_unknown_result_raises():
    with pytest.raises(ValueError):
        single_pnl("void", 2.0)


# --- american_to_decimal / odds_to_probability round-trips ---------------------

def test_american_to_decimal_positive():
    assert american_to_decimal(150) == pytest.approx(2.5)


def test_american_to_decimal_negative():
    assert american_to_decimal(-200) == pytest.approx(1.5)


def test_odds_to_probability_matches_decimal_inverse():
    for odds in (150, -200, 100, -110):
        decimal_odds = american_to_decimal(odds)
        implied = odds_to_probability(odds)
        # implied probability should equal 1/decimal_odds
        assert implied == pytest.approx(1 / decimal_odds)


def test_devig_sums_to_one():
    p_over, p_under = devig(-110, -110)
    assert p_over == pytest.approx(0.5)
    assert p_under == pytest.approx(0.5)
    assert p_over + p_under == pytest.approx(1.0)
