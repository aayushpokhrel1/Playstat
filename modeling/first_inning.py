"""First-inning total-runs model (MLB): P(home + away first-inning runs < 1.5),
studying both teams — each side's recent first-inning scoring AND what they've
recently allowed in first innings.

2026 base rate: ~71.5% of games stay under 1.5 total first-inning runs, so the
model's job is deviation from that prior, not beating a coin flip. XGBoost with
a Poisson objective predicts the expected total (a count), and the Poisson
distribution converts that mean to P(0 or 1 runs) = e^-lambda * (1 + lambda).

v2 adds each side's starting pitcher's recent form (from the probablePitcher
hydrate + our pitcher game logs) on top of v1's team-level rolling form —
the starter is the single biggest driver of first-inning scoring, and v1
without it couldn't beat the base rate on holdout.

Writes game_predictions (market='first_inning_runs', line 1.5) for upcoming
games; run after ingestion.mlb_backfill --only linescores.
"""

import argparse
from datetime import date, timedelta

import numpy as np
import pandas as pd
import xgboost as xgb
from sqlalchemy import text

from ingestion import db

MARKET = "first_inning_runs"
LINE = 1.5
MODEL_VERSION = "xgb_fi_v2"
ROLLING_GAMES = 15
STARTER_STATS = ["earned_runs", "outs_recorded", "hits_allowed", "walks_allowed"]
# Rate-form beats raw per-appearance totals: ERA-style runs per 27 outs and
# WHIP-style baserunners per inning generalize across start lengths.
STARTER_FEATURES = ["era_form", "whip_form", "outs_form"]
FEATURE_COLS = (
    ["home_scored_fi", "home_allowed_fi", "away_scored_fi", "away_allowed_fi"]
    + [f"home_st_{f}" for f in STARTER_FEATURES]
    + [f"away_st_{f}" for f in STARTER_FEATURES]
)


def prob_under_2(lam):
    """P(X < 2) for X ~ Poisson(lam): the 'under 1.5' probability."""
    lam = max(float(lam), 1e-6)
    return float(np.exp(-lam) * (1 + lam))


def _load_game_frame(conn, sport="mlb"):
    """One row per game with each team's first-inning runs, plus upcoming games
    (NaN actuals) so the same shift+rolling produces as-of features for them.
    """
    played = pd.read_sql(
        text(
            """
            SELECT g.game_id, g.date, g.home_team_id, g.away_team_id,
                   MAX(t.value) FILTER (WHERE t.team_id = g.home_team_id) AS home_fi,
                   MAX(t.value) FILTER (WHERE t.team_id = g.away_team_id) AS away_fi
            FROM games g
            JOIN team_game_stats t ON t.game_id = g.game_id AND t.stat_type = 'runs_inning_1'
            WHERE g.sport = :sport
            GROUP BY g.game_id, g.date, g.home_team_id, g.away_team_id
            """
        ),
        conn,
        params={"sport": sport},
    )
    upcoming = pd.read_sql(
        text(
            """
            SELECT game_id, date, home_team_id, away_team_id,
                   NULL::numeric AS home_fi, NULL::numeric AS away_fi
            FROM games
            WHERE sport = :sport AND status != 'FT' AND date >= :today
            """
        ),
        conn,
        params={"sport": sport, "today": date.today()},
    )
    df = pd.concat([played, upcoming], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("home_fi", "away_fi"):
        df[col] = df[col].astype("float64")
    return df.sort_values("date")


def _add_team_form(df):
    """Per game: each team's rolling first-inning runs scored and allowed over
    their last ROLLING_GAMES games, strictly before this game (shift(1)).
    """
    home = df[["game_id", "date", "home_team_id", "home_fi", "away_fi"]].rename(
        columns={"home_team_id": "team_id", "home_fi": "scored", "away_fi": "allowed"}
    )
    away = df[["game_id", "date", "away_team_id", "away_fi", "home_fi"]].rename(
        columns={"away_team_id": "team_id", "away_fi": "scored", "home_fi": "allowed"}
    )
    team_games = pd.concat([home, away]).sort_values(["team_id", "date"])
    for col in ("scored", "allowed"):
        team_games[f"{col}_fi_avg"] = team_games.groupby("team_id")[col].transform(
            lambda s: s.shift(1).rolling(ROLLING_GAMES, min_periods=5).mean()
        )

    form = team_games[["game_id", "team_id", "scored_fi_avg", "allowed_fi_avg"]]
    df = df.merge(
        form.rename(columns={"team_id": "home_team_id", "scored_fi_avg": "home_scored_fi",
                             "allowed_fi_avg": "home_allowed_fi"}),
        on=["game_id", "home_team_id"], how="left",
    ).merge(
        form.rename(columns={"team_id": "away_team_id", "scored_fi_avg": "away_scored_fi",
                             "allowed_fi_avg": "away_allowed_fi"}),
        on=["game_id", "away_team_id"], how="left",
    )
    return df


def _add_starter_form(conn, df):
    """v2: each side's starting pitcher's recent form (last 3 appearances,
    strictly before the game) — the biggest driver of first-inning scoring
    that team-level form can't see. Starters come from the probablePitcher
    hydrate (team_game_stats stat 'starter_player_id', ingested for played
    AND scheduled games); their form comes straight from player_game_stats.
    A scratched/unknown starter just leaves NaN features, which XGBoost
    handles as missing.
    """
    starters = pd.read_sql(
        text("SELECT game_id, team_id, value AS starter_id FROM team_game_stats "
             "WHERE stat_type = 'starter_player_id'"),
        conn,
    )
    logs = pd.read_sql(
        text(
            """
            SELECT pgs.player_id, pgs.game_id, g.date, pgs.stat_type, pgs.value
            FROM player_game_stats pgs
            JOIN games g ON g.game_id = pgs.game_id
            WHERE g.sport = 'mlb' AND pgs.stat_type = ANY(:stats)
            """
        ),
        conn,
        params={"stats": STARTER_STATS},
    )
    logs = logs.pivot_table(
        index=["player_id", "game_id", "date"], columns="stat_type", values="value", aggfunc="first"
    ).reset_index().sort_values(["player_id", "date"])

    def add_rates(frame, rolled):
        outs = rolled["outs_recorded"].clip(lower=1)
        frame["era_form"] = rolled["earned_runs"] / outs * 27
        frame["whip_form"] = (rolled["hits_allowed"] + rolled["walks_allowed"]) / (outs / 3)
        frame["outs_form"] = rolled["outs_recorded"]
        return frame

    rolled = logs.groupby("player_id")[STARTER_STATS].transform(
        lambda x: x.shift(1).rolling(10, min_periods=3).mean()
    )
    logs = add_rates(logs, rolled)

    # As-of form per (pitcher, game) for played games; latest form for upcoming.
    at_game = logs[["player_id", "game_id"] + STARTER_FEATURES]
    current = logs.groupby("player_id").tail(10).groupby("player_id")[STARTER_STATS].mean()
    current = add_rates(current, current).reset_index()[["player_id"] + STARTER_FEATURES]

    for side in ("home", "away"):
        side_starters = starters.rename(
            columns={"team_id": f"{side}_team_id", "starter_id": f"{side}_starter_id"}
        )
        df = df.merge(side_starters, on=["game_id", f"{side}_team_id"], how="left")
        # Played games: form as of that game (leakage-safe via shift(1)).
        df = df.merge(
            at_game.rename(columns={"player_id": f"{side}_starter_id",
                                    **{f: f"{side}_st_{f}" for f in STARTER_FEATURES}}),
            on=[f"{side}_starter_id", "game_id"], how="left",
        )
        # Upcoming games: the starter's current form (their last 3 appearances).
        cur = current.rename(columns={"player_id": f"{side}_starter_id",
                                      **{f: f"{side}_cur_{f}" for f in STARTER_FEATURES}})
        df = df.merge(cur, on=f"{side}_starter_id", how="left")
        upcoming_mask = df["home_fi"].isna()
        for f in STARTER_FEATURES:
            df.loc[upcoming_mask, f"{side}_st_{f}"] = df.loc[upcoming_mask, f"{side}_cur_{f}"]
        df = df.drop(columns=[f"{side}_cur_{f}" for f in STARTER_FEATURES])
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=2, help="predict games this many days out")
    args = parser.parse_args()

    engine = db.get_engine()
    with engine.begin() as conn:
        df = _load_game_frame(conn)
        df = _add_starter_form(conn, df)
    df = _add_team_form(df)
    df["total_fi"] = df["home_fi"] + df["away_fi"]

    # Starter features may be NaN (unannounced/scratched starter, or fewer
    # than 2 prior appearances) — XGBoost handles missing; only team form is required.
    team_form_cols = [c for c in FEATURE_COLS if not c.startswith(("home_st_", "away_st_"))]
    played = df.dropna(subset=["total_fi"] + team_form_cols).sort_values("date")
    if len(played) < 200:
        print(f"only {len(played)} playable rows — need more linescore history.")
        return

    played["under"] = (played["total_fi"] < LINE).astype(int)

    # Date-based holdout to sanity-check calibration before trusting the probs.
    # A Poisson conversion from a mean-runs regressor ran ~7 points low here
    # (first-inning runs are overdispersed vs Poisson: more scoreless innings
    # AND more crooked numbers than the mean implies), so the under-probability
    # is modeled directly as a small binary classifier instead; the Poisson
    # regressor is kept only for predicted_mean.
    cutoff = played.iloc[int(len(played) * 0.8)]["date"]
    train_df, test_df = played[played["date"] < cutoff], played[played["date"] >= cutoff]

    def fit_models(frame):
        clf = xgb.XGBClassifier(
            objective="binary:logistic", n_estimators=40, max_depth=2, learning_rate=0.1
        )
        clf.fit(frame[FEATURE_COLS], frame["under"])
        reg = xgb.XGBRegressor(objective="count:poisson", n_estimators=40, max_depth=2)
        reg.fit(frame[FEATURE_COLS], frame["total_fi"])
        return clf, reg

    clf, _ = fit_models(train_df)
    p_under = clf.predict_proba(test_df[FEATURE_COLS])[:, 1]
    outcome_under = test_df["under"].to_numpy().astype(float)
    brier = float(np.mean((p_under - outcome_under) ** 2))
    base_brier = float(np.mean((train_df["under"].mean() - outcome_under) ** 2))
    print(f"holdout n={len(test_df)}: empirical under rate {outcome_under.mean():.1%}, "
          f"mean predicted P(under) {p_under.mean():.1%}, Brier {brier:.4f} "
          f"(always-predict-base-rate Brier: {base_brier:.4f})")

    # Refit on everything, predict upcoming games.
    clf, reg = fit_models(played)
    horizon = pd.Timestamp(date.today() + timedelta(days=args.days))
    upcoming = df[df["total_fi"].isna() & (df["date"] <= horizon)].dropna(subset=team_form_cols)
    if upcoming.empty:
        print("no upcoming games with enough team history in the horizon.")
        return

    probs = clf.predict_proba(upcoming[FEATURE_COLS])[:, 1]
    lams = reg.predict(upcoming[FEATURE_COLS])
    rows = 0
    with engine.begin() as conn:
        for (_, rec), pu, lam in zip(upcoming.iterrows(), probs, lams):
            pu = float(pu)
            db.upsert(
                conn,
                "game_predictions",
                ["game_id", "market", "model_version"],
                {
                    "game_id": int(rec["game_id"]),
                    "market": MARKET,
                    "predicted_mean": float(lam),
                    "prob_under": pu,
                    "prob_over": 1 - pu,
                    "line_value": LINE,
                    "model_version": MODEL_VERSION,
                },
            )
            rows += 1
    print(f"game_predictions: upserted {rows} upcoming games (market={MARKET}, line {LINE})")


if __name__ == "__main__":
    main()
