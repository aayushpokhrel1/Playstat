"""Tests for team-name/matchup context on builder parlay legs
(docs/superpowers/plans/2026-07-28-leg-team-names.md).

Builder parlay legs previously carried only `leg.label` -- no team context,
so a player prop like "Andrew Benintendi batter_strikeouts over 0.5" gave no
hint which sportsbook matchup to look under, and team-market (NRFI/F5) legs
gave no team context at all. `BuilderLegOut` now carries additive
`home_team` / `away_team` / `player_team_side` fields, resolved via ONE
batched games+teams join and ONE batched players query (no N+1) and enriched
onto every leg via the pure, DB-free `player_side` / `_resolve_leg_teams`
helpers in api/main.py.

CRITICAL SAFETY: there is no test DB -- ingestion.db.get_engine() is the LIVE
production database. Like tests/test_builder_record_api.py, this file never
opens a real connection: the pure helpers are tested directly with in-memory
fixtures, and the endpoint test fakes the SQLAlchemy engine with the same
queue-based _FakeEngine/_FakeConn/_FakeResult stand-in.
"""

import api.main as api_main
from api.schemas import BuilderLegOut


# --- player_side: the pure home/away/traded-neither helper ------------------


def test_player_side_matches_home():
    assert api_main.player_side(100, 100, 200) == "home"


def test_player_side_matches_away():
    assert api_main.player_side(200, 100, 200) == "away"


def test_player_side_matches_neither_traded_player():
    # players.team_id is a "latest pull" (README §15.10 NBA note): a traded
    # player's stored team can differ from the team they played for in this
    # game. Matching neither side must return None, not a wrong guess.
    assert api_main.player_side(999, 100, 200) is None


# --- _resolve_leg_teams: the pure per-leg enrichment-shaping helper ----------


def test_resolve_leg_teams_player_leg_gets_home_team_and_side():
    games = {5: (100, 200, "Home Team", "Away Team")}
    players = {10: 100}  # player's team_id matches game 5's home_id
    leg = {"game_id": 5, "player_id": 10}

    out = api_main._resolve_leg_teams(leg, games, players)

    assert out == {
        "home_team": "Home Team",
        "away_team": "Away Team",
        "player_team_side": "home",
    }


def test_resolve_leg_teams_player_leg_away_side():
    games = {5: (100, 200, "Home Team", "Away Team")}
    players = {10: 200}
    leg = {"game_id": 5, "player_id": 10}

    out = api_main._resolve_leg_teams(leg, games, players)

    assert out["player_team_side"] == "away"


def test_resolve_leg_teams_traded_player_side_is_none():
    games = {5: (100, 200, "Home Team", "Away Team")}
    players = {10: 999}  # traded: stored team_id matches neither side
    leg = {"game_id": 5, "player_id": 10}

    out = api_main._resolve_leg_teams(leg, games, players)

    assert out["home_team"] == "Home Team" and out["away_team"] == "Away Team"
    assert out["player_team_side"] is None


def test_resolve_leg_teams_team_leg_gets_both_names_and_side_none():
    # Team-market (NRFI/F5) legs carry no player_id at all -- game-level.
    games = {7: (300, 400, "Athletics", "Reds")}
    players = {}
    leg = {"game_id": 7, "kind": "team", "player_id": None}

    out = api_main._resolve_leg_teams(leg, games, players)

    assert out == {
        "home_team": "Athletics",
        "away_team": "Reds",
        "player_team_side": None,
    }


def test_resolve_leg_teams_missing_game_id_is_all_none_no_raise():
    # game_id not present in the `games` map (unresolved) -- best-effort
    # context, never required for the leg to render.
    leg = {"game_id": 12345, "player_id": 10}

    out = api_main._resolve_leg_teams(leg, {}, {10: 100})

    assert out == {"home_team": None, "away_team": None, "player_team_side": None}


def test_resolve_leg_teams_player_id_not_in_players_map_is_side_none():
    # Player leg whose player_id has no row in the batched players query
    # (e.g. deleted/unknown player) -- graceful, not a raise.
    games = {5: (100, 200, "Home Team", "Away Team")}
    leg = {"game_id": 5, "player_id": 999}

    out = api_main._resolve_leg_teams(leg, games, {})

    assert out["player_team_side"] is None
    assert out["home_team"] == "Home Team"


# --- fake DB plumbing (same isolation as tests/test_builder_record_api.py) ---


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


# --- endpoint test: saved_builder_parlays() query order + enrichment --------


def test_saved_builder_parlays_query_order_and_enrichment(monkeypatch):
    """Confirms the exact call order this plan requires: the main
    parlay_recommendations row query, THEN the batched games+teams query,
    THEN the batched players query -- and that the resolved fields land on
    the right legs."""
    builder_row = (
        42, "2026-07-27 20:00:00-04", 1.4, 0.71, 1.4,
        {"class": "across_game", "legs": [
            {"kind": "player", "game_id": 1, "player_id": 10, "stat_type": "hits",
             "market": None, "side": "over", "odds": -150, "line": 0.5,
             "label": "Player A hits over 0.5", "market_prob": 0.62, "model_prob": 0.60},
            {"kind": "team", "game_id": 2, "player_id": None, "stat_type": None,
             "market": "f5_runs", "side": "under", "odds": 105, "line": 1.5,
             "label": "f5_runs under 1.5", "market_prob": 0.55, "model_prob": None},
        ]},
    )
    games_rows = [
        (1, 11, 12, "Oakland Athletics", "Cincinnati Reds"),
        (2, 13, 14, "Boston Red Sox", "Chicago White Sox"),
    ]
    players_rows = [(10, 12)]  # player's team_id matches game 1's away_id

    fake_engine = _FakeEngine([[builder_row], games_rows, players_rows])
    monkeypatch.setattr(api_main, "engine", fake_engine)

    out = api_main.saved_builder_parlays(limit=10)

    assert len(out) == 1
    legs = {leg.kind: leg for leg in out[0].legs}
    player_leg = legs["player"]
    assert isinstance(player_leg, BuilderLegOut)
    assert (player_leg.home_team, player_leg.away_team) == (
        "Oakland Athletics", "Cincinnati Reds",
    )
    assert player_leg.player_team_side == "away"

    team_leg = legs["team"]
    assert (team_leg.home_team, team_leg.away_team) == (
        "Boston Red Sox", "Chicago White Sox",
    )
    assert team_leg.player_team_side is None
    # The FakeConn queue was fully drained in the required order (main rows,
    # games, players) -- if the order were wrong, one of the two assertions
    # above would have picked up the wrong result set instead of raising.
    assert fake_engine._queue == []


def test_saved_builder_parlays_no_legs_skips_enrichment_queries(monkeypatch):
    """No rows at all -> no game_ids/player_ids -> the games/players queries
    are never issued (only the main query is popped from the queue)."""
    fake_engine = _FakeEngine([[]])
    monkeypatch.setattr(api_main, "engine", fake_engine)

    out = api_main.saved_builder_parlays(limit=10)

    assert out == []
    assert fake_engine._queue == []
