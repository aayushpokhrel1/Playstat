-- Sports Analytics + Parlay Optimizer schema (multi-sport: nba live, mlb/nfl planned)
-- Primary keys for teams/players/games use the API-Sports numeric IDs directly,
-- so ingestion can upsert on the API's own IDs instead of maintaining a mapping table.
-- Each sport's API has its own overlapping ID space, so ingestion applies a
-- deterministic per-sport ID offset (see SPORTS in ingestion/config.py) — nba +0,
-- so all pre-multi-sport rows are unchanged.
-- Applied migrations: db/migrations/001_multi_sport.sql,
-- db/migrations/002_team_game_markets.sql, db/migrations/003_clv.sql,
-- db/migrations/004_recommendation_outcomes.sql
-- (a fresh install of this file needs no migrations).

CREATE TABLE teams (
    team_id     INTEGER PRIMARY KEY,
    sport       TEXT NOT NULL,
    name        TEXT NOT NULL,
    conference  TEXT,
    pace        NUMERIC,
    def_rating  NUMERIC
);

CREATE TABLE players (
    player_id   INTEGER PRIMARY KEY,
    sport       TEXT NOT NULL,
    name        TEXT NOT NULL,
    team_id     INTEGER REFERENCES teams(team_id),
    position    TEXT
);

CREATE TABLE games (
    game_id         INTEGER PRIMARY KEY,
    sport           TEXT NOT NULL,
    date            DATE NOT NULL,
    home_team_id    INTEGER REFERENCES teams(team_id),
    away_team_id    INTEGER REFERENCES teams(team_id),
    status          TEXT
);
CREATE INDEX idx_games_date ON games(date);
CREATE INDEX idx_games_sport_date ON games(sport, date);

-- Actual results, used for training + backtesting. Long format: one row per
-- (player, game, stat), so each sport brings its own stat_type vocabulary
-- (nba: points/rebounds/assists/minutes) without schema changes.
CREATE TABLE player_game_stats (
    player_id   INTEGER REFERENCES players(player_id),
    game_id     INTEGER REFERENCES games(game_id),
    stat_type   TEXT NOT NULL,
    value       NUMERIC,
    PRIMARY KEY (player_id, game_id, stat_type)
);
CREATE INDEX idx_player_game_stats_game ON player_game_stats(game_id);

-- Computed rolling features, not raw stats. Long format for the same reason:
-- feature names are per-sport (nba: pts_avg_5, ..., is_back_to_back as 0/1).
CREATE TABLE rolling_player_features (
    player_id   INTEGER REFERENCES players(player_id),
    as_of_date  DATE NOT NULL,
    feature     TEXT NOT NULL,
    value       NUMERIC,
    PRIMARY KEY (player_id, as_of_date, feature)
);

-- Team/game-level analogues of the player-side trio (migration 002): actuals
-- (e.g. runs_inning_1 from MLB linescores), book lines for game-scoped markets
-- (e.g. first-inning total runs), and game-level model outputs.
CREATE TABLE team_game_stats (
    team_id     INTEGER REFERENCES teams(team_id),
    game_id     INTEGER REFERENCES games(game_id),
    stat_type   TEXT NOT NULL,
    value       NUMERIC,
    PRIMARY KEY (team_id, game_id, stat_type)
);
CREATE INDEX idx_team_game_stats_game ON team_game_stats(game_id);

CREATE TABLE game_lines (
    line_id     SERIAL PRIMARY KEY,
    game_id     INTEGER REFERENCES games(game_id),
    market      TEXT NOT NULL,
    line_value  NUMERIC,
    over_odds   INTEGER,
    under_odds  INTEGER,
    pulled_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_game_lines_game ON game_lines(game_id);

CREATE TABLE game_predictions (
    game_id         INTEGER REFERENCES games(game_id),
    market          TEXT NOT NULL,
    predicted_mean  NUMERIC,
    prob_under      NUMERIC,
    prob_over       NUMERIC,
    line_value      NUMERIC,
    model_version   TEXT NOT NULL,
    PRIMARY KEY (game_id, market, model_version)
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

-- Model probability vs. de-vigged sportsbook implied probability.
CREATE TABLE edges (
    player_id       INTEGER REFERENCES players(player_id),
    game_id         INTEGER REFERENCES games(game_id),
    stat_type       TEXT NOT NULL,
    model_prob      NUMERIC,
    implied_prob    NUMERIC,
    edge            NUMERIC,
    side            TEXT CHECK (side IN ('over', 'under')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),  -- first flagged; upserts preserve it
    PRIMARY KEY (player_id, game_id, stat_type)
);

-- Closing-line value per finished-game edge (migration 003, modeling/clv.py):
-- de-vigged implied prob of our side at close minus at flag time; positive =
-- the market moved toward us. The leading indicator of real edge quality.
CREATE TABLE clv_records (
    player_id             INTEGER REFERENCES players(player_id),
    game_id               INTEGER REFERENCES games(game_id),
    stat_type             TEXT NOT NULL,
    side                  TEXT NOT NULL,
    rec_odds              INTEGER,
    rec_implied_prob      NUMERIC,
    closing_odds          INTEGER,
    closing_implied_prob  NUMERIC,
    clv                   NUMERIC,
    n_snapshots           INTEGER,
    rec_at                TIMESTAMPTZ,
    closing_pulled_at     TIMESTAMPTZ,
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

-- Paper-trading ledger (migration 004, modeling/settle.py, README §14.1):
-- one row per recommended parlay and per flagged edge, written once when its
-- game(s) finish and the actual stat lands. pnl is 1-unit paper P&L at the
-- odds frozen when the bet was recommended (not the closing line).
CREATE TABLE recommendation_outcomes (
    outcome_id      SERIAL PRIMARY KEY,
    bet_type        TEXT NOT NULL CHECK (bet_type IN ('parlay','edge')),
    parlay_id       INTEGER REFERENCES parlay_recommendations(parlay_id), -- set iff bet_type='parlay'
    player_id       INTEGER REFERENCES players(player_id),  -- set iff bet_type='edge'
    game_id         INTEGER REFERENCES games(game_id),      -- set iff bet_type='edge'
    stat_type       TEXT,   -- set iff bet_type='edge'
    side            TEXT,   -- set iff bet_type='edge'
    result          TEXT NOT NULL CHECK (result IN ('win','loss','push')),
    n_legs          INTEGER NOT NULL DEFAULT 1,
    stake           NUMERIC NOT NULL DEFAULT 1,   -- 1 unit paper stake
    decimal_odds    NUMERIC,   -- combined decimal over settled(non-push) legs (parlay) or single-bet decimal (edge)
    pnl             NUMERIC NOT NULL,  -- profit/loss in units
    legs            JSONB,     -- per-leg audit: [{player_id,game_id,stat_type,side,line,odds,actual,result}]
    recommended_at  TIMESTAMPTZ,       -- source rec's created_at
    settled_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX recommendation_outcomes_parlay_uq ON recommendation_outcomes (parlay_id) WHERE bet_type='parlay';
CREATE UNIQUE INDEX recommendation_outcomes_edge_uq   ON recommendation_outcomes (player_id, game_id, stat_type) WHERE bet_type='edge';

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
