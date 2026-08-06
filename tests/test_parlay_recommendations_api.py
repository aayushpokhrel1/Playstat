"""Tests for the shared api.main._as_legs_list JSONB unwrap helper.

Historically this file also covered GET /parlay-recommendations (README §15.10
bug #5). That endpoint was DEPRECATED then removed 2026-08-06 (README §16 —
the model is shelved; Budgerr migrated onto /parlay-builder/saved), so its
DB-query / kind-filter tests are gone. `_as_legs_list` itself survives: it is
still used by GET /parlay-builder/saved to unwrap the {"class", "legs": [...]}
JSONB wrapper (psycopg2 hands JSONB back already parsed, so it arrives as a
dict and json.loads(dict) raises TypeError). These unit tests guard that
unwrap.

DB-free by design, matching tests/test_settle.py's conventions.
"""

import api.main as api_main


# --- _as_legs_list: the unwrap fix (README §15.10 bug #5) --------------------

def test_as_legs_list_passes_through_bare_list():
    # The legacy kind='player' shape: legs is already a bare list.
    legs = [{"player_id": 1, "game_id": 2, "stat_type": "hits", "side": "over",
             "model_prob": 0.6, "odds": -120}]
    assert api_main._as_legs_list(legs) is legs


def test_as_legs_list_parses_json_string():
    assert api_main._as_legs_list('[{"player_id": 1}]') == [{"player_id": 1}]


def test_as_legs_list_unwraps_builder_dict_wrapper():
    """Regression: this previously raised TypeError, because psycopg2 hands
    JSONB back already parsed (a dict), and json.loads(dict) is invalid."""
    blob = {"class": "across_game", "legs": [{"kind": "player", "player_id": 9}]}
    assert api_main._as_legs_list(blob) == blob["legs"]


def test_as_legs_list_unwraps_team_dict_wrapper():
    # optimizer/team_parlay.py's wrapper additionally carries an "ev" key.
    blob = {"class": "team_pair", "ev": 0.1, "legs": [{"game_id": 1, "market": "f5_runs"}]}
    assert api_main._as_legs_list(blob) == blob["legs"]
