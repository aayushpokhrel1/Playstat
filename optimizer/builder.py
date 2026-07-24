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


def load_player_legs(engine, floor=DEFAULT_FLOOR, slate_date=None):
    """Latest two-sided player prop lines on TODAY'S slate (unfinished games),
    + model_prob context.

    slate_date restricts candidate games to `g.date = slate_date` (default:
    CURRENT_DATE, evaluated server-side so it tracks the DB's timezone).
    Without this, futures prop lines can leak games weeks/months out and mix
    a tonight leg with a September leg in the same parlay (README §15.10
    KNOWN ISSUE / §15.9 item 6).
    """
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
                    AND g.date = COALESCE(:slate_date, CURRENT_DATE)
                JOIN players p ON p.player_id = pl.player_id
                LEFT JOIN edges e ON e.player_id = pl.player_id
                    AND e.game_id = pl.game_id AND e.stat_type = pl.stat_type
                """
            ),
            conn, params={"slate_date": slate_date},
        )
    return _normalize(df, normalize_player_leg, floor)


def load_team_legs(engine, floor=DEFAULT_FLOOR, slate_date=None):
    """Latest two-sided team-market lines on TODAY'S slate (unfinished games),
    + model_prob context. See load_player_legs for the slate_date rationale.
    """
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
                    AND g.date = COALESCE(:slate_date, CURRENT_DATE)
                LEFT JOIN game_edges ge ON ge.game_id = gl.game_id AND ge.market = gl.market
                """
            ),
            conn, params={"markets": list(TEAM_MARKETS), "slate_date": slate_date},
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


def load_legs(engine, floor=DEFAULT_FLOOR, slate_date=None):
    return (load_player_legs(engine, floor, slate_date)
            + load_team_legs(engine, floor, slate_date))


def save_builds(engine, target_payout, results, parlay_class="across_game"):
    """Persist constructions. No EV/edge field is written — this builder makes no such claim.

    parlay_class is written into the legs blob's {"class": ...} wrapper.
    The default "across_game" is the existing player-tier mixed build; a
    dedicated team-only build (--team-only) passes "team_tier" so the saved
    endpoint (api/main.py GET /parlay-builder/saved) can tell the two apart
    (README §15.9 item 5 / §15.10 team-legs note).
    """
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
                    # allow_nan=False: emit a loud Python error rather than bare
                    # NaN, which is invalid JSON and Postgres rejects downstream.
                    "legs": json.dumps({"class": parlay_class, "legs": legs_json},
                                       allow_nan=False),
                    "jp": r["joint_prob"],
                    "co": r["combined_odds"],
                },
            )
            rows += 1
    return rows


def main():
    parser = argparse.ArgumentParser(description="Low-risk parlay builder (no edge/EV claim).")
    parser.add_argument("--target-payout", type=float, default=None,
                        help="minimum payout to construct — a FLOOR, not a target band. "
                             "Returns the safest (highest joint-prob) construction that "
                             "pays AT LEAST this much.")
    parser.add_argument("--min-prob", type=float, default=None)
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help="initial search width above --target-payout, as a fraction "
                             "(e.g. 0.10 = search up to 10%% above the floor first). Only "
                             "affects search performance, not correctness: if nothing "
                             "qualifies within it, the search automatically widens "
                             "(1.5x, 3x, then unbounded) until it finds the cheapest "
                             "qualifying construction. Ignored when --target-payout is unset.")
    parser.add_argument("--floor", type=float, default=DEFAULT_FLOOR)
    parser.add_argument("--min-legs", type=int, default=DEFAULT_MIN_LEGS)
    parser.add_argument("--max-legs", type=int, default=DEFAULT_MAX_LEGS)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--slate-date", type=str, default=None,
                        help="restrict candidate games to this date (YYYY-MM-DD). "
                             "Default: today (CURRENT_DATE, server-tz).")
    parser.add_argument("--team-only", action="store_true",
                        help="build from team-market (NRFI/F5) legs only — a dedicated, "
                             "higher-variance team tier (README §15.9 item 5). --save "
                             "writes class=\"team_tier\" instead of \"across_game\".")
    parser.add_argument("--save", action="store_true", help="persist to parlay_recommendations")
    args = parser.parse_args()

    if args.target_payout is None and args.min_prob is None:
        parser.error("pin at least one axis: --target-payout and/or --min-prob")

    engine = db.get_engine()
    if args.team_only:
        legs = load_team_legs(engine, args.floor, args.slate_date)
    else:
        legs = load_legs(engine, args.floor, args.slate_date)
    print(f"candidate legs (favorite side, market prob >= {args.floor:.0%}): {len(legs)}")
    if not legs:
        print("no candidate legs — nothing to build.")
        return

    if args.target_payout is not None:
        print(f"target payout {args.target_payout:.2f}x is a FLOOR — every construction "
              f"below will pay AT LEAST that much (search widens automatically if "
              f"nothing qualifies within --tolerance).")

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
        parlay_class = "team_tier" if args.team_only else "across_game"
        saved = save_builds(engine, args.target_payout or 0.0, results, parlay_class)
        print(f"parlay_recommendations (kind=builder, class={parlay_class}): "
              f"inserted {saved} rows")


if __name__ == "__main__":
    main()
