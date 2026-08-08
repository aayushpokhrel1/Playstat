"""ET slate window -> UTC ISO bounds for the SGO /events pull.

README §15.9 item 11 Option B. The SGO free tier meters ENTITIES (2,500/month,
1 returned event = 1 entity), and an unfiltered MLB pull returns ~51 events —
~3-4 days of schedule — of which the builder uses only today's slate
(load_player_legs filters `g.date` to the slate). Narrowing the pull to the
slate window cuts it to ~15 and is what makes a second and third daily pull
affordable (measured: 3 narrowed pulls ~27 entities/day vs 51 for today's
single unfiltered pull).

The 06:00 ET boundary is deliberate: no MLB game starts near it (earliest
observed ~12:00 ET), and it is clear of both the UTC date rollover and the
02:00 ET DST transition instant, so a window never splits a slate and never
straddles a clock change.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Hour (ET) at which one slate ends and the next begins.
SLATE_BOUNDARY_HOUR = 6


def _to_utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slate_window(now, sport="mlb", not_before_now=False):
    """(starts_after, starts_before) as UTC ISO-8601 strings for `sport`'s slate.

    `now` must be timezone-aware. The window covers the ET slate date containing
    `now` through + SLATE_WINDOW_DAYS[sport] further ET days (NFL bets a Thu..Mon
    card, window_days=4; every other sport is 0 = a single day).

    not_before_now=True raises the lower bound to `now`, so an already-started
    game is neither fetched nor billed — used by the 17:30 and 19:45 pulls. It
    also keeps in-play prices out of a snapshot meant to represent a pre-game
    line. max() is used rather than assignment so the bound never moves backwards.
    """
    # Imported here: optimizer.builder imports pandas, and ingestion callers
    # should not pay that cost at module import.
    from optimizer.builder import SLATE_WINDOW_DAYS

    et_now = now.astimezone(ET)
    slate_date = et_now.date()
    if et_now.hour < SLATE_BOUNDARY_HOUR:
        slate_date -= timedelta(days=1)

    boundary = time(SLATE_BOUNDARY_HOUR, 0)
    start = datetime.combine(slate_date, boundary, tzinfo=ET)
    end = datetime.combine(
        slate_date + timedelta(days=SLATE_WINDOW_DAYS.get(sport, 0) + 1),
        boundary, tzinfo=ET,
    )

    if not_before_now:
        start = max(start, now)

    return _to_utc_iso(start), _to_utc_iso(end)
