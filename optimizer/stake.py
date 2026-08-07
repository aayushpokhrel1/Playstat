"""Kelly stake sizing for builder parlays (README §15.9 item 4).

PURE math + a slate-sizing pass. The edge sized on is the line-shopping edge:
p = consensus devig joint_prob, d = shopped combined_odds. There is NO +EV/edge
claim in any UI — this is stake sizing only.
"""

import argparse

from sqlalchemy import text

from ingestion.db import get_engine


def kelly_fraction(p, decimal_odds):
    """Kelly fraction of bankroll for a single bet with win prob p and net
    decimal odds (decimal_odds - 1). f* = (p*d - 1)/(d - 1), clamped to >= 0
    (never stake into a non-positive edge). Returns 0 when decimal_odds <= 1.
    """
    if decimal_odds <= 1:
        return 0.0
    f = (p * decimal_odds - 1) / (decimal_odds - 1)
    return f if f > 0 else 0.0


def quarter_kelly_stake(p, decimal_odds, *, fraction=0.25, bankroll_units=100):
    """Fractional-Kelly stake in UNITS, where 1 unit = 1% of bankroll
    (bankroll_units = 100). stake = fraction * f* * bankroll_units.
    """
    return fraction * kelly_fraction(p, decimal_odds) * bankroll_units


def apply_exposure_cap(stakes, cap):
    """Scale a group of stakes down proportionally so their sum never exceeds
    cap. No-op when already under the cap or all-zero. Pure list-in/list-out.
    """
    total = sum(stakes)
    if total <= cap or total <= 0:
        return list(stakes)
    scale = cap / total
    return [s * scale for s in stakes]


def size_slate(rows, *, exposure_cap=5.0, cap_scope="global", fraction=0.25, bankroll_units=100):
    """Size a whole night's builder parlays. rows: (parlay_id, p, decimal_odds,
    sport). Computes the per-parlay quarter-Kelly stake, then applies the
    exposure cap within each cap group ('global' = one group for the date;
    'per-sport' = one group per sport). Returns {parlay_id: stake}. Pure.
    """
    raw = {pid: quarter_kelly_stake(p, d, fraction=fraction, bankroll_units=bankroll_units)
           for pid, p, d, _sport in rows}
    groups = {}
    for pid, _p, _d, sport in rows:
        key = "all" if cap_scope == "global" else (sport or "mlb")
        groups.setdefault(key, []).append(pid)
    out = {}
    for pids in groups.values():
        capped = apply_exposure_cap([raw[pid] for pid in pids], exposure_cap)
        out.update(zip(pids, capped))
    return out


# Group the slate by ET-local calendar date, matching README §15.10's slate
# reasoning (created_at is an ET timestamptz; a plain ::date in a UTC session
# would split a night). NULL sport (legacy rows) defaults to 'mlb'.
_SELECT = text(
    """
    SELECT pr.parlay_id,
           pr.joint_prob,
           pr.combined_odds,
           COALESCE(pr.legs->>'sport', 'mlb') AS sport
    FROM parlay_recommendations pr
    WHERE pr.kind = 'builder'
      AND (pr.created_at AT TIME ZONE 'America/New_York')::date
          = COALESCE(:date, (now() AT TIME ZONE 'America/New_York')::date)
    """
)

_UPDATE = text("UPDATE parlay_recommendations SET stake = :stake WHERE parlay_id = :pid")


def size_and_persist(engine, *, date=None, exposure_cap=5.0, cap_scope="global",
                     fraction=0.25, bankroll_units=100):
    """Read the given date's (ET, default today) builder parlays, compute the
    quarter-Kelly stake per parlay under the exposure cap, and UPDATE the stake
    column. Idempotent: recomputes from scratch each run. Returns rows updated.
    """
    with engine.begin() as conn:
        rows = conn.execute(_SELECT, {"date": date}).fetchall()
        sized = size_slate(
            [(int(r[0]), float(r[1]), float(r[2]), r[3]) for r in rows],
            exposure_cap=exposure_cap, cap_scope=cap_scope,
            fraction=fraction, bankroll_units=bankroll_units,
        )
        for pid, stake in sized.items():
            conn.execute(_UPDATE, {"pid": pid, "stake": stake})
    return len(sized)


def main():
    ap = argparse.ArgumentParser(description="Kelly stake sizing for builder parlays (no EV claim).")
    ap.add_argument("--date", default=None, help="ET slate date YYYY-MM-DD (default: today)")
    ap.add_argument("--exposure-cap", type=float, default=5.0, help="same-night total-stake cap in units")
    ap.add_argument("--cap-scope", choices=["global", "per-sport"], default="global")
    ap.add_argument("--kelly-fraction", type=float, default=0.25)
    ap.add_argument("--bankroll-units", type=float, default=100)
    args = ap.parse_args()
    n = size_and_persist(
        get_engine(), date=args.date, exposure_cap=args.exposure_cap,
        cap_scope=args.cap_scope, fraction=args.kelly_fraction, bankroll_units=args.bankroll_units,
    )
    print(f"stake: sized {n} builder parlays (cap {args.exposure_cap}u {args.cap_scope}, "
          f"{args.kelly_fraction:g}-Kelly)")


if __name__ == "__main__":
    main()
