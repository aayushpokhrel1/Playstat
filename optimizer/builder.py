"""Low-risk parlay builder (README §15).

Constructs across-game parlays from MLB player props + team markets, ranked by
DE-VIGGED MARKET probability. This is an honest constructor and paper-trading
sandbox: it makes no claim of edge or positive expected value.

Market-centric on purpose: it de-vigs the raw two-sided odds itself and takes
the FAVORITE side. It never depended on the model's own preferred side
(`edges.side`/`edges.implied_prob`, possibly an underdog — README §15.4); the
`edges`/`game_edges` tables are gone entirely now (model teardown, §16/#3B),
so `model_prob` is always None.
"""

import argparse
import json

import pandas as pd
from sqlalchemy import text

from ingestion import db
from optimizer.builder_core import (
    DEFAULT_FLOOR, DEFAULT_MAX_LEGS, DEFAULT_MIN_LEGS, DEFAULT_TOLERANCE,
    build, normalize_player_leg, normalize_team_leg, passes_floor, same_game_pairs,
)
from modeling.correlation import nrfi_f5_lift

TEAM_MARKETS = {
    "mlb": ("first_inning_runs", "f5_runs"),
    "nfl": ("full_game_total", "full_game_spread", "full_game_moneyline"),
    "nba": ("full_game_total", "full_game_spread", "full_game_moneyline"),
    "mls": ("full_game_total",),
    "ucl": ("full_game_total",),
    "nhl": ("full_game_total",),
}

# Per-sport slate window (days added to the lower bound). MLB bets a single day's
# slate; NFL bets a weekly Thu..Mon card (see 2026-07-29-nfl-chain-record spec).
SLATE_WINDOW_DAYS = {"mlb": 0, "nfl": 4, "nba": 0, "mls": 0, "ucl": 0, "nhl": 0}


def _team_class(sport):
    """--team-only save class: NFL/NBA/MLS/UCL/NHL full-game markets save game_tier,
    distinct from MLB NRFI/F5 team_tier. Both kind=builder."""
    return "game_tier" if sport in ("nfl", "nba", "mls", "ucl", "nhl") else "team_tier"


# Lookback for the start-probability filter (README §15.9 item 11 / item 9d).
# 21 days ≈ 18 team games — long enough to separate an everyday starter from a
# bench/platoon bat, short enough to track a recent role change.
START_RATE_WINDOW_DAYS = 21


def load_start_rates(engine, slate_date=None, sport="mlb", window_days=START_RATE_WINDOW_DAYS):
    """{player_id: start_rate} over the prior `window_days`, where start_rate is
    the player's appearances divided by THEIR TEAM'S finished games in the window.

    Normalising by team games (not calendar days) is what makes the number
    interpretable: teams play ~6 games a week, so a calendar-day denominator
    understates every player and an everyday starter reads ~0.85 instead of ~1.0.

    Only players whose team actually played in the window get a rate. A player
    with team games but zero appearances correctly reads 0.0 (bench/injured);
    a player whose team has NO finished games in the window is absent from the
    map entirely, and callers must treat that as "unknown — do not filter", so
    an early-season or data-gap slate can't silently empty the candidate pool.
    """
    with engine.begin() as conn:
        df = pd.read_sql(
            text(
                """
                WITH window_games AS (
                    SELECT game_id, home_team_id, away_team_id
                    FROM games
                    WHERE sport = :sport AND status = 'FT'
                      AND date BETWEEN COALESCE(:slate_date, CURRENT_DATE) - :win
                                   AND COALESCE(:slate_date, CURRENT_DATE) - 1
                ),
                team_games AS (
                    SELECT team_id, COUNT(*) AS n FROM (
                        SELECT game_id, home_team_id AS team_id FROM window_games
                        UNION ALL
                        SELECT game_id, away_team_id AS team_id FROM window_games
                    ) s GROUP BY team_id
                ),
                player_apps AS (
                    SELECT pgs.player_id, COUNT(DISTINCT pgs.game_id) AS n
                    FROM player_game_stats pgs
                    JOIN window_games wg ON wg.game_id = pgs.game_id
                    GROUP BY pgs.player_id
                )
                SELECT p.player_id,
                       COALESCE(pa.n, 0)::float / tg.n AS start_rate
                FROM players p
                JOIN team_games tg ON tg.team_id = p.team_id
                LEFT JOIN player_apps pa ON pa.player_id = p.player_id
                WHERE tg.n > 0
                """
            ),
            conn, params={"slate_date": slate_date, "sport": sport,
                          "win": window_days},
        )
    if df.empty:
        return {}
    return {int(r.player_id): float(r.start_rate) for r in df.itertuples()}


def filter_by_start_rate(legs, start_rates, min_start_rate):
    """Drop player legs whose player rarely appears (README §15.9 item 11).

    Pure. Team legs are never filtered (a game always happens). A player MISSING
    from start_rates is kept — an absent rate means "can't judge" (their team had
    no finished games in the window), not "never plays"; dropping those would
    empty the pool on an early-season or backfill-gap slate.

    Why this exists: the chain builds ~08:39 ET, hours before MLB lineups post,
    so 18.3% of legs voided on players who were then rested/scratched — 34.9% of
    cards lost a leg and 22.9% degraded to <=1 graded leg (README §15.9 item 9d).
    """
    if not min_start_rate:
        return legs
    return [
        leg for leg in legs
        if leg["kind"] != "player"
        or start_rates.get(leg["player_id"], None) is None
        or start_rates[leg["player_id"]] >= min_start_rate
    ]


def filter_by_confirmed_lineup(legs, confirmed_ids, started_game_ids):
    """Restrict legs to CONFIRMED starters in games that have not started.

    README §15.9 item 11 Option B. Pure — the caller fetches the lineup.

    confirmed_ids=None is OFF and returns `legs` unchanged (byte-identical
    default). An EMPTY set is meaningfully different: it means no lineup has
    posted, and every player leg is correctly dropped. Team legs carry no player
    so a lineup can never confirm them — they are never lineup-filtered, only
    excluded when their game has already started.
    """
    if confirmed_ids is None:
        return legs
    started = started_game_ids or set()
    return [
        leg for leg in legs
        if leg["game_id"] not in started
        and (leg["kind"] != "player" or leg["player_id"] in confirmed_ids)
    ]


def load_player_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb", window_days=0,
                     min_start_rate=0.0, confirmed_ids=None, started_game_ids=None):
    """Latest two-sided player prop lines on TODAY'S slate (unfinished games).

    model_prob is now ALWAYS None: the `edges` table it came from was dropped
    with the model teardown (README §16 / #3B, 2026-08-06), so the old
    `LEFT JOIN edges` was removed. normalize_player_leg tolerates its absence
    (row.get("model_prob") -> None), and model_prob was never used for ranking —
    the builder ranks purely on de-vigged market probability (§15.4/§15.8).

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
                       pl.best_over_odds, pl.best_over_book,
                       pl.best_under_odds, pl.best_under_book
                FROM (
                    SELECT DISTINCT ON (player_id, game_id, stat_type)
                        player_id, game_id, stat_type, line_value, over_odds, under_odds,
                        best_over_odds, best_over_book, best_under_odds, best_under_book
                    FROM prop_lines
                    ORDER BY player_id, game_id, stat_type, pulled_at DESC
                ) pl
                JOIN games g ON g.game_id = pl.game_id AND g.status != 'FT'
                    AND g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)
                                   AND COALESCE(:slate_date, CURRENT_DATE) + :window_days
                    AND g.sport = :sport
                JOIN players p ON p.player_id = pl.player_id
                """
            ),
            conn, params={"slate_date": slate_date, "sport": sport, "window_days": window_days},
        )
    legs = _normalize(df, normalize_player_leg, floor)
    # Start-probability filter (README §15.9 item 11 Option A). Default 0.0 = OFF,
    # so the library default stays byte-identical; the chain/CLI opt in explicitly.
    if min_start_rate:
        legs = filter_by_start_rate(legs, load_start_rates(engine, slate_date, sport), min_start_rate)
    # Confirmed-lineup filter (Option B). Default None = OFF. Deliberately NOT
    # combined with min_start_rate by the chain: a posted lineup is direct
    # evidence of starting, and the start-rate proxy would drop confirmed
    # starters with thin history for no reason.
    legs = filter_by_confirmed_lineup(legs, confirmed_ids, started_game_ids)
    return legs


def load_team_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb", window_days=0,
                   started_game_ids=None):
    """Latest two-sided team-market lines on TODAY'S slate (unfinished games).
    See load_player_legs for the slate_date/sport/window_days rationale and for
    why model_prob is now always None (the `game_edges` table was dropped with
    the model teardown, §16/#3B, so its `LEFT JOIN` was removed). markets are
    per-sport (TEAM_MARKETS[sport]); a sport with no game markets configured
    (unknown sport) short-circuits to no legs.
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
                       gl.best_over_odds, gl.best_over_book,
                       gl.best_under_odds, gl.best_under_book,
                       gl.best_home_odds, gl.best_home_book,
                       gl.best_away_odds, gl.best_away_book
                FROM (
                    SELECT DISTINCT ON (game_id, market)
                        game_id, market, line_value, over_odds, under_odds, home_odds, away_odds,
                        best_over_odds, best_over_book, best_under_odds, best_under_book,
                        best_home_odds, best_home_book, best_away_odds, best_away_book
                    FROM game_lines
                    WHERE market = ANY(:markets)
                    ORDER BY game_id, market, pulled_at DESC
                ) gl
                JOIN games g ON g.game_id = gl.game_id AND g.status != 'FT'
                    AND g.date BETWEEN COALESCE(:slate_date, CURRENT_DATE)
                                   AND COALESCE(:slate_date, CURRENT_DATE) + :window_days
                    AND g.sport = :sport
                """
            ),
            conn, params={"markets": markets, "slate_date": slate_date, "sport": sport,
                          "window_days": window_days},
        )
    legs = _normalize(df, normalize_team_leg, floor)
    # Team legs are never lineup-filtered (no player), but a started game is
    # still out of scope for a confirmed-lineup card. confirmed_ids is passed as
    # an empty set purely to switch the helper on; it is not consulted for team legs.
    if started_game_ids is not None:
        legs = filter_by_confirmed_lineup(legs, set(), started_game_ids)
    return legs


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


def load_legs(engine, floor=DEFAULT_FLOOR, slate_date=None, sport="mlb", window_days=0,
              min_start_rate=0.0, confirmed_ids=None, started_game_ids=None):
    return (load_player_legs(engine, floor, slate_date, sport, window_days, min_start_rate,
                             confirmed_ids, started_game_ids)
            + load_team_legs(engine, floor, slate_date, sport, window_days, started_game_ids))


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
                    # Best-price book for the shopped odds (§15.9 item 3). .get:
                    # legacy/hand-built legs without the key store book=None.
                    "book": leg.get("book"),
                }
                for leg in r["legs"]
            ]
            sig = construction_signature(legs_json)
            if sig in seen:
                continue
            seen.add(sig)
            wrapper = {"class": parlay_class, "sport": sport}
            # Same-game cards (README §15.9 item 1) carry correlation metadata at
            # the wrapper level — it's a property of the pair, not a leg. Absent on
            # every other class, so their persisted shape is byte-unchanged.
            for k in ("lift", "lift_n", "both_n", "small_sample"):
                if k in r:
                    wrapper[k] = r[k]
            wrapper["legs"] = legs_json
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
                    "legs": json.dumps(wrapper, allow_nan=False),
                    "jp": r["joint_prob"],
                    "co": r["combined_odds"],
                },
            )
            rows += 1
    return rows


def build_same_game(team_legs, lift_fn, top_n=10):
    """Same-game NRFI+F5 cards from floor-passing team legs (README §15.9 item 1).

    Thin wrapper over builder_core.same_game_pairs with the default sample gate,
    kept so main() stays thin and this wiring is unit-testable without a DB.
    """
    return same_game_pairs(team_legs, lift_fn, top_n=top_n)


def _same_game_lift_fn(engine):
    """Cached (side_nrfi, side_f5, nrfi_line, f5_line) -> (lift, n_games, both_n).
    Each distinct combo hits the box-score history once per run."""
    cache = {}

    def lift_fn(side_nrfi, side_f5, nrfi_line, f5_line):
        key = (side_nrfi, side_f5, nrfi_line, f5_line)
        if key not in cache:
            cache[key] = nrfi_f5_lift(engine, side_nrfi, side_f5, nrfi_line, f5_line)
        return cache[key]

    return lift_fn


def _run_same_game(engine, args, window_days):
    """The --same-game build: one lift-adjusted NRFI+F5 card per eligible game.

    Deliberately labelled EXCEPTION to the across-game-only guardrail (§15.8 #5).
    The printed payout is a NON-PLACEABLE reference — a book reprices or restricts
    correlated same-game legs — so the honest quantity is the lift-adjusted joint.
    """
    legs = load_team_legs(engine, args.floor, args.slate_date, args.sport, window_days)
    print(f"same-game team legs (favorite side, market prob >= {args.floor:.0%}): {len(legs)}")
    cards = build_same_game(legs, _same_game_lift_fn(engine), top_n=args.top_n)
    print(f"same-game cards (post-gate): {len(cards)}")
    for c in cards:
        warn = " [SMALL SAMPLE]" if c["small_sample"] else ""
        print(f"  ~{c['joint_prob']:.1%} joint  (lift x{c['lift']:.2f}, "
              f"n={c['lift_n']:,} games{warn})  ref payout {c['combined_odds']:.2f}x "
              f"— NOT a placeable same-game price")
        for leg in c["legs"]:
            print(f"      - {leg['label']} @ {leg['american_odds']:+d} "
                  f"(market {leg['market_prob']:.1%})")
    if args.save:
        saved = save_builds(engine, 0.0, cards, "same_game_pair", args.sport)
        print(f"parlay_recommendations (kind=builder, class=same_game_pair): "
              f"inserted {saved} rows")


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
    parser.add_argument("--min-start-rate", type=float, default=0.0,
                        help="drop player legs whose player appeared in fewer than "
                             "this fraction of their team's games over the last 21 days "
                             "(README §15.9 item 11). The chain builds hours before "
                             "lineups post, so rarely-used players void ~1 leg in 5. "
                             "0.0 = off (default); the daily chain passes 0.65. Team "
                             "legs and players with no measurable history are kept.")
    parser.add_argument("--require-confirmed-lineup", action="store_true",
                        help="MLB only: restrict player legs to players in a POSTED "
                             "lineup and to games not yet started; saves class "
                             "'confirmed_lineup' (README §15.9 item 11 Option B)")
    parser.add_argument("--same-game", action="store_true",
                        help="build the same-game NRFI+F5 combos class (README "
                             "§15.9 item 1): one lift-adjusted card per game that "
                             "has both markets clearing the floor. Pins no payout "
                             "axis (--target-payout/--min-prob are ignored). --save "
                             "writes class=\"same_game_pair\".")
    parser.add_argument("--sport", default="mlb",
                        help="which sport's candidate legs to build from "
                             "(default: mlb — the daily chain passes no --sport). "
                             "nfl builds player-only until the team tier lands (#3).")
    parser.add_argument("--save", action="store_true", help="persist to parlay_recommendations")
    args = parser.parse_args()

    if args.same_game and args.team_only:
        parser.error("--same-game and --team-only are mutually exclusive")

    # The confirmed-lineup card is the MIXED player+team build (load_legs), whose
    # team legs are still start-time filtered. Combining it with --team-only would
    # save a pure team card mislabeled class='confirmed_lineup' (the class check
    # below is evaluated first), and --same-game returns before the lineup fetch
    # runs, so the flag would be silently ignored. Both are rejected loudly.
    if args.require_confirmed_lineup and (args.team_only or args.same_game):
        parser.error("--require-confirmed-lineup is incompatible with --team-only "
                     "and --same-game")

    window_days = args.window_days if args.window_days is not None else SLATE_WINDOW_DAYS.get(args.sport, 0)

    engine = db.get_engine()

    # Same-game pins no payout axis (one card per game, not a search), so it
    # returns before the --target-payout/--min-prob requirement below.
    if args.same_game:
        _run_same_game(engine, args, window_days)
        return

    if args.target_payout is None and args.min_prob is None:
        parser.error("pin at least one axis: --target-payout and/or --min-prob")

    confirmed_ids = started_game_ids = None
    if args.require_confirmed_lineup:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        from ingestion.mlb_lineups import fetch_lineups
        now = datetime.now(timezone.utc)
        slate = args.slate_date or now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        confirmed_ids, start_times = fetch_lineups(slate)
        started_game_ids = {gid for gid, start in start_times.items() if start <= now}
        print(f"confirmed lineups: {len(confirmed_ids)} players, "
              f"{len(started_game_ids)} games already started")
        if not confirmed_ids:
            print("no posted lineups yet — nothing to build.")
            return

    if args.team_only:
        legs = load_team_legs(engine, args.floor, args.slate_date, args.sport, window_days,
                              started_game_ids)
    else:
        legs = load_legs(engine, args.floor, args.slate_date, args.sport, window_days,
                         args.min_start_rate, confirmed_ids, started_game_ids)
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
        if args.require_confirmed_lineup:
            parlay_class = "confirmed_lineup"
        elif args.team_only:
            parlay_class = _team_class(args.sport)
        else:
            parlay_class = "across_game"
        saved = save_builds(engine, args.target_payout or 0.0, results, parlay_class, args.sport)
        print(f"parlay_recommendations (kind=builder, class={parlay_class}): "
              f"inserted {saved} rows")


if __name__ == "__main__":
    main()
