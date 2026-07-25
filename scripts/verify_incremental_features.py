"""Read-only verification for the incremental feature upsert
(docs/superpowers/plans/2026-07-25-incremental-feature-upsert.md).

Proves the immutability gate that makes skipping old rows safe: for a sample
of historical rows (as_of_date < cutoff), the value already stored in
rolling_player_features must equal the value a fresh full COMPUTE produces.
100% match means "skip rows older than the lookback window" is exact, not an
approximation.

CRITICAL SAFETY: this script is READ-ONLY against the LIVE production DB
(ingestion.db.get_engine()). It never calls compute_features()'s upsert path
(the INSERT ... ON CONFLICT) and never issues any INSERT/UPDATE/DELETE. It
only:
  - runs the same read-only compute steps compute_features() runs
    (_load_player_games, _add_rolling_stat_averages, etc. — all SELECTs), and
  - reads existing rows back from rolling_player_features (SELECT).
Do NOT add any write statements to this script.

Usage:
    python -m scripts.verify_incremental_features [--sport mlb] [--lookback-days 7] [--sample 500]
"""

import argparse
import random
from datetime import date

import pandas as pd
from sqlalchemy import text

from ingestion import db
from modeling.features import (
    SPORT_CONFIG,
    _add_opponent_def_rating,
    _add_rest_and_schedule_features,
    _add_rolling_stat_averages,
    _incremental_cutoff,
    _load_player_games,
)


def _compute_full_values(engine, sport):
    """Mirrors compute_features()'s compute path exactly (read-only queries
    only, no upcoming rows, no upsert) so we can compare against what's
    already stored.
    """
    config = SPORT_CONFIG[sport]
    windows = config["windows"]

    with engine.connect() as conn:
        stats = _load_player_games(conn, sport, windows)
        games_df = pd.read_sql(
            text("SELECT game_id, date, home_team_id, away_team_id FROM games WHERE sport = :sport"),
            conn,
            params={"sport": sport},
        )
    games_df["date"] = pd.to_datetime(games_df["date"])

    stats = _add_rolling_stat_averages(stats, windows)
    stats = _add_rest_and_schedule_features(stats, games_df)
    stats = _add_opponent_def_rating(stats, games_df, config["scoring_stat"])
    stats["is_home"] = stats["team_id"] == stats["home_team_id"]

    feature_cols = list(windows) + ["opp_def_rating", "rest_days", "is_home", "is_back_to_back"]

    values = {}
    for record in stats.to_dict("records"):
        for col in feature_cols:
            v = record.get(col)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            key = (record["player_id"], record["date"].date(), col)
            values[key] = float(v)
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", choices=list(SPORT_CONFIG), default="mlb")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--sample", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    engine = db.get_engine()
    cutoff = _incremental_cutoff(date.today(), args.lookback_days)
    print(f"sport={args.sport} lookback_days={args.lookback_days} cutoff={cutoff} (today={date.today()})")

    print("computing full values (read-only, mirrors compute_features's compute path)...")
    computed = _compute_full_values(engine, args.sport)
    old_keys = [k for k in computed if k[1] < cutoff]
    print(f"computed {len(computed):,} total rows; {len(old_keys):,} are historical (as_of_date < cutoff)")

    if not old_keys:
        print("no historical rows to sample -- nothing to verify")
        return

    random.seed(args.seed)
    sample_size = min(args.sample, len(old_keys))
    sample_keys = random.sample(old_keys, sample_size)

    print(f"loading stored historical rows (as_of_date < {cutoff}) from rolling_player_features (SELECT only)...")
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT player_id, as_of_date, feature, value "
                "FROM rolling_player_features WHERE as_of_date < :cutoff"
            ),
            {"cutoff": cutoff},
        )
        stored = {(r.player_id, r.as_of_date, r.feature): float(r.value) for r in rows}

    from collections import Counter

    matches = 0
    mismatches = []
    missing = 0
    checked_by_feature = Counter()
    matched_by_feature = Counter()
    for key in sample_keys:
        stored_val = stored.get(key)
        computed_val = computed[key]
        feature = key[2]
        if stored_val is None:
            missing += 1
            continue
        checked_by_feature[feature] += 1
        if abs(stored_val - computed_val) < 1e-9:
            matches += 1
            matched_by_feature[feature] += 1
        else:
            mismatches.append((key, stored_val, computed_val))

    checked = sample_size - missing
    match_rate = (matches / checked * 100) if checked else 0.0
    print(f"\nsampled {sample_size:,} historical rows")
    print(f"  missing from rolling_player_features: {missing}")
    print(f"  checked: {checked:,}, matched: {matches:,}, mismatched: {len(mismatches):,}")
    print(f"  MATCH RATE: {match_rate:.2f}%")
    print("  per-feature match rate:")
    for feature in sorted(checked_by_feature):
        c = checked_by_feature[feature]
        m = matched_by_feature[feature]
        print(f"    {feature}: {m}/{c} ({m / c * 100:.2f}%)")
    if mismatches:
        by_feature = Counter(key[2] for key, _, _ in mismatches)
        print(f"  mismatches by feature: {dict(by_feature)}")
        print("  sample mismatches (up to 10):")
        for key, stored_val, computed_val in mismatches[:10]:
            print(f"    {key}: stored={stored_val} computed={computed_val}")


if __name__ == "__main__":
    main()
