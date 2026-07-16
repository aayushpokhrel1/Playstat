-- Migration 004: paper-trading ledger (README §14.1 "Bet-outcome tracking").
--
-- The system recommends parlays and flags edges but never records whether
-- they would have won. recommendation_outcomes is a settlement ledger: one
-- row per recommended parlay (bet_type='parlay') and one row per flagged
-- edge above the optimizer's min-edge threshold (bet_type='edge'), written
-- once each, when the underlying game(s) finish and the actual stat lands.
-- `pnl` is paper P&L in units at a flat 1-unit stake, computed at the odds
-- frozen when the bet was recommended (never the closing line) — this is a
-- ledger of "would this recommendation have paid off", not a CLV measure.
-- See modeling/settle.py, the daily-chain step that populates this table.

BEGIN;

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

-- One settlement per recommendation, ever (idempotency guard for modeling/settle.py).
CREATE UNIQUE INDEX recommendation_outcomes_parlay_uq ON recommendation_outcomes (parlay_id) WHERE bet_type='parlay';
CREATE UNIQUE INDEX recommendation_outcomes_edge_uq   ON recommendation_outcomes (player_id, game_id, stat_type) WHERE bet_type='edge';

COMMIT;
