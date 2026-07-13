import re
import unicodedata
from datetime import datetime, timedelta

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


def load_team_index(conn, sport):
    """normalized team name -> team_id, for one sport — cross-sport name
    collisions (and multi-sport ambiguity generally) are excluded up front."""
    rows = conn.execute(
        text("SELECT team_id, name FROM teams WHERE sport = :sport"), {"sport": sport}
    ).fetchall()
    return {normalize_name(name): team_id for team_id, name in rows}


def load_player_index(conn, sport):
    """normalized player name -> list of (player_id, team_id), for one sport.

    API-Basketball stores names as "Last First" (e.g. "James LeBron"), but other
    providers use "First Last". Index both word orders for two-word names so either
    convention matches.
    """
    rows = conn.execute(
        text("SELECT player_id, name, team_id FROM players WHERE sport = :sport"),
        {"sport": sport},
    ).fetchall()
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


def load_game_index(conn, sport):
    """(home_team_id, away_team_id, date) -> game_id, for one sport"""
    rows = conn.execute(
        text("SELECT game_id, home_team_id, away_team_id, date FROM games WHERE sport = :sport"),
        {"sport": sport},
    ).fetchall()
    return {(home_id, away_id, str(date)): game_id for game_id, home_id, away_id, date in rows}


def match_game(home_team_id, away_team_id, date, game_index):
    return game_index.get((home_team_id, away_team_id, str(date)))


def utc_start_to_local_date(starts_at):
    """Local calendar date of a game from its UTC start timestamp.

    games.date stores the home team's local date, but odds providers report
    start times in UTC — a 7pm ET start is already the next day in UTC, so
    taking the UTC date directly mismatches every US night game. And matching
    "date or the day before" is unsafe in MLB, where series mean the same two
    teams really do play on consecutive days. Subtracting 6 hours (between
    ET's -4 and PT's -7 in season) recovers the local date exactly for any
    US game starting between 10am and midnight local, in any US timezone.
    Doubleheaders remain inherently ambiguous by (teams, date) alone — the
    index keeps one game per key, so both legs' lines attach to one of them.
    """
    if not starts_at:
        return None
    dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
    return (dt - timedelta(hours=6)).date().isoformat()
