"""Unit tests for modeling/edges.py's pure odds-conversion functions:
odds_to_probability and devig. No DB access — the root conftest.py supplies
dummy DATABASE_URL/API_BASKETBALL_KEY so modeling.edges (which imports
ingestion.db and modeling.train) can be imported without a .env.

Run with: python -m pytest tests/test_odds.py -q
"""

import pytest

from optimizer.devig import devig, odds_to_probability


# --- odds_to_probability ------------------------------------------------------

def test_odds_to_probability_plus_150():
    assert odds_to_probability(150) == pytest.approx(0.4)


def test_odds_to_probability_minus_200():
    assert odds_to_probability(-200) == pytest.approx(2 / 3, rel=1e-4)


def test_odds_to_probability_even_money():
    assert odds_to_probability(100) == pytest.approx(0.5)
    assert odds_to_probability(-100) == pytest.approx(0.5)


@pytest.mark.parametrize("odds", [110, 150, 200, 300, 500])
def test_odds_to_probability_symmetry(odds):
    # +N and -N are the two "fair coin at this many odds" descriptions;
    # their implied probabilities should sum to 1 (no vig at this pairing).
    assert odds_to_probability(odds) + odds_to_probability(-odds) == pytest.approx(1.0)


def test_odds_to_probability_decreasing_as_positive_odds_grow():
    # Longer positive underdog odds imply a lower probability of winning.
    probs = [odds_to_probability(o) for o in (100, 150, 200, 400)]
    assert probs == sorted(probs, reverse=True)


def test_odds_to_probability_increasing_as_negative_odds_grow():
    # A steeper favorite (-150 vs -300) implies a higher win probability.
    probs = [odds_to_probability(o) for o in (-110, -150, -200, -300)]
    assert probs == sorted(probs)


# --- devig ---------------------------------------------------------------------

def test_devig_sums_to_one():
    p_over, p_under = devig(-110, -110)
    assert p_over + p_under == pytest.approx(1.0)


def test_devig_symmetric_line_splits_evenly():
    p_over, p_under = devig(-110, -110)
    assert p_over == pytest.approx(0.5)
    assert p_under == pytest.approx(0.5)


def test_devig_preserves_relative_order():
    # The side with the more favorable (less negative / more positive) odds
    # has the higher raw implied probability... actually shorter (more
    # negative) odds imply a HIGHER probability. devig must preserve that
    # ordering after removing the shared overround.
    raw_over = odds_to_probability(-150)
    raw_under = odds_to_probability(120)
    assert raw_over > raw_under

    p_over, p_under = devig(-150, 120)
    assert p_over > p_under
    assert p_over + p_under == pytest.approx(1.0)


@pytest.mark.parametrize(
    "over_odds,under_odds",
    [(-110, -110), (-150, 130), (-200, 170), (100, -120), (-115, -105)],
)
def test_devig_always_sums_to_one(over_odds, under_odds):
    p_over, p_under = devig(over_odds, under_odds)
    assert p_over + p_under == pytest.approx(1.0)
    assert 0.0 < p_over < 1.0
    assert 0.0 < p_under < 1.0


def test_devig_removes_overround_proportionally():
    # Manually compute the raw (vig-inflated) probabilities and confirm devig
    # just rescales them down by the shared overround, preserving the ratio.
    over_odds, under_odds = -120, -110
    raw_over = odds_to_probability(over_odds)
    raw_under = odds_to_probability(under_odds)
    overround = raw_over + raw_under
    assert overround > 1.0  # sportsbook margin

    p_over, p_under = devig(over_odds, under_odds)
    assert p_over == pytest.approx(raw_over / overround)
    assert p_under == pytest.approx(raw_under / overround)
    assert p_over / p_under == pytest.approx(raw_over / raw_under)
