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

TEAM_MARKETS = {
    "mlb": ("first_inning_runs", "f5_runs"),
    "nfl": ("full_game_total", "full_game_spread", "full_game_moneyline"),
    "nba": ("full_game_total", "full_game_spread", "full_game_moneyline"),
}

# Per-sport slate window (days added to the lower bound). MLB bets a single day's
# slate; NFL bets a weekly Thu..Mon card (see 2026-07-29-nfl-chain-record spec).
SLATE_WINDOW_DAYS = {"mlb": 0, "nfl": 4, "nba": 0}


def _team_class(sport):
    """--team-only save class: NFL AND NBA full-game markets save game_tier
    (spread/ML/total), distinct from MLB NRFI/F5 team_tier. Both kind=builder."""
    return "game_tier" if sport in ("nfl", "nba") else "team_tier"


def load_player_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb", window_days=0):
    """Latest two-sided player prop lines on TODAY'S slate (unfinished games),
    + model_prob context.

    slate_date restricts candidate games to a `g.date` range starting at
    slate_date (default: CURRENT_DATE, evaluated server-side so it tracks the
    DB's timezone) through slate_date + window_days inclusive. window_days=0
    (MLB's default) collapses the range to a single day, identical to the old
    `g.date = slate_date` behavior. Without this, futures prop lines can leak
    games weeks/months out and mix a tonight leg with a September leg in the
    same parlay (README §15.10 KNOWN ISSUE / §15.9 item 6).

    sport restricts candidate games to `g.sport = sport` (default: "mlb"),
    so an NFL builder run never pools MLB legs into the same parlay
    (NFL builder sub-project #2). window_days lets NFL span its weekly
    Thu..Mon card (NFL builder chain #4a) while MLB stays single-day.
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
                    AND g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)
                                   AND COALESCE(:slate_date, CURRENT_DATE) + :window_days
                    AND g.sport = :sport
                JOIN players p ON p.player_id = pl.player_id
                LEFT JOIN edges e ON e.player_id = pl.player_id
                    AND e.game_id = pl.game_id AND e.stat_type = pl.stat_type
                """
            ),
            conn, params={"slate_date": slate_date, "sport": sport, "window_days": window_days},
        )
    return _normalize(df, normalize_player_leg, floor)


def load_team_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb", window_days=0):
    """Latest two-sided team-market lines on TODAY'S slate (unfinished games),
    + model_prob context. See load_player_legs for the slate_date/sport/window_days
    rationale. markets are per-sport (TEAM_MARKETS[sport]); a sport with no game
    markets configured (unknown sport) short-circuits to no legs.
    """
    markets = list(TEAM_MARKETS.get(sport, ()))
    if not markets:
        return []
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                SELECT gl.game_id, gl.market, gl.line_value,
                       gl.over_odds, gl.under_odds, gl.home_odds, gl.away_odds,
                       ge.model_prob
                FROM (
                    SELECT DISTINCT ON (game_id, market)
                        game_id, market, line_value, over_odds, under_odds, home_odds, away_odds
                    FROM game_lines
                    WHERE market = ANY(:markets)
                    ORDER BY game_id, market, pulled_at DESC
                ) gl
                JOIN games g ON g.game_id = gl.game_id AND g.status != 'FT'
                    AND g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)
                                   AND COALESCE(:slate_date, CURRENT_DATE) + :window_days
                    AND g.sport = :sport
                LEFT JOIN game_edges ge ON ge.game_id = gl.game_id AND ge.market = gl.market
                """
            ),
            conn, params={"markets": markets, "slate_date": slate_date, "sport": sport,
                          "window_days": window_days},
        )
    return _normalize(df, normalize_team_leg, floor)


def _normalize(df, normalizer, floor):
    if df.empty:
        return []
    from optimizer.builder_core import is_home_away_market
    def _valid(r):
        m = r.get("market")
        if m is not None and is_home_away_market(m):
            if r.get("home_odds") is None or r.get("away_odds") is None:
                return False
            if m == "full_game_spread" and r.get("line_value") is None:
                return False
            return True
        # player props + over/under game markets
        return not (r.get("over_odds") is None or r.get("under_odds") is None or r.get("line_value") is None)
    # Convert pandas NaN -> None at the Python level. df.where(pd.notna(df), None)
    # does NOT stick for numeric columns — pandas re-coerces None back to NaN, and
    # `NaN is None` is False, so the _valid None-checks (and normalize_team_leg's
    # moneyline `line is None`) would miss a one-sided/absent value and crash on
    # int(NaN)/float(NaN). A dict of objects holds None fine (NaN != NaN, uniquely).
    records = [{k: (None if (v is None or v != v) else v) for k, v in r.items()}
               for r in df.to_dict("records")]
    records = [r for r in records if _valid(r)]
    legs = [normalizer(r) for r in records]
    return [leg for leg in legs if passes_floor(leg, floor)]


def load_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb", window_days=0):
    return (load_player_legs(engine, floor, slate_date, sport, window_days)
            + load_team_legs(engine, floor, slate_date, sport, window_days))


def construction_signature(legs_json):
    """Order-independent identity of a construction's leg set (the stored
    legs->'legs' shape). Two constructions with the same legs are the SAME bet
    regardless of which target-payout floor produced them, so they must not both
    be persisted: a thin tier (NRFI/F5) often has one 2-leg card that clears BOTH
    the 1.4x and 2.0x floors, and each --target-payout build would otherwise save
    it — a duplicate dashboard row AND a double-counted paper-ledger bet (found
    live 2026-07-30). frozenset makes it leg-order-independent and hashable.
    """
    return frozenset(
        (l.get("kind"), l.get("game_id"), l.get("player_id"), l.get("stat_type"),
         l.get("market"), l.get("side"), l.get("line"), l.get("odds"))
        for l in legs_json
    )


def _stored_legs(raw):
    """The legs list from a legs->'legs' JSONB cell (psycopg2 returns it parsed;
    tolerate a raw string on other driver paths)."""
    return raw if isinstance(raw, list) else json.loads(raw)


def save_builds(engine, target_payout, results, parlay_class="across_game", sport="mlb"):
    """Persist constructions. No EV/edge field is written — this builder makes no such claim.

    parlay_class is written into the legs blob's {"class": ...} wrapper.
    The default "across_game" is the existing player-tier mixed build; a
    dedicated team-only build (--team-only) passes "team_tier" so the saved
    endpoint (api/main.py GET /parlay-builder/saved) can tell the two apart
    (README §15.9 item 5 / §15.10 team-legs note).

    sport is written into the same wrapper as {"sport": ...} (default "mlb").
    Existing MLB rows predate this field and have no "sport" key; readers
    treat an absent key as "mlb" (NFL builder sub-project #2).

    DEDUP (found live 2026-07-30): a construction identical to one already saved
    for TODAY'S slate of the same (kind='builder', class, sport) is skipped — the
    1.4x and 2.0x builds of a thin tier otherwise each save the same card. Scoped
    to today's builds (CURRENT_DATE) so it never suppresses a legitimately-repeated
    card on a later slate; also dedups within the current batch.
    """
    rows = 0
    with engine.begin() as conn:
        existing = conn.execute(
            text(
                """
                SELECT legs->'legs' FROM parlay_recommendations
                WHERE kind = 'builder' AND created_at::date = CURRENT_DATE
                  AND legs->>'class' = :cls
                  AND COALESCE(legs->>'sport', 'mlb') = :sport
                """
            ),
            {"cls": parlay_class, "sport": sport},
        ).fetchall()
        seen = {construction_signature(_stored_legs(row[0])) for row in existing}
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
            sig = construction_signature(legs_json)
            if sig in seen:
                continue
            seen.add(sig)
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
                    "legs": json.dumps({"class": parlay_class, "sport": sport,
                                        "legs": legs_json}, allow_nan=False),
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
    parser.add_argument("--max-leg-reuse", type=int, default=2,
                        help="cap how many saved constructions may reuse the same "
                             "player (or, for team markets, the same game). 1 = fully "
                             "disjoint. Diversifies the top-N so one outcome can't "
                             "cascade across many cards.")
    parser.add_argument("--slate-date", type=str, default=None,
                        help="restrict candidate games to this date (YYYY-MM-DD). "
                             "Default: today (CURRENT_DATE, server-tz).")
    parser.add_argument("--window-days", type=int, default=None,
                        help="slate window length in days added to the lower bound "
                             "(default: per-sport — mlb 0 = today only, nfl 4 = Thu..Mon "
                             "weekly card). Override to force a specific span.")
    parser.add_argument("--team-only", action="store_true",
                        help="build from team-market (NRFI/F5) legs only — a dedicated, "
                             "higher-variance team tier (README §15.9 item 5). --save "
                             "writes class=\"team_tier\" instead of \"across_game\".")
    parser.add_argument("--sport", default="mlb",
                        help="which sport's candidate legs to build from "
                             "(default: mlb — the daily chain passes no --sport). "
                             "nfl builds player-only until the team tier lands (#3).")
    parser.add_argument("--save", action="store_true", help="persist to parlay_recommendations")
    args = parser.parse_args()

    if args.target_payout is None and args.min_prob is None:
        parser.error("pin at least one axis: --target-payout and/or --min-prob")

    window_days = args.window_days if args.window_days is not None else SLATE_WINDOW_DAYS.get(args.sport, 0)

    engine = db.get_engine()
    if args.team_only:
        legs = load_team_legs(engine, args.floor, args.slate_date, args.sport, window_days)
    else:
        legs = load_legs(engine, args.floor, args.slate_date, args.sport, window_days)
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
                    max_legs=args.max_legs, top_n=args.top_n, stats=stats,
                    max_uses=args.max_leg_reuse)
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
        if args.team_only:
            parlay_class = _team_class(args.sport)
        else:
            parlay_class = "across_game"
        saved = save_builds(engine, args.target_payout or 0.0, results, parlay_class, args.sport)
        print(f"parlay_recommendations (kind=builder, class={parlay_class}): "
              f"inserted {saved} rows")


if __name__ == "__main__":
    main()
