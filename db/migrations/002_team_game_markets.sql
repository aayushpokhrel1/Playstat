-- Migration 002: team/game-level stats and markets (README §13.3).
--
-- prop_lines/model_predictions are player-keyed, but markets like
-- "first inning total runs under 1.5" belong to a game, not a player.
-- Three long-format tables mirror the player-side trio:
--   team_game_stats   ~ player_game_stats   (actuals, e.g. runs_inning_1)
--   game_lines        ~ prop_lines          (book lines, market-keyed)
--   game_predictions  ~ model_predictions   (model outputs)

BEGIN;

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
    market      TEXT NOT NULL,          -- e.g. 'first_inning_runs'
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
    prob_under      NUMERIC,            -- P(actual < line_value)
    prob_over       NUMERIC,
    line_value      NUMERIC,
    model_version   TEXT NOT NULL,
    PRIMARY KEY (game_id, market, model_version)
);

COMMIT;
