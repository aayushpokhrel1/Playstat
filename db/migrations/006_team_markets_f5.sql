-- Team-market parlays (NRFI + F5). See docs/superpowers/specs/2026-07-17-mlb-team-market-parlays-design.md
BEGIN;

-- Discriminator so the team pipeline and legacy player parlays share one table + ledger.
ALTER TABLE parlay_recommendations
    ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'player';

-- Game-level edges: today game_lines/game_predictions are served but never turned
-- into edges (edges is player-keyed). This is the team-market analogue of `edges`.
CREATE TABLE IF NOT EXISTS game_edges (
    game_id       integer NOT NULL REFERENCES games(game_id),
    market        text    NOT NULL,
    side          text    NOT NULL,
    model_prob    numeric,
    implied_prob  numeric,
    edge          numeric,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (game_id, market)
);
CREATE INDEX IF NOT EXISTS idx_game_edges_market ON game_edges (market);

COMMIT;
