import pytest

from optimizer.line_movement import leg_movement, summarize_movement


def _build(prob=0.60, line=0.5, side="under"):
    return {"market_prob": prob, "line": line, "side": side,
            "player_id": 1, "game_id": 10, "stat_type": "home_runs"}


def test_movement_is_positive_when_the_market_moves_toward_our_side():
    # We took it at 0.60; by the close the market prices our side at 0.65.
    out = leg_movement(_build(prob=0.60), {"market_prob": 0.65, "line_value": 0.5})
    assert out["movement_pp"] == pytest.approx(5.0)


def test_movement_is_negative_when_the_market_moves_against_us():
    out = leg_movement(_build(prob=0.60), {"market_prob": 0.55, "line_value": 0.5})
    assert out["movement_pp"] == pytest.approx(-5.0)


def test_a_moved_line_is_excluded_rather_than_compared():
    # 0.5 -> 1.5 is a DIFFERENT bet. Returning a number here would be fabrication.
    assert leg_movement(_build(line=0.5), {"market_prob": 0.9, "line_value": 1.5}) is None


def test_a_missing_close_row_is_excluded():
    assert leg_movement(_build(), None) is None


def test_summary_reports_coverage_and_excludes_uncompared_legs():
    pairs = [
        (_build(prob=0.60), {"market_prob": 0.65, "line_value": 0.5}),
        (_build(prob=0.60), {"market_prob": 0.55, "line_value": 0.5}),
        (_build(prob=0.60, line=0.5), {"market_prob": 0.99, "line_value": 1.5}),  # moved
        (_build(prob=0.60), None),                                                # missing
    ]
    summary = summarize_movement(pairs)
    assert summary["n_legs"] == 4
    assert summary["n_compared"] == 2
    assert summary["coverage"] == pytest.approx(0.5)
    assert summary["mean_movement_pp"] == pytest.approx(0.0)


def test_summary_of_nothing_comparable_is_zero_coverage_not_a_crash():
    summary = summarize_movement([(_build(), None)])
    assert summary["n_compared"] == 0
    assert summary["coverage"] == 0.0
    assert summary["mean_movement_pp"] is None


from optimizer.line_movement import close_prob_for_side


def test_close_prob_devigs_the_over_side():
    # -120/+100 -> raw .5455/.5000, overround 1.0455 -> over .5218
    row = {"over_odds": -120, "under_odds": 100}
    assert close_prob_for_side(row, "over") == pytest.approx(0.5218, abs=1e-4)


def test_close_prob_devigs_the_under_side_to_the_complement():
    row = {"over_odds": -120, "under_odds": 100}
    over = close_prob_for_side(row, "over")
    under = close_prob_for_side(row, "under")
    assert over + under == pytest.approx(1.0)


def test_close_prob_uses_home_away_columns_for_team_markets():
    row = {"home_odds": -200, "away_odds": 170, "over_odds": None, "under_odds": None}
    assert close_prob_for_side(row, "home") == pytest.approx(0.642857, abs=1e-4)


def test_close_prob_is_none_when_one_sided():
    # A one-sided row cannot be de-vigged; it must be excluded, not guessed.
    assert close_prob_for_side({"over_odds": -120, "under_odds": None}, "over") is None


# --- the production path: a RAW odds row, de-vigged inside leg_movement ------
# The endpoint passes raw prop_lines/game_lines rows (no precomputed
# market_prob), so this is what actually runs in production.

def test_leg_movement_devigs_a_raw_odds_row_for_the_side_we_took():
    build = _build(prob=0.50, line=0.5, side="under")
    close = {"line_value": 0.5, "over_odds": -120, "under_odds": 100}
    out = leg_movement(build, close)
    # devig(-120, 100) -> under = 0.47826; movement = (0.47826 - 0.50) * 100
    assert out["close_prob"] == pytest.approx(0.47826, abs=1e-4)
    assert out["movement_pp"] == pytest.approx(-2.174, abs=1e-3)


def test_leg_movement_on_a_raw_team_row_uses_home_away_columns():
    build = {"market_prob": 0.60, "line": 0.5, "side": "home",
             "player_id": None, "game_id": 10, "stat_type": "full_game_total"}
    close = {"line_value": 0.5, "home_odds": -200, "away_odds": 170}
    out = leg_movement(build, close)
    assert out["close_prob"] == pytest.approx(0.642857, abs=1e-4)


def test_leg_movement_excludes_a_raw_one_sided_row():
    build = _build(prob=0.50, line=0.5, side="under")
    assert leg_movement(build, {"line_value": 0.5, "over_odds": -120,
                                "under_odds": None}) is None
