import argparse
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text

from ingestion import db

# Per-sport feature definitions. windows: feature name -> (stat_type, n games).
# scoring_stat drives the opponent "defensive rating" proxy (points allowed
# per game for the NBA, runs allowed per game for MLB). Pitcher windows use 3
# appearances (starters pitch every ~5 days, so 3 starts ≈ two weeks of form).
SPORT_CONFIG = {
    "nba": {
        "windows": {
            "pts_avg_5": ("points", 5), "pts_avg_10": ("points", 10),
            "reb_avg_5": ("rebounds", 5), "ast_avg_5": ("assists", 5),
        },
        "scoring_stat": "points",
    },
    "mlb": {
        "windows": {
            "hits_avg_5": ("hits", 5), "hits_avg_10": ("hits", 10),
            "tb_avg_5": ("total_bases", 5), "tb_avg_10": ("total_bases", 10),
            "hr_avg_10": ("home_runs", 10),
            "runs_avg_5": ("runs", 5),
            "rbis_avg_5": ("rbis", 5),
            "bso_avg_5": ("batter_strikeouts", 5),
            "walks_avg_5": ("walks", 5),
            "sb_avg_10": ("stolen_bases", 10),
            "ab_avg_5": ("at_bats", 5),
            "pso_avg_3": ("pitcher_strikeouts", 3),
            "er_avg_3": ("earned_runs", 3),
            "outs_avg_3": ("outs_recorded", 3),
            "ha_avg_3": ("hits_allowed", 3),
            "wa_avg_3": ("walks_allowed", 3),
        },
        "scoring_stat": "runs",
    },
}


def _load_player_games(conn, sport, windows):
    """One row per (player, game) played, joined with game/team context —
    pivoted back to wide (one column per stat_type) from the long-format
    player_game_stats table, since rolling windows want columnar stats.

    Uses players.team_id as the player's team for every game (Phase 1's known
    simplification for traded players — team_id reflects the latest roster pull,
    not the point-in-time team).
    """
    df = pd.read_sql(
        text(
            """
            SELECT pgs.player_id, pgs.game_id, pgs.stat_type, pgs.value,
                   g.date, g.home_team_id, g.away_team_id, p.team_id
            FROM player_game_stats pgs
            JOIN games g ON g.game_id = pgs.game_id
            JOIN players p ON p.player_id = pgs.player_id
            WHERE g.sport = :sport
            """
        ),
        conn,
        params={"sport": sport},
    )
    wide = df.pivot_table(
        index=["player_id", "game_id"], columns="stat_type", values="value", aggfunc="first"
    ).reset_index()
    meta = df[
        ["player_id", "game_id", "date", "home_team_id", "away_team_id", "team_id"]
    ].drop_duplicates()
    wide = meta.merge(wide, on=["player_id", "game_id"], how="left")

    stat_cols = {stat for stat, _ in windows.values()}
    for col in stat_cols:
        if col not in wide.columns:
            wide[col] = np.nan
        wide[col] = wide[col].astype("float64")

    wide["date"] = pd.to_datetime(wide["date"])
    return wide.sort_values(["player_id", "date"])


def _load_upcoming_player_games(conn, sport, upcoming_days, stat_cols):
    """Synthesized (player, upcoming game) rows with NaN stats, so the same
    rolling/shift(1) machinery produces as-of features for games that haven't
    been played — which is what live predictions (and therefore live edges)
    need. Candidate players are everyone currently rostered (players.team_id)
    on either team of each upcoming game.
    """
    today = date.today()
    df = pd.read_sql(
        text(
            """
            SELECT p.player_id, g.game_id, g.date, g.home_team_id, g.away_team_id, p.team_id
            FROM games g
            JOIN players p ON p.team_id IN (g.home_team_id, g.away_team_id)
            WHERE g.sport = :sport AND g.status != 'FT'
              AND g.date >= :today AND g.date <= :horizon
            """
        ),
        conn,
        params={"sport": sport, "today": today, "horizon": today + timedelta(days=upcoming_days)},
    )
    for col in stat_cols:
        df[col] = np.nan
    df["date"] = pd.to_datetime(df["date"])
    return df


def _add_rolling_stat_averages(df, windows):
    for feature_name, (source_col, window) in windows.items():
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


def _add_opponent_def_rating(df, games_df, scoring_stat):
    """Proxy for opponent defensive rating: opponent's average scoring-stat
    allowed per game so far this season (points for nba, runs for mlb), using
    only games strictly before this one. Unplayed games contribute nothing —
    their team totals are NaN, which shift+expanding skips.
    """
    team_game_scoring = df.groupby(["team_id", "game_id"], as_index=False)[scoring_stat].sum(min_count=1)

    merged = games_df.merge(
        team_game_scoring.rename(columns={"team_id": "home_team_id", scoring_stat: "home_scored"}),
        on=["game_id", "home_team_id"],
        how="left",
    ).merge(
        team_game_scoring.rename(columns={"team_id": "away_team_id", scoring_stat: "away_scored"}),
        on=["game_id", "away_team_id"],
        how="left",
    )

    home_rows = merged[["game_id", "date", "home_team_id", "away_scored"]].rename(
        columns={"home_team_id": "team_id", "away_scored": "allowed"}
    )
    away_rows = merged[["game_id", "date", "away_team_id", "home_scored"]].rename(
        columns={"away_team_id": "team_id", "home_scored": "allowed"}
    )
    team_defense = pd.concat([home_rows, away_rows]).sort_values(["team_id", "date"])
    team_defense["opp_def_rating"] = team_defense.groupby("team_id")["allowed"].transform(
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


def _incremental_cutoff(today, lookback_days):
    """Pure: the earliest as_of_date an incremental run should upsert.
    Rows older than this are immutable (already stored, provably identical
    to a recompute) and are safe to skip.
    """
    return today - timedelta(days=lookback_days)


# Features whose value has an UNBOUNDED retroactive dependency, so they cannot be
# treated as immutable outside the lookback window. opp_def_rating is a per-team
# shift(1).expanding().mean() over the team's ENTIRE history (not a fixed window,
# not reset per season), so a late-arriving stat for any prior game shifts every
# later value however old. Verified 2026-07-25: the 19 window/schedule features
# re-derive 100% identical across runs; opp_def_rating only ~42%. These are always
# re-upserted (still cheap — one feature, ~1 row per player-game) so the
# incremental upsert stays EXACT for every feature. opp_def_rating feeds only the
# model pipeline, never the market-ranked builder.
_ALWAYS_UPSERT_FEATURES = frozenset({"opp_def_rating"})


def _filter_values(values, cutoff, always_upsert=frozenset()):
    """Pure: keep rows with as_of_date >= cutoff, PLUS any row whose feature is
    in `always_upsert` (regardless of date). Never mutates a kept row — just
    drops immutable rows outside the incremental window.
    """
    return [row for row in values
            if row["as_of_date"] >= cutoff or row["feature"] in always_upsert]


def compute_features(engine, sport="nba", upcoming_days=0, lookback_days=7, full=False):
    """Computes rolling features for every played game, and — when
    upcoming_days > 0 — for scheduled games up to that many days out, so
    modeling/predict_upcoming.py can predict games before they're played.

    The compute above is always full (cheap, ~77s, guarantees values
    identical to a full recompute). The upsert below is incremental by
    default: rows with as_of_date older than `lookback_days` are immutable
    (a past game's rolling features never change once played) and already
    stored, so skipping them avoids re-writing ~2.3M unchanged rows nightly.
    (opp_def_rating is exempt and always re-upserted — its value is not
    window-bounded; see _ALWAYS_UPSERT_FEATURES.) Pass full=True to force
    upserting every computed row (e.g. a one-time rebuild).
    """
    config = SPORT_CONFIG[sport]
    windows = config["windows"]
    stat_cols = {stat for stat, _ in windows.values()}

    with engine.begin() as conn:
        stats = _load_player_games(conn, sport, windows)
        if upcoming_days > 0:
            upcoming = _load_upcoming_player_games(conn, sport, upcoming_days, stat_cols)
            stats = pd.concat([stats, upcoming], ignore_index=True).sort_values(["player_id", "date"])
        games_df = pd.read_sql(
            text("SELECT game_id, date, home_team_id, away_team_id FROM games WHERE sport = :sport"),
            conn,
            params={"sport": sport},
        )
    games_df["date"] = pd.to_datetime(games_df["date"])

    stats = _add_rolling_stat_averages(stats, windows)
    stats = _add_rest_and_schedule_features(stats, games_df)
    stats = _add_opponent_def_rating(stats, games_df, config["scoring_stat"])
    stats["is_home"] = stats["team_id"] == stats["home_team_id"]

    feature_cols = list(windows) + ["opp_def_rating", "rest_days", "is_home", "is_back_to_back"]

    # rolling_player_features is long format: one row per non-null feature,
    # booleans stored as 0/1 numerics (the models always consumed them as floats).
    # Values are batched into one executemany-style INSERT — row-at-a-time
    # upserts took ~35 minutes for a single MLB season, which doesn't survive
    # multi-season history on a daily schedule.
    values = []
    rows = 0
    for record in stats.to_dict("records"):
        for col in feature_cols:
            v = record.get(col)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            values.append(
                {
                    "player_id": record["player_id"],
                    "as_of_date": record["date"].date(),
                    "feature": col,
                    "value": float(v),
                }
            )
        rows += 1

    total_computed = len(values)
    if not full:
        cutoff = _incremental_cutoff(date.today(), lookback_days)
        values = _filter_values(values, cutoff, _ALWAYS_UPSERT_FEATURES)

    if values:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO rolling_player_features (player_id, as_of_date, feature, value) "
                    "VALUES (:player_id, :as_of_date, :feature, :value) "
                    "ON CONFLICT (player_id, as_of_date, feature) DO UPDATE SET value = EXCLUDED.value"
                ),
                values,
            )

    mode_desc = "full" if full else f"incremental, lookback {lookback_days}d"
    print(f"({sport}) rolling_player_features: upserted {len(values):,} of {total_computed:,} computed "
          f"rows for {rows} player-games ({mode_desc})"
          + (f" (incl. upcoming through +{upcoming_days}d)" if upcoming_days else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=list(SPORT_CONFIG), default="nba")
    parser.add_argument("--upcoming-days", type=int, default=0)
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    compute_features(db.get_engine(), args.sport, args.upcoming_days, args.lookback_days, args.full)
