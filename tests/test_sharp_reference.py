"""Pure tests for the sharp-reference snapshot pipeline (README §15.9 item 14e).

DB-free and socket-free, following tests/test_parlay.py's conventions:
ingestion/sharp_ingest.py's matching + parsing and optimizer/sharp_compare.py's
comparison rules are all pure functions.
"""

from datetime import date, datetime, timezone

from ingestion.sharp_ingest import (
    card_markets_by_game,
    event_et_date,
    event_sharp_rows,
    match_events,
    normalize_name,
)
from optimizer.sharp_compare import leg_vs_sharp, sharp_prob_for_side, summarize


# --- name + date normalization -------------------------------------------

def test_normalize_name_accents_dots_case_whitespace():
    assert normalize_name("Albert Suárez") == normalize_name("albert  suarez")
    assert normalize_name("J.T. Brubaker") == normalize_name("JT Brubaker")
    assert normalize_name(None) == ""


def test_event_et_date_handles_utc_rollover():
    # 02:10 UTC is 22:10 ET the PREVIOUS day — a west-coast night game.
    assert event_et_date("2026-08-12T02:10:00Z") == date(2026, 8, 11)
    assert event_et_date("2026-08-11T22:41:00Z") == date(2026, 8, 11)


# --- event -> game matching ----------------------------------------------

_SLATE = [
    (1, date(2026, 8, 11), "Detroit Tigers", "Cleveland Guardians"),
    (2, date(2026, 8, 11), "New York Yankees", "Boston Red Sox"),
]


def _event(home, away, commence="2026-08-11T22:41:00Z", eid="ev1"):
    return {"id": eid, "home_team": home, "away_team": away,
            "commence_time": commence}


def test_match_events_by_names_and_et_date():
    events = [_event("Detroit Tigers", "Cleveland Guardians")]
    assert match_events(events, _SLATE) == {1: events[0]}


def test_match_events_skips_unknown_team_and_wrong_date():
    events = [_event("Detroit Tigres", "Cleveland Guardians"),
              _event("New York Yankees", "Boston Red Sox",
                     commence="2026-08-13T22:00:00Z")]
    assert match_events(events, _SLATE) == {}


def test_match_events_applies_team_aliases():
    # The Odds API says "Athletics"; our teams table says "Oakland Athletics".
    slate = [(7, date(2026, 8, 11), "Oakland Athletics", "Tampa Bay Rays")]
    events = [_event("Athletics", "Tampa Bay Rays")]
    assert match_events(events, slate) == {7: events[0]}


def test_match_events_drops_doubleheader_as_ambiguous():
    slate = _SLATE + [(3, date(2026, 8, 11),
                       "Detroit Tigers", "Cleveland Guardians")]
    events = [_event("Detroit Tigers", "Cleveland Guardians", eid="g1"),
              _event("Detroit Tigers", "Cleveland Guardians",
                     commence="2026-08-11T17:10:00Z", eid="g2")]
    assert match_events(events, slate) == {}


# --- bookmaker payload -> sharp_lines rows -------------------------------

_PLAYERS = {normalize_name("Jake Bird"): 55}


def _payload(markets, book="pinnacle"):
    return {"id": "ev1", "home_team": "Detroit Tigers",
            "away_team": "Cleveland Guardians",
            "bookmakers": [{"key": book, "markets": markets}]}


def test_h2h_becomes_moneyline_home_away_row():
    event = _payload([{"key": "h2h", "outcomes": [
        {"name": "Detroit Tigers", "price": -123},
        {"name": "Cleveland Guardians", "price": 114}]}])
    rows, skipped = event_sharp_rows(event, 9, _PLAYERS)
    assert skipped == []
    assert rows == [{"game_id": 9, "player_id": None, "market": "moneyline",
                     "line_value": None, "book": "pinnacle", "over_odds": None,
                     "under_odds": None, "home_odds": -123, "away_odds": 114}]


def test_inning_totals_map_to_our_market_names():
    event = _payload([{"key": "totals_1st_1_innings", "outcomes": [
        {"name": "Over", "point": 0.5, "price": 120},
        {"name": "Under", "point": 0.5, "price": -145}]}])
    rows, _ = event_sharp_rows(event, 9, _PLAYERS)
    assert rows[0]["market"] == "first_inning_runs"
    assert rows[0]["line_value"] == 0.5
    assert rows[0]["over_odds"] == 120 and rows[0]["under_odds"] == -145


def test_player_prop_matches_by_normalized_name():
    event = _payload([{"key": "batter_home_runs", "outcomes": [
        {"name": "Over", "description": "Jake  Bird", "point": 0.5, "price": 400},
        {"name": "Under", "description": "Jake  Bird", "point": 0.5, "price": -650},
        {"name": "Over", "description": "Unknown Guy", "point": 0.5, "price": 300},
        {"name": "Under", "description": "Unknown Guy", "point": 0.5, "price": -400}]}])
    rows, skipped = event_sharp_rows(event, 9, _PLAYERS)
    assert [r["player_id"] for r in rows] == [55]
    assert rows[0]["market"] == "home_runs"
    assert skipped == ["Unknown Guy"]


def test_one_sided_pair_and_foreign_book_are_dropped():
    event = _payload([{"key": "batter_home_runs", "outcomes": [
        {"name": "Over", "description": "Jake Bird", "point": 0.5, "price": 400}]}])
    assert event_sharp_rows(event, 9, _PLAYERS)[0] == []
    event = _payload([{"key": "totals_1st_5_innings", "outcomes": [
        {"name": "Over", "point": 4.5, "price": -110},
        {"name": "Under", "point": 4.5, "price": -110}]}], book="draftkings")
    assert event_sharp_rows(event, 9, _PLAYERS)[0] == []


def test_card_markets_by_game_collects_only_mapped_markets():
    wrappers = [
        {"legs": [{"game_id": 1, "stat_type": "home_runs"},
                  {"game_id": 1, "market": "first_inning_runs"},
                  {"game_id": 2, "stat_type": "not_a_market"}]},
        '{"legs": [{"game_id": 2, "stat_type": "hits"}]}',
    ]
    assert card_markets_by_game(wrappers) == {
        1: {"home_runs", "first_inning_runs"}, 2: {"hits"}}


# --- booked leg vs sharp row ---------------------------------------------

def _sharp(over=-106, under=-106, line=0.5, **extra):
    row = {"over_odds": over, "under_odds": under, "line_value": line,
           "home_odds": None, "away_odds": None}
    row.update(extra)
    return row


def test_fair_ratio_math():
    # Symmetric -106/-106 de-vigs to 0.5; booked -110 pays 1.909x.
    leg = {"side": "under", "line": 0.5, "odds": -110, "market_prob": 0.52}
    row = leg_vs_sharp(leg, _sharp())
    assert abs(row["fair_prob"] - 0.5) < 1e-9
    assert abs(row["fair_ratio"] - 0.5 * (1 + 100 / 110)) < 1e-6
    assert abs(row["fair_delta_pp"] - (-2.0)) < 1e-9


def test_moved_line_is_a_different_bet():
    leg = {"side": "under", "line": 0.5, "odds": -110, "market_prob": 0.52}
    assert leg_vs_sharp(leg, _sharp(line=1.5)) is None
    assert leg_vs_sharp(leg, _sharp(line=None)) is None


def test_one_sided_sharp_row_is_not_comparable():
    leg = {"side": "under", "line": 0.5, "odds": -110}
    assert leg_vs_sharp(leg, _sharp(over=None)) is None
    assert leg_vs_sharp(leg, None) is None


def test_moneyline_sides_use_home_away_columns():
    leg = {"side": "away", "line": None, "odds": 120, "market_prob": 0.45}
    row = leg_vs_sharp(leg, {"line_value": None, "over_odds": None,
                             "under_odds": None, "home_odds": -123,
                             "away_odds": 114})
    assert row is not None and 0 < row["fair_prob"] < 0.5
    assert sharp_prob_for_side({"home_odds": -123, "away_odds": 114},
                               "sideways") is None


def test_summarize_coverage_counts_incomparable_legs():
    good = ({"side": "under", "line": 0.5, "odds": -110, "market_prob": 0.5},
            _sharp())
    bad = ({"side": "under", "line": 0.5, "odds": -110}, None)
    summary = summarize([good, bad])
    assert summary["n_legs"] == 2 and summary["n_compared"] == 1
    assert summary["coverage"] == 0.5
    assert summary["n_below_fair"] == 1  # 0.5 * 1.909 < 1
