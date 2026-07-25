"""Tests for modeling/features.py's incremental upsert (README §15.9 item 7 /
docs/superpowers/plans/2026-07-25-incremental-feature-upsert.md).

CRITICAL SAFETY: ingestion.db.get_engine() points at the LIVE production
database (from .env). compute_features() itself is never called here — only
the pure cutoff/filter helpers, which take no engine and touch no DB. This
matches tests/test_builder.py's convention for DB-adjacent code.
"""

from datetime import date, timedelta

from modeling.features import _filter_values, _incremental_cutoff


# --- _incremental_cutoff (pure) ----------------------------------------------

def test_incremental_cutoff_subtracts_lookback_days_from_today():
    today = date(2026, 7, 25)
    assert _incremental_cutoff(today, 7) == date(2026, 7, 18)
    assert _incremental_cutoff(today, 0) == today
    assert _incremental_cutoff(today, 1) == date(2026, 7, 24)


# --- _filter_values (pure) ---------------------------------------------------

def _row(as_of_date, value=1.0, feature="pts_avg_5", player_id=1):
    return {"player_id": player_id, "as_of_date": as_of_date, "feature": feature, "value": value}


def test_filter_values_keeps_only_rows_on_or_after_cutoff():
    today = date(2026, 7, 25)
    lookback_days = 7
    cutoff = _incremental_cutoff(today, lookback_days)  # 2026-07-18

    old_row = _row(cutoff - timedelta(days=1))          # 07-17: dropped
    boundary_row = _row(cutoff)                          # 07-18: kept (>=)
    recent_row = _row(today - timedelta(days=2))          # 07-23: kept
    today_row = _row(today)                                # 07-25: kept
    upcoming_row = _row(today + timedelta(days=2))          # 07-27: kept

    values = [old_row, boundary_row, recent_row, today_row, upcoming_row]
    kept = _filter_values(values, cutoff)

    assert kept == [boundary_row, recent_row, today_row, upcoming_row]
    assert old_row not in kept


def test_filter_values_with_full_semantics_keeps_everything_via_far_past_cutoff():
    """full=True in compute_features simply skips calling _filter_values, so
    every computed row is upserted. Confirm the filter itself is a no-op when
    given a cutoff at or before every row (the equivalent condition)."""
    values = [_row(date(2020, 1, 1)), _row(date(2026, 7, 25)), _row(date(2026, 8, 1))]
    kept = _filter_values(values, cutoff=date(2000, 1, 1))
    assert kept == values


def test_filter_values_empty_list_returns_empty_list():
    assert _filter_values([], cutoff=date(2026, 7, 25)) == []


def test_filter_values_all_rows_dropped_when_all_older_than_cutoff():
    values = [_row(date(2020, 1, 1)), _row(date(2020, 6, 1))]
    assert _filter_values(values, cutoff=date(2026, 7, 25)) == []


def test_filter_values_only_drops_rows_never_mutates_kept_rows():
    """The filter must not change a kept row's value/feature/player_id — it
    only removes rows outside the window."""
    cutoff = date(2026, 7, 18)
    kept_row = _row(date(2026, 7, 20), value=12.5, feature="reb_avg_5", player_id=42)
    dropped_row = _row(date(2026, 7, 1), value=99.9, feature="ast_avg_5", player_id=7)
    original_kept_row = dict(kept_row)

    result = _filter_values([kept_row, dropped_row], cutoff)

    assert result == [kept_row]
    assert result[0] is kept_row  # same object, not a copy/mutation
    assert result[0] == original_kept_row  # every field unchanged
