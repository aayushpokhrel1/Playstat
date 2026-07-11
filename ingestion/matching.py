import re
import unicodedata

from sqlalchemy import text

SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv)\.?\s*$")


def normalize_name(name):
    if not name:
        return ""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    ascii_name = ascii_name.lower().strip()
    ascii_name = SUFFIX_RE.sub("", ascii_name).strip()
    ascii_name = re.sub(r"[^a-z0-9\s]", "", ascii_name)
    ascii_name = re.sub(r"\s+", " ", ascii_name)
    return ascii_name


def load_team_index(conn):
    """normalized team name -> team_id"""
    rows = conn.execute(text("SELECT team_id, name FROM teams")).fetchall()
    return {normalize_name(name): team_id for team_id, name in rows}


def load_player_index(conn):
    """normalized player name -> list of (player_id, team_id).

    API-Basketball stores names as "Last First" (e.g. "James LeBron"), but other
    providers use "First Last". Index both word orders for two-word names so either
    convention matches.
    """
    rows = conn.execute(text("SELECT player_id, name, team_id FROM players")).fetchall()
    index = {}
    for player_id, name, team_id in rows:
        normalized = normalize_name(name)
        index.setdefault(normalized, []).append((player_id, team_id))

        words = normalized.split(" ")
        if len(words) == 2:
            reversed_name = f"{words[1]} {words[0]}"
            index.setdefault(reversed_name, []).append((player_id, team_id))
    return index


def match_team(name, team_index):
    return team_index.get(normalize_name(name))


def match_player(name, player_index, team_id=None):
    """Returns player_id, or None if no match / an unresolvable ambiguous match."""
    candidates = player_index.get(normalize_name(name))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0][0]
    if team_id is not None:
        on_team = [pid for pid, tid in candidates if tid == team_id]
        if len(on_team) == 1:
            return on_team[0]
    return None


def load_game_index(conn):
    """(home_team_id, away_team_id, date) -> game_id"""
    rows = conn.execute(
        text("SELECT game_id, home_team_id, away_team_id, date FROM games")
    ).fetchall()
    return {(home_id, away_id, str(date)): game_id for game_id, home_id, away_id, date in rows}


def match_game(home_team_id, away_team_id, date, game_index):
    return game_index.get((home_team_id, away_team_id, str(date)))
