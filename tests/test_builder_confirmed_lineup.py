from optimizer.builder import filter_by_confirmed_lineup


def _player(pid, gid=1):
    return {"kind": "player", "player_id": pid, "game_id": gid}


def _team(gid=1):
    return {"kind": "team", "player_id": None, "game_id": gid}


def test_keeps_only_players_in_the_posted_lineup():
    legs = [_player(1), _player(2), _player(3)]
    kept = filter_by_confirmed_lineup(legs, {1, 3}, set())
    assert [leg["player_id"] for leg in kept] == [1, 3]


def test_drops_legs_from_games_already_started():
    legs = [_player(1, gid=10), _player(2, gid=20)]
    kept = filter_by_confirmed_lineup(legs, {1, 2}, {20})
    assert [leg["player_id"] for leg in kept] == [1]


def test_team_legs_survive_the_lineup_filter_but_not_the_started_filter():
    # Team markets have no player, so a lineup can never confirm them; they are
    # never lineup-filtered. A started game is still excluded.
    legs = [_team(gid=10), _team(gid=20)]
    kept = filter_by_confirmed_lineup(legs, set(), {20})
    assert [leg["game_id"] for leg in kept] == [10]


def test_none_confirmed_ids_is_off_and_returns_input_unchanged():
    legs = [_player(1), _player(2), _team(3)]
    assert filter_by_confirmed_lineup(legs, None, None) == legs


def test_empty_confirmed_set_drops_every_player_leg():
    # Distinct from None: an empty posted set means "no lineups yet", and the
    # caller (not this pure helper) decides that is a no-build.
    legs = [_player(1), _team(2)]
    kept = filter_by_confirmed_lineup(legs, set(), set())
    assert kept == [_team(2)]


# --- CLI guards --------------------------------------------------------------
# --require-confirmed-lineup builds the MIXED player+team card. Combined with
# --team-only the class check would label a pure team card 'confirmed_lineup';
# --same-game returns before the lineup fetch, silently ignoring the flag.

import pytest


@pytest.mark.parametrize("extra", ["--team-only", "--same-game"])
def test_require_confirmed_lineup_rejects_incompatible_modes(monkeypatch, extra):
    monkeypatch.setattr(
        "sys.argv",
        ["builder", "--target-payout", "1.4", "--require-confirmed-lineup", extra],
    )
    from optimizer.builder import main
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2  # argparse usage error
