-- 011: sharp_lines — sharp-reference (Pinnacle) close snapshots.
-- README §15.9 item 14e / spec docs/superpowers/specs/2026-08-11-sharp-reference-snapshot-design.md
--
-- Append-only, same conventions as prop_lines/game_lines: our game_id/player_id
-- id space, our market vocabulary, American odds in side-pair columns
-- (over/under for totals-shaped markets incl. player props, home/away for
-- moneyline), pulled_at snapshot timestamp. player_id is NULL for game markets.
-- line_value is NULL for moneyline. No FKs (prop_lines/game_lines precedent).
-- Nothing else reads this table — it feeds only the CLI comparison
-- (optimizer/sharp_compare.py), never the builder, API, or dashboard.

CREATE TABLE IF NOT EXISTS sharp_lines (
    id          BIGSERIAL PRIMARY KEY,
    game_id     INTEGER      NOT NULL,
    player_id   INTEGER,
    market      TEXT         NOT NULL,
    line_value  NUMERIC,
    book        TEXT         NOT NULL,
    over_odds   INTEGER,
    under_odds  INTEGER,
    home_odds   INTEGER,
    away_odds   INTEGER,
    pulled_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sharp_lines_lookup
    ON sharp_lines (game_id, market, pulled_at DESC);
