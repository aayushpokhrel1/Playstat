from datetime import datetime, timedelta, timezone

import pytest

from api.main import _shape_line_movement

BUILT_AT = datetime(2026, 8, 8, 8, 39, tzinfo=timezone.utc)
LATER = BUILT_AT + timedelta(hours=9)      # the 17:30 ET pull
SAME_PULL = BUILT_AT                       # the very pull the card was built from


def _saved(parlay_id, legs, built_at=BUILT_AT):
    return (parlay_id, "2026-08-08", built_at, {"class": "across_game", "legs": legs})


def _leg(pid, prob, line=0.5):
    return {"kind": "player", "player_id": pid, "game_id": 10,
            "stat_type": "home_runs", "side": "under", "line": line,
            "market_prob": prob}


def _close(prob, line=0.5, pulled_at=LATER):
    return {"market_prob": prob, "line_value": line, "pulled_at": pulled_at}


def test_shapes_movement_against_the_close_rows():
    saved = [_saved(1, [_leg(11, 0.60), _leg(12, 0.70)])]
    close = {(11, 10, "home_runs"): _close(0.65),
             (12, 10, "home_runs"): _close(0.68)}
    out = _shape_line_movement(saved, close)
    assert out.n_compared == 2
    assert out.coverage == pytest.approx(1.0)
    assert out.mean_movement_pp == pytest.approx(1.5)


def test_missing_close_rows_lower_coverage_without_crashing():
    saved = [_saved(1, [_leg(11, 0.60), _leg(12, 0.70)])]
    close = {(11, 10, "home_runs"): _close(0.65)}
    out = _shape_line_movement(saved, close)
    assert out.n_legs == 2
    assert out.n_compared == 1
    assert out.coverage == pytest.approx(0.5)


def test_no_saved_rows_is_an_empty_honest_result():
    out = _shape_line_movement([], {})
    assert out.n_legs == 0
    assert out.coverage == 0.0
    assert out.mean_movement_pp is None


# --- the self-comparison guard ----------------------------------------------
# With one pull per day the newest line row IS the pull the card was built from.
# Comparing a price to itself yields movement 0.0 at "100% coverage", which
# falsely implies a measurement was taken. Verified live 2026-08-08: 412 of 454
# legs were exactly this. Coverage must report what was actually measured.

def test_a_close_row_from_the_same_pull_is_not_a_measurement():
    saved = [_saved(1, [_leg(11, 0.60)])]
    close = {(11, 10, "home_runs"): _close(0.60, pulled_at=SAME_PULL)}
    out = _shape_line_movement(saved, close)
    assert out.n_legs == 1
    assert out.n_compared == 0
    assert out.coverage == 0.0
    assert out.mean_movement_pp is None


def test_a_close_row_pulled_before_the_build_is_not_a_measurement():
    saved = [_saved(1, [_leg(11, 0.60)])]
    close = {(11, 10, "home_runs"): _close(0.65, pulled_at=BUILT_AT - timedelta(hours=1))}
    out = _shape_line_movement(saved, close)
    assert out.n_compared == 0


def test_a_strictly_later_pull_counts():
    saved = [_saved(1, [_leg(11, 0.60)])]
    close = {(11, 10, "home_runs"): _close(0.65, pulled_at=LATER)}
    out = _shape_line_movement(saved, close)
    assert out.n_compared == 1
    assert out.mean_movement_pp == pytest.approx(5.0)


def test_a_close_row_without_pulled_at_is_excluded():
    # Defensive: a row missing pulled_at cannot be proven later than the build.
    saved = [_saved(1, [_leg(11, 0.60)])]
    close = {(11, 10, "home_runs"): {"market_prob": 0.65, "line_value": 0.5}}
    out = _shape_line_movement(saved, close)
    assert out.n_compared == 0
