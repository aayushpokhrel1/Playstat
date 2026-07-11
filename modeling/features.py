import numpy as np
import pandas as pd
from sqlalchemy import text

from ingestion import db

ROLLING_WINDOWS = {"pts_avg_5": ("points", 5), "pts_avg_10": ("points", 10),
                    "reb_avg_5": ("rebounds", 5), "ast_avg_5": ("assists", 5)}


def _load_player_games(conn):
    """One row per (player, game) played, joined with game/team context.

    Uses players.team_id as the player's team for every game (Phase 1's known
    simplification for traded players — team_id reflects the latest roster pull,
    not the point-in-time team).
    """
    df = pd.read_sql(
        text(
            """
            SELECT pgs.player_id, pgs.game_id, pgs.points, pgs.rebounds, pgs.assists,
                   g.date, g.home_team_id, g.away_team_id, p.team_id
            FROM player_game_stats pgs
            JOIN games g ON g.game_id = pgs.game_id
            JOIN players p ON p.player_id = pgs.player_id
            """
        ),
        conn,
    )
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["player_id", "date"])


def _add_rolling_stat_averages(df):
    for feature_name, (source_col, window) in ROLLING_WINDOWS.items():
        df[feature_name] = df.groupby("player_id")[source_col].transform(
            lambda s, w=window: s.shift(1).rolling(w, min_periods=w).mean()
        )
    return df


def _add_rest_and_schedule_features(df, games_df):
    home_sched = games_df[["game_id", "date", "home_team_id"]].rename(columns={"home_team_id": "team_id"})
    away_sched = games_df[["game_id", "date", "away_team_id"]].rename(columns={"away_team_id": "team_id"})
    team_sched = pd.concat([home_sched, away_sched]).sort_values(["team_id", "date"])

    team_sched["prev_date"] = team_sched.groupby("team_id")["date"].shift(1)
    days_since_prev = (team_sched["date"] - team_sched["prev_date"]).dt.days
    team_sched["rest_days"] = days_since_prev - 1
    team_sched["is_back_to_back"] = np.where(days_since_prev.isna(), None, days_since_prev == 1)

    return df.merge(
        team_sched[["game_id", "team_id", "rest_days", "is_back_to_back"]],
        on=["game_id", "team_id"],
        how="left",
    )


def _add_opponent_def_rating(df, games_df):
    """Proxy for opponent defensive rating: opponent's average points allowed
    per game so far this season, using only games strictly before this one.
    """
    team_game_points = df.groupby(["team_id", "game_id"], as_index=False)["points"].sum()

    merged = games_df.merge(
        team_game_points.rename(columns={"team_id": "home_team_id", "points": "home_points"}),
        on=["game_id", "home_team_id"],
        how="left",
    ).merge(
        team_game_points.rename(columns={"team_id": "away_team_id", "points": "away_points"}),
        on=["game_id", "away_team_id"],
        how="left",
    )

    home_rows = merged[["game_id", "date", "home_team_id", "away_points"]].rename(
        columns={"home_team_id": "team_id", "away_points": "points_allowed"}
    )
    away_rows = merged[["game_id", "date", "away_team_id", "home_points"]].rename(
        columns={"away_team_id": "team_id", "home_points": "points_allowed"}
    )
    team_defense = pd.concat([home_rows, away_rows]).sort_values(["team_id", "date"])
    team_defense["opp_def_rating"] = team_defense.groupby("team_id")["points_allowed"].transform(
        lambda s: s.shift(1).expanding().mean()
    )

    df["opponent_team_id"] = np.where(
        df["team_id"] == df["home_team_id"], df["away_team_id"], df["home_team_id"]
    )
    return df.merge(
        team_defense[["game_id", "team_id", "opp_def_rating"]].rename(
            columns={"team_id": "opponent_team_id"}
        ),
        on=["game_id", "opponent_team_id"],
        how="left",
    )


def compute_features(engine):
    with engine.begin() as conn:
        stats = _load_player_games(conn)
        games_df = pd.read_sql(
            text("SELECT game_id, date, home_team_id, away_team_id FROM games"), conn
        )
    games_df["date"] = pd.to_datetime(games_df["date"])

    stats = _add_rolling_stat_averages(stats)
    stats = _add_rest_and_schedule_features(stats, games_df)
    stats = _add_opponent_def_rating(stats, games_df)
    stats["is_home"] = stats["team_id"] == stats["home_team_id"]

    feature_cols = [
        "pts_avg_5", "pts_avg_10", "reb_avg_5", "ast_avg_5",
        "opp_def_rating", "rest_days", "is_home", "is_back_to_back",
    ]

    rows = 0
    with engine.begin() as conn:
        for record in stats.to_dict("records"):
            values = {
                "player_id": record["player_id"],
                "as_of_date": record["date"].date(),
            }
            for col in feature_cols:
                v = record.get(col)
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    values[col] = None
                elif col in ("is_home", "is_back_to_back"):
                    values[col] = bool(v)
                elif col == "rest_days":
                    values[col] = int(v)
                else:
                    values[col] = float(v)

            db.upsert(conn, "rolling_player_features", ["player_id", "as_of_date"], values)
            rows += 1

    print(f"rolling_player_features: upserted {rows} rows")


if __name__ == "__main__":
    compute_features(db.get_engine())
