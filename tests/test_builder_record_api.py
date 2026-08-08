"""Tests for GET /parlay-builder/record (docs/superpowers/plans/
2026-07-25-builder-record-per-tier.md, Phase 1).

The dashboard's pooled builder record ("44-24-3 · ROI ...") lumps together
very different bets: the ~67%-to-hit 1.4x player parlays, the ~50%-to-hit
2.0x player parlays, and the team tier. This endpoint returns one row per
(tier, target_payout) instead, so the dashboard can render them separately.

Row shape carries `staked` (sum of Kelly stakes, README §15.9 item 4) before
pnl; ROI is pnl/staked (stake-weighted), not pnl/n — variable Kelly stakes
would otherwise aggregate wrong.

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
# Row: (cls, target_payout, n, wins, losses, pushes, staked, pnl)


def test_maps_across_game_to_player_tier():
    # Player 1.4x (across_game): 18-6-1, staked 25, pnl -0.14
    rows = [("across_game", 1.4, 25, 18, 6, 1, 25, -0.14)]
    out = api_main._shape_builder_record(rows)
    assert len(out) == 1
    assert out[0].tier == "player"


def test_maps_team_tier_to_team_tier_label():
    # Team 1.4x (team_tier): 7-3-0, staked 10, pnl +9.59
    rows = [("team_tier", 1.4, 10, 7, 3, 0, 10, 9.59)]
    out = api_main._shape_builder_record(rows)
    assert len(out) == 1
    assert out[0].tier == "team"


def test_roi_is_pnl_over_staked_not_n():
    # staked (18.0) deliberately != n (26): ROI must divide by staked.
    rows = [("across_game", 2.0, 26, 12, 12, 2, 18.0, -5.21)]
    out = api_main._shape_builder_record(rows)
    assert out[0].n == 26
    assert out[0].staked == 18.0
    assert out[0].roi == -5.21 / 18.0


def test_roi_is_zero_when_staked_is_zero():
    # All stake=0 cards (no shopped edge): staked 0 -> ROI 0, no divide-by-zero.
    rows = [("across_game", 1.4, 3, 0, 3, 0, 0.0, 0.0)]
    out = api_main._shape_builder_record(rows)
    assert out[0].roi == 0.0


def test_orders_player_before_team_then_ascending_target_payout():
    # Deliberately fed out of order: team before player, descending payout.
    rows = [
        ("team_tier", 2.0, 10, 7, 3, 0, 10, 9.59),
        ("team_tier", 1.4, 10, 7, 3, 0, 10, 9.59),
        ("across_game", 2.0, 26, 12, 12, 2, 26, -5.21),
        ("across_game", 1.4, 25, 18, 6, 1, 25, -0.14),
    ]
    out = api_main._shape_builder_record(rows)
    assert [(r.tier, r.target_payout) for r in out] == [
        ("player", 1.4),
        ("player", 2.0),
        ("team", 1.4),
        ("team", 2.0),
    ]


def test_decimal_inputs_become_float_outputs():
    rows = [("across_game", Decimal("1.4"), 25, 18, 6, 1, Decimal("25"), Decimal("-0.14"))]
    out = api_main._shape_builder_record(rows)
    assert out[0].target_payout == 1.4
    assert isinstance(out[0].target_payout, float)
    assert out[0].pnl == -0.14
    assert isinstance(out[0].pnl, float)
    assert out[0].staked == 25.0
    assert isinstance(out[0].staked, float)


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
        ("across_game", Decimal("1.4"), 25, 18, 6, 1, Decimal("25"), Decimal("-0.14")),
        ("across_game", Decimal("2.0"), 26, 12, 12, 2, Decimal("26"), Decimal("-5.21")),
        ("team_tier", Decimal("1.4"), 10, 7, 3, 0, Decimal("10"), Decimal("9.59")),
        ("team_tier", Decimal("2.0"), 10, 7, 3, 0, Decimal("10"), Decimal("9.59")),
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
# Row: (slate_date, n, wins, losses, pushes, staked, pnl)


def test_daily_roi_is_pnl_over_staked_not_n():
    # staked (18.0) != n (26): ROI divides by staked.
    rows = [("2026-07-27", 26, 12, 12, 2, 18.0, -5.21)]
    out = api_main._shape_builder_record_daily(rows)
    assert len(out) == 1
    assert out[0].date == "2026-07-27"
    assert out[0].staked == 18.0
    assert out[0].roi == -5.21 / 18.0


def test_daily_roi_is_zero_when_staked_is_zero():
    rows = [("2026-07-27", 3, 0, 3, 0, 0.0, 0.0)]
    out = api_main._shape_builder_record_daily(rows)
    assert out[0].roi == 0.0


def test_daily_preserves_newest_first_order():
    # The SQL side does ORDER BY 1 DESC; the pure helper must not re-sort.
    rows = [
        ("2026-07-27", 10, 7, 3, 0, 10, 9.59),
        ("2026-07-26", 8, 5, 2, 1, 8, 1.20),
        ("2026-07-25", 7, 6, 1, 0, 7, 4.10),
    ]
    out = api_main._shape_builder_record_daily(rows)
    assert [r.date for r in out] == ["2026-07-27", "2026-07-26", "2026-07-25"]


def test_daily_decimal_pnl_becomes_float():
    rows = [("2026-07-27", 25, 18, 6, 1, Decimal("25"), Decimal("-0.14"))]
    out = api_main._shape_builder_record_daily(rows)
    assert out[0].pnl == -0.14
    assert isinstance(out[0].pnl, float)


def test_daily_date_object_becomes_string():
    from datetime import date as date_type

    rows = [(date_type(2026, 7, 27), 25, 18, 6, 1, 25, -0.14)]
    out = api_main._shape_builder_record_daily(rows)
    assert out[0].date == "2026-07-27"


def test_daily_empty_rows_returns_empty_list():
    assert api_main._shape_builder_record_daily([]) == []


# --- endpoint test: api_main.builder_record_daily() -------------------------


def test_daily_endpoint_shapes_grouped_rows_newest_first(monkeypatch):
    rows = [
        ("2026-07-27", 10, 7, 3, 0, Decimal("10"), Decimal("9.59")),
        ("2026-07-26", 8, 5, 2, 1, Decimal("8"), Decimal("1.20")),
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


def test_record_sql_sums_stake_for_stake_weighted_roi():
    # README §15.9 item 4: variable Kelly stakes -> ROI must be pnl/SUM(stake).
    for fn in (api_main.builder_record, api_main.builder_record_daily):
        src = inspect.getsource(fn)
        assert "sum(ro.stake)" in src


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
    eng = _CapturingEngine([("across_game", 1.4, 25, 18, 6, 1, 25, -0.14)])
    monkeypatch.setattr(api_main, "engine", eng)
    api_main.builder_record(sport="nfl")
    assert eng.calls[0]["sport"] == "nfl"


def test_builder_record_daily_threads_sport_param(monkeypatch):
    eng = _CapturingEngine([("2026-09-11", 5, 3, 2, 0, 5, 1.2)])
    monkeypatch.setattr(api_main, "engine", eng)
    api_main.builder_record_daily(sport="nfl")
    assert eng.calls[0]["sport"] == "nfl"


def test_builder_record_defaults_sport_to_mlb(monkeypatch):
    eng = _CapturingEngine([])
    monkeypatch.setattr(api_main, "engine", eng)
    api_main.builder_record()
    assert eng.calls[0]["sport"] == "mlb"


# --- NFL game tier (docs/superpowers/plans/2026-07-29-nfl-dashboard-view.md) -


def test_game_tier_maps_to_game_label():
    rows = [("game_tier", 1.4, 5, 3, 2, 0, 5, 1.2)]
    out = api_main._shape_builder_record(rows)
    assert out[0].tier == "game"


def test_tier_sort_orders_player_team_game():
    rows = [
        ("game_tier", 1.4, 5, 3, 2, 0, 5, 1.2),
        ("team_tier", 1.4, 5, 3, 2, 0, 5, 1.2),
        ("across_game", 1.4, 5, 3, 2, 0, 5, 1.2),
    ]
    out = api_main._shape_builder_record(rows)
    assert [r.tier for r in out] == ["player", "team", "game"]


def test_tier_to_class_has_game_entry():
    assert api_main.TIER_TO_CLASS["game"] == "game_tier"


# --- _merge_parlay_legs: rec labels + audit results by builder_leg_key -------

from api.schemas import DailyParlayLegOut


def test_merge_parlay_legs_joins_label_from_rec_and_result_from_audit():
    rec = [
        {"kind": "player", "game_id": 5, "player_id": 9, "stat_type": "hits",
         "label": "Aaron Judge hits over 1.5", "side": "over", "line": 1.5, "odds": -120,
         "home_team": "NYY", "away_team": "BOS"},
        {"kind": "team", "game_id": 7, "market": "f5_runs",
         "label": "f5_runs under 5.5", "side": "under", "line": 5.5, "odds": -143},
    ]
    audit = [
        {"kind": "team", "game_id": 7, "market": "f5_runs", "actual": 4.0, "result": "hit"},
        {"kind": "player", "game_id": 5, "player_id": 9, "stat_type": "hits",
         "actual": 2.0, "result": "hit"},
    ]
    out = api_main._merge_parlay_legs(rec, audit)
    assert [l.label for l in out] == ["Aaron Judge hits over 1.5", "f5_runs under 5.5"]  # rec order
    assert out[0].actual == 2.0 and out[0].result == "hit"      # matched across list order
    assert out[0].home_team == "NYY"
    assert out[1].actual == 4.0 and out[1].result == "hit"


def test_merge_parlay_legs_audit_missing_leaves_result_none():
    rec = [{"kind": "team", "game_id": 7, "market": "f5_runs", "label": "x", "side": "under",
            "line": 5.5, "odds": -143}]
    out = api_main._merge_parlay_legs(rec, [])
    assert out[0].result is None and out[0].actual is None


# --- _shape_daily_parlays: pure per-day parlay shaper (Task 3) ----------------

from api.schemas import DailyParlayOut


def test_shape_daily_parlays_maps_tier_and_merges_legs():
    rec_wrapper = {"class": "team_tier", "sport": "mlb", "legs": [
        {"kind": "team", "game_id": 7, "market": "f5_runs", "label": "f5_runs under 5.5",
         "side": "under", "line": 5.5, "odds": -143}]}
    audit = [{"kind": "team", "game_id": 7, "market": "f5_runs", "actual": 4.0, "result": "hit"}]
    rows = [(297, "loss", "team_tier", Decimal("1.4"), Decimal("2.913"), Decimal("1.0"),
             Decimal("-1.0"), rec_wrapper, audit)]
    out = api_main._shape_daily_parlays(rows)
    assert len(out) == 1
    p = out[0]
    assert isinstance(p, DailyParlayOut)
    assert p.tier == "team" and p.result == "loss"
    assert p.stake == 1.0 and p.pnl == -1.0 and p.combined_odds == 2.913
    assert p.legs[0].label == "f5_runs under 5.5" and p.legs[0].result == "hit"


def test_shape_daily_parlays_empty_rows_returns_empty_list():
    assert api_main._shape_daily_parlays([]) == []


# --- endpoint test: api_main.builder_record_daily_parlays() ------------------


def test_daily_parlays_endpoint_shapes_rows(monkeypatch):
    rec = {"class": "across_game", "sport": "mlb", "legs": [
        {"kind": "player", "game_id": 5, "player_id": 9, "stat_type": "hits",
         "label": "J hits o1.5", "side": "over", "line": 1.5, "odds": -120}]}
    audit = [{"kind": "player", "game_id": 5, "player_id": 9, "stat_type": "hits",
              "actual": 2.0, "result": "hit"}]
    rows = [(1, "win", "across_game", Decimal("1.4"), Decimal("1.4"), Decimal("0.5"),
             Decimal("0.2"), rec, audit)]
    eng = _CapturingEngine(rows)
    monkeypatch.setattr(api_main, "engine", eng)
    out = api_main.builder_record_daily_parlays(date="2026-08-06")
    assert len(out) == 1 and out[0].result == "win"
    assert out[0].legs[0].result == "hit"
    assert eng.calls[0]["sport"] == "mlb" and eng.calls[0]["date"] == "2026-08-06"


def test_daily_parlays_endpoint_threads_sport(monkeypatch):
    eng = _CapturingEngine([])
    monkeypatch.setattr(api_main, "engine", eng)
    api_main.builder_record_daily_parlays(date="2026-08-06", sport="nfl")
    assert eng.calls[0]["sport"] == "nfl"
