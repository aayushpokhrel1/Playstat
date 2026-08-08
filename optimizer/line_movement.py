"""Line movement between a card's build price and its last pre-start price.

README §15.9 item 12's MANDATORY validation gate. The builder's apparent "+EV"
is measured against a consensus of six SOFT books with no sharp reference, so
best-of-six beating consensus-of-six may be a genuine stale line OR an artifact
of one outlier dragging the average. The industry-standard discriminator is
closing-line value.

This is a MEASUREMENT-ONLY module. `modeling/clv.py` was DELETED in §16 #3B and
this is deliberately NOT a revival of it: it lives beside optimizer/devig.py and
optimizer/stake.py because it measures the builder, not a model.

HONESTY (§15.8 #2). This is NOT the true closing line — the last snapshot lands
a median ~100 minutes (worst case ~150) before first pitch. It must be presented
as "line movement, build -> last pre-start snapshot" with the lead time stated,
and never as edge/value/+EV.

Both sides of the comparison already exist in the DB — prop_lines/game_lines
carry `pulled_at` and inserts are append-only — so this needs no migration.
"""


from optimizer.devig import devig

# Which (over, under)-shaped column pair a side is priced from. Team home/away
# markets store home_odds/away_odds; everything else is over/under.
_SIDE_COLUMNS = {
    "over": ("over_odds", "under_odds", 0),
    "under": ("over_odds", "under_odds", 1),
    "home": ("home_odds", "away_odds", 0),
    "away": ("home_odds", "away_odds", 1),
}


def close_prob_for_side(row, side):
    """De-vigged probability of `side` in a raw prop_lines/game_lines row.

    Returns None for an unknown side or a ONE-SIDED row: a single price cannot be
    de-vigged, and inventing a probability from it would manufacture movement.
    Excluded rows are counted in `coverage` by summarize_movement.
    """
    columns = _SIDE_COLUMNS.get(side)
    if columns is None:
        return None
    first, second, index = columns
    a, b = row.get(first), row.get(second)
    if a is None or b is None:
        return None
    return devig(int(a), int(b))[index]


def leg_movement(build_leg, close_row):
    """Movement in percentage points for one leg, or None if not comparable.

    Positive means the market moved TOWARD the side we took (our side became more
    probable, i.e. the price we got was better than the later one).

    Returns None when there is no later snapshot, or when `line_value` moved —
    a different line is a DIFFERENT BET, and comparing across it would invent
    movement that is really a change of market. Excluded legs are counted in
    `coverage` by summarize_movement rather than silently dropped.
    """
    if not close_row or build_leg.get("market_prob") is None:
        return None
    if float(close_row["line_value"]) != float(build_leg["line"]):
        return None
    # Accept either a pre-computed market_prob (unit tests, callers that already
    # de-vigged) or a raw odds row, which we de-vig for the side we actually took.
    close_prob = close_row.get("market_prob")
    if close_prob is None:
        close_prob = close_prob_for_side(close_row, build_leg.get("side"))
    if close_prob is None:
        return None
    close_row = {**close_row, "market_prob": close_prob}
    return {
        "player_id": build_leg.get("player_id"),
        "game_id": build_leg.get("game_id"),
        "stat_type": build_leg.get("stat_type"),
        "side": build_leg.get("side"),
        "line": float(build_leg["line"]),
        "build_prob": float(build_leg["market_prob"]),
        "close_prob": float(close_row["market_prob"]),
        "movement_pp": (float(close_row["market_prob"]) - float(build_leg["market_prob"])) * 100.0,
    }


def summarize_movement(pairs):
    """Aggregate (build_leg, close_row) pairs.

    coverage is first-class output, not a footnote: it is the share of legs that
    could honestly be compared, and a low value is itself the finding.
    """
    moves = []
    for build_leg, close_row in pairs:
        move = leg_movement(build_leg, close_row)
        if move is not None:
            moves.append(move)

    n_legs = len(pairs)
    n_compared = len(moves)
    values = [m["movement_pp"] for m in moves]
    return {
        "n_legs": n_legs,
        "n_compared": n_compared,
        "coverage": (n_compared / n_legs) if n_legs else 0.0,
        "mean_movement_pp": (sum(values) / len(values)) if values else None,
        "n_toward": sum(1 for v in values if v > 0),
        "n_against": sum(1 for v in values if v < 0),
        "legs": moves,
    }
