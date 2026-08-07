-- Line shopping (README §15.9 item 3): best single-book price + book per leg
-- side, kept ALONGSIDE the existing consensus over/under/home/away columns.
-- Additive + nullable: old rows and any line with no eligible book stay NULL,
-- and the builder falls back to the consensus price (like model_prob=None).
-- v1 populates only the over/under (ou) columns; the home/away (sp/ml) columns
-- are created for forward-compat but left NULL until NFL/NBA line shopping.
ALTER TABLE prop_lines
  ADD COLUMN best_over_odds  INTEGER,
  ADD COLUMN best_over_book  TEXT,
  ADD COLUMN best_under_odds INTEGER,
  ADD COLUMN best_under_book TEXT;

ALTER TABLE game_lines
  ADD COLUMN best_over_odds  INTEGER,
  ADD COLUMN best_over_book  TEXT,
  ADD COLUMN best_under_odds INTEGER,
  ADD COLUMN best_under_book TEXT,
  ADD COLUMN best_home_odds  INTEGER,
  ADD COLUMN best_home_book  TEXT,
  ADD COLUMN best_away_odds  INTEGER,
  ADD COLUMN best_away_book  TEXT;
