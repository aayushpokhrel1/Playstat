"""Tests for GET /parlay-builder/record (docs/superpowers/plans/
2026-07-25-builder-record-per-tier.md, Phase 1).

The dashboard's pooled builder record ("44-24-3 · ROI ...") lumps together
very different bets: the ~67%-to-hit 1.4x player parlays, the ~50%-to-hit
2.0x player parlays, and the team tier. This endpoint returns one row per
(tier, target_payout) instead, so the dashboard can render them separately.

CRITICAL SAFETY: there is no test DB -- ingestion.db.get_engine() is the LIVE
production database. Like tests/test_parlay_recommendations_api.py, this
file never opens a real connection: the pure helper (_shape_builder_record)
is tested directly with in-memory row tuples, and the endpoint function is
tested with the same _FakeEngine/_FakeConn/_FakeResult in-memory stand-in for
the SQLAlchemy engine, monkeypatched over api_main.engine.
"""

from decimal import Decimal

import api.main as api_main
from api.schemas import BuilderRecordDailyOut, BuilderRecordOut


# --- _shape_builder_record: the pure row -> BuilderRecordOut helper --------


def test_maps_across_game_to_player_tier():
    # Live data (docs/superpowers/plans/2026-07-25-builder-record-per-tier.md):
    # Player 1.4x (across_game): 18-6-1, pnl -0.14
    rows = [("across_game", 1.4, 25, 18, 6, 1, -0.14)]
    out = api_main._shape_builder_record(rows)
    assert len(out) == 1
    assert out[0].tier == "player"


def test_maps_team_tier_to_team_tier_label():
    # Team 1.4x (team_tier): 7-3-0, pnl +9.59
    rows = [("team_tier", 1.4, 10, 7, 3, 0, 9.59)]
    out = api_main._shape_builder_record(rows)
    assert len(out) == 1
    assert out[0].tier == "team"


def test_roi_is_pnl_over_n():
    # Player 2.0x (across_game): 12-12-2, pnl -5.21
    rows = [("across_game", 2.0, 26, 12, 12, 2, -5.21)]
    out = api_main._shape_builder_record(rows)
    assert out[0].n == 26
    assert out[0].roi == -5.21 / 26


def test_roi_is_zero_when_n_is_zero():
    rows = [("across_game", 1.4, 0, 0, 0, 0, 0.0)]
    out = api_main._shape_builder_record(rows)
    assert out[0].roi == 0.0


def test_orders_player_before_team_then_ascending_target_payout():
    # Deliberately fed out of order: team before player, descending payout.
    rows = [
        ("team_tier", 2.0, 10, 7, 3, 0, 9.59),
        ("team_tier", 1.4, 10, 7, 3, 0, 9.59),
        ("across_game", 2.0, 26, 12, 12, 2, -5.21),
        ("across_game", 1.4, 25, 18, 6, 1, -0.14),
    ]
    out = api_main._shape_builder_record(rows)
    assert [(r.tier, r.target_payout) for r in out] == [
        ("player", 1.4),
        ("player", 2.0),
        ("team", 1.4),
        ("team", 2.0),
    ]


def test_decimal_inputs_become_float_outputs():
    rows = [("across_game", Decimal("1.4"), 25, 18, 6, 1, Decimal("-0.14"))]
    out = api_main._shape_builder_record(rows)
    assert out[0].target_payout == 1.4
    assert isinstance(out[0].target_payout, float)
    assert out[0].pnl == -0.14
    assert isinstance(out[0].pnl, float)


def test_empty_rows_returns_empty_list():
    assert api_main._shape_builder_record([]) == []


# --- fake DB plumbing (same isolation as test_parlay_recommendations_api.py) -


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, queue):
        self._queue = queue

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return _FakeResult(self._queue.pop(0))


class _FakeEngine:
    def __init__(self, results_sequence):
        self._queue = list(results_sequence)

    def begin(self):
        return _FakeConn(self._queue)


# --- endpoint test: api_main.builder_record() -------------------------------


def test_endpoint_shapes_grouped_rows(monkeypatch):
    rows = [
        ("across_game", Decimal("1.4"), 25, 18, 6, 1, Decimal("-0.14")),
        ("across_game", Decimal("2.0"), 26, 12, 12, 2, Decimal("-5.21")),
        ("team_tier", Decimal("1.4"), 10, 7, 3, 0, Decimal("9.59")),
        ("team_tier", Decimal("2.0"), 10, 7, 3, 0, Decimal("9.59")),
    ]
    fake_engine = _FakeEngine([rows])
    monkeypatch.setattr(api_main, "engine", fake_engine)

    results = api_main.builder_record()

    assert len(results) == 4
    assert all(isinstance(r, BuilderRecordOut) for r in results)
    assert [(r.tier, r.target_payout) for r in results] == [
        ("player", 1.4), ("player", 2.0), ("team", 1.4), ("team", 2.0),
    ]
    player_14 = results[0]
    assert (player_14.n, player_14.wins, player_14.losses, player_14.pushes) == (25, 18, 6, 1)
    assert player_14.pnl == -0.14


def test_endpoint_empty_queue_returns_empty_list(monkeypatch):
    fake_engine = _FakeEngine([[]])
    monkeypatch.setattr(api_main, "engine", fake_engine)

    assert api_main.builder_record() == []


# --- _shape_builder_record_daily: the pure per-day helper (Phase 2) --------


def test_daily_roi_is_pnl_over_n():
    rows = [("2026-07-27", 26, 12, 12, 2, -5.21)]
    out = api_main._shape_builder_record_daily(rows)
    assert len(out) == 1
    assert out[0].date == "2026-07-27"
    assert out[0].roi == -5.21 / 26


def test_daily_roi_is_zero_when_n_is_zero():
    rows = [("2026-07-27", 0, 0, 0, 0, 0.0)]
    out = api_main._shape_builder_record_daily(rows)
    assert out[0].roi == 0.0


def test_daily_preserves_newest_first_order():
    # The SQL side does ORDER BY 1 DESC; the pure helper must not re-sort.
    rows = [
        ("2026-07-27", 10, 7, 3, 0, 9.59),
        ("2026-07-26", 8, 5, 2, 1, 1.20),
        ("2026-07-25", 7, 6, 1, 0, 4.10),
    ]
    out = api_main._shape_builder_record_daily(rows)
    assert [r.date for r in out] == ["2026-07-27", "2026-07-26", "2026-07-25"]


def test_daily_decimal_pnl_becomes_float():
    rows = [("2026-07-27", 25, 18, 6, 1, Decimal("-0.14"))]
    out = api_main._shape_builder_record_daily(rows)
    assert out[0].pnl == -0.14
    assert isinstance(out[0].pnl, float)


def test_daily_date_object_becomes_string():
    from datetime import date as date_type

    rows = [(date_type(2026, 7, 27), 25, 18, 6, 1, -0.14)]
    out = api_main._shape_builder_record_daily(rows)
    assert out[0].date == "2026-07-27"


def test_daily_empty_rows_returns_empty_list():
    assert api_main._shape_builder_record_daily([]) == []


# --- endpoint test: api_main.builder_record_daily() -------------------------


def test_daily_endpoint_shapes_grouped_rows_newest_first(monkeypatch):
    rows = [
        ("2026-07-27", 10, 7, 3, 0, Decimal("9.59")),
        ("2026-07-26", 8, 5, 2, 1, Decimal("1.20")),
    ]
    fake_engine = _FakeEngine([rows])
    monkeypatch.setattr(api_main, "engine", fake_engine)

    results = api_main.builder_record_daily()

    assert len(results) == 2
    assert all(isinstance(r, BuilderRecordDailyOut) for r in results)
    assert [r.date for r in results] == ["2026-07-27", "2026-07-26"]
    assert results[0].pnl == 9.59
    assert results[0].n == 10


def test_daily_endpoint_empty_queue_returns_empty_list(monkeypatch):
    fake_engine = _FakeEngine([[]])
    monkeypatch.setattr(api_main, "engine", fake_engine)

    assert api_main.builder_record_daily() == []


# --- ?sport filter (NFL builder chain #4a) -----------------------------------

import inspect


def test_record_endpoints_have_sport_param_defaulting_to_mlb():
    for fn in (api_main.builder_record, api_main.builder_record_daily):
        sig = inspect.signature(fn)
        assert sig.parameters["sport"].default == "mlb"


def test_record_sql_has_sport_coalesce_filter():
    for fn in (api_main.builder_record, api_main.builder_record_daily):
        src = inspect.getsource(fn)
        assert "COALESCE(pr.legs->>'sport', 'mlb') = :sport" in src


class _CapturingConn:
    def __init__(self, rows, calls):
        self._rows, self._calls = rows, calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        self._calls.append(params)
        return _FakeResult(self._rows)


class _CapturingEngine:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def begin(self):
        return _CapturingConn(self.rows, self.calls)


def test_builder_record_threads_sport_param(monkeypatch):
    eng = _CapturingEngine([("across_game", 1.4, 25, 18, 6, 1, -0.14)])
    monkeypatch.setattr(api_main, "engine", eng)
    api_main.builder_record(sport="nfl")
    assert eng.calls[0]["sport"] == "nfl"


def test_builder_record_daily_threads_sport_param(monkeypatch):
    eng = _CapturingEngine([("2026-09-11", 5, 3, 2, 0, 1.2)])
    monkeypatch.setattr(api_main, "engine", eng)
    api_main.builder_record_daily(sport="nfl")
    assert eng.calls[0]["sport"] == "nfl"


def test_builder_record_defaults_sport_to_mlb(monkeypatch):
    eng = _CapturingEngine([])
    monkeypatch.setattr(api_main, "engine", eng)
    api_main.builder_record()
    assert eng.calls[0]["sport"] == "mlb"
