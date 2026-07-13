"""First-inning total-runs model (MLB): P(home + away first-inning runs < 1.5),
studying both teams — each side's recent first-inning scoring AND what they've
recently allowed in first innings.

2026 base rate: ~71.5% of games stay under 1.5 total first-inning runs, so the
model's job is deviation from that prior, not beating a coin flip. XGBoost with
a Poisson objective predicts the expected total (a count), and the Poisson
distribution converts that mean to P(0 or 1 runs) = e^-lambda * (1 + lambda).

Known limitation: no probable-starting-pitcher feature — the single biggest
driver of first-inning scoring. Team-level rolling form is the honest v1;
StatsAPI exposes probable pitchers if this market earns a v2.

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
MODEL_VERSION = "xgb_poisson_fi_v1"
ROLLING_GAMES = 15
FEATURE_COLS = ["home_scored_fi", "home_allowed_fi", "away_scored_fi", "away_allowed_fi"]


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=2, help="predict games this many days out")
    args = parser.parse_args()

    engine = db.get_engine()
    with engine.begin() as conn:
        df = _load_game_frame(conn)
    df = _add_team_form(df)
    df["total_fi"] = df["home_fi"] + df["away_fi"]

    played = df.dropna(subset=["total_fi"] + FEATURE_COLS).sort_values("date")
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
    upcoming = df[df["total_fi"].isna() & (df["date"] <= horizon)].dropna(subset=FEATURE_COLS)
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
