"""F5 (first-5-innings) total-runs model (MLB): P(home + away runs in innings
1-5 < the game's book line), studying both teams — each side's recent F5
scoring AND what they've recently allowed over the first five innings.

Innings 1-5 are "the starters' game": mostly the two starting pitchers, so the
starter-form features are directly on-point (same source as first_inning.py).
Target mean is ~4.5 (far less zero-inflated than NRFI), so the classifier has
more signal. As with NRFI, XGBoost with a Poisson objective predicts the
expected total (a count); a classifier trained at the fixed representative LINE
is kept only as a holdout calibration check.

Variable-line note (Phase 0 probe): F5 book lines vary per game (the probe saw
3.5, not a fixed value like NRFI's 0.5). A single fixed-threshold classifier
can't price every game's actual line, so the *traded* prob_under/prob_over for
each upcoming game is derived from the Poisson mean at THAT game's own
game_lines line_value (market='f5_runs') via prob_under_line_poisson. If a game
has no F5 line yet, we fall back to the constant LINE. The fixed-LINE
classifier's holdout Brier vs the always-base-rate baseline is still printed,
exactly as first_inning.py does, as a calibration sanity check.

Writes game_predictions (market='f5_runs') for upcoming games; run after
ingestion.mlb_backfill --only linescores (which now populates f5_runs).
"""

import argparse
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import xgboost as xgb
from sqlalchemy import text

from ingestion import db
from modeling.distributions import prob_over_discrete

MARKET = "f5_runs"
LINE = 4.5          # representative training threshold only — see the variable-line note above
MODEL_VERSION = "xgb_f5_v1"
ROLLING_GAMES = 15
STARTER_STATS = ["earned_runs", "outs_recorded", "hits_allowed", "walks_allowed"]
# Rate-form beats raw per-appearance totals: ERA-style runs per 27 outs and
# WHIP-style baserunners per inning generalize across start lengths.
STARTER_FEATURES = ["era_form", "whip_form", "outs_form"]
FEATURE_COLS = (
    ["home_scored_f5", "home_allowed_f5", "away_scored_f5", "away_allowed_f5"]
    + [f"home_st_{f}" for f in STARTER_FEATURES]
    + [f"away_st_{f}" for f in STARTER_FEATURES]
)


def prob_under_line_nb(mean, dispersion_r, line):
    """P(total < line) for an NB2 count law reconstructed from (mean, dispersion_r).

    F5 totals are overdispersed (holdout Var/Mean ~2.16): a plain Poisson
    understates the under-tail by ~5 points and would manufacture fake edges.
    We give the mean a matching NB2 variance via std = sqrt(mean + mean^2/r) and
    reuse the shared discrete reconstruction in modeling/distributions.py (the
    same NB2/Poisson math the player-prop edges use), which also handles the
    half-integer F5 line exactly. As r -> inf the NB2 collapses to Poisson.
    """
    mean = max(float(mean), 1e-6)
    std = math.sqrt(mean + mean * mean / dispersion_r)
    return 1.0 - prob_over_discrete(mean, std, line)


def _load_game_frame(conn, sport="mlb"):
    """One row per game with each team's first-five-innings runs, plus upcoming
    games (NaN actuals) so the same shift+rolling produces as-of features for them.
    """
    played = pd.read_sql(
        text(
            """
            SELECT g.game_id, g.date, g.home_team_id, g.away_team_id,
                   MAX(t.value) FILTER (WHERE t.team_id = g.home_team_id) AS home_f5,
                   MAX(t.value) FILTER (WHERE t.team_id = g.away_team_id) AS away_f5
            FROM games g
            JOIN team_game_stats t ON t.game_id = g.game_id AND t.stat_type = 'runs_f5'
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
                   NULL::numeric AS home_f5, NULL::numeric AS away_f5
            FROM games
            WHERE sport = :sport AND status != 'FT' AND date >= :today
            """
        ),
        conn,
        params={"sport": sport, "today": date.today()},
    )
    df = pd.concat([played, upcoming], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("home_f5", "away_f5"):
        df[col] = df[col].astype("float64")
    return df.sort_values("date")


def _add_team_form(df):
    """Per game: each team's rolling F5 runs scored and allowed over their last
    ROLLING_GAMES games, strictly before this game (shift(1)).
    """
    home = df[["game_id", "date", "home_team_id", "home_f5", "away_f5"]].rename(
        columns={"home_team_id": "team_id", "home_f5": "scored", "away_f5": "allowed"}
    )
    away = df[["game_id", "date", "away_team_id", "away_f5", "home_f5"]].rename(
        columns={"away_team_id": "team_id", "away_f5": "scored", "home_f5": "allowed"}
    )
    team_games = pd.concat([home, away]).sort_values(["team_id", "date"])
    for col in ("scored", "allowed"):
        team_games[f"{col}_f5_avg"] = team_games.groupby("team_id")[col].transform(
            lambda s: s.shift(1).rolling(ROLLING_GAMES, min_periods=5).mean()
        )

    form = team_games[["game_id", "team_id", "scored_f5_avg", "allowed_f5_avg"]]
    df = df.merge(
        form.rename(columns={"team_id": "home_team_id", "scored_f5_avg": "home_scored_f5",
                             "allowed_f5_avg": "home_allowed_f5"}),
        on=["game_id", "home_team_id"], how="left",
    ).merge(
        form.rename(columns={"team_id": "away_team_id", "scored_f5_avg": "away_scored_f5",
                             "allowed_f5_avg": "away_allowed_f5"}),
        on=["game_id", "away_team_id"], how="left",
    )
    return df


def _add_starter_form(conn, df):
    """Each side's starting pitcher's recent form (strictly before the game) —
    the biggest driver of first-five-innings scoring that team-level form can't
    see. Starters come from the probablePitcher hydrate (team_game_stats stat
    'starter_player_id', ingested for played AND scheduled games); their form
    comes straight from player_game_stats. A scratched/unknown starter just
    leaves NaN features, which XGBoost handles as missing.
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
        # Upcoming games: the starter's current form (their last 10 appearances).
        cur = current.rename(columns={"player_id": f"{side}_starter_id",
                                      **{f: f"{side}_cur_{f}" for f in STARTER_FEATURES}})
        df = df.merge(cur, on=f"{side}_starter_id", how="left")
        upcoming_mask = df["home_f5"].isna()
        for f in STARTER_FEATURES:
            df.loc[upcoming_mask, f"{side}_st_{f}"] = df.loc[upcoming_mask, f"{side}_cur_{f}"]
        df = df.drop(columns=[f"{side}_cur_{f}" for f in STARTER_FEATURES])
    return df


def _load_f5_lines(conn):
    """Latest F5 book line per game (market='f5_runs'), keyed by game_id.

    F5 lines vary per game, so the traded under/over is derived at each upcoming
    game's own line. Returns {game_id: line_value}; games absent here fall back
    to the constant LINE.
    """
    lines = pd.read_sql(
        text(
            """
            SELECT DISTINCT ON (game_id) game_id, line_value
            FROM game_lines
            WHERE market = :market AND line_value IS NOT NULL
            ORDER BY game_id, pulled_at DESC
            """
        ),
        conn,
        params={"market": MARKET},
    )
    return {int(r.game_id): float(r.line_value) for r in lines.itertuples()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=2, help="predict games this many days out")
    args = parser.parse_args()

    engine = db.get_engine()
    with engine.begin() as conn:
        df = _load_game_frame(conn)
        df = _add_starter_form(conn, df)
        f5_lines = _load_f5_lines(conn)
    df = _add_team_form(df)
    df["total_f5"] = df["home_f5"] + df["away_f5"]

    # Starter features may be NaN (unannounced/scratched starter, or fewer
    # than 3 prior appearances) — XGBoost handles missing; only team form is required.
    team_form_cols = [c for c in FEATURE_COLS if not c.startswith(("home_st_", "away_st_"))]
    played = df.dropna(subset=["total_f5"] + team_form_cols).sort_values("date")
    if len(played) < 200:
        print(f"only {len(played)} playable rows — need more linescore history.")
        return

    played["under"] = (played["total_f5"] < LINE).astype(int)

    # Date-based holdout to sanity-check calibration. F5 aggregates 10 team-innings
    # so it should be closer to Poisson than first-inning runs (which were
    # overdispersed); still, the fixed-LINE under-probability is modeled directly
    # by a small binary classifier here as the calibration check, mirroring
    # first_inning.py. The Poisson regressor supplies predicted_mean and the
    # per-game variable-line under probability.
    cutoff = played.iloc[int(len(played) * 0.8)]["date"]
    train_df, test_df = played[played["date"] < cutoff], played[played["date"] >= cutoff]

    def fit_models(frame):
        clf = xgb.XGBClassifier(
            objective="binary:logistic", n_estimators=40, max_depth=2, learning_rate=0.1
        )
        clf.fit(frame[FEATURE_COLS], frame["under"])
        reg = xgb.XGBRegressor(objective="count:poisson", n_estimators=40, max_depth=2)
        reg.fit(frame[FEATURE_COLS], frame["total_f5"])
        return clf, reg

    clf, reg_tr = fit_models(train_df)
    p_under = clf.predict_proba(test_df[FEATURE_COLS])[:, 1]
    outcome_under = test_df["under"].to_numpy().astype(float)
    brier = float(np.mean((p_under - outcome_under) ** 2))
    base_brier = float(np.mean((train_df["under"].mean() - outcome_under) ** 2))
    print(f"holdout n={len(test_df)}: empirical under rate {outcome_under.mean():.1%}, "
          f"mean predicted P(under) {p_under.mean():.1%}, Brier {brier:.4f} "
          f"(always-predict-base-rate Brier: {base_brier:.4f})")

    # Global NB2 dispersion for the traded prob_under. F5 totals are overdispersed
    # (a plain Poisson understated the under-tail by ~5 points on holdout), so fit
    # r by method of moments on the TRAIN residuals (out-of-mean-model, leakage-safe
    # via the train-only reg above): E[((Y-mu)^2 - mu)/mu^2] = 1/r. Larger r = closer
    # to Poisson. Carried into the prediction loop as std = sqrt(mu + mu^2/r).
    mu_tr = np.clip(reg_tr.predict(train_df[FEATURE_COLS]), 0.1, None)
    y_tr = train_df["total_f5"].to_numpy()
    inv_r = float(np.mean(((y_tr - mu_tr) ** 2 - mu_tr) / mu_tr ** 2))
    dispersion_r = 1.0 / max(inv_r, 1e-6)
    print(f"F5 NB2 dispersion: r={dispersion_r:.2f} "
          f"(train Var/Mean={y_tr.var() / y_tr.mean():.2f})")

    # Refit on everything, predict upcoming games.
    clf, reg = fit_models(played)
    horizon = pd.Timestamp(date.today() + timedelta(days=args.days))
    upcoming = df[df["total_f5"].isna() & (df["date"] <= horizon)].dropna(subset=team_form_cols)
    if upcoming.empty:
        print("no upcoming games with enough team history in the horizon.")
        return

    lams = reg.predict(upcoming[FEATURE_COLS])
    rows = 0
    with engine.begin() as conn:
        for (_, rec), lam in zip(upcoming.iterrows(), lams):
            game_id = int(rec["game_id"])
            # Traded prob is derived at THIS game's own F5 book line; fall back to
            # the constant LINE when no game_lines row exists yet.
            line = f5_lines.get(game_id, LINE)
            pu = prob_under_line_nb(float(lam), dispersion_r, line)
            db.upsert(
                conn,
                "game_predictions",
                ["game_id", "market", "model_version"],
                {
                    "game_id": game_id,
                    "market": MARKET,
                    "predicted_mean": float(lam),
                    "prob_under": pu,
                    "prob_over": 1 - pu,
                    "line_value": line,
                    "model_version": MODEL_VERSION,
                },
            )
            rows += 1
    print(f"game_predictions: upserted {rows} upcoming games (market={MARKET}, "
          f"per-game F5 line, fallback line {LINE})")


if __name__ == "__main__":
    main()
