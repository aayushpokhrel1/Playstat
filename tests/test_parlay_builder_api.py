import pytest
from fastapi import HTTPException

import api.main as main


# --- fake DB plumbing for GET /parlay-builder/saved -------------------------
# Mirrors the stand-in in tests/test_parlay_recommendations_api.py: no httpx/
# TestClient is installed in this environment, so DB access is faked with a
# minimal in-memory replacement for the SQLAlchemy engine.

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return _FakeResult(self._rows)


class _FakeEngine:
    def __init__(self, rows):
        self._rows = rows

    def begin(self):
        return _FakeConn(self._rows)


def _fake_engine(rows):
    return _FakeEngine(rows)


def test_saved_builder_reads_only_builder_rows_and_unwraps_dict(monkeypatch):
    builder_row = (
        90, "2026-07-21 12:38:56-04", 2.0, 0.5416, 1.8224,
        {"class": "across_game", "legs": [
            {"kind": "player", "game_id": 1, "player_id": 10, "stat_type": "home_runs",
             "market": None, "side": "under", "odds": -1670, "line": 0.5,
             "label": "X home_runs under 0.5", "market_prob": 0.908, "model_prob": None},
            {"kind": "team", "game_id": 2, "player_id": None, "stat_type": None,
             "market": "first_inning_runs", "side": "under", "odds": -150, "line": 0.5,
             "label": "first_inning_runs under 0.5", "market_prob": 0.60, "model_prob": None},
        ]},
    )
    monkeypatch.setattr(main, "engine", _fake_engine([builder_row]))

    out = main.saved_builder_parlays(limit=10)

    assert len(out) == 1
    assert out[0].parlay_id == 90 and out[0].target_payout == 2.0
    assert out[0].n_legs == 2
    team_leg = [l for l in out[0].legs if l.kind == "team"][0]
    assert team_leg.player_id is None and team_leg.market == "first_inning_runs"


# --- GET /parlay-builder/saved?tier= (README §15 Change 3) ------------------
# tier selects the legs->>'class' filter added to the WHERE clause. Additive:
# no `tier` (or `tier=player`) must reproduce today's exact query — filtering
# on class='across_game', the shape every existing saved row has. There is no
# test DB, so actual row-level filtering happens in Postgres; what's testable
# here without touching the live engine is that the endpoint builds the right
# SQL/params for each tier, which is what actually drives that filtering.

class _CapturingConn:
    def __init__(self, calls):
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        self._calls.append((str(stmt), params))
        return _FakeResult([])


class _CapturingEngine:
    def __init__(self):
        self.calls = []

    def begin(self):
        return _CapturingConn(self.calls)


def test_saved_default_tier_filters_to_across_game_class(monkeypatch):
    engine = _CapturingEngine()
    monkeypatch.setattr(main, "engine", engine)

    out = main.saved_builder_parlays(limit=10)  # no tier passed

    assert out == []
    sql, params = engine.calls[0]
    assert "legs->>'class' = :cls" in sql
    assert params["cls"] == "across_game"


def test_saved_tier_player_matches_default_behaviour(monkeypatch):
    engine = _CapturingEngine()
    monkeypatch.setattr(main, "engine", engine)

    main.saved_builder_parlays(limit=10, tier="player")

    sql, params = engine.calls[0]
    assert "legs->>'class' = :cls" in sql
    assert params["cls"] == "across_game"


def test_saved_tier_team_filters_to_team_tier_class(monkeypatch):
    engine = _CapturingEngine()
    monkeypatch.setattr(main, "engine", engine)

    main.saved_builder_parlays(limit=10, tier="team")

    sql, params = engine.calls[0]
    assert "legs->>'class' = :cls" in sql
    assert params["cls"] == "team_tier"


def test_saved_tier_all_skips_the_class_filter_entirely(monkeypatch):
    engine = _CapturingEngine()
    monkeypatch.setattr(main, "engine", engine)

    main.saved_builder_parlays(limit=10, tier="all")

    sql, _ = engine.calls[0]
    assert "legs->>'class'" not in sql


def test_saved_rejects_unknown_tier_without_touching_the_engine(monkeypatch):
    engine = _CapturingEngine()
    monkeypatch.setattr(main, "engine", engine)

    with pytest.raises(HTTPException) as exc_info:
        main.saved_builder_parlays(limit=10, tier="bogus")

    assert exc_info.value.status_code == 422
    assert engine.calls == []  # never reached the DB layer


def test_parlay_builder_returns_object_with_truncation_fields(monkeypatch):
    legs = [
        {"game_id": 1, "kind": "player", "label": "A over 0.5", "side": "over",
         "decimal_odds": 1.3, "american_odds": -333, "market_prob": 0.77,
         "model_prob": None, "line_value": 0.5, "player_id": 10, "stat_type": "hits", "market": None},
        {"game_id": 2, "kind": "player", "label": "B under 0.5", "side": "under",
         "decimal_odds": 1.25, "american_odds": -400, "market_prob": 0.80,
         "model_prob": 0.79, "line_value": 0.5, "player_id": 11, "stat_type": "runs", "market": None},
    ]
    monkeypatch.setattr(main.builder, "load_legs", lambda engine, floor: legs)

    out = main.parlay_builder(min_prob=0.5)

    assert out.constructions and out.constructions[0].n_legs == 2
    assert out.truncated is False
    assert out.exhaustive is True
    assert isinstance(out.nodes_searched, int) and out.nodes_searched > 0
    # No EV/edge field leaked into the payload.
    assert not hasattr(out.constructions[0], "ev")
