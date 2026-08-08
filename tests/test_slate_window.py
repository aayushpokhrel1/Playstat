from datetime import datetime, timezone

from ingestion.slate_window import slate_window


def _dt(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_mlb_window_is_one_et_day_in_summer():
    # 2026-08-08 14:00Z == 10:00 ET (EDT, -04:00).
    after, before = slate_window(_dt(2026, 8, 8, 14), "mlb")
    assert after == "2026-08-08T10:00:00Z"   # 06:00 EDT
    assert before == "2026-08-09T10:00:00Z"


def test_mlb_window_shifts_with_est_in_winter():
    # 2026-11-15 15:00Z == 10:00 ET (EST, -05:00). The window must move an hour,
    # which a hardcoded -04:00 offset would get wrong.
    after, before = slate_window(_dt(2026, 11, 15, 15), "mlb")
    assert after == "2026-11-15T11:00:00Z"   # 06:00 EST
    assert before == "2026-11-16T11:00:00Z"


def test_before_06_et_still_belongs_to_the_previous_slate():
    # 2026-08-08 08:00Z == 04:00 ET, i.e. still the 08-07 slate's late games.
    after, before = slate_window(_dt(2026, 8, 8, 8), "mlb")
    assert after == "2026-08-07T10:00:00Z"
    assert before == "2026-08-08T10:00:00Z"


def test_nfl_window_spans_its_weekly_slate():
    # SLATE_WINDOW_DAYS['nfl'] == 4 -> Thu..Mon, so the window is 5 ET days.
    after, before = slate_window(_dt(2026, 8, 8, 14), "nfl")
    assert after == "2026-08-08T10:00:00Z"
    assert before == "2026-08-13T10:00:00Z"


def test_not_before_now_clamps_the_lower_bound():
    # At 21:30Z (17:30 ET) the lower bound becomes now, excluding started games.
    after, before = slate_window(_dt(2026, 8, 8, 21, 30), "mlb", not_before_now=True)
    assert after == "2026-08-08T21:30:00Z"
    assert before == "2026-08-09T10:00:00Z"


def test_not_before_now_never_moves_the_bound_backwards():
    # At 08:00Z the slate start (previous day 10:00Z) is already in the past;
    # clamping must take the LATER of the two, i.e. now.
    after, _ = slate_window(_dt(2026, 8, 8, 8), "mlb", not_before_now=True)
    assert after == "2026-08-08T08:00:00Z"
