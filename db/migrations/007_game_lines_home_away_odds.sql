-- Migration 007: home/away odds on game_lines (NFL spread + moneyline, README §16 / NFL #3).
-- game_lines was over/under-only (line_value, over_odds, under_odds). Spread and
-- moneyline are home/away markets. Additive: existing MLB (first_inning_runs, f5_runs)
-- and the NFL full_game_total rows keep using over/under and read NULL here. No backfill.
BEGIN;
ALTER TABLE game_lines
    ADD COLUMN IF NOT EXISTS home_odds INTEGER,
    ADD COLUMN IF NOT EXISTS away_odds INTEGER;
COMMIT;
