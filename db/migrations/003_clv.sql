-- Migration 003: closing-line-value (CLV) tracking (README §13.2).
--
-- CLV = (de-vigged implied probability of our side at the closing line)
--     - (same at the line when the edge was first flagged).
-- Positive CLV means the market moved toward our position — the strongest
-- early evidence that flagged edges are real, available weeks before
-- win/loss records mean anything.

BEGIN;

-- When the edge was first flagged. edges.py's upsert never passes this
-- column, so ON CONFLICT updates leave the original first-seen timestamp.
ALTER TABLE edges ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TABLE clv_records (
    player_id             INTEGER REFERENCES players(player_id),
    game_id               INTEGER REFERENCES games(game_id),
    stat_type             TEXT NOT NULL,
    side                  TEXT NOT NULL,
    rec_odds              INTEGER,
    rec_implied_prob      NUMERIC,     -- de-vigged, our side, at flag time
    closing_odds          INTEGER,
    closing_implied_prob  NUMERIC,     -- de-vigged, our side, last pull
    clv                   NUMERIC,     -- closing_implied_prob - rec_implied_prob
    n_snapshots           INTEGER,     -- line pulls seen for this key; 1 => clv is trivially 0
    rec_at                TIMESTAMPTZ,
    closing_pulled_at     TIMESTAMPTZ,
    PRIMARY KEY (player_id, game_id, stat_type)
);

COMMIT;
