"""De-vigging: turning a two-sided American price into fair probabilities.

Extracted from the now-deleted modeling/edges.py (README §16 / #3B model
teardown, 2026-08-06) because the market-ranked low-risk builder
(optimizer/builder_core.py) ranks legs on the de-vigged MARKET probability and
so depends on these two pure functions — they were never model-specific. Pure
math, no imports, DB-free.
"""


def odds_to_probability(american_odds):
    """Implied (vig-inclusive) probability of a single American-odds price."""
    if american_odds > 0:
        return 100 / (american_odds + 100)
    return abs(american_odds) / (abs(american_odds) + 100)


def devig(over_odds, under_odds):
    """Removes the sportsbook's overround so the two implied probabilities sum to 1."""
    p_over_raw = odds_to_probability(over_odds)
    p_under_raw = odds_to_probability(under_odds)
    overround = p_over_raw + p_under_raw
    return p_over_raw / overround, p_under_raw / overround
