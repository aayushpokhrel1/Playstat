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
    return raw if isinstance(raw, list) else json.loads(raw)


def _rec_snapshot(snaps, created_at):
    """The prop_lines snapshot in effect when a recommendation was made: the
    last pull at or before created_at, else the earliest pull available.
    Mirrors modeling/clv.py's rec_snap logic exactly.
    """
    at_flag = snaps[snaps["pulled_at"] <= created_at]
    return at_flag.iloc[-1] if not at_flag.empty else snaps.iloc[0]


def settle_parlays(engine):
    with engine.begin() as conn:
        candidates = conn.execute(
            text(
                """
                SELECT pr.parlay_id, pr.created_at, pr.legs
                FROM parlay_recommendations pr
                WHERE NOT EXISTS (
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

                if game_status.get(game_id) != "FT":
                    ready = False
                    break
                actual = stats_lookup.get((player_id, game_id, stat_type))
                if actual is None or pd.isna(actual):
                    ready = False
                    break
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


def print_summary(engine):
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT bet_type,
                       SUM((result = 'win')::int) AS wins,
                       SUM((result = 'loss')::int) AS losses,
                       SUM((result = 'push')::int) AS pushes,
                       SUM(stake) AS total_staked,
                       SUM(pnl) AS total_pnl
                FROM recommendation_outcomes
                GROUP BY bet_type
                ORDER BY bet_type
                """
            )
        ).fetchall()

    if not rows:
        print("settle (all-time): no settled bets yet.")
        return

    total_w = total_l = total_p = 0
    total_staked = 0.0
    total_pnl = 0.0
    for bet_type, wins, losses, pushes, staked, pnl in rows:
        staked, pnl = float(staked or 0), float(pnl or 0)
        roi = pnl / staked if staked else 0.0
        print(f"settle (all-time, {bet_type}): {wins}-{losses}-{pushes} W-L-P, P&L {pnl:+.2f}u, ROI {roi:+.1%}")
        total_w, total_l, total_p = total_w + wins, total_l + losses, total_p + pushes
        total_staked += staked
        total_pnl += pnl

    total_roi = total_pnl / total_staked if total_staked else 0.0
    print(f"settle (all-time, all): {total_w}-{total_l}-{total_p} W-L-P, P&L {total_pnl:+.2f}u, ROI {total_roi:+.1%}")


def settle(engine):
    settle_parlays(engine)
    settle_edges(engine)
    print_summary(engine)


if __name__ == "__main__":
    settle(db.get_engine())
