-- 010_kelly_stake.sql
-- Kelly stake sizing (README §15.9 item 4). Additive, nullable: the ¼-Kelly
-- stake per builder parlay, written by the stake-sizing pass (optimizer/stake.py).
-- settle reads it; NULL means "not sized" and falls back to 1.0u (preserves the
-- prior flat-stake behaviour for historical rows). Budgerr's /parlay-builder/saved
-- does not select this column, so the external contract is byte-unchanged.
ALTER TABLE parlay_recommendations ADD COLUMN IF NOT EXISTS stake NUMERIC;
