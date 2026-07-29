"""Pure unit tests for NFL odds ingestion (SGO event -> prop_lines/game_lines
rows). No DB, no network: the collectors are pure (event, map) -> list[dict].
SGO statIDs in STAT_MAPS['nfl'] are PROVISIONAL (verify via --dry-run at
preseason); these tests are self-consistent with the map, so they verify the
mapping mechanism, not real-feed statID correctness.
"""

from ingestion.odds_ingest import (
    STAT_MAPS,
    GAME_MARKETS,
    collect_prop_rows,
    collect_game_rows,
    observed_statid_summary,
)


def _prop_odd(stat_id, entity, side, line, price):
    return {
        "statID": stat_id, "statEntityID": entity, "periodID": "game",
        "betTypeID": "ou", "sideID": side, "bookOverUnder": line, "bookOdds": price,
    }


def test_nfl_stat_map_covers_the_twelve_settleable_stat_types():
    assert set(STAT_MAPS["nfl"].values()) == {
        "passing_yards", "rushing_yards", "receiving_yards", "receptions",
        "targets", "passing_tds", "rushing_tds", "receiving_tds",
        "completions", "carries", "pass_attempts", "interceptions_thrown",
    }


def test_collect_prop_rows_maps_an_nfl_passing_yards_market():
    # Pick whatever statID the map uses for passing_yards, so the test stays
    # correct even if the provisional string is corrected later.
    pass_yards_statid = next(k for k, v in STAT_MAPS["nfl"].items() if v == "passing_yards")
    event = {
        "players": {"MAHOMES_1": {"name": "Patrick Mahomes"}},
        "odds": {
            "o1": _prop_odd(pass_yards_statid, "MAHOMES_1", "over", 274.5, "-110"),
            "o2": _prop_odd(pass_yards_statid, "MAHOMES_1", "under", 274.5, "-105"),
        },
    }
    rows = collect_prop_rows(event, STAT_MAPS["nfl"])
    assert rows == [{
        "player_name": "Patrick Mahomes", "stat_type": "passing_yards",
        "line_value": 274.5, "over_odds": -110, "under_odds": -105,
    }]


def test_collect_game_rows_maps_the_nfl_full_game_total():
    event = {
        "odds": {
            "g1": {"statID": "points", "statEntityID": "all", "periodID": "game",
                   "betTypeID": "ou", "sideID": "over", "bookOverUnder": 47.5, "bookOdds": "-110"},
            "g2": {"statID": "points", "statEntityID": "all", "periodID": "game",
                   "betTypeID": "ou", "sideID": "under", "bookOverUnder": 47.5, "bookOdds": "-108"},
        },
    }
    rows = collect_game_rows(event, GAME_MARKETS["nfl"])
    assert rows == [{
        "market": "game_total", "line_value": 47.5,
        "over_odds": -110, "under_odds": -108,
    }]


def test_unmapped_statid_is_skipped_not_raised():
    event = {
        "players": {"K_1": {"name": "Some Kicker"}},
        "odds": {  # kicking points isn't in STAT_MAPS['nfl'] -> ignored
            "o1": _prop_odd("kicking_points", "K_1", "over", 7.5, "-110"),
            "o2": _prop_odd("kicking_points", "K_1", "under", 7.5, "-110"),
        },
    }
    assert collect_prop_rows(event, STAT_MAPS["nfl"]) == []


def test_mapped_stat_on_non_game_period_is_skipped():
    # A passing_yards market but for the 1st half (periodID "1h") -- we only
    # ingest full-game ("game") lines.
    pass_yards_statid = next(k for k, v in STAT_MAPS["nfl"].items() if v == "passing_yards")
    odd = _prop_odd(pass_yards_statid, "P_1", "over", 120.5, "-110")
    odd["periodID"] = "1h"
    event = {"players": {"P_1": {"name": "Half QB"}}, "odds": {"o1": odd}}
    assert collect_prop_rows(event, STAT_MAPS["nfl"]) == []


def test_observed_statid_summary_partitions_mapped_and_unmapped():
    pass_yards_statid = next(k for k, v in STAT_MAPS["nfl"].items() if v == "passing_yards")
    events = [
        {"odds": {
            "a": _prop_odd(pass_yards_statid, "P_1", "over", 250.5, "-110"),
            "b": _prop_odd(pass_yards_statid, "P_1", "under", 250.5, "-110"),
            "c": _prop_odd("kicking_points", "K_1", "over", 7.5, "-110"),
            "d": {"statID": "points", "statEntityID": "all", "periodID": "game",
                  "betTypeID": "ou", "sideID": "over", "bookOverUnder": 47.5, "bookOdds": "-110"},
        }},
    ]
    summary = observed_statid_summary(events, STAT_MAPS["nfl"], GAME_MARKETS["nfl"])
    assert summary["mapped"] == {pass_yards_statid: 2, "points": 1}
    assert summary["unmapped"] == {"kicking_points": 1}
