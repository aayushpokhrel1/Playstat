"""Unit tests for the pure scoring functions in modeling/settle.py — no DB.

Run with: python -m pytest tests/test_settle.py -q
"""

import inspect

import pytest

from modeling.settle import (
    aggregate_bet_performance, bet_type_label, parlay_result, settle_builder_parlays,
    settle_leg, settle_parlays, settle_team_parlays, single_pnl,
)
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


# --- bet_type_label / aggregate_bet_performance (README §15 Task 2, 2026-07-21) ---
# Splits the builder's paper record out of the pooled 'parlay' bucket without
# touching the bet_type CHECK constraint or writing a migration: the split is
# derived, in the read path, from parlay_recommendations.kind.

def test_bet_type_label_edge_passes_through():
    assert bet_type_label("edge", None) == "edge"


def test_bet_type_label_maps_parlay_kinds():
    assert bet_type_label("parlay", "player") == "parlay_model"
    assert bet_type_label("parlay", "team") == "parlay_team"
    assert bet_type_label("parlay", "builder") == "parlay_builder"


def test_bet_type_label_unknown_kind_falls_back_to_parlay():
    # Defensive only — would mean a broken FK, should never happen live.
    assert bet_type_label("parlay", None) == "parlay"
    assert bet_type_label("parlay", "something_new") == "parlay"


def test_aggregate_bet_performance_splits_parlay_by_kind():
    # Mirrors the live shape reported in README §15 Task 2: all 64 existing
    # parlay outcomes are kind='player' legacy rows, so they land under
    # parlay_model, with no parlay_builder row (since no builder rows exist).
    rows = [
        ("edge", None, 3885, 2220, 1665, 0, 3885.0, -255.0805232923075),
        ("parlay", "player", 64, 16, 48, 0, 64.0, -36.475829126430455),
    ]
    out = aggregate_bet_performance(rows)
    by_label = {r[0]: r for r in out}

    assert set(by_label) == {"edge", "parlay_model", "all"}

    label, n, wins, losses, pushes, staked, pnl = by_label["parlay_model"]
    assert (n, wins, losses, pushes) == (64, 16, 48, 0)
    assert staked == pytest.approx(64.0)
    assert pnl == pytest.approx(-36.475829126430455)

    # The 'all' row must reconcile against the live combined totals.
    label, n, wins, losses, pushes, staked, pnl = by_label["all"]
    assert (n, wins, losses, pushes) == (3949, 2236, 1713, 0)
    assert staked == pytest.approx(3949.0)
    assert pnl == pytest.approx(-291.55635241873796)


def test_aggregate_bet_performance_keeps_builder_and_team_kinds_separate():
    rows = [
        ("parlay", "player", 2, 1, 1, 0, 2.0, 0.5),
        ("parlay", "team", 3, 2, 1, 0, 3.0, 1.0),
        ("parlay", "builder", 4, 3, 1, 0, 4.0, 2.0),
    ]
    out = aggregate_bet_performance(rows)
    labels = [r[0] for r in out]
    assert labels == ["parlay_builder", "parlay_model", "parlay_team", "all"]

    by_label = {r[0]: r for r in out}
    assert by_label["all"][1] == 9  # n
    assert by_label["all"][6] == pytest.approx(3.5)  # pnl


def test_aggregate_bet_performance_empty_is_empty():
    assert aggregate_bet_performance([]) == []


# --- dedupe cross-kind safety (README §15 Task 2) ---
# parlay_recommendations.parlay_id is one SERIAL PK shared across
# kind='player'/'team'/'builder' rows, and each settle_*_parlays() candidate
# query filters on its own pr.kind — so a given parlay_id can only ever be a
# candidate for exactly one of the three functions, and the shared
# bet_type='parlay' NOT EXISTS guard still correctly prevents any of them
# from re-settling a row another one already wrote. These checks pin that
# invariant to the actual SQL text, so an edit that drops a kind filter (and
# would let two settle_* functions race on the same parlay_id) fails loudly.

@pytest.mark.parametrize(
    "fn, kind",
    [(settle_parlays, "player"), (settle_team_parlays, "team"), (settle_builder_parlays, "builder")],
)
def test_settle_functions_scope_candidates_to_their_own_kind(fn, kind):
    source = inspect.getsource(fn)
    assert f"pr.kind = '{kind}'" in source
    assert "ro.bet_type = 'parlay' AND ro.parlay_id = pr.parlay_id" in source
