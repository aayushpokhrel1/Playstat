"""NFL ingestion via nflverse-data GitHub release assets (nflverse/nflverse-data).

Why not API-Sports like the NBA path: nflverse publishes static, free,
key-less CSVs with prop-market-shaped columns already computed (passing_yards,
receptions, etc.), a full schedule from 1999 through the current season
(including the unplayed slate), and no rate limit — a strict upgrade over
polling a metered API for the same data. API-Sports' american-football API
(SPORTS['nfl'] in config.py) is kept as an unverified fallback only; it has a
100 req/day quota and is NOT used by this module. Source data license:
nflverse-data is CC-BY-4.0 (verified 2026-07-14 via its LICENSE.md) — free to
use with attribution, which this docstring provides.

Sources (no auth, plain HTTPS GET):
  - schedules: https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv
  - player box scores: https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_<season>.csv

Writes to the same tables as ingestion/backfill.py and ingestion/mlb_backfill.py,
with sport='nfl' and every derived numeric ID shifted by SPORTS['nfl']['id_offset']
(see config.py) so they can't collide with NBA/MLB rows.

ID scheme (all deterministic and idempotent across re-runs):
  - teams: nflverse uses stable string abbreviations (e.g. 'KC', 'BUF'), not
    numeric IDs. team_id = NFL_ID_OFFSET + canonical_index(abbreviation),
    1-based, over CANONICAL_TEAMS -- a HARDCODED sorted list of the 32
    current abbreviations, so IDs never depend on which seasons a given run
    happens to pull. An abbreviation outside the list raises immediately:
    historical relocations (OAK, SD, STL) appear in pre-2016 seasons, which
    this path deliberately doesn't support -- we only need 2023+.
  - games: nflverse's own numeric ID candidates (espn, old_game_id) are
    inconsistent -- `espn` is blank for ~270 rows including the entire
    not-yet-played current season, and formats have drifted across nflverse's
    own history. Instead: game_id = NFL_ID_OFFSET + season*100000 + week*1000
    + canonical_index(home_team). Within a single (season, week) each team
    hosts at most one game, so the home team is a unique key -- and unlike a
    rank within the week's game set, it is independent of the set's
    membership: a cancellation (Bills-Bengals, Jan 2023) or a postponement
    that moves a game to another week cannot shift any other game's ID.
    Max index 32 and week <= 22 (playoffs) keep the 100000/1000 slot sizes
    collision-free.
  - players: nflverse player_id is 'AA-BAAAAAA' (e.g. '00-0033873'); the
    trailing 7 digits are already a unique, stable numeric tail per player
    across nflverse's whole history. player_id = NFL_ID_OFFSET + int(tail).
    Like the MLB path, players are derived from player_stats rows (not a
    roster endpoint) so mid-season trades/call-ups can't break the
    player_game_stats FK; a player's team_id reflects the team column on the
    most recently processed stat row (same "latest pull" simplification as
    the NBA/MLB paths).

season_type / preseason: player_stats_<season>.csv only contains season_type
in {REG, POST} (verified across 2023-2025) -- no preseason rows exist, so
there's nothing to filter out on that side. The schedule file's game_type
column adds the finer playoff round labels (WC/DIV/CON/SB) that all map to
POST; regular season is REG. Both sources are already preseason-free, so no
extra filtering is applied here -- this module ingests all REG + playoff
rows from both files.

Stat vocabulary (long-format player_game_stats.stat_type, aligned to prop
markets, mirroring the MLB module's docstring convention):
    passing_yards, passing_tds, interceptions_thrown (nflverse's
    'passing_interceptions' column; 'def_interceptions' exists separately
    and is deliberately not ingested), completions, pass_attempts,
    rushing_yards, rushing_tds, carries, receiving_yards, receiving_tds,
    receptions, targets.

games.status: nflverse marks a played game by having a non-blank home_score;
stored as 'FT' to match the existing API/Budgerr convention, 'NS' otherwise
(nflverse has no in-progress state in this static file).

Player-week rows for players who changed teams mid-week (rare, e.g. a
Tuesday/Wednesday trade before a Thursday game) are handled by joining to the
schedule on (season, week, team) using the stat row's own `team`
column, so each stint's game_id is resolved independently per row.
"""

import argparse
import csv
import io

import requests

from ingestion import db
from ingestion.config import SPORTS

NFL_ID_OFFSET = SPORTS["nfl"]["id_offset"]

SCHEDULES_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
# nflverse retired the old `player_stats` release assets partway through 2025
# (player_stats_2025.csv 404s); the `stats_player` release carries all seasons
# under a renamed schema (recent_team -> team, interceptions ->
# passing_interceptions). All seasons are pulled from the new asset so there is
# exactly one schema to map.
PLAYER_STATS_URL_TMPL = (
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
)

DEFAULT_SEASONS = "2023,2024,2025"

# Only these position groups produce the offensive stats our prop-market
# vocabulary covers (see STAT_COLUMN_MAP).
OFFENSE_POSITION_GROUPS = {"QB", "RB", "WR", "TE"}
REQUEST_TIMEOUT_SECONDS = 60

# Canonical, HARDCODED list of the 32 current NFL abbreviations (sorted).
# Team and game IDs are derived from a team's 1-based index in this list, so
# they never depend on which seasons a particular run pulls. Do not reorder or
# remove entries — that would reassign IDs existing rows are FK'd to; a future
# relocation/rename should APPEND its new abbreviation instead.
CANONICAL_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
    "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
    "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
]
TEAM_INDEX = {abbr: i + 1 for i, abbr in enumerate(CANONICAL_TEAMS)}


def _team_index(abbr):
    try:
        return TEAM_INDEX[abbr]
    except KeyError:
        raise ValueError(
            f"team abbreviation {abbr!r} is not in CANONICAL_TEAMS — likely a "
            "pre-2016 relocation alias (OAK/SD/STL). This path only supports "
            "seasons 2023+; add newer abbreviations by APPENDING to the list."
        ) from None


# Stat columns to pull off player_stats_<season>.csv, mapped to our stat_type
# vocabulary. Values are cast to int/float as-is (nflverse already reports
# these as whole-number counts for these particular columns).
STAT_COLUMN_MAP = {
    "passing_yards": "passing_yards",
    "passing_tds": "passing_tds",
    "interceptions_thrown": "passing_interceptions",
    "completions": "completions",
    "pass_attempts": "attempts",
    "rushing_yards": "rushing_yards",
    "rushing_tds": "rushing_tds",
    "carries": "carries",
    "receiving_yards": "receiving_yards",
    "receiving_tds": "receiving_tds",
    "receptions": "receptions",
    "targets": "targets",
}


def _fetch_csv(url):
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return list(csv.DictReader(io.StringIO(response.text)))


def _team_id_map(schedule_rows, seasons):
    seasons = {str(s) for s in seasons}
    abbrevs = set()
    for row in schedule_rows:
        if row["season"] in seasons:
            abbrevs.add(row["home_team"])
            abbrevs.add(row["away_team"])
    return {abbr: NFL_ID_OFFSET + _team_index(abbr) for abbr in sorted(abbrevs)}


def _game_id_map(schedule_rows, seasons):
    """(season, week, nflverse game_id string) -> our integer game_id.

    The integer only depends on (season, week, home_team) — see the module
    docstring's ID-scheme rationale.
    """
    seasons = {str(s) for s in seasons}
    mapping = {}
    for row in schedule_rows:
        if row["season"] not in seasons:
            continue
        mapping[(row["season"], row["week"], row["game_id"])] = (
            NFL_ID_OFFSET
            + int(row["season"]) * 100_000
            + int(row["week"]) * 1_000
            + _team_index(row["home_team"])
        )
    return mapping


def backfill_teams(schedule_rows, engine, seasons):
    team_ids = _team_id_map(schedule_rows, seasons)
    with engine.begin() as conn:
        for abbr, team_id in team_ids.items():
            db.upsert(
                conn,
                "teams",
                ["team_id"],
                {
                    "team_id": team_id,
                    "sport": "nfl",
                    "name": abbr,
                    "conference": None,
                },
            )
    print(f"teams: upserted {len(team_ids)}")
    return team_ids


def backfill_games(schedule_rows, engine, seasons, team_ids):
    seasons = {str(s) for s in seasons}
    game_ids = _game_id_map(schedule_rows, seasons)

    rows = [r for r in schedule_rows if r["season"] in seasons]
    with engine.begin() as conn:
        for row in rows:
            game_id = game_ids[(row["season"], row["week"], row["game_id"])]
            played = bool(row.get("home_score"))
            db.upsert(
                conn,
                "games",
                ["game_id"],
                {
                    "game_id": game_id,
                    "sport": "nfl",
                    "date": row["gameday"],
                    "home_team_id": team_ids[row["home_team"]],
                    "away_team_id": team_ids[row["away_team"]],
                    "status": "FT" if played else "NS",
                },
            )
    print(f"games: upserted {len(rows)}")
    return game_ids


def _player_id(nflverse_player_id):
    # '00-0033873' -> tail '0033873' -> 33873
    tail = nflverse_player_id.split("-")[-1]
    return NFL_ID_OFFSET + int(tail)


def backfill_player_stats(engine, seasons, team_ids, game_ids, only_weeks=None):
    total_stat_rows = 0
    total_player_rows = 0
    for season in seasons:
        stat_rows = _fetch_csv(PLAYER_STATS_URL_TMPL.format(season=season))
        if only_weeks is not None:
            stat_rows = [r for r in stat_rows if r["week"].isdigit() and int(r["week"]) in only_weeks]

        loaded_players = set()
        stat_count = 0
        with engine.begin() as conn:
            for row in stat_rows:
                if not row.get("player_id") or not row.get("week", "").isdigit():
                    continue
                # The stats_player_week files cover every rostered player;
                # defenders/OL/kickers carry literal 0s in all offensive
                # columns, and no prop market in our stat vocabulary quotes
                # them — skip so player_game_stats doesn't fill with zero rows
                # (~60% of the file) that features.py would later compute
                # rolling averages over.
                if row.get("position_group") not in OFFENSE_POSITION_GROUPS:
                    continue
                team_abbr = row.get("team")
                if team_abbr not in team_ids:
                    continue  # team not in this run's schedule pull (e.g. an old alias)

                week_padded = f"{int(row['week']):02d}"
                opponent = row.get("opponent_team")
                candidates = [
                    (row["season"], row["week"], f"{row['season']}_{week_padded}_{team_abbr}_{opponent}"),
                    (row["season"], row["week"], f"{row['season']}_{week_padded}_{opponent}_{team_abbr}"),
                ]
                game_id = None
                for cand in candidates:
                    if cand in game_ids:
                        game_id = game_ids[cand]
                        break
                if game_id is None:
                    continue  # bye week / no matching schedule row

                player_id = _player_id(row["player_id"])
                db.upsert(
                    conn,
                    "players",
                    ["player_id"],
                    {
                        "player_id": player_id,
                        "sport": "nfl",
                        "name": row.get("player_display_name") or row.get("player_name"),
                        "team_id": team_ids[team_abbr],
                        "position": row.get("position"),
                    },
                )
                loaded_players.add(player_id)

                for stat_type, column in STAT_COLUMN_MAP.items():
                    raw = row.get(column)
                    if raw in (None, ""):
                        continue
                    db.upsert(
                        conn,
                        "player_game_stats",
                        ["player_id", "game_id", "stat_type"],
                        {
                            "player_id": player_id,
                            "game_id": game_id,
                            "stat_type": stat_type,
                            "value": float(raw),
                        },
                    )
                    stat_count += 1

        print(f"season {season}: players upserted {len(loaded_players)}, stat rows upserted {stat_count}")
        total_stat_rows += stat_count
        total_player_rows += len(loaded_players)

    print(f"player_game_stats: {total_stat_rows} stat rows across {total_player_rows} player upserts")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["games", "stats", "all"], default="all")
    parser.add_argument("--seasons", default=DEFAULT_SEASONS, help="comma-separated, e.g. 2023,2024,2025")
    parser.add_argument(
        "--weeks",
        default=None,
        help="optional comma-separated week filter for --only stats, e.g. 1 (for test slices)",
    )
    args = parser.parse_args()

    seasons = [s.strip() for s in args.seasons.split(",") if s.strip()]
    only_weeks = None
    if args.weeks:
        only_weeks = {int(w.strip()) for w in args.weeks.split(",") if w.strip()}

    engine = db.get_engine()
    schedule_rows = _fetch_csv(SCHEDULES_URL)

    team_ids = _team_id_map(schedule_rows, seasons)
    game_ids = _game_id_map(schedule_rows, seasons)

    if args.only in ("games", "all"):
        team_ids = backfill_teams(schedule_rows, engine, seasons)
        game_ids = backfill_games(schedule_rows, engine, seasons, team_ids)

    if args.only in ("stats", "all"):
        backfill_player_stats(engine, seasons, team_ids, game_ids, only_weeks=only_weeks)


if __name__ == "__main__":
    main()
