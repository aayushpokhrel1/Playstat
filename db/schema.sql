-- Basketball Analytics + Parlay Optimizer schema
-- Primary keys for teams/players/games use the API-Basketball numeric IDs directly,
-- so ingestion can upsert on the API's own IDs instead of maintaining a mapping table.

CREATE TABLE teams (
    team_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    conference  TEXT,
    pace        NUMERIC,
    def_rating  NUMERIC
);

CREATE TABLE players (
    player_id   INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    team_id     INTEGER REFERENCES teams(team_id),
    position    TEXT
);

CREATE TABLE games (
    game_id         INTEGER PRIMARY KEY,
    date            DATE NOT NULL,
    home_team_id    INTEGER REFERENCES teams(team_id),
    away_team_id    INTEGER REFERENCES teams(team_id),
    status          TEXT
);
CREATE INDEX idx_games_date ON games(date);

-- Actual results, used for training + backtesting.
CREATE TABLE player_game_stats (
    player_id   INTEGER REFERENCES players(player_id),
    game_id     INTEGER REFERENCES games(game_id),
    points      INTEGER,
    rebounds    INTEGER,
    assists     INTEGER,
    minutes     NUMERIC,
    usage_rate  NUMERIC,
    PRIMARY KEY (player_id, game_id)
);
CREATE INDEX idx_player_game_stats_game ON player_game_stats(game_id);

-- Computed rolling features, not raw stats. Populated in a later phase.
CREATE TABLE rolling_player_features (
    player_id           INTEGER REFERENCES players(player_id),
    as_of_date          DATE NOT NULL,
    pts_avg_5           NUMERIC,
    pts_avg_10          NUMERIC,
    reb_avg_5           NUMERIC,
    ast_avg_5           NUMERIC,
    opp_def_rating      NUMERIC,
    rest_days           INTEGER,
    is_home             BOOLEAN,
    is_back_to_back     BOOLEAN,
    PRIMARY KEY (player_id, as_of_date)
);

-- Sportsbook prop lines, from the odds API. Populated in a later phase.
CREATE TABLE prop_lines (
    line_id     SERIAL PRIMARY KEY,
    player_id   INTEGER REFERENCES players(player_id),
    game_id     INTEGER REFERENCES games(game_id),
    stat_type   TEXT NOT NULL,
    line_value  NUMERIC,
    over_odds   INTEGER,
    under_odds  INTEGER,
    pulled_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_prop_lines_game ON prop_lines(game_id);

-- Model outputs. Populated in a later phase.
CREATE TABLE model_predictions (
    player_id       INTEGER REFERENCES players(player_id),
    game_id         INTEGER REFERENCES games(game_id),
    stat_type       TEXT NOT NULL,
    predicted_mean  NUMERIC,
    predicted_std   NUMERIC,
    prob_over       NUMERIC,
    prob_under      NUMERIC,
    model_version   TEXT NOT NULL,
    PRIMARY KEY (player_id, game_id, stat_type, model_version)
);

-- Model probability vs. de-vigged sportsbook implied probability. Populated in a later phase.
CREATE TABLE edges (
    player_id       INTEGER REFERENCES players(player_id),
    game_id         INTEGER REFERENCES games(game_id),
    stat_type       TEXT NOT NULL,
    model_prob      NUMERIC,
    implied_prob    NUMERIC,
    edge            NUMERIC,
    side            TEXT CHECK (side IN ('over', 'under')),
    PRIMARY KEY (player_id, game_id, stat_type)
);

-- Parlay optimizer output. Populated in a later phase.
CREATE TABLE parlay_recommendations (
    parlay_id       SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    target_payout   NUMERIC NOT NULL,
    legs            JSONB NOT NULL,
    joint_prob      NUMERIC,
    combined_odds   NUMERIC
);

-- Accumulating history of backtest snapshots, so accuracy/calibration trends
-- over time are queryable rather than only ever printed to stdout and lost.
CREATE TABLE backtest_runs (
    run_id          SERIAL PRIMARY KEY,
    run_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    stat_type       TEXT NOT NULL,
    model_version   TEXT NOT NULL,
    n_test_games    INTEGER,
    mae             NUMERIC,
    coverage_16     NUMERIC,
    coverage_84     NUMERIC
);
CREATE INDEX idx_backtest_runs_stat_type ON backtest_runs(stat_type, run_at);
