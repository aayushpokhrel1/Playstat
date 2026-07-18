from ingestion.mlb_backfill import f5_runs


def test_f5_sums_first_five_innings():
    innings = [{"home": {"runs": r}} for r in (1, 0, 2, 0, 1, 3, 0)]  # 7 innings
    assert f5_runs(innings, "home") == 4  # 1+0+2+0+1, ignores innings 6-7


def test_f5_short_game_sums_what_exists():
    innings = [{"away": {"runs": r}} for r in (2, 1, 0)]  # rain-shortened, 3 innings
    assert f5_runs(innings, "away") == 3


def test_f5_missing_side_treated_as_zero():
    innings = [{"home": {"runs": 1}}, {}, {"home": {"runs": 2}}]
    assert f5_runs(innings, "home") == 3  # missing inning-2 home -> 0


def test_f5_no_innings_returns_none():
    assert f5_runs([], "home") is None
