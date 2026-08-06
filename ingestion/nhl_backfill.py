"""NHL ingestion via NHL's own public API (api-web.nhle.com).

Why not API-Sports like the NBA path: their hockey API's current season is
paid (free tier is older seasons only), while NHL's own API is free, needs no
key, current-season, and includes per-player box scores + final scores — the
MLB StatsAPI pattern (verified 2026-08-06). A polite request pace is still
kept; some endpoints (e.g. /standings/now) 307-redirect, so redirects are
followed and a User-Agent is sent.

Writes to the same tables as ingestion/backfill.py, with sport='nhl' and every
NHL numeric ID shifted by SPORTS['nhl']['id_offset'] (see config.py) so they
can't collide with other sports' rows. The offset is +1B, NOT the next +100M
band: NHL native game ids are ~2.03e9 (season*1e6+...), already above every
band and fitting INT4 (2,147,483,647) with no room for a positive offset, so
games are stored as `1e9 + (raw - 2e9)` (NHL_GAME_ID_EPOCH); teams/players are
plain `1e9 + raw`.

games.status: the feed's gameState for a finished game is 'OFF' or 'FINAL'
(official); both are stored as 'FT' to match the convention the API layer and
Budgerr's auto-settlement filter on. All finals (REG/OT/SO) map to 'FT' — no
distinct OT/SO status. Other states are stored as the raw gameState.
"""

import argparse
import time
from datetime import date as _date

import requests
from sqlalchemy import text

from ingestion import db
from ingestion.config import SPORTS

NHL_API_BASE = "https://api-web.nhle.com/v1"
NHL_ID_OFFSET = SPORTS["nhl"]["id_offset"]
# NHL native game ids embed the season at the 1e9 place (2025020740 =
# season*1e6 + gametype*1e4 + gamenum). Subtract this epoch so the stored id
# = 1e9 + (raw - 2e9) fits INT4 with room to spare; add it back to refetch.
NHL_GAME_ID_EPOCH = 2_000_000_000

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
SECONDS_BETWEEN_REQUESTS = 0.3


class NHLClient:
    def __init__(self):
        self.session = requests.Session()
        # The NHL API 307s/blocks a bare urllib User-Agent on some endpoints.
        self.session.headers.update({"User-Agent": "playstat-nhl-backfill/1.0"})
        self._last_request_at = 0.0

    def get(self, path, params=None):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < SECONDS_BETWEEN_REQUESTS:
            time.sleep(SECONDS_BETWEEN_REQUESTS - elapsed)

        url = f"{NHL_API_BASE}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            self._last_request_at = time.monotonic()
            try:
                response = self.session.get(url, params=params, timeout=30, allow_redirects=True)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                    continue
                raise
            if response.status_code >= 500 and attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"Exhausted retries fetching {path}")


def nhl_team_id(raw):
    return NHL_ID_OFFSET + int(raw)


def nhl_player_id(raw):
    return NHL_ID_OFFSET + int(raw)


def nhl_game_id(raw):
    raw = int(raw)
    assert raw >= NHL_GAME_ID_EPOCH, f"pre-2000 NHL id not supported: {raw}"
    return NHL_ID_OFFSET + (raw - NHL_GAME_ID_EPOCH)


def nhl_raw_game_id(game_id):  # inverse, for boxscore refetch
    return int(game_id) - NHL_ID_OFFSET + NHL_GAME_ID_EPOCH


def parse_team_names(standings_payload):
    """{teamAbbrev.default -> teamName.default} from /standings/now — the
    authoritative full-name source for the 32 current teams, clean for the NY
    teams and accents ('New York Rangers', 'Montréal Canadiens', 'St. Louis
    Blues'). The /score feed's team block only carries the common name
    ('Sabres') + abbrev, so full names are resolved by abbrev against this map.
    """
    out = {}
    for row in standings_payload.get("standings", []):
        ab = (row.get("teamAbbrev") or {}).get("default")
        full = (row.get("teamName") or {}).get("default")
        if ab and full:
            out[ab] = full
    return out


def fetch_team_names(client):
    return parse_team_names(client.get("/standings/now"))


def resolve_team_name(team_block, names_by_abbrev):
    """Full name for a /score-feed team block: the standings map by abbrev, else
    the feed's common name (e.g. a relocated team absent from current standings —
    stored as its common name, which the nickname map falls back on harmlessly).
    """
    return names_by_abbrev.get(team_block.get("abbrev")) or (team_block.get("name") or {}).get("default")


def extract_skater_stats(skater):
    """Long-format stats from a boxscore skater entry — stat names chosen to
    line up with common NHL prop markets, plus the full scoring/penalty set
    (STAT_MAPS['nhl'] prices shots_on_goal/saves; the richer set is stored
    cheaply since it's all in the boxscore). Zeros are real outcomes and are
    kept: a 0-SOG game is exactly what an under needs.
    """
    return {
        "shots_on_goal": skater.get("sog", 0),
        "goals": skater.get("goals", 0),
        "assists": skater.get("assists", 0),
        "points": skater.get("points", 0),
        "hits": skater.get("hits", 0),
        "blocked_shots": skater.get("blockedShots", 0),
        "pim": skater.get("pim", 0),
    }


def extract_goalie_stats(goalie):
    """Long-format stats from a boxscore goalie entry. Returns None for a goalie
    who never entered the game (toi == '00:00' and no shots against) — the mlb
    analogue of skipping bench players.
    """
    if goalie.get("toi") == "00:00" and goalie.get("shotsAgainst", 0) == 0:
        return None
    return {
        "saves": goalie.get("saves", 0),
        "shots_against": goalie.get("shotsAgainst", 0),
        "goals_against": goalie.get("goalsAgainst", 0),
    }


def final_status(game):
    """Map the NHL feed's gameState to our status convention. A finished game
    reports 'OFF' or 'FINAL' (official) — both store as 'FT'. ALL finals
    (REG/OT/SO) map to 'FT': there is no distinct OT/SO status, because the
    official final already includes OT/shootout goals, so full_game_total
    settles correctly and _FINAL_GAME_STATUSES needs no change.

    SO caveat: a shootout adds the decider goal to the winner's official score;
    most books settle NHL totals on the official final incl. the SO goal, but a
    minority exclude it — a ±1 near-line edge case on ~5-8% of games. We settle
    on the official final (what the NHL API reports). Flag for re-check once
    live settled lines exist; do not block.

    Any other state (LIVE, FUT, ...) passes through raw.
    """
    state = game.get("gameState")
    if state in ("OFF", "FINAL"):
        return "FT"
    return state


def current_nhl_season(today=None):
    """NHL season start year (e.g. 2025 for 2025-26) for the season in progress.
    NHL season N runs Sep(N)..Jun(N+1): Sep..Dec -> this year starts the season;
    Jan..Aug -> the prior year did."""
    today = today or _date.today()
    return today.year if today.month >= 9 else today.year - 1


def backfill_games(client, engine, season):
    """Enumerate a season's games by walking the /score/{date} feed day-by-day
    via its nextDate pointer, from {season}-09-15 through {season+1}-06-30.
    Upserts teams from each game's homeTeam/awayTeam blocks (every team recurs
    across the season — no separate teams endpoint needed, and the score feed's
    placeName is clean for the NY teams), the games themselves, and each final
    game's goals as team_game_stats points (the match-total unit full_game_total
    settles against). Returns the list of finished games (with their raw ids)
    for the player-stats pass.
    """
    start_date = f"{season}-09-15"
    end_date = f"{season + 1}-06-30"

    # The /score feed's team block carries only the common name ('Sabres'); resolve
    # full names ('Buffalo Sabres') by abbrev against one /standings/now pull.
    team_names = fetch_team_names(client)

    games_count = 0
    finished = []
    date = start_date
    # Fetch (paced HTTP) OUTSIDE the DB transaction and commit per day, so the
    # ~200-day season walk never holds one transaction open across minutes of
    # network I/O on the live DB, and a mid-walk failure keeps prior days' work.
    while date <= end_date:
        payload = client.get(f"/score/{date}")
        page_date = payload.get("currentDate") or date
        with engine.begin() as conn:
            for game in payload.get("games", []):
                for side in ("homeTeam", "awayTeam"):
                    t = game[side]
                    db.upsert(
                        conn,
                        "teams",
                        ["team_id"],
                        {
                            "team_id": nhl_team_id(t["id"]),
                            "sport": "nhl",
                            # Full name via /standings/now by abbrev, e.g. "New York Rangers".
                            "name": resolve_team_name(t, team_names),
                            # No clean conference in this feed — leave NULL (matches
                            # the NBA-era nullable conference column).
                            "conference": None,
                        },
                    )
                game_id = nhl_game_id(game["id"])
                status = final_status(game)
                db.upsert(
                    conn,
                    "games",
                    ["game_id"],
                    {
                        "game_id": game_id,
                        "sport": "nhl",
                        # The score page's local date (home-local date for that day's games);
                        # a per-game gameDate field wins if present.
                        "date": (game.get("gameDate") or page_date)[:10],
                        "home_team_id": nhl_team_id(game["homeTeam"]["id"]),
                        "away_team_id": nhl_team_id(game["awayTeam"]["id"]),
                        "status": status,
                    },
                )
                if status == "FT":
                    finished.append(game)
                    # Each side's goals are the match-total unit (mirrors NBA
                    # points / soccer goals-as-points).
                    for side in ("homeTeam", "awayTeam"):
                        t = game[side]
                        db.upsert(
                            conn,
                            "team_game_stats",
                            ["team_id", "game_id", "stat_type"],
                            {
                                "team_id": nhl_team_id(t["id"]),
                                "game_id": game_id,
                                "stat_type": "points",
                                "value": t.get("score", 0),
                            },
                        )
                games_count += 1
        next_date = payload.get("nextDate")
        if not next_date or next_date <= date:
            break  # feed didn't advance; avoid an infinite loop
        date = next_date

    print(f"games: upserted {games_count} ({len(finished)} finished)")
    return finished


def _upsert_player_rows(conn, game, boxscore):
    """Players and their long-format stat rows for one finished game's boxscore."""
    stat_rows = 0
    game_id = nhl_game_id(game["id"])
    by_game = (boxscore.get("playerByGameStats") or {})
    for side in ("homeTeam", "awayTeam"):
        team_id = nhl_team_id(game[side]["id"])
        team_block = by_game.get(side) or {}
        for group in ("forwards", "defense", "goalies"):
            for entry in team_block.get(group) or []:
                player_id = nhl_player_id(entry["playerId"])
                # The boxscore carries only an abbreviated name ("Z. Benson") and
                # no firstName/lastName; prefer the full name if a future payload
                # adds it, else store the abbreviated name. FOLLOW-UP: enrich to
                # full names via /roster before player-prop matching goes live at
                # preseason (names are an updatable players-table attribute keyed
                # on player_id — no box-score re-backfill needed).
                first = (entry.get("firstName") or {}).get("default")
                last = (entry.get("lastName") or {}).get("default")
                if first and last:
                    name = f"{first} {last}"
                else:
                    name = (entry.get("name") or {}).get("default")
                db.upsert(
                    conn,
                    "players",
                    ["player_id"],
                    {
                        "player_id": player_id,
                        "sport": "nhl",
                        "name": name,
                        "team_id": team_id,
                        "position": entry.get("positionCode") or entry.get("position"),
                    },
                )
                if group == "goalies":
                    stats = extract_goalie_stats(entry)
                    if stats is None:
                        continue  # bench goalie, never entered the game
                else:
                    stats = extract_skater_stats(entry)
                for stat_type, value in stats.items():
                    db.upsert(
                        conn,
                        "player_game_stats",
                        ["player_id", "game_id", "stat_type"],
                        {
                            "player_id": player_id,
                            "game_id": game_id,
                            "stat_type": stat_type,
                            "value": value,
                        },
                    )
                    stat_rows += 1
    return stat_rows


def backfill_player_stats(client, engine, finished_games):
    with engine.begin() as conn:
        already_done = db.game_ids_with_stats(conn)

    remaining = [g for g in finished_games if nhl_game_id(g["id"]) not in already_done]
    print(f"player_game_stats: {len(finished_games) - len(remaining)} finished games already loaded, "
          f"{len(remaining)} remaining")

    loaded = stat_rows = 0
    for game in remaining:
        boxscore = client.get(f"/gamecenter/{game['id']}/boxscore")
        with engine.begin() as conn:
            stat_rows += _upsert_player_rows(conn, game, boxscore)
        loaded += 1
        if loaded % 100 == 0:
            print(f"  ...{loaded}/{len(remaining)} games loaded")

    print(f"player_game_stats: loaded box scores for {loaded} games this run ({stat_rows} stat rows)")


def player_full_name(landing):
    """firstName.default + ' ' + lastName.default from /player/{id}/landing —
    the full name the boxscore only abbreviates ('Zach Benson' vs 'Z. Benson').
    None if the payload carries no names."""
    first = (landing.get("firstName") or {}).get("default")
    last = (landing.get("lastName") or {}).get("default")
    if first and last:
        return f"{first} {last}"
    return None


# A stored name is still abbreviated iff it starts with a single initial + dot
# ("Z. Benson"). Full names ("Zach Benson", "J.T. Miller") don't match, so this
# both selects rows to enrich AND makes the pass resumable/idempotent.
_ABBREV_NAME_RE = r"^[A-Z]\. "


def backfill_player_names(client, engine):
    """Enrich abbreviated boxscore names ('Z. Benson') to full names ('Zach
    Benson') via /player/{id}/landing, so SGO player-prop name-matching resolves
    when props open at preseason. UPDATE-in-place on players.name keyed on
    player_id — no box-score re-backfill. Resumable/idempotent: only players
    whose stored name is still abbreviated are fetched, so a re-run is a no-op.
    """
    with engine.begin() as conn:
        player_ids = [r[0] for r in conn.execute(
            text("SELECT player_id FROM players WHERE sport='nhl' AND name ~ :re"),
            {"re": _ABBREV_NAME_RE},
        ).fetchall()]

    print(f"player names: {len(player_ids)} abbreviated names to enrich")
    updated = 0
    for pid in player_ids:
        raw = pid - NHL_ID_OFFSET
        try:
            landing = client.get(f"/player/{raw}/landing")
        except requests.exceptions.HTTPError:
            continue  # no landing for this player id — keep the abbreviated name
        full = player_full_name(landing)
        if not full:
            continue
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE players SET name = :n WHERE player_id = :pid"),
                {"n": full, "pid": pid},
            )
        updated += 1
        if updated % 200 == 0:
            print(f"  ...{updated}/{len(player_ids)} enriched")
    print(f"player names: enriched {updated} players")


def main():
    parser = argparse.ArgumentParser()
    # Teams, games and final scores share the single score-feed walk
    # (backfill_games upserts teams from each game's team blocks), so
    # --only teams and --only games both run that walk.
    parser.add_argument("--only", choices=["teams", "games", "stats", "names", "all"], default="all")
    parser.add_argument("--season", type=int, default=current_nhl_season(), help="season start year, e.g. 2025")
    args = parser.parse_args()

    client = NHLClient()
    engine = db.get_engine()

    finished_games = None
    if args.only in ("teams", "games", "stats", "all"):
        finished_games = backfill_games(client, engine, args.season)

    if args.only in ("stats", "all"):
        backfill_player_stats(client, engine, finished_games)

    # Full-name enrichment (players.name UPDATE) — resumable, independent of the
    # score/box-score walk, so `--only names` runs it standalone; `all` appends it.
    if args.only in ("names", "all"):
        backfill_player_names(client, engine)


if __name__ == "__main__":
    main()
