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
    """Pops one canned result set per .execute() call, in call order --
    matches the docs/superpowers/plans/2026-07-28-leg-team-names.md
    enrichment, which now issues additional (batched) games/players queries
    after the main row query."""

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


def _fake_engine(results_sequence):
    return _FakeEngine(results_sequence)


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
    # Query order: main row query, THEN the batched games query (game_ids
    # {1, 2}), THEN the batched players query (player_ids {10}).
    games_rows = [
        (1, 900, 901, "Team Home One", "Team Away One"),
        (2, 902, 903, "Team Home Two", "Team Away Two"),
    ]
    players_rows = [(10, 900)]  # player 10's team_id matches game 1's home_id
    monkeypatch.setattr(
        main, "engine", _fake_engine([[builder_row], games_rows, players_rows])
    )

    out = main.saved_builder_parlays(limit=10)

    assert len(out) == 1
    assert out[0].parlay_id == 90 and out[0].target_payout == 2.0
    assert out[0].n_legs == 2
    team_leg = [l for l in out[0].legs if l.kind == "team"][0]
    assert team_leg.player_id is None and team_leg.market == "first_inning_runs"
    # Team-market leg is game-level: both team names resolved, no player side.
    assert (team_leg.home_team, team_leg.away_team) == ("Team Home Two", "Team Away Two")
    assert team_leg.player_team_side is None
    player_leg = [l for l in out[0].legs if l.kind == "player"][0]
    assert (player_leg.home_team, player_leg.away_team) == ("Team Home One", "Team Away One")
    assert player_leg.player_team_side == "home"


def test_saved_builder_leg_exposes_book(monkeypatch):
    """Line shopping (§15.9 item 3): a leg's shopped `book` round-trips; a
    legacy leg without the key defaults to None."""
    builder_row = (
        91, "2026-08-06 12:00:00-04", 1.4, 0.66, 1.67,
        {"class": "across_game", "legs": [
            {"kind": "player", "game_id": 1, "player_id": 10, "stat_type": "hits",
             "market": None, "side": "over", "odds": -150, "line": 0.5,
             "label": "X hits over 0.5", "market_prob": 0.66, "model_prob": None,
             "book": "fanduel"},
            {"kind": "player", "game_id": 1, "player_id": 11, "stat_type": "runs",
             "market": None, "side": "over", "odds": -140, "line": 0.5,
             "label": "Y runs over 0.5", "market_prob": 0.64, "model_prob": None},
        ]},
    )
    games_rows = [(1, 900, 901, "Team Home One", "Team Away One")]
    players_rows = [(10, 900), (11, 900)]
    monkeypatch.setattr(
        main, "engine", _fake_engine([[builder_row], games_rows, players_rows])
    )

    out = main.saved_builder_parlays(limit=10, tier="all")

    shopped = [l for l in out[0].legs if l.player_id == 10][0]
    legacy = [l for l in out[0].legs if l.player_id == 11][0]
    assert shopped.book == "fanduel"     # shopped price's book round-trips
    assert legacy.book is None           # absent key -> None, no crash


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
    monkeypatch.setattr(main.builder, "load_legs", lambda engine, floor, **kw: legs)
    # This endpoint now ALSO resolves team-name context (docs/superpowers/
    # plans/2026-07-28-leg-team-names.md) via batched games/players queries
    # -- main.engine MUST be faked here too, or this would open a real
    # connection to the live production DB (CRITICAL SAFETY).
    games_rows = [(1, 100, 101, "Home A", "Away A"), (2, 102, 103, "Home B", "Away B")]
    players_rows = [(10, 100), (11, 103)]
    monkeypatch.setattr(main, "engine", _fake_engine([games_rows, players_rows]))

    out = main.parlay_builder(min_prob=0.5)

    assert out.constructions and out.constructions[0].n_legs == 2
    assert out.truncated is False
    assert out.exhaustive is True
    assert isinstance(out.nodes_searched, int) and out.nodes_searched > 0
    # No EV/edge field leaked into the payload.
    assert not hasattr(out.constructions[0], "ev")
    by_game = {leg.game_id: leg for leg in out.constructions[0].legs}
    assert (by_game[1].home_team, by_game[1].away_team, by_game[1].player_team_side) == (
        "Home A", "Away A", "home",
    )
    assert (by_game[2].home_team, by_game[2].away_team, by_game[2].player_team_side) == (
        "Home B", "Away B", "away",
    )


# --- GET /parlay-builder?max_leg_reuse= (docs/superpowers/specs/
# 2026-07-29-builder-independence-design.md) ----------------------------------
# Additive optional query param, default 2 — no test DB, so the live search
# itself (builder_core.build) is monkeypatched to capture kwargs, matching
# this file's fake-engine convention (main.engine faked so the batched
# games/players enrichment never opens a real connection either).

def test_parlay_builder_max_leg_reuse_defaults_to_2_and_threads_into_build(monkeypatch):
    captured = {}

    def _fake_build(*a, **k):
        captured.update(k)
        return []

    monkeypatch.setattr(main.builder_core, "build", _fake_build)
    monkeypatch.setattr(main.builder, "load_legs", lambda engine, floor, **kw: [{"x": 1}])
    monkeypatch.setattr(main, "engine", _fake_engine([]))

    main.parlay_builder(min_prob=0.5)

    assert captured["max_uses"] == 2


def test_parlay_builder_max_leg_reuse_param_overrides_default(monkeypatch):
    captured = {}

    def _fake_build(*a, **k):
        captured.update(k)
        return []

    monkeypatch.setattr(main.builder_core, "build", _fake_build)
    monkeypatch.setattr(main.builder, "load_legs", lambda engine, floor, **kw: [{"x": 1}])
    monkeypatch.setattr(main, "engine", _fake_engine([]))

    main.parlay_builder(min_prob=0.5, max_leg_reuse=1)

    assert captured["max_uses"] == 1


# --- GET /parlay-builder?sport= (NBA build 2026-07-30) -----------------------
# The live "Build" control is rendered on every sport tab, so the search
# endpoint must scope candidate legs to the requested sport (+ its slate window)
# or the NBA/NFL tab's Build returns MLB parlays. Additive, default mlb.

def _capture_load_legs(monkeypatch):
    captured = {}

    def _fake(engine, floor, **kw):
        captured.update(kw)
        return []  # empty -> endpoint returns before build/enrichment

    monkeypatch.setattr(main.builder, "load_legs", _fake)
    monkeypatch.setattr(main, "engine", _fake_engine([]))
    return captured


def test_parlay_builder_sport_defaults_to_mlb_daily_window(monkeypatch):
    captured = _capture_load_legs(monkeypatch)
    main.parlay_builder(min_prob=0.5)
    assert captured["sport"] == "mlb"
    assert captured["window_days"] == 0


def test_parlay_builder_sport_nba_uses_daily_window(monkeypatch):
    captured = _capture_load_legs(monkeypatch)
    main.parlay_builder(min_prob=0.5, sport="nba")
    assert captured["sport"] == "nba"
    assert captured["window_days"] == 0


def test_parlay_builder_sport_nfl_uses_weekly_window(monkeypatch):
    captured = _capture_load_legs(monkeypatch)
    main.parlay_builder(min_prob=0.5, sport="nfl")
    assert captured["sport"] == "nfl"
    assert captured["window_days"] == 4


# --- GET /parlay-builder/saved?sport= (NFL builder sub-project #2) ----------

def test_saved_builder_parlays_has_sport_param_defaulting_to_mlb():
    import inspect
    sig = inspect.signature(main.saved_builder_parlays)
    assert sig.parameters["sport"].default == "mlb"


def test_saved_builder_query_filters_by_sport_with_mlb_default_coalesce():
    import inspect
    source = inspect.getsource(main.saved_builder_parlays)
    assert "COALESCE(legs->>'sport', 'mlb') = :sport" in source
    assert '"sport": sport' in source


# --- same-game combos tier (README §15.9 item 1) ------------------------------

def test_saved_tier_same_game_filters_to_same_game_pair_class(monkeypatch):
    engine = _CapturingEngine()
    monkeypatch.setattr(main, "engine", engine)

    main.saved_builder_parlays(limit=10, tier="same_game")

    sql, params = engine.calls[0]
    assert "legs->>'class' = :cls" in sql
    assert params["cls"] == "same_game_pair"


def test_saved_same_game_row_exposes_lift_metadata(monkeypatch):
    """The wrapper's correlation metadata surfaces on the response; the payout is
    a NON-PLACEABLE reference, so the honest quantity is the lift-adjusted joint."""
    same_game_row = (
        300, "2026-08-07 08:31:00-04", 0.0, 0.3796, 3.2706,
        {"class": "same_game_pair", "sport": "mlb",
         "lift": 1.39, "lift_n": 6588, "both_n": 2197, "small_sample": False,
         "legs": [
             {"kind": "team", "game_id": 7, "player_id": None, "stat_type": None,
              "market": "first_inning_runs", "side": "under", "odds": -130, "line": 0.5,
              "label": "first_inning_runs under 0.5", "market_prob": 0.532,
              "model_prob": None, "book": None},
             {"kind": "team", "game_id": 7, "player_id": None, "stat_type": None,
              "market": "f5_runs", "side": "under", "odds": -118, "line": 4.0,
              "label": "f5_runs under 4.0", "market_prob": 0.514,
              "model_prob": None, "book": None},
         ]},
    )
    # Two team legs on ONE game -> main row query THEN the batched games query
    # only (no player ids, so _load_builder_team_context skips the players query).
    games_rows = [(7, 910, 911, "Team Home", "Team Away")]
    monkeypatch.setattr(main, "engine", _fake_engine([[same_game_row], games_rows]))

    out = main.saved_builder_parlays(limit=10, tier="same_game")

    assert len(out) == 1
    assert out[0].lift == 1.39 and out[0].lift_n == 6588
    assert out[0].both_n == 2197 and out[0].small_sample is False
    assert out[0].n_legs == 2
    assert all(leg.kind == "team" for leg in out[0].legs)


def test_saved_player_tier_lift_fields_default_to_none(monkeypatch):
    """Budgerr's default tier is byte-unchanged: the new fields are None/False."""
    player_row = (
        301, "2026-08-07 08:31:00-04", 1.4, 0.6772, 1.4213,
        {"class": "across_game", "sport": "mlb", "legs": [
            {"kind": "player", "game_id": 1, "player_id": 10, "stat_type": "hits",
             "market": None, "side": "over", "odds": -200, "line": 0.5,
             "label": "X hits over 0.5", "market_prob": 0.66, "model_prob": None,
             "book": None},
        ]},
    )
    games_rows = [(1, 900, 901, "Home One", "Away One")]
    players_rows = [(10, 900)]
    monkeypatch.setattr(
        main, "engine", _fake_engine([[player_row], games_rows, players_rows])
    )

    out = main.saved_builder_parlays(limit=10)

    assert out[0].lift is None and out[0].lift_n is None
    assert out[0].both_n is None and out[0].small_sample is False


def test_saved_same_game_small_sample_flag_round_trips(monkeypatch):
    row = (
        302, "2026-08-07 08:31:00-04", 0.0, 0.30, 3.1,
        {"class": "same_game_pair", "sport": "mlb",
         "lift": 1.2, "lift_n": 900, "both_n": 220, "small_sample": True,
         "legs": [
             {"kind": "team", "game_id": 8, "player_id": None, "stat_type": None,
              "market": "first_inning_runs", "side": "under", "odds": -120, "line": 0.5,
              "label": "nrfi", "market_prob": 0.55, "model_prob": None, "book": None},
             {"kind": "team", "game_id": 8, "player_id": None, "stat_type": None,
              "market": "f5_runs", "side": "under", "odds": -110, "line": 4.5,
              "label": "f5u", "market_prob": 0.55, "model_prob": None, "book": None},
         ]},
    )
    monkeypatch.setattr(main, "engine", _fake_engine([[row], [(8, 1, 2, "H", "A")]]))

    out = main.saved_builder_parlays(limit=10, tier="same_game")

    assert out[0].small_sample is True and out[0].lift_n == 900


def test_wrapper_meta_defaults_for_bare_list_legacy_rows():
    """A legacy bare-list legs value (no wrapper) yields the additive defaults."""
    meta = main._wrapper_meta([{"kind": "player"}])
    assert meta == {"lift": None, "lift_n": None, "both_n": None, "small_sample": False}
