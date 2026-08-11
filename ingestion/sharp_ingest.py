"""Sharp-reference (Pinnacle) close snapshots via The Odds API.

README §15.9 item 14e / spec 2026-08-11-sharp-reference-snapshot-design.md.

BUDGET IS THE DESIGN CONSTRAINT: the free tier meters 500 credits/month.
The /events list is free; a slate-wide h2h+totals pull costs 2 credits; each
per-event market costs 1 credit per market actually returned (requesting an
unoffered market bills nothing). So this job pulls per-event markets ONLY for
games that appear on TODAY'S saved builder cards, keeping a full trial day at
~35 credits. `--budget-guard` (default 60) skips the run entirely when the
remaining budget is below the floor, so month-end never zeroes the meter.

Matching is exact-or-skipped, never guessed: events map to our games by
normalized team names + ET date; prop outcomes map to players by normalized
name. Unmatched anything is logged and counted, and the row is dropped.

Feeds ONLY optimizer/sharp_compare.py (a CLI report). No API, no dashboard,
no builder input — §15.8 #2 by construction.
"""

import argparse
import json
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

THEODDS_SPORT = {"mlb": "baseball_mlb"}
EASTERN = ZoneInfo("America/New_York")

# Our market vocabulary -> The Odds API market keys. Prop keys beyond the
# verified batter_home_runs are included speculatively: an unoffered market
# returns nothing and bills nothing, so coverage decides, not us.
MARKET_MAP = {
    "home_runs": "batter_home_runs",
    "stolen_bases": "batter_stolen_bases",
    "hits": "batter_hits",
    "rbis": "batter_rbis",
    "runs": "batter_runs_scored",
    "walks": "batter_walks",
    "total_bases": "batter_total_bases",
    "batter_strikeouts": "batter_strikeouts",
    "pitcher_strikeouts": "pitcher_strikeouts",
    "first_inning_runs": "totals_1st_1_innings",
    "f5_runs": "totals_1st_5_innings",
}
REVERSE_MARKET_MAP = {v: k for k, v in MARKET_MAP.items()}
# Slate-wide context markets, one cheap pull for every matched game.
MAINLINE_MAP = {"h2h": "moneyline", "totals": "total_runs"}

# The Odds API team names -> ours, applied AFTER normalize_name. Found live
# 2026-08-11: they say "Athletics", our teams row says "Oakland Athletics",
# which silently unmatched every A's game.
TEAM_ALIASES = {"athletics": "oakland athletics"}


def normalize_name(name):
    """Accent-, case-, dot- and whitespace-insensitive name key.

    "Albert Suárez" == "Albert Suarez"; "J.T. Brubaker" == "JT Brubaker".
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.replace(".", "").casefold().split())


def event_et_date(commence_time):
    """The Odds API commence_time (UTC ISO-8601, 'Z' suffix) -> ET date."""
    dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    return dt.astimezone(EASTERN).date()


def match_events(events, slate_games):
    """{our game_id: event} by normalized home+away team names + ET date.

    slate_games: iterable of (game_id, date, home_name, away_name).
    Unmatched on EITHER side is skipped, never guessed. A DOUBLEHEADER makes
    (home, away, date) ambiguous on both sides — those keys are dropped
    entirely, because attaching game 1's odds to game 2 would be a silent
    wrong-bet comparison.
    """
    by_key, seen = {}, set()
    for gid, d, h, a in slate_games:
        key = (normalize_name(h), normalize_name(a), d)
        if key in seen:
            by_key.pop(key, None)  # doubleheader: ambiguous, drop
            continue
        seen.add(key)
        by_key[key] = gid
    matched = {}
    for event in events:
        home = normalize_name(event.get("home_team"))
        away = normalize_name(event.get("away_team"))
        key = (TEAM_ALIASES.get(home, home), TEAM_ALIASES.get(away, away),
               event_et_date(event.get("commence_time", "1970-01-01T00:00:00Z")))
        game_id = by_key.get(key)
        if game_id is not None and game_id not in matched:
            matched[game_id] = event
    return matched


def _pair_outcomes(outcomes):
    """Group Over/Under outcomes by (player description, point).

    Returns {(description_or_None, point): {"over": odds, "under": odds}}.
    A missing side leaves None — the caller drops one-sided pairs (they cannot
    be de-vigged; same rule as line_movement.py).
    """
    pairs = defaultdict(lambda: {"over": None, "under": None})
    for outcome in outcomes or []:
        side = (outcome.get("name") or "").casefold()
        if side not in ("over", "under"):
            continue
        key = (outcome.get("description"), outcome.get("point"))
        pairs[key][side] = outcome.get("price")
    return pairs


def event_sharp_rows(event, game_id, players_by_name, books=("pinnacle",)):
    """(rows, skipped_players) for one event's bookmaker payload.

    rows are sharp_lines dicts in OUR vocabulary. Player props whose
    description doesn't match a known player are counted, not guessed.
    """
    rows, skipped = [], []
    for bookmaker in event.get("bookmakers", []):
        book = bookmaker.get("key")
        if book not in books:
            continue
        for market in bookmaker.get("markets", []):
            their_key = market.get("key")
            if their_key == "h2h":
                home = away = None
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") == event.get("home_team"):
                        home = outcome.get("price")
                    elif outcome.get("name") == event.get("away_team"):
                        away = outcome.get("price")
                if home is not None and away is not None:
                    rows.append({"game_id": game_id, "player_id": None,
                                 "market": "moneyline", "line_value": None,
                                 "book": book, "over_odds": None,
                                 "under_odds": None, "home_odds": int(home),
                                 "away_odds": int(away)})
                continue

            our_market = MAINLINE_MAP.get(their_key) or REVERSE_MARKET_MAP.get(their_key)
            if our_market is None or our_market == "moneyline":
                continue
            for (description, point), sides in _pair_outcomes(
                    market.get("outcomes")).items():
                if sides["over"] is None or sides["under"] is None:
                    continue  # one-sided: cannot be de-vigged
                player_id = None
                if description is not None:  # player prop
                    player_id = players_by_name.get(normalize_name(description))
                    if player_id is None:
                        skipped.append(description)
                        continue
                rows.append({"game_id": game_id, "player_id": player_id,
                             "market": our_market,
                             "line_value": float(point) if point is not None else None,
                             "book": book, "over_odds": int(sides["over"]),
                             "under_odds": int(sides["under"]),
                             "home_odds": None, "away_odds": None})
    return rows, skipped


def card_markets_by_game(card_wrappers):
    """{game_id: set of our-market names} across today's saved card legs."""
    needed = defaultdict(set)
    for wrapper in card_wrappers:
        if isinstance(wrapper, str):
            wrapper = json.loads(wrapper)
        for leg in (wrapper or {}).get("legs", []):
            market = leg.get("stat_type") or leg.get("market")
            game_id = leg.get("game_id")
            if game_id is not None and market in MARKET_MAP:
                needed[game_id].add(market)
    return needed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sport", default="mlb", choices=sorted(THEODDS_SPORT))
    parser.add_argument("--dry-run", action="store_true",
                        help="pull and report, insert nothing")
    parser.add_argument("--budget-guard", type=int, default=60,
                        help="skip the run if x-requests-remaining is below this")
    parser.add_argument("--books", default="pinnacle",
                        help="comma-separated bookmaker keys to keep")
    args = parser.parse_args()

    from sqlalchemy import text

    from ingestion.db import get_engine
    from ingestion.theodds_client import TheOddsClient

    books = tuple(b.strip() for b in args.books.split(",") if b.strip())
    sport_key = THEODDS_SPORT[args.sport]
    engine = get_engine()

    with engine.begin() as conn:
        slate_games = conn.execute(text(
            """
            SELECT g.game_id, g.date, th.name AS home, ta.name AS away
            FROM games g
            JOIN teams th ON th.team_id = g.home_team_id
            JOIN teams ta ON ta.team_id = g.away_team_id
            WHERE g.sport = :sport AND g.date = CURRENT_DATE
            """
        ), {"sport": args.sport}).fetchall()
        card_wrappers = [r[0] for r in conn.execute(text(
            """
            SELECT legs FROM parlay_recommendations
            WHERE kind = 'builder'
              AND COALESCE(legs->>'sport', 'mlb') = :sport
              AND created_at::date = CURRENT_DATE
            """
        ), {"sport": args.sport}).fetchall()]
        # A normalized name shared by two players (the two Will Smiths
        # problem) is ambiguous and DROPPED — mis-assigning a player_id would
        # silently compare the wrong player's prop. Same rule as doubleheaders.
        players_by_name, ambiguous = {}, set()
        for pid, name in conn.execute(text(
                "SELECT player_id, name FROM players WHERE sport = :sport"
        ), {"sport": args.sport}).fetchall():
            key = normalize_name(name)
            if key in players_by_name and players_by_name[key] != pid:
                ambiguous.add(key)
            players_by_name[key] = pid
        for key in ambiguous:
            del players_by_name[key]

    needed = card_markets_by_game(card_wrappers)
    print(f"slate games {len(slate_games)}, card games {len(needed)}, "
          f"card markets {sorted({m for ms in needed.values() for m in ms})}")

    client = TheOddsClient()
    events = client.get(f"/sports/{sport_key}/events/")  # free
    remaining = int(client.last_headers.get("x-requests-remaining", "0"))
    if remaining < args.budget_guard:
        print(f"BUDGET GUARD: {remaining} credits remaining < "
              f"{args.budget_guard} floor — skipping (not an error)")
        return 0
    matched = match_events(events, slate_games)
    # In-play prices are NOT a close snapshot: The Odds API keeps serving odds
    # after first pitch, so commenced games are dropped — the same rationale as
    # the SGO pull's --not-before-now. At 19:45 ET this leaves the late games,
    # which is exactly the coverage the SGO close snapshot has.
    now_utc = datetime.now(timezone.utc)
    commenced = [gid for gid, event in matched.items()
                 if datetime.fromisoformat(
                     event["commence_time"].replace("Z", "+00:00")) <= now_utc]
    for gid in commenced:
        del matched[gid]
    print(f"events {len(events)}, matched to games "
          f"{len(matched)} (+{len(commenced)} commenced, dropped), "
          f"credits remaining {remaining}")

    all_rows, all_skipped = [], []

    # One slate-wide mainlines pull (2 credits): ML/totals anchor for every
    # matched game, card or not.
    event_by_id = {event.get("id"): gid for gid, event in matched.items()}
    mainlines = client.get(
        f"/sports/{sport_key}/odds/",
        params={"regions": "eu", "markets": "h2h,totals",
                "oddsFormat": "american"})
    for event in mainlines:
        game_id = event_by_id.get(event.get("id"))
        if game_id is None:
            continue
        rows, skipped = event_sharp_rows(event, game_id, players_by_name, books)
        all_rows.extend(rows)
        all_skipped.extend(skipped)

    # Per-event pulls, CARD GAMES ONLY, card markets only.
    for game_id, markets in sorted(needed.items()):
        event = matched.get(game_id)
        if event is None:
            print(f"  card game {game_id}: no matched event — skipped")
            continue
        their_markets = ",".join(sorted(MARKET_MAP[m] for m in markets))
        payload = client.get(
            f"/sports/{sport_key}/events/{event['id']}/odds/",
            params={"regions": "eu", "markets": their_markets,
                    "oddsFormat": "american"})
        rows, skipped = event_sharp_rows(payload, game_id, players_by_name, books)
        all_rows.extend(rows)
        all_skipped.extend(skipped)

    used = client.last_headers.get("x-requests-used", "?")
    remaining = client.last_headers.get("x-requests-remaining", "?")
    by_market = defaultdict(int)
    for row in all_rows:
        by_market[row["market"]] += 1
    print(f"rows {len(all_rows)} {dict(sorted(by_market.items()))}; "
          f"unmatched players {len(set(all_skipped))} "
          f"{sorted(set(all_skipped))[:5]}; "
          f"credits used {used}, remaining {remaining}")

    if args.dry_run:
        print("DRY RUN — nothing inserted")
        return 0

    if all_rows:
        with engine.begin() as conn:
            conn.execute(text(
                """
                INSERT INTO sharp_lines
                    (game_id, player_id, market, line_value, book,
                     over_odds, under_odds, home_odds, away_odds)
                VALUES
                    (:game_id, :player_id, :market, :line_value, :book,
                     :over_odds, :under_odds, :home_odds, :away_odds)
                """
            ), all_rows)
        print(f"inserted {len(all_rows)} sharp_lines rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
