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
from api.schemas import BuilderRecordOut


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
