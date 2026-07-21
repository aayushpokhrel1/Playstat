"""Low-risk parlay builder (README §15).

Constructs across-game parlays from MLB player props + team markets, ranked by
DE-VIGGED MARKET probability. This is an honest constructor and paper-trading
sandbox: it makes no claim of edge or positive expected value.

Market-centric on purpose: it de-vigs the raw two-sided odds itself and takes
the FAVORITE side. It deliberately does not read `edges.side`/`edges.implied_prob`,
which hold the side the MODEL prefers (possibly an underdog) — see README §15.4.
"""

import argparse
import json

import pandas as pd
from sqlalchemy import text

from ingestion import db
from optimizer.builder_core import (
    DEFAULT_FLOOR, DEFAULT_MAX_LEGS, DEFAULT_MIN_LEGS, DEFAULT_TOLERANCE,
    build, normalize_player_leg, normalize_team_leg, passes_floor,
)

TEAM_MARKETS = ("first_inning_runs", "f5_runs")


def load_player_legs(engine, floor=DEFAULT_FLOOR):
    """Latest two-sided player prop lines on unfinished games, + model_prob context."""
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT pl.player_id, pl.game_id, pl.stat_type, pl.line_value,
                       pl.over_odds, pl.under_odds, p.name AS player_name,
                       e.model_prob
                FROM (
                    SELECT DISTINCT ON (player_id, game_id, stat_type)
                        player_id, game_id, stat_type, line_value, over_odds, under_odds
                    FROM prop_lines
                    ORDER BY player_id, game_id, stat_type, pulled_at DESC
                ) pl
                JOIN games g ON g.game_id = pl.game_id AND g.status != 'FT'
                JOIN players p ON p.player_id = pl.player_id
                LEFT JOIN edges e ON e.player_id = pl.player_id
                    AND e.game_id = pl.game_id AND e.stat_type = pl.stat_type
                """
            ),
            conn,
        )
    return _normalize(df, normalize_player_leg, floor)


def load_team_legs(engine, floor=DEFAULT_FLOOR):
    """Latest two-sided team-market lines on unfinished games, + model_prob context."""
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT gl.game_id, gl.market, gl.line_value, gl.over_odds, gl.under_odds,
                       ge.model_prob
                FROM (
                    SELECT DISTINCT ON (game_id, market)
                        game_id, market, line_value, over_odds, under_odds
                    FROM game_lines
                    WHERE market = ANY(:markets)
                    ORDER BY game_id, market, pulled_at DESC
                ) gl
                JOIN games g ON g.game_id = gl.game_id AND g.status != 'FT'
                LEFT JOIN game_edges ge ON ge.game_id = gl.game_id AND ge.market = gl.market
                """
            ),
            conn, params={"markets": list(TEAM_MARKETS)},
        )
    return _normalize(df, normalize_team_leg, floor)


def _normalize(df, normalizer, floor):
    if df.empty:
        return []
    # A book quoting only one side can't be de-vigged (~8% of live MLB lines).
    df = df.dropna(subset=["over_odds", "under_odds", "line_value"])
    if df.empty:
        return []
    df = df.where(pd.notna(df), None)
    legs = [normalizer(row) for row in df.to_dict("records")]
    return [leg for leg in legs if passes_floor(leg, floor)]


def load_legs(engine, floor=DEFAULT_FLOOR):
    return load_player_legs(engine, floor) + load_team_legs(engine, floor)


def save_builds(engine, target_payout, results):
    """Persist constructions. No EV/edge field is written — this builder makes no such claim."""
    rows = 0
    with engine.begin() as conn:
        for r in results:
            legs_json = [
                {
                    "kind": leg["kind"], "game_id": leg["game_id"],
                    "player_id": leg["player_id"], "stat_type": leg["stat_type"],
                    "market": leg["market"], "side": leg["side"],
                    "odds": leg["american_odds"], "line": leg["line_value"],
                    "label": leg["label"], "market_prob": leg["market_prob"],
                    "model_prob": leg["model_prob"],
                }
                for leg in r["legs"]
            ]
            conn.execute(
                text(
                    """
                    INSERT INTO parlay_recommendations
                        (kind, target_payout, legs, joint_prob, combined_odds)
                    VALUES ('builder', :tp, CAST(:legs AS JSONB), :jp, :co)
                    """
                ),
                {
                    "tp": target_payout,
                    "legs": json.dumps({"class": "across_game", "legs": legs_json}),
                    "jp": r["joint_prob"],
                    "co": r["combined_odds"],
                },
            )
            rows += 1
    return rows


def main():
    parser = argparse.ArgumentParser(description="Low-risk parlay builder (no edge/EV claim).")
    parser.add_argument("--target-payout", type=float, default=None)
    parser.add_argument("--min-prob", type=float, default=None)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument("--floor", type=float, default=DEFAULT_FLOOR)
    parser.add_argument("--min-legs", type=int, default=DEFAULT_MIN_LEGS)
    parser.add_argument("--max-legs", type=int, default=DEFAULT_MAX_LEGS)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--save", action="store_true", help="persist to parlay_recommendations")
    args = parser.parse_args()

    if args.target_payout is None and args.min_prob is None:
        parser.error("pin at least one axis: --target-payout and/or --min-prob")

    engine = db.get_engine()
    legs = load_legs(engine, args.floor)
    print(f"candidate legs (favorite side, market prob >= {args.floor:.0%}): {len(legs)}")
    if not legs:
        print("no candidate legs — nothing to build.")
        return

    stats = {}
    results = build(legs, target_payout=args.target_payout, tolerance=args.tolerance,
                    min_prob=args.min_prob, min_legs=args.min_legs,
                    max_legs=args.max_legs, top_n=args.top_n, stats=stats)
    print(f"searched {stats['candidate_games']} games, {stats['nodes']:,} nodes")
    if stats["truncated"]:
        print("WARNING: search hit its node budget — results are partial, not exhaustive.")
    print(f"constructions found: {stats['matches']} (showing {len(results)})")
    for r in results:
        print(f"  {r['combined_odds']:.2f}x  ~{r['joint_prob']:.1%} to hit  "
              f"({r['n_legs']} legs)")
        for leg in r["legs"]:
            print(f"      - {leg['label']} @ {leg['american_odds']:+d} "
                  f"(market {leg['market_prob']:.1%})")

    if args.save:
        saved = save_builds(engine, args.target_payout or 0.0, results)
        print(f"parlay_recommendations (kind=builder): inserted {saved} rows")


if __name__ == "__main__":
    main()
