"""Paper-trading ledger — the system's real report card (README §14.1).

The pipeline recommends parlays and flags edges but never recorded whether
they'd have won. This module settles both against actual results once their
games finish, writing one row per recommendation to `recommendation_outcomes`
(migration 004): `pnl` is 1-unit paper P&L at the odds *frozen when the bet
was recommended* (parlay legs' `odds` in the JSONB, or the prop_lines
snapshot in effect at `edges.created_at`) — this is a settlement ledger, not
a CLV measure (see modeling/clv.py for that).

Idempotent like clv.py: each (parlay_id) / (player, game, stat) settles once,
via a NOT EXISTS guard against recommendation_outcomes. Only games that are
`status='FT'` with the relevant `player_game_stats.value` present are
eligible; a parlay with any leg not yet ready is skipped whole and retried
on a later run.

Run daily after box scores finalize games, alongside modeling/clv.py (the
architect wires this into the com.playstat.mlb chain).
"""

import json

import pandas as pd
from sqlalchemy import text

from ingestion import db
from optimizer.parlay import DEFAULT_MIN_EDGE, american_to_decimal


def settle_leg(side, actual, line):
    """Score a single leg's actual value against its line. 'over'/'under' are
    mirrors of each other; a tie is always a push regardless of side.
    """
    if side == "over":
        if actual > line:
            return "hit"
        if actual < line:
            return "miss"
        return "push"
    if side == "under":
        if actual < line:
            return "hit"
        if actual > line:
            return "miss"
        return "push"
    raise ValueError(f"unknown side: {side!r}")


def leg_status(game_status, actual):
    """Classify a leg's settlement readiness from its game status and actual
    stat value (None/NaN when no stat row exists for this player/game/stat or
    team/game/market). Pure and DB-free — the decision used by settle_parlays,
    settle_team_parlays and settle_builder_parlays.

    - "pending": the game is not yet FT. Not an error — the whole parlay is
      skipped and retried on a later run.
    - "void": the game IS FT but there is no stat row for this leg (a
      scratched/DNP player prop, or a missing team-stat aggregate). Standard
      sportsbook rule: void the leg like a push rather than strand the
      parlay pending forever (README §15.10 KNOWN ISSUE / §15.9 item 6).
    - "ready": the game is FT and a stat value exists — settle_leg can score it.
    """
    if game_status != "FT":
        return "pending"
    if actual is None or actual != actual:  # NaN != NaN — no pandas import needed
        return "void"
    return "ready"


def parlay_result(leg_results, leg_decimal_odds, stake=1.0):
    """Combine per-leg 'hit'/'miss'/'push' results into a parlay outcome.

    Any miss loses the whole parlay (recorded combined odds are the product
    over every leg, pushed or not — informational only since pnl is fixed at
    -stake). Otherwise pushed legs are dropped and the combined decimal odds
    are recomputed over the remaining hit legs (standard sportsbook parlay
    push handling); if every leg pushed, the bet is a no-action push.
    """
    combined_over_all = 1.0
    for odds in leg_decimal_odds:
        combined_over_all *= odds

    if any(r == "miss" for r in leg_results):
        return "loss", combined_over_all, -stake

    hit_odds = [o for r, o in zip(leg_results, leg_decimal_odds) if r == "hit"]
    if not hit_odds:
        return "push", 1.0, 0.0

    recomputed = 1.0
    for odds in hit_odds:
        recomputed *= odds
    return "win", recomputed, stake * (recomputed - 1)


def single_pnl(result, decimal_odds, stake=1.0):
    if result == "win":
        return stake * (decimal_odds - 1)
    if result == "loss":
        return -stake
    if result == "push":
        return 0.0
    raise ValueError(f"unknown result: {result!r}")


def _as_legs_list(raw):
    """JSONB legs sometimes arrive pre-parsed (list/dict) and sometimes as a
    raw string depending on driver path — same defensive pattern as
    api/main.py's parlay-recommendations handler.
    """
    # dict covers the {"class", "legs": [...]} wrapper the team and builder
    # paths write: psycopg2 hands JSONB back already parsed, so json.loads would
    # be called on a dict and raise TypeError. Caught 2026-07-21, the first time
    # real builder rows existed — the team path shares this shape and would have
    # hit it too the moment it went live.
    if isinstance(raw, (list, dict)):
        return raw
    return json.loads(raw)


def _rec_snapshot(snaps, created_at):
    """The prop_lines snapshot in effect when a recommendation was made: the
    last pull at or before created_at, else the earliest pull available.
    Mirrors modeling/clv.py's rec_snap logic exactly.
    """
    at_flag = snaps[snaps["pulled_at"] <= created_at]
    return at_flag.iloc[-1] if not at_flag.empty else snaps.iloc[0]


def settle_parlays(engine):
    # Dedupe note (README §15 Task 2, 2026-07-21): this NOT EXISTS guard checks
    # bet_type='parlay' only — the same DB value settle_team_parlays() and
    # settle_builder_parlays() write, since the recommendation_outcomes.bet_type
    # CHECK constraint only allows ('parlay','edge'). This cannot cause a
    # cross-kind collision: parlay_recommendations.parlay_id is one SERIAL
    # PRIMARY KEY shared across kind='player'/'team'/'builder' rows (never
    # reused), and this query's own `pr.kind = 'player'` filter means only
    # kind='player' parlay_ids ever reach the guard. So a builder or team
    # parlay_id can never appear as a candidate here, and the guard's NOT
    # EXISTS still correctly stops any given parlay_id (of any kind) from
    # being settled twice by whichever function already settled it.
    with engine.begin() as conn:
        candidates = conn.execute(
            text(
                """
                SELECT pr.parlay_id, pr.created_at, pr.legs
                FROM parlay_recommendations pr
                WHERE pr.kind = 'player' AND NOT EXISTS (
                    SELECT 1 FROM recommendation_outcomes ro
                    WHERE ro.bet_type = 'parlay' AND ro.parlay_id = pr.parlay_id
                )
                """
            )
        ).fetchall()

        if not candidates:
            print("settle: no new parlays to evaluate.")
            return 0

        parlays = [(pid, created_at, _as_legs_list(legs_raw)) for pid, created_at, legs_raw in candidates]
        game_ids = sorted({int(leg["game_id"]) for _, _, legs in parlays for leg in legs})

        games = pd.read_sql(
            text("SELECT game_id, status FROM games WHERE game_id = ANY(:gids)"),
            conn, params={"gids": game_ids},
        )
        stats = pd.read_sql(
            text(
                """
                SELECT player_id, game_id, stat_type, value
                FROM player_game_stats WHERE game_id = ANY(:gids)
                """
            ),
            conn, params={"gids": game_ids},
        )
        lines = pd.read_sql(
            text(
                """
                SELECT player_id, game_id, stat_type, line_value, pulled_at
                FROM prop_lines WHERE game_id = ANY(:gids)
                ORDER BY pulled_at
                """
            ),
            conn, params={"gids": game_ids},
        )

    game_status = dict(zip(games["game_id"], games["status"]))
    stats_lookup = {(r.player_id, r.game_id, r.stat_type): r.value for r in stats.itertuples()}
    lines_grouped = lines.groupby(["player_id", "game_id", "stat_type"])

    rows_inserted = 0
    with engine.begin() as conn:
        for parlay_id, created_at, legs in parlays:
            leg_results, leg_decimal_odds, leg_audit = [], [], []
            ready = True

            for leg in legs:
                player_id, game_id, stat_type = int(leg["player_id"]), int(leg["game_id"]), leg["stat_type"]
                side, odds = leg["side"], leg["odds"]

                gstatus = game_status.get(game_id)
                actual = stats_lookup.get((player_id, game_id, stat_type))
                state = leg_status(gstatus, actual)
                if state == "pending":
                    ready = False
                    break
                if state == "void":
                    # FT game, no player_game_stats row -> scratched/DNP. Void
                    # like a push: no line_value needed, settle on the rest.
                    leg_results.append("void")
                    leg_decimal_odds.append(american_to_decimal(odds))
                    leg_audit.append(
                        {
                            "player_id": player_id, "game_id": game_id, "stat_type": stat_type,
                            "side": side, "odds": int(odds), "result": "void", "dnp": True,
                        }
                    )
                    continue
                try:
                    snaps = lines_grouped.get_group((player_id, game_id, stat_type))
                except KeyError:
                    ready = False
                    break
                rec_snap = _rec_snapshot(snaps, created_at)
                line_value = rec_snap["line_value"]
                if line_value is None or pd.isna(line_value):
                    ready = False
                    break

                leg_result = settle_leg(side, float(actual), float(line_value))
                decimal_odds = american_to_decimal(odds)
                leg_results.append(leg_result)
                leg_decimal_odds.append(decimal_odds)
                leg_audit.append(
                    {
                        "player_id": player_id, "game_id": game_id, "stat_type": stat_type,
                        "side": side, "line": float(line_value), "odds": int(odds),
                        "actual": float(actual), "result": leg_result,
                    }
                )

            if not ready:
                continue  # a leg's game/stats/line isn't ready yet — retry next run

            result, decimal_odds, pnl = parlay_result(leg_results, leg_decimal_odds)
            conn.execute(
                text(
                    """
                    INSERT INTO recommendation_outcomes
                        (bet_type, parlay_id, result, n_legs, stake, decimal_odds, pnl, legs, recommended_at)
                    VALUES ('parlay', :parlay_id, :result, :n_legs, 1, :decimal_odds, :pnl, CAST(:legs AS JSONB), :recommended_at)
                    """
                ),
                {
                    "parlay_id": int(parlay_id),
                    "result": result,
                    "n_legs": len(legs),
                    "decimal_odds": float(decimal_odds),
                    "pnl": float(pnl),
                    "legs": json.dumps(leg_audit),
                    "recommended_at": created_at,
                },
            )
            rows_inserted += 1

    print(f"settle: settled {rows_inserted} new parlays ({len(parlays) - rows_inserted} not yet ready)")
    return rows_inserted


MARKET_TO_STAT = {"first_inning_runs": "runs_inning_1", "f5_runs": "runs_f5"}


def team_leg_actual(totals, game_id, market):
    """Game total for a team market: totals maps (game_id, market) -> summed runs."""
    return totals.get((game_id, market))


def settle_team_parlays(engine):
    # Dedupe note: see settle_parlays() above — same guard shape, same
    # reasoning. `pr.kind = 'team'` here means only team parlay_ids are ever
    # candidates, so this can't collide with the player or builder paths.
    with engine.begin() as conn:
        candidates = conn.execute(
            text(
                """
                SELECT pr.parlay_id, pr.created_at, pr.legs
                FROM parlay_recommendations pr
                WHERE pr.kind = 'team' AND NOT EXISTS (
                    SELECT 1 FROM recommendation_outcomes ro
                    WHERE ro.bet_type = 'parlay' AND ro.parlay_id = pr.parlay_id)
                """
            )
        ).fetchall()
        if not candidates:
            print("settle: no new team parlays to evaluate.")
            return 0

        parsed = [(pid, ca, _as_legs_list(raw)) for pid, ca, raw in candidates]
        # team legs live under a {"class","ev","legs":[...]} wrapper
        parsed = [(pid, ca, blob["legs"] if isinstance(blob, dict) else blob) for pid, ca, blob in parsed]
        game_ids = sorted({int(l["game_id"]) for _, _, legs in parsed for l in legs})

        games = pd.read_sql(text("SELECT game_id, status FROM games WHERE game_id = ANY(:g)"),
                            conn, params={"g": game_ids})
        team_totals = pd.read_sql(
            text(
                """
                SELECT game_id, stat_type, SUM(value) AS total
                FROM team_game_stats
                WHERE game_id = ANY(:g) AND stat_type IN ('runs_inning_1','runs_f5')
                GROUP BY game_id, stat_type
                """
            ),
            conn, params={"g": game_ids})
        lines = pd.read_sql(
            text(
                """
                SELECT game_id, market, line_value, pulled_at
                FROM game_lines WHERE game_id = ANY(:g) ORDER BY pulled_at
                """
            ),
            conn, params={"g": game_ids})

    status = dict(zip(games["game_id"], games["status"]))
    totals = {(int(r.game_id), {"runs_inning_1": "first_inning_runs", "runs_f5": "f5_runs"}[r.stat_type]):
              float(r.total) for r in team_totals.itertuples()}
    lines_grp = lines.groupby(["game_id", "market"])

    inserted = 0
    with engine.begin() as conn:
        for parlay_id, created_at, legs in parsed:
            results, odds_list, audit, ready = [], [], [], True
            for leg in legs:
                gid, market, side, odds = int(leg["game_id"]), leg["market"], leg["side"], leg["odds"]
                gstatus = status.get(gid)
                actual = team_leg_actual(totals, gid, market)
                state = leg_status(gstatus, actual)
                if state == "pending":
                    ready = False; break
                if state == "void":
                    # FT game, no team_game_stats aggregate -> void like a push
                    # rather than strand the parlay pending forever.
                    results.append("void")
                    odds_list.append(american_to_decimal(odds))
                    audit.append({"game_id": gid, "market": market, "side": side,
                                  "odds": int(odds), "result": "void", "dnp": True})
                    continue
                try:
                    snaps = lines_grp.get_group((gid, market))
                except KeyError:
                    ready = False; break
                rec_snap = _rec_snapshot(snaps, created_at)
                line_value = rec_snap["line_value"]
                if line_value is None or pd.isna(line_value):
                    ready = False; break
                res = settle_leg(side, float(actual), float(line_value))
                results.append(res)
                odds_list.append(american_to_decimal(odds))
                audit.append({"game_id": gid, "market": market, "side": side,
                              "line": float(line_value), "odds": int(odds),
                              "actual": float(actual), "result": res})
            if not ready:
                continue
            result, decimal_odds, pnl = parlay_result(results, odds_list)
            conn.execute(
                text(
                    """
                    INSERT INTO recommendation_outcomes
                        (bet_type, parlay_id, result, n_legs, stake, decimal_odds, pnl, legs, recommended_at)
                    VALUES ('parlay', :pid, :res, :n, 1, :co, :pnl, CAST(:legs AS JSONB), :ra)
                    """
                ),
                {"pid": int(parlay_id), "res": result, "n": len(legs),
                 "co": float(decimal_odds), "pnl": float(pnl),
                 "legs": json.dumps(audit), "ra": created_at})
            inserted += 1
    print(f"settle: settled {inserted} new team parlays ({len(parsed) - inserted} not yet ready)")
    return inserted


def builder_leg_key(leg):
    """Lookup key for a builder leg, dispatched on its kind.

    Builder parlays mix player-prop and team-market legs in one bet, so each leg
    must resolve against a different source table — unlike settle_parlays /
    settle_team_parlays, which each assume a homogeneous leg list.
    """
    kind = leg.get("kind")
    if kind == "player":
        return ("player", int(leg["player_id"]), int(leg["game_id"]), leg["stat_type"])
    if kind == "team":
        return ("team", int(leg["game_id"]), leg["market"])
    raise ValueError(f"unknown builder leg kind: {kind!r}")


def settle_builder_parlays(engine):
    # Dedupe note: see settle_parlays() above — same guard shape, same
    # reasoning. `pr.kind = 'builder'` here means only builder parlay_ids are
    # ever candidates, so this can't collide with the player or team paths.
    with engine.begin() as conn:
        candidates = conn.execute(
            text(
                """
                SELECT pr.parlay_id, pr.created_at, pr.legs
                FROM parlay_recommendations pr
                WHERE pr.kind = 'builder' AND NOT EXISTS (
                    SELECT 1 FROM recommendation_outcomes ro
                    WHERE ro.bet_type = 'parlay' AND ro.parlay_id = pr.parlay_id)
                """
            )
        ).fetchall()
        if not candidates:
            print("settle: no new builder parlays to evaluate.")
            return 0

        parsed = [(pid, ca, _as_legs_list(raw)) for pid, ca, raw in candidates]
        parsed = [(pid, ca, blob["legs"] if isinstance(blob, dict) else blob)
                  for pid, ca, blob in parsed]
        game_ids = sorted({int(l["game_id"]) for _, _, legs in parsed for l in legs})

        games = pd.read_sql(text("SELECT game_id, status FROM games WHERE game_id = ANY(:g)"),
                            conn, params={"g": game_ids})
        pstats = pd.read_sql(
            text("""SELECT player_id, game_id, stat_type, value
                    FROM player_game_stats WHERE game_id = ANY(:g)"""),
            conn, params={"g": game_ids})
        tstats = pd.read_sql(
            text("""SELECT game_id, stat_type, SUM(value) AS total
                    FROM team_game_stats
                    WHERE game_id = ANY(:g) AND stat_type IN ('runs_inning_1','runs_f5')
                    GROUP BY game_id, stat_type"""),
            conn, params={"g": game_ids})
        plines = pd.read_sql(
            text("""SELECT player_id, game_id, stat_type, line_value, pulled_at
                    FROM prop_lines WHERE game_id = ANY(:g) ORDER BY pulled_at"""),
            conn, params={"g": game_ids})
        glines = pd.read_sql(
            text("""SELECT game_id, market, line_value, pulled_at
                    FROM game_lines WHERE game_id = ANY(:g) ORDER BY pulled_at"""),
            conn, params={"g": game_ids})

    status = dict(zip(games["game_id"], games["status"]))
    pstats_lookup = {(r.player_id, r.game_id, r.stat_type): r.value for r in pstats.itertuples()}
    stat_to_market = {"runs_inning_1": "first_inning_runs", "runs_f5": "f5_runs"}
    tstats_lookup = {(int(r.game_id), stat_to_market[r.stat_type]): float(r.total)
                     for r in tstats.itertuples()}
    plines_grp = plines.groupby(["player_id", "game_id", "stat_type"])
    glines_grp = glines.groupby(["game_id", "market"])

    inserted = 0
    with engine.begin() as conn:
        for parlay_id, created_at, legs in parsed:
            results, odds_list, audit, ready = [], [], [], True
            for leg in legs:
                gid = int(leg["game_id"])
                gstatus = status.get(gid)

                key = builder_leg_key(leg)
                if key[0] == "player":
                    _, pid, _, stat_type = key
                    actual = pstats_lookup.get((pid, gid, stat_type))
                    audit_id = {"player_id": pid, "stat_type": stat_type}
                else:
                    _, _, market = key
                    actual = tstats_lookup.get((gid, market))
                    audit_id = {"market": market}

                state = leg_status(gstatus, actual)
                if state == "pending":
                    ready = False; break
                if state == "void":
                    # FT game, no stat row on either the player or team side
                    # (DNP/scratched player, or a missing team-stat
                    # aggregate) -> void like a push, no line_value needed.
                    results.append("void")
                    odds_list.append(american_to_decimal(leg["odds"]))
                    audit.append({**audit_id, "kind": leg["kind"], "game_id": gid,
                                  "side": leg["side"], "odds": int(leg["odds"]),
                                  "result": "void", "dnp": True})
                    continue

                if key[0] == "player":
                    try:
                        snaps = plines_grp.get_group((pid, gid, stat_type))
                    except KeyError:
                        ready = False; break
                else:
                    try:
                        snaps = glines_grp.get_group((gid, market))
                    except KeyError:
                        ready = False; break

                line_value = _rec_snapshot(snaps, created_at)["line_value"]
                if line_value is None or pd.isna(line_value):
                    ready = False; break

                res = settle_leg(leg["side"], float(actual), float(line_value))
                results.append(res)
                odds_list.append(american_to_decimal(leg["odds"]))
                audit.append({**audit_id, "kind": leg["kind"], "game_id": gid,
                              "side": leg["side"], "line": float(line_value),
                              "odds": int(leg["odds"]), "actual": float(actual),
                              "result": res})
            if not ready:
                continue
            result, decimal_odds, pnl = parlay_result(results, odds_list)
            conn.execute(
                text(
                    """
                    INSERT INTO recommendation_outcomes
                        (bet_type, parlay_id, result, n_legs, stake, decimal_odds, pnl, legs, recommended_at)
                    VALUES ('parlay', :pid, :res, :n, 1, :co, :pnl, CAST(:legs AS JSONB), :ra)
                    """
                ),
                {"pid": int(parlay_id), "res": result, "n": len(legs),
                 "co": float(decimal_odds), "pnl": float(pnl),
                 "legs": json.dumps(audit), "ra": created_at})
            inserted += 1
    print(f"settle: settled {inserted} new builder parlays ({len(parsed) - inserted} not yet ready)")
    return inserted


def settle_edges(engine, min_edge=DEFAULT_MIN_EDGE):
    with engine.begin() as conn:
        candidates = pd.read_sql(
            text(
                """
                SELECT e.player_id, e.game_id, e.stat_type, e.side, e.created_at, pgs.value AS actual
                FROM edges e
                JOIN games g ON g.game_id = e.game_id
                JOIN player_game_stats pgs
                    ON pgs.player_id = e.player_id AND pgs.game_id = e.game_id AND pgs.stat_type = e.stat_type
                WHERE e.edge > :min_edge AND g.status = 'FT'
                  AND NOT EXISTS (
                      SELECT 1 FROM recommendation_outcomes ro
                      WHERE ro.bet_type = 'edge'
                        AND ro.player_id = e.player_id AND ro.game_id = e.game_id AND ro.stat_type = e.stat_type
                  )
                """
            ),
            conn, params={"min_edge": min_edge},
        )
        if candidates.empty:
            print("settle: no new edges to evaluate.")
            return 0

        lines = pd.read_sql(
            text(
                """
                SELECT player_id, game_id, stat_type, line_value, over_odds, under_odds, pulled_at
                FROM prop_lines
                WHERE game_id = ANY(:gids)
                ORDER BY pulled_at
                """
            ),
            conn, params={"gids": [int(g) for g in candidates["game_id"].unique()]},
        )

    lines_grouped = lines.groupby(["player_id", "game_id", "stat_type"])

    rows_inserted = 0
    with engine.begin() as conn:
        for rec in candidates.to_dict("records"):
            key = (rec["player_id"], rec["game_id"], rec["stat_type"])
            try:
                snaps = lines_grouped.get_group(key)
            except KeyError:
                continue  # no prop_lines snapshot at all for this leg — not settle-ready

            rec_snap = _rec_snapshot(snaps, rec["created_at"])
            line_value = rec_snap["line_value"]
            odds = rec_snap["over_odds"] if rec["side"] == "over" else rec_snap["under_odds"]
            if line_value is None or pd.isna(line_value) or odds is None or pd.isna(odds):
                continue

            leg_result = settle_leg(rec["side"], float(rec["actual"]), float(line_value))
            result = {"hit": "win", "miss": "loss", "push": "push"}[leg_result]
            decimal_odds = american_to_decimal(odds)
            pnl = single_pnl(result, decimal_odds)

            leg_audit = [
                {
                    "player_id": int(rec["player_id"]), "game_id": int(rec["game_id"]),
                    "stat_type": rec["stat_type"], "side": rec["side"],
                    "line": float(line_value), "odds": int(odds),
                    "actual": float(rec["actual"]), "result": leg_result,
                }
            ]

            conn.execute(
                text(
                    """
                    INSERT INTO recommendation_outcomes
                        (bet_type, player_id, game_id, stat_type, side, result, n_legs, stake,
                         decimal_odds, pnl, legs, recommended_at)
                    VALUES ('edge', :player_id, :game_id, :stat_type, :side, :result, 1, 1,
                            :decimal_odds, :pnl, CAST(:legs AS JSONB), :recommended_at)
                    """
                ),
                {
                    "player_id": int(rec["player_id"]),
                    "game_id": int(rec["game_id"]),
                    "stat_type": rec["stat_type"],
                    "side": rec["side"],
                    "result": result,
                    "decimal_odds": float(decimal_odds),
                    "pnl": float(pnl),
                    "legs": json.dumps(leg_audit),
                    "recommended_at": rec["created_at"],
                },
            )
            rows_inserted += 1

    print(f"settle: settled {rows_inserted} new edges")
    return rows_inserted


# The bet_type CHECK constraint pins every parlay row's DB value at 'parlay'
# regardless of which recommender produced it (README §15 Task 2, 2026-07-21):
# no migration, no constraint change. The builder's first settlements were
# about to land in the same 'parlay' bucket as the legacy model-ranked
# parlays (16-48-0, -57% ROI over 64 bets) and the team-parlay path, making
# all three unreadable as one pooled record. The split happens entirely in
# the read path via parlay_recommendations.kind, which every parlay_id FKs to.
PARLAY_KIND_LABEL = {"player": "parlay_model", "team": "parlay_team", "builder": "parlay_builder"}


def bet_type_label(bet_type, kind):
    """Map a recommendation_outcomes row's bet_type plus its source
    parlay_recommendations.kind (None for 'edge' rows, which carry no
    parlay_id) to the reporting bucket used by both print_summary and the
    /bet-performance API. 'parlay' is the defensive fallback for a kind this
    map doesn't recognize (or a NULL kind on a 'parlay' row, which would mean
    a broken FK — should not happen, but better a labeled bucket than a
    silently dropped row).
    """
    if bet_type != "parlay":
        return bet_type
    return PARLAY_KIND_LABEL.get(kind, "parlay")


def aggregate_bet_performance(rows):
    """Pure aggregation over (bet_type, kind, n, wins, losses, pushes, staked,
    pnl) rows — one row per (bet_type, kind) group, as produced by a
    `GROUP BY ro.bet_type, pr.kind` query joined against
    parlay_recommendations. Buckets each row via bet_type_label and appends a
    combined 'all' row last. Returns a list of
    (label, n, wins, losses, pushes, staked, pnl) tuples, sorted by label
    with 'all' always last.

    DB-free by design: the SQL side only has to GROUP BY and hand back raw
    per-kind sums: everything after that is pure Python and unit-testable
    without a database.
    """
    buckets = {}
    order = []
    for bet_type, kind, n, wins, losses, pushes, staked, pnl in rows:
        label = bet_type_label(bet_type, kind)
        staked, pnl = float(staked or 0), float(pnl or 0)
        if label not in buckets:
            buckets[label] = {"n": 0, "wins": 0, "losses": 0, "pushes": 0,
                               "staked": 0.0, "pnl": 0.0}
            order.append(label)
        agg = buckets[label]
        agg["n"] += n
        agg["wins"] += wins
        agg["losses"] += losses
        agg["pushes"] += pushes
        agg["staked"] += staked
        agg["pnl"] += pnl

    results = [(label, *buckets[label].values()) for label in sorted(order)]
    if results:
        total = {"n": 0, "wins": 0, "losses": 0, "pushes": 0, "staked": 0.0, "pnl": 0.0}
        for label in order:
            for key in total:
                total[key] += buckets[label][key]
        results.append(("all", *total.values()))
    return results


def print_summary(engine):
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ro.bet_type, pr.kind,
                       COUNT(*) AS n,
                       SUM((ro.result = 'win')::int) AS wins,
                       SUM((ro.result = 'loss')::int) AS losses,
                       SUM((ro.result = 'push')::int) AS pushes,
                       SUM(ro.stake) AS total_staked,
                       SUM(ro.pnl) AS total_pnl
                FROM recommendation_outcomes ro
                LEFT JOIN parlay_recommendations pr ON pr.parlay_id = ro.parlay_id
                GROUP BY ro.bet_type, pr.kind
                """
            )
        ).fetchall()

    if not rows:
        print("settle (all-time): no settled bets yet.")
        return

    for label, n, wins, losses, pushes, staked, pnl in aggregate_bet_performance(rows):
        roi = pnl / staked if staked else 0.0
        print(f"settle (all-time, {label}): {wins}-{losses}-{pushes} W-L-P, P&L {pnl:+.2f}u, ROI {roi:+.1%}")


def settle(engine):
    total = 0
    total += settle_parlays(engine)
    total += settle_team_parlays(engine)
    total += settle_builder_parlays(engine)
    total += settle_edges(engine)
    print_summary(engine)
    return total


if __name__ == "__main__":
    settle(db.get_engine())
