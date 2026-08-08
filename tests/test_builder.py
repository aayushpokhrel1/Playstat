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
    assert "g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)" in source
    # The pre-existing FT guard must stay — slate window is additive, not a
    # replacement for the not-yet-finished-games filter.
    assert "g.status != 'FT'" in source


def test_load_legs_threads_slate_date_through_to_both_loaders():
    sig = inspect.signature(builder.load_legs)
    assert sig.parameters["slate_date"].default is None
    source = inspect.getsource(builder.load_legs)
    # NFL tier #2 threads a trailing `sport` param alongside slate_date (see
    # test_load_legs_threads_sport_to_both_loaders below); NFL chain #4a adds a
    # further trailing `window_days` param; §15.9 item 11 Option B adds trailing
    # confirmed_ids/started_game_ids — updated here to match, same intent:
    # slate_date is still passed positionally to both.
    assert "load_player_legs(engine, floor, slate_date, sport, window_days, min_start_rate," in source
    assert "load_team_legs(engine, floor, slate_date, sport, window_days, started_game_ids)" in source


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


class _SelectResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _CapturingConn:
    def __init__(self, calls, select_rows):
        self._calls = calls
        self._select_rows = select_rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        # save_builds first SELECTs the constructions already saved today (dedup),
        # then INSERTs. Return the seeded existing rows for the SELECT; only the
        # INSERT params are recorded in .calls (what these tests assert on).
        if "SELECT" in str(stmt):
            return _SelectResult(self._select_rows)
        self._calls.append(params)
        return None


class _CapturingEngine:
    """Records the params passed to conn.execute — never touches a real DB.
    select_rows seeds the dedup SELECT (each row a 1-tuple whose [0] is the
    already-saved construction's legs list); default empty = nothing saved yet."""

    def __init__(self, select_rows=()):
        self.calls = []
        self._select_rows = list(select_rows)

    def begin(self):
        return _CapturingConn(self.calls, self._select_rows)


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


def test_save_builds_writes_book_into_legs_json():
    """Line shopping (§15.9 item 3): a leg's shopped `book` round-trips into the
    stored legs JSONB."""
    engine = _CapturingEngine()
    builder.save_builds(engine, 1.4, _one_result("player", book="fanduel"))
    blob = json.loads(engine.calls[0]["legs"])
    assert blob["legs"][0]["book"] == "fanduel"


def test_save_builds_book_defaults_none_when_absent():
    """A hand-built/legacy leg without a `book` key stores book=None (no crash)."""
    engine = _CapturingEngine()
    builder.save_builds(engine, 1.4, _one_result("player"))  # _one_result has no book
    blob = json.loads(engine.calls[0]["legs"])
    assert blob["legs"][0]["book"] is None


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
    assert "g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)" in source
    assert "g.status != 'FT'" in source


def test_load_legs_threads_sport_to_both_loaders():
    sig = inspect.signature(builder.load_legs)
    assert sig.parameters["sport"].default == "mlb"
    source = inspect.getsource(builder.load_legs)
    assert "load_player_legs(engine, floor, slate_date, sport, window_days, min_start_rate," in source
    assert "load_team_legs(engine, floor, slate_date, sport, window_days, started_game_ids)" in source


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


# --- dedup identical constructions across target-payout builds ---------------
# A thin team tier often has ONE 2-leg card that clears both the 1.4x and 2.0x
# floors, so each --target-payout build saved the SAME construction -> duplicate
# dashboard rows + a double-counted paper-ledger bet (found live 2026-07-30).

def _saved_legs(*legs):
    """The stored legs->'legs' shape (as save_builds writes it)."""
    return [
        {"kind": k, "game_id": g, "player_id": p, "stat_type": st, "market": m,
         "side": s, "line": ln, "odds": o, "label": "x", "market_prob": 0.6, "model_prob": None}
        for (k, g, p, st, m, s, ln, o) in legs
    ]


def test_construction_signature_order_independent_and_target_agnostic():
    a = _saved_legs(
        ("team", 1, None, None, "first_inning_runs", "under", 0.5, -150),
        ("team", 2, None, None, "first_inning_runs", "over", 0.5, -145),
    )
    b = list(reversed(a))  # same legs, different order -> same signature
    assert builder.construction_signature(a) == builder.construction_signature(b)
    c = _saved_legs(  # one leg's odds differ -> different signature
        ("team", 1, None, None, "first_inning_runs", "under", 0.5, -150),
        ("team", 2, None, None, "first_inning_runs", "over", 0.5, -140),
    )
    assert builder.construction_signature(a) != builder.construction_signature(c)


def _team_result():
    return [{
        "legs": [
            {"kind": "team", "game_id": 1, "player_id": None, "stat_type": None,
             "market": "first_inning_runs", "side": "under", "line_value": 0.5,
             "american_odds": -150, "market_prob": 0.6, "model_prob": None, "label": "x"},
            {"kind": "team", "game_id": 2, "player_id": None, "stat_type": None,
             "market": "first_inning_runs", "side": "over", "line_value": 0.5,
             "american_odds": -145, "market_prob": 0.6, "model_prob": None, "label": "y"},
        ],
        "joint_prob": 0.315, "combined_odds": 2.816,
    }]


def test_save_builds_skips_construction_already_saved_today():
    existing = _saved_legs(
        ("team", 1, None, None, "first_inning_runs", "under", 0.5, -150),
        ("team", 2, None, None, "first_inning_runs", "over", 0.5, -145),
    )
    engine = _CapturingEngine(select_rows=[(existing,)])  # the 1.4x build already saved it
    saved = builder.save_builds(engine, 2.0, _team_result(), parlay_class="team_tier")
    assert saved == 0
    assert engine.calls == []  # no INSERT — identical construction skipped


def test_save_builds_saves_when_nothing_identical_today():
    engine = _CapturingEngine(select_rows=[])  # nothing saved yet today
    saved = builder.save_builds(engine, 1.4, _team_result(), parlay_class="team_tier")
    assert saved == 1
    assert len(engine.calls) == 1


def test_save_builds_dedups_within_one_batch():
    # Two identical constructions in ONE build must save once (belt-and-suspenders).
    engine = _CapturingEngine(select_rows=[])
    saved = builder.save_builds(engine, 1.4, _team_result() + _team_result(), parlay_class="team_tier")
    assert saved == 1
    assert len(engine.calls) == 1


def test_main_has_sport_flag_defaulting_to_mlb_and_threads_it():
    source = inspect.getsource(builder.main)
    assert '"--sport"' in source or "'--sport'" in source
    assert 'default="mlb"' in source
    # threaded into loading and saving
    assert "args.sport" in source
    assert "load_legs(engine, args.floor, args.slate_date, args.sport, window_days," in source
    assert "load_team_legs(engine, args.floor, args.slate_date, args.sport, window_days," in source
    assert ", args.sport)" in source  # save_builds call carries sport last


# --- per-sport slate window (NFL builder chain #4a) --------------------------

def test_slate_window_days_map_has_mlb_zero_nfl_four():
    assert builder.SLATE_WINDOW_DAYS["mlb"] == 0
    assert builder.SLATE_WINDOW_DAYS["nfl"] == 4


@pytest.mark.parametrize("fn", [builder.load_player_legs, builder.load_team_legs, builder.load_legs])
def test_loaders_have_window_days_param_defaulting_to_zero(fn):
    sig = inspect.signature(fn)
    assert "window_days" in sig.parameters
    assert sig.parameters["window_days"].default == 0


@pytest.mark.parametrize("fn", [builder.load_player_legs, builder.load_team_legs])
def test_loaders_thread_window_days_into_query_params(fn):
    src = inspect.getsource(fn)
    # upper bound of the BETWEEN range uses the bound param
    assert "COALESCE(:slate_date, CURRENT_DATE) + :window_days" in src
    assert '"window_days": window_days' in src


def _fake_build_with_stats(*a, **k):
    # main() reads stats['candidate_games'/'nodes'/'matches'/'truncated'] after
    # the call (see test_cli_max_leg_reuse_threads_into_build) — populate them
    # like the real build() does, since this fake replaces it entirely.
    if k.get("stats") is not None:
        k["stats"].update({"candidate_games": 0, "nodes": 0, "matches": 0, "truncated": False})
    return []


def test_main_resolves_window_days_from_sport(monkeypatch):
    # --sport nfl with no --window-days -> build() gets window_days from the map (4)
    captured = {}
    monkeypatch.setattr("optimizer.builder.db.get_engine", lambda: object())
    monkeypatch.setattr("optimizer.builder.load_legs",
                        lambda engine, floor, slate_date, sport, window_days, min_start_rate=0.0, confirmed_ids=None, started_game_ids=None: captured.update(window_days=window_days) or [{"x": 1}])
    monkeypatch.setattr("optimizer.builder.build", _fake_build_with_stats)
    monkeypatch.setattr("sys.argv", ["builder", "--target-payout", "1.4", "--sport", "nfl"])
    from optimizer.builder import main
    main()
    assert captured["window_days"] == 4


def test_main_window_days_flag_overrides_sport_default(monkeypatch):
    captured = {}
    monkeypatch.setattr("optimizer.builder.db.get_engine", lambda: object())
    monkeypatch.setattr("optimizer.builder.load_legs",
                        lambda engine, floor, slate_date, sport, window_days, min_start_rate=0.0, confirmed_ids=None, started_game_ids=None: captured.update(window_days=window_days) or [{"x": 1}])
    monkeypatch.setattr("optimizer.builder.build", _fake_build_with_stats)
    monkeypatch.setattr("sys.argv", ["builder", "--target-payout", "1.4", "--sport", "nfl", "--window-days", "0"])
    from optimizer.builder import main
    main()
    assert captured["window_days"] == 0


# --- per-sport game-market tier (NFL builder sub-project #3) -----------------

def test_team_markets_per_sport():
    assert builder.TEAM_MARKETS["mlb"] == ("first_inning_runs", "f5_runs")
    assert builder.TEAM_MARKETS["nfl"] == ("full_game_total", "full_game_spread", "full_game_moneyline")


def test_team_class_nfl_is_game_tier_mlb_is_team_tier():
    assert builder._team_class("nfl") == "game_tier"
    assert builder._team_class("mlb") == "team_tier"
    assert builder._team_class("other") == "team_tier"


def test_normalize_keeps_homeaway_and_applies_floor():
    import pandas as pd
    df = pd.DataFrame([
        # moneyline home favorite (~0.71) -> kept
        {"game_id": 1, "market": "full_game_moneyline", "line_value": None,
         "over_odds": None, "under_odds": None, "home_odds": -250, "away_odds": 200, "model_prob": None},
        # coin-flip total (~0.52) -> filtered by the 0.55 floor
        {"game_id": 2, "market": "full_game_total", "line_value": 44.5,
         "over_odds": -105, "under_odds": -105, "home_odds": None, "away_odds": None, "model_prob": None},
    ])
    legs = builder._normalize(df, normalize_team_leg, floor=0.55)
    assert [l["market"] for l in legs] == ["full_game_moneyline"]


def test_normalize_drops_one_sided_line_with_nan_odds():
    # REGRESSION (architect, live-caught 2026-07-29): a book quoting only one
    # side arrives as pandas NaN in a numeric column — NOT Python None — so the
    # `is None` validity check let it through and normalize_player_leg crashed on
    # int(NaN). The row must be DROPPED, exactly like the old dropna did. Green
    # unit tests missed this because they built dicts with real None, not NaN.
    import pandas as pd
    from optimizer.builder_core import normalize_player_leg
    df = pd.DataFrame([
        # one-sided (under_odds NaN in a float column) -> must be dropped
        {"player_id": 1, "game_id": 10, "stat_type": "hits", "line_value": 1.5,
         "over_odds": -120, "under_odds": None, "player_name": "X", "model_prob": None},
        # two-sided -> kept
        {"player_id": 2, "game_id": 11, "stat_type": "hits", "line_value": 1.5,
         "over_odds": -120, "under_odds": 100, "player_name": "Y", "model_prob": None},
    ])
    assert df["under_odds"].isna().iloc[0]  # confirm the fixture is real NaN, not None
    legs = builder._normalize(df, normalize_player_leg, floor=0.0)
    assert [l["player_id"] for l in legs] == [2]


# --- builder independence: --max-leg-reuse CLI flag (docs/superpowers/specs/
# 2026-07-29-builder-independence-design.md) ----------------------------------

def test_main_has_max_leg_reuse_flag_defaulting_to_2():
    source = inspect.getsource(builder.main)
    assert "--max-leg-reuse" in source
    assert "default=2" in source
    assert "max_uses=args.max_leg_reuse" in source


def test_cli_max_leg_reuse_threads_into_build(monkeypatch):
    # CRITICAL SAFETY: db.get_engine/load_legs are stubbed below so this never
    # touches the live production DB (ingestion.db.get_engine() is LIVE).
    captured = {}

    def _fake_build(*a, **k):
        captured.update(k)
        # main() reads stats['candidate_games'/'nodes'/'matches'/'truncated']
        # after the call — populate them like the real build() does, since
        # this fake replaces it entirely.
        if k.get("stats") is not None:
            k["stats"].update(
                {"candidate_games": 0, "nodes": 0, "matches": 0, "truncated": False}
            )
        return []

    monkeypatch.setattr("optimizer.builder.build", _fake_build)
    monkeypatch.setattr("optimizer.builder.db.get_engine", lambda: object())
    monkeypatch.setattr("optimizer.builder.load_legs", lambda *a, **k: [{"x": 1}])
    monkeypatch.setattr(
        "sys.argv",
        ["builder", "--target-payout", "1.4", "--max-leg-reuse", "3"],
    )
    from optimizer.builder import main
    main()
    assert captured["max_uses"] == 3


def test_cli_max_leg_reuse_defaults_to_2(monkeypatch):
    captured = {}

    def _fake_build(*a, **k):
        captured.update(k)
        if k.get("stats") is not None:
            k["stats"].update(
                {"candidate_games": 0, "nodes": 0, "matches": 0, "truncated": False}
            )
        return []

    monkeypatch.setattr("optimizer.builder.build", _fake_build)
    monkeypatch.setattr("optimizer.builder.db.get_engine", lambda: object())
    monkeypatch.setattr("optimizer.builder.load_legs", lambda *a, **k: [{"x": 1}])
    monkeypatch.setattr("sys.argv", ["builder", "--target-payout", "1.4"])
    from optimizer.builder import main
    main()
    assert captured["max_uses"] == 2


def test_normalize_moneyline_nan_line_coerced_to_none():
    # A moneyline's NULL line arrives as NaN when the line_value column is float
    # (a spread row alongside forces float dtype). It must land as None, not NaN,
    # so normalize_team_leg's `line is None` label branch and settlement work.
    import pandas as pd
    df = pd.DataFrame([
        {"game_id": 1, "market": "full_game_moneyline", "line_value": None,
         "over_odds": None, "under_odds": None, "home_odds": -250, "away_odds": 200, "model_prob": None},
        {"game_id": 2, "market": "full_game_spread", "line_value": -3.5,
         "over_odds": None, "under_odds": None, "home_odds": -110, "away_odds": -110, "model_prob": None},
    ])
    assert df["line_value"].isna().iloc[0]  # real NaN in a float column
    legs = {l["market"]: l for l in builder._normalize(df, normalize_team_leg, floor=0.0)}
    assert legs["full_game_moneyline"]["line_value"] is None


def test_save_builds_writes_lift_metadata_for_same_game():
    engine = _CapturingEngine()
    results = _one_result("team")
    results[0].update({"lift": 1.30, "lift_n": 2100, "both_n": 1000, "small_sample": False})
    builder.save_builds(engine, 0.0, results, parlay_class="same_game_pair", sport="mlb")
    blob = json.loads(engine.calls[0]["legs"])
    assert blob["class"] == "same_game_pair"
    assert blob["lift"] == 1.30 and blob["lift_n"] == 2100
    assert blob["both_n"] == 1000 and blob["small_sample"] is False
    assert len(blob["legs"]) == 1  # _one_result yields a single-leg construction


def test_save_builds_omits_lift_keys_for_normal_classes():
    engine = _CapturingEngine()
    builder.save_builds(engine, 1.4, _one_result("player"))
    blob = json.loads(engine.calls[0]["legs"])
    assert "lift" not in blob and "small_sample" not in blob
    assert set(blob.keys()) == {"class", "sport", "legs"}


def test_build_same_game_helper_wires_pairs():
    """--same-game (§15.9 item 1) threads floor-passing team legs through the pure
    pairing fn with an injected lift — no DB."""
    legs = [
        {"game_id": 1, "market": "first_inning_runs", "side": "under",
         "market_prob": 0.56, "decimal_odds": 1.8, "american_odds": -120,
         "line_value": 0.5, "label": "nrfi", "book": None, "kind": "team"},
        {"game_id": 1, "market": "f5_runs", "side": "under", "market_prob": 0.57,
         "decimal_odds": 1.9, "american_odds": -110, "line_value": 4.5,
         "label": "f5u", "book": None, "kind": "team"},
    ]
    cards = builder.build_same_game(legs, lambda sn, sf, nl, fl: (1.30, 2100, 1000), top_n=5)
    assert len(cards) == 1
    assert cards[0]["lift"] == 1.30 and cards[0]["n_legs"] == 2


def test_same_game_lift_fn_caches_per_side_line_combo():
    """One history read per distinct (sides, lines) combo per run."""
    calls = []

    def fake_lift(engine, sn, sf, nl, fl):
        calls.append((sn, sf, nl, fl))
        return (1.3, 2000, 900)

    import modeling.correlation
    original = builder.nrfi_f5_lift
    builder.nrfi_f5_lift = fake_lift
    try:
        lift_fn = builder._same_game_lift_fn(object())
        lift_fn("under", "under", 0.5, 4.5)
        lift_fn("under", "under", 0.5, 4.5)   # cached
        lift_fn("under", "under", 0.5, 5.5)   # different f5 line -> new read
    finally:
        builder.nrfi_f5_lift = original
    assert len(calls) == 2


# --- start-probability filter (README §15.9 item 11) --------------------------
# The chain builds ~08:39 ET, before MLB lineups post, so 18.3% of legs voided on
# players who were rested/scratched. These lock the pure filter's contract.

def _pleg(player_id, prob=0.9):
    return {"kind": "player", "player_id": player_id, "game_id": 1,
            "stat_type": "home_runs", "market": None, "side": "under",
            "market_prob": prob, "line_value": 0.5, "american_odds": -300,
            "decimal_odds": 1.33, "label": "x", "book": None}


def _tmleg(game_id=1):
    return {"kind": "team", "player_id": None, "game_id": game_id,
            "stat_type": None, "market": "first_inning_runs", "side": "under",
            "market_prob": 0.56, "line_value": 0.5, "american_odds": -130,
            "decimal_odds": 1.77, "label": "nrfi", "book": None}


def test_filter_by_start_rate_drops_infrequent_players():
    legs = [_pleg(1), _pleg(2), _pleg(3)]
    rates = {1: 0.90, 2: 0.40, 3: 0.65}
    kept = builder.filter_by_start_rate(legs, rates, 0.65)
    assert [l["player_id"] for l in kept] == [1, 3]  # 0.65 is inclusive


def test_filter_by_start_rate_never_drops_team_legs():
    legs = [_tmleg(1), _pleg(2)]
    kept = builder.filter_by_start_rate(legs, {2: 0.10}, 0.65)
    assert len(kept) == 1 and kept[0]["kind"] == "team"


def test_filter_by_start_rate_keeps_players_with_no_measurable_history():
    """An ABSENT rate means 'their team had no finished games in the window', not
    'never plays'. Dropping those would empty the pool on an early-season slate."""
    legs = [_pleg(7)]
    assert builder.filter_by_start_rate(legs, {}, 0.65) == legs


def test_filter_by_start_rate_zero_threshold_is_a_no_op():
    legs = [_pleg(1), _pleg(2), _tmleg()]
    assert builder.filter_by_start_rate(legs, {1: 0.0, 2: 0.0}, 0.0) == legs


def test_filter_by_start_rate_drops_explicit_zero_rate():
    """A player whose team played but who never appeared reads 0.0 -> dropped."""
    assert builder.filter_by_start_rate([_pleg(5)], {5: 0.0}, 0.65) == []


def test_load_start_rates_normalises_by_team_games_not_calendar_days():
    """The denominator must be the player's TEAM's games in the window — a
    calendar-day denominator understates everyone (teams play ~6 games/7 days)."""
    source = inspect.getsource(builder.load_start_rates)
    assert "team_games" in source and "player_apps" in source
    assert "COALESCE(pa.n, 0)::float / tg.n" in source
    assert "status = 'FT'" in source          # only finished games count
    assert "tg.n > 0" in source               # no divide-by-zero


def test_load_player_legs_accepts_min_start_rate_defaulting_to_off():
    sig = inspect.signature(builder.load_player_legs)
    assert sig.parameters["min_start_rate"].default == 0.0
    assert inspect.signature(builder.load_legs).parameters["min_start_rate"].default == 0.0


def test_main_exposes_min_start_rate_flag():
    assert "--min-start-rate" in inspect.getsource(builder.main)
