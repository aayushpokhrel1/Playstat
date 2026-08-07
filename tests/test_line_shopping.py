from ingestion.odds_ingest import best_price, collect_prop_rows


def _bb(entries):
    return {bk: e for bk, e in entries.items()}


def test_best_price_picks_max_decimal_among_available_same_line():
    bb = {
        "draftkings": {"odds": "-120", "overUnder": "0.5", "available": True},
        "fanduel":    {"odds": "-105", "overUnder": "0.5", "available": True},  # best (least juice)
        "betmgm":     {"odds": "-130", "overUnder": "0.5", "available": True},
    }
    assert best_price(bb, "overUnder", 0.5) == (-105, "fanduel")


def test_best_price_positive_beats_negative():
    bb = {
        "a": {"odds": "+120", "overUnder": "1.5", "available": True},  # best payout
        "b": {"odds": "-105", "overUnder": "1.5", "available": True},
    }
    assert best_price(bb, "overUnder", 1.5) == (120, "a")


def test_best_price_skips_unavailable():
    bb = {
        "a": {"odds": "+200", "overUnder": "0.5", "available": False},  # ignored
        "b": {"odds": "-110", "overUnder": "0.5", "available": True},
    }
    assert best_price(bb, "overUnder", 0.5) == (-110, "b")


def test_best_price_skips_line_mismatch_exact_only():
    bb = {
        "a": {"odds": "+150", "overUnder": "2.5", "available": True},  # different line -> excluded
        "b": {"odds": "-110", "overUnder": "0.5", "available": True},
    }
    assert best_price(bb, "overUnder", 0.5) == (-110, "b")


def test_best_price_none_when_empty_or_no_eligible():
    assert best_price(None, "overUnder", 0.5) == (None, None)
    assert best_price({}, "overUnder", 0.5) == (None, None)
    assert best_price({"a": {"odds": "-110", "overUnder": "9.5", "available": True}},
                      "overUnder", 0.5) == (None, None)


def test_best_price_moneyline_no_line_matches_all_available():
    bb = {"a": {"odds": "-150", "available": True}, "b": {"odds": "-140", "available": True}}
    assert best_price(bb, None, None) == (-140, "b")


def test_collect_prop_rows_attaches_best_over_and_under():
    event = {
        "players": {"P1": {"name": "Player One"}},
        "odds": {
            "o1": {"statID": "batting_hits", "periodID": "game", "betTypeID": "ou",
                   "statEntityID": "P1", "sideID": "over", "bookOverUnder": "0.5",
                   "bookOdds": "-115",
                   "byBookmaker": {"dk": {"odds": "-110", "overUnder": "0.5", "available": True},
                                   "fd": {"odds": "-108", "overUnder": "0.5", "available": True}}},
            "u1": {"statID": "batting_hits", "periodID": "game", "betTypeID": "ou",
                   "statEntityID": "P1", "sideID": "under", "bookOverUnder": "0.5",
                   "bookOdds": "-105",
                   "byBookmaker": {"dk": {"odds": "-102", "overUnder": "0.5", "available": True}}},
        },
    }
    rows = collect_prop_rows(event, {"batting_hits": "hits"})
    assert len(rows) == 1
    r = rows[0]
    assert r["over_odds"] == -115 and r["under_odds"] == -105          # consensus unchanged
    assert r["best_over_odds"] == -108 and r["best_over_book"] == "fd"  # shopped
    assert r["best_under_odds"] == -102 and r["best_under_book"] == "dk"
