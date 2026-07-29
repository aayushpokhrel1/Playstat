"""Tests for optimizer/builder.py's DB-facing layer (README §15 Stage 3).

CRITICAL SAFETY: ingestion.db.get_engine() points at the LIVE production
database (from .env). None of these tests call it, and none call save_builds
against a real engine — SQL construction is checked by source inspection
(matching tests/test_parlay_recommendations_api.py's convention), and
save_builds's blob construction is checked against a minimal in-memory
fake engine that only records the params it was called with.
"""

import inspect
import json

import pytest

from optimizer import builder
from optimizer.builder_core import build, normalize_team_leg


# --- slate window (README §15 Change 1) --------------------------------------
# No DB harness exists, so the date predicate and its default are checked
# purely: the SQL text and the slate_date=None default. The real-data
# exclusion of future games is verified separately, read-only, against the
# live (read-only-safe) engine — see the architect's verification notes.

@pytest.mark.parametrize("fn", [builder.load_player_legs, builder.load_team_legs])
def test_slate_date_defaults_to_none(fn):
    sig = inspect.signature(fn)
    assert "slate_date" in sig.parameters
    assert sig.parameters["slate_date"].default is None


@pytest.mark.parametrize("fn", [builder.load_player_legs, builder.load_team_legs])
def test_games_join_has_date_predicate_defaulting_to_current_date(fn):
    source = inspect.getsource(fn)
    assert "g.date = COALESCE(:slate_date, CURRENT_DATE)" in source
    # The pre-existing FT guard must stay — slate window is additive, not a
    # replacement for the not-yet-finished-games filter.
    assert "g.status != 'FT'" in source


def test_load_legs_threads_slate_date_through_to_both_loaders():
    sig = inspect.signature(builder.load_legs)
    assert sig.parameters["slate_date"].default is None
    source = inspect.getsource(builder.load_legs)
    # NFL tier #2 threads a trailing `sport` param alongside slate_date (see
    # test_load_legs_threads_sport_to_both_loaders below) — updated here to
    # match, same intent: slate_date is still passed positionally to both.
    assert "load_player_legs(engine, floor, slate_date, sport)" in source
    assert "load_team_legs(engine, floor, slate_date, sport)" in source


def test_main_has_slate_date_and_team_only_cli_flags():
    source = inspect.getsource(builder.main)
    assert "--slate-date" in source
    assert "--team-only" in source


# --- dedicated team tier (README §15 Change 3) -------------------------------

def _team_leg(game_id, market, over_odds, under_odds, line_value=0.5):
    return normalize_team_leg({
        "game_id": game_id, "market": market, "line_value": line_value,
        "over_odds": over_odds, "under_odds": under_odds, "model_prob": None,
    })


def test_build_on_team_only_legs_returns_team_only_parlays():
    """build() itself needs no team-tier-specific code (README §15 Change 3
    reuses it unmodified) — a team-only leg pool just yields team-only results."""
    legs = [
        _team_leg(1, "first_inning_runs", -150, 130),
        _team_leg(2, "f5_runs", -140, 120),
        _team_leg(3, "first_inning_runs", -160, 135),
    ]
    results = build(legs, target_payout=1.0, min_legs=2, max_legs=2, top_n=5)
    assert results
    for r in results:
        assert r["n_legs"] == 2
        assert all(leg["kind"] == "team" for leg in r["legs"])
        # same 0.55 floor already applied by _normalize/passes_floor upstream;
        # here just confirm no player leg ever leaks in.
        assert all(leg["player_id"] is None for leg in r["legs"])


def test_build_on_team_only_legs_can_legitimately_return_nothing():
    """Team markets price near coin-flip (README §15.10 team-legs note) so a
    too-thin team pool should return [] cleanly, not raise."""
    legs = [_team_leg(1, "first_inning_runs", -150, 130)]  # only one game
    results = build(legs, target_payout=1.0, min_legs=2, max_legs=4, top_n=5)
    assert results == []


class _CapturingConn:
    def __init__(self, calls):
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        self._calls.append(params)
        return None


class _CapturingEngine:
    """Records the params passed to conn.execute — never touches a real DB."""

    def __init__(self):
        self.calls = []

    def begin(self):
        return _CapturingConn(self.calls)


def _one_result(kind="team", **overrides):
    leg = {
        "kind": kind, "game_id": 1, "player_id": None, "stat_type": None,
        "market": "first_inning_runs", "side": "under", "american_odds": -150,
        "line_value": 0.5, "label": "first_inning_runs under 0.5",
        "market_prob": 0.6, "model_prob": None,
    }
    if kind == "player":
        leg.update({"player_id": 5, "stat_type": "hits", "market": None,
                    "label": "X hits over 1.5"})
    leg.update(overrides)
    return [{"legs": [leg], "joint_prob": 0.6, "combined_odds": 1.6}]


def test_save_builds_writes_team_tier_class_when_instructed():
    engine = _CapturingEngine()
    saved = builder.save_builds(engine, 1.4, _one_result("team"), parlay_class="team_tier")
    assert saved == 1
    assert len(engine.calls) == 1
    blob = json.loads(engine.calls[0]["legs"])
    assert blob["class"] == "team_tier"
    assert blob["legs"][0]["market"] == "first_inning_runs"


def test_save_builds_defaults_to_across_game_class_unchanged():
    """No parlay_class argument -> today's exact existing behaviour."""
    engine = _CapturingEngine()
    builder.save_builds(engine, 1.4, _one_result("player"))
    blob = json.loads(engine.calls[0]["legs"])
    assert blob["class"] == "across_game"


# --- sport filtering (NFL builder sub-project #2) ----------------------------

@pytest.mark.parametrize("fn", [builder.load_player_legs, builder.load_team_legs])
def test_loaders_have_sport_param_defaulting_to_mlb(fn):
    sig = inspect.signature(fn)
    assert "sport" in sig.parameters
    assert sig.parameters["sport"].default == "mlb"


@pytest.mark.parametrize("fn", [builder.load_player_legs, builder.load_team_legs])
def test_loaders_filter_games_join_by_sport(fn):
    source = inspect.getsource(fn)
    assert "g.sport = :sport" in source
    # slate + FT guards must remain alongside the new sport filter
    assert "g.date = COALESCE(:slate_date, CURRENT_DATE)" in source
    assert "g.status != 'FT'" in source


def test_load_legs_threads_sport_to_both_loaders():
    sig = inspect.signature(builder.load_legs)
    assert sig.parameters["sport"].default == "mlb"
    source = inspect.getsource(builder.load_legs)
    assert "load_player_legs(engine, floor, slate_date, sport)" in source
    assert "load_team_legs(engine, floor, slate_date, sport)" in source


def test_save_builds_stamps_sport_into_blob():
    engine = _CapturingEngine()
    builder.save_builds(engine, 2.0, _one_result("player"), sport="nfl")
    blob = json.loads(engine.calls[0]["legs"])
    assert blob["sport"] == "nfl"
    assert blob["class"] == "across_game"  # class still written as before


def test_save_builds_sport_defaults_to_mlb():
    engine = _CapturingEngine()
    builder.save_builds(engine, 1.4, _one_result("player"))
    blob = json.loads(engine.calls[0]["legs"])
    assert blob["sport"] == "mlb"


def test_main_has_sport_flag_defaulting_to_mlb_and_threads_it():
    source = inspect.getsource(builder.main)
    assert '"--sport"' in source or "'--sport'" in source
    assert 'default="mlb"' in source
    # threaded into loading and saving
    assert "args.sport" in source
    assert "load_legs(engine, args.floor, args.slate_date, args.sport)" in source
    assert "load_team_legs(engine, args.floor, args.slate_date, args.sport)" in source
    assert ", args.sport)" in source  # save_builds call carries sport last
