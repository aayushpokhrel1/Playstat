-- Migration 001: multi-sport support (README §13.1) — MLB first, then NFL.
--
-- Two changes:
--   1. `sport` column on teams/players/games (existing rows are all 'nba').
--      No default going forward: ingestion must say which sport it's writing,
--      so a forgotten sport fails loudly instead of silently becoming NBA.
--   2. player_game_stats and rolling_player_features move from hardcoded NBA
--      columns to long format keyed by stat_type/feature name, so a new sport
--      is a new stat vocabulary, not new DDL.
--
-- The old wide tables are kept, renamed *_wide_legacy, as an on-disk rollback
-- point. Drop them once the migrated pipeline has run clean for a while:
--   DROP TABLE player_game_stats_wide_legacy, rolling_player_features_wide_legacy;
--
-- Cross-sport ID collisions: teams/players/games PKs are API-Sports numeric IDs,
-- and each sport's API has its own overlapping ID space. Handled at ingestion,
-- not here — non-NBA sports get a deterministic ID offset (see SPORTS in
-- ingestion/config.py), so every existing NBA row and FK is untouched.

BEGIN;

ALTER TABLE teams ADD COLUMN sport TEXT NOT NULL DEFAULT 'nba';
ALTER TABLE teams ALTER COLUMN sport DROP DEFAULT;

ALTER TABLE players ADD COLUMN sport TEXT NOT NULL DEFAULT 'nba';
ALTER TABLE players ALTER COLUMN sport DROP DEFAULT;

ALTER TABLE games ADD COLUMN sport TEXT NOT NULL DEFAULT 'nba';
ALTER TABLE games ALTER COLUMN sport DROP DEFAULT;
CREATE INDEX idx_games_sport_date ON games(sport, date);

-- player_game_stats: wide NBA columns -> long (stat_type, value).
-- usage_rate is dropped outright: never populated (README §11).
-- NULL stats are dropped: a NULL cell carried no information in wide format,
-- and "which games already have stats" (db.game_ids_with_stats) only needs
-- one row per game to exist.
ALTER TABLE player_game_stats RENAME TO player_game_stats_wide_legacy;
-- Renaming a table doesn't rename its indexes/constraints — move them aside so
-- the new table can reuse the canonical names.
ALTER INDEX player_game_stats_pkey RENAME TO player_game_stats_wide_legacy_pkey;
ALTER INDEX idx_player_game_stats_game RENAME TO idx_player_game_stats_game_wide_legacy;

CREATE TABLE player_game_stats (
    player_id   INTEGER REFERENCES players(player_id),
    game_id     INTEGER REFERENCES games(game_id),
    stat_type   TEXT NOT NULL,
    value       NUMERIC,
    PRIMARY KEY (player_id, game_id, stat_type)
);
CREATE INDEX idx_player_game_stats_game ON player_game_stats(game_id);

INSERT INTO player_game_stats (player_id, game_id, stat_type, value)
SELECT w.player_id, w.game_id, s.stat_type, s.value
FROM player_game_stats_wide_legacy w
CROSS JOIN LATERAL (VALUES
    ('points',   w.points::numeric),
    ('rebounds', w.rebounds::numeric),
    ('assists',  w.assists::numeric),
    ('minutes',  w.minutes)
) AS s(stat_type, value)
WHERE s.value IS NOT NULL;

-- rolling_player_features: wide NBA feature columns -> long (feature, value).
-- Booleans become 0/1 numerics — the models already consumed them as floats.
ALTER TABLE rolling_player_features RENAME TO rolling_player_features_wide_legacy;
ALTER INDEX rolling_player_features_pkey RENAME TO rolling_player_features_wide_legacy_pkey;

CREATE TABLE rolling_player_features (
    player_id   INTEGER REFERENCES players(player_id),
    as_of_date  DATE NOT NULL,
    feature     TEXT NOT NULL,
    value       NUMERIC,
    PRIMARY KEY (player_id, as_of_date, feature)
);

INSERT INTO rolling_player_features (player_id, as_of_date, feature, value)
SELECT w.player_id, w.as_of_date, f.feature, f.value
FROM rolling_player_features_wide_legacy w
CROSS JOIN LATERAL (VALUES
    ('pts_avg_5',       w.pts_avg_5),
    ('pts_avg_10',      w.pts_avg_10),
    ('reb_avg_5',       w.reb_avg_5),
    ('ast_avg_5',       w.ast_avg_5),
    ('opp_def_rating',  w.opp_def_rating),
    ('rest_days',       w.rest_days::numeric),
    ('is_home',         (w.is_home::int)::numeric),
    ('is_back_to_back', (w.is_back_to_back::int)::numeric)
) AS f(feature, value)
WHERE f.value IS NOT NULL;

COMMIT;
