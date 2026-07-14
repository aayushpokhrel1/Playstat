"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import type { Edge } from "../lib/api";
import styles from "./edges.module.css";

type SortKey = "edge" | "model_prob" | "odds" | "line_value";
type SortDirection = "asc" | "desc";

const STAT_LABELS: Record<string, string> = {
  total_bases: "Total bases",
  batter_strikeouts: "Batter Ks",
  pitcher_strikeouts: "Pitcher Ks",
  home_runs: "Home runs",
  stolen_bases: "Stolen bases",
  earned_runs: "Earned runs",
  walks_allowed: "Walks allowed",
  hits_allowed: "Hits allowed",
  outs_recorded: "Outs recorded",
};

function statLabel(statType: string): string {
  return STAT_LABELS[statType] ?? statType.replace(/_/g, " ");
}

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export default function EdgesExplorer({ edges }: { edges: Edge[] }) {
  const [statFilter, setStatFilter] = useState<Set<string>>(new Set());
  const [sideFilter, setSideFilter] = useState<Set<"over" | "under">>(new Set());
  const [sortKey, setSortKey] = useState<SortKey>("edge");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");

  const statTypes = useMemo(
    () => [...new Set(edges.map((e) => e.stat_type))].sort(),
    [edges],
  );

  const filtered = useMemo(() => {
    return edges.filter((e) => {
      if (statFilter.size > 0 && !statFilter.has(e.stat_type)) return false;
      if (sideFilter.size > 0 && !sideFilter.has(e.side)) return false;
      return true;
    });
  }, [edges, statFilter, sideFilter]);

  const sorted = useMemo(() => {
    const copy = [...filtered];
    copy.sort((a, b) => {
      const diff = a[sortKey] - b[sortKey];
      return sortDirection === "desc" ? -diff : diff;
    });
    return copy;
  }, [filtered, sortKey, sortDirection]);

  const showDateColumn = useMemo(
    () => new Set(edges.map((e) => e.date)).size > 1,
    [edges],
  );

  function toggleStat(stat: string) {
    setStatFilter((prev) => {
      const next = new Set(prev);
      if (next.has(stat)) next.delete(stat);
      else next.add(stat);
      return next;
    });
  }

  function toggleSide(side: "over" | "under") {
    setSideFilter((prev) => {
      const next = new Set(prev);
      if (next.has(side)) next.delete(side);
      else next.add(side);
      return next;
    });
  }

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDirection((prev) => (prev === "desc" ? "asc" : "desc"));
    } else {
      setSortKey(key);
      setSortDirection("desc");
    }
  }

  function clearFilters() {
    setStatFilter(new Set());
    setSideFilter(new Set());
  }

  const hasActiveFilters = statFilter.size > 0 || sideFilter.size > 0;

  function sortAriaFor(key: SortKey): "ascending" | "descending" | "none" {
    if (sortKey !== key) return "none";
    return sortDirection === "asc" ? "ascending" : "descending";
  }

  function SortHeader({ label, sortableKey }: { label: string; sortableKey: SortKey }) {
    const active = sortKey === sortableKey;
    return (
      <th className={styles.th} scope="col" aria-sort={sortAriaFor(sortableKey)}>
        <button
          type="button"
          className={styles.thButton}
          onClick={() => handleSort(sortableKey)}
        >
          {label}
          <span className={`${styles.sortIcon} ${active ? styles.sortIconActive : ""}`}>
            {active ? (sortDirection === "desc" ? "↓" : "↑") : "↕"}
          </span>
        </button>
      </th>
    );
  }

  if (edges.length === 0) {
    return (
      <div className={styles.emptyState}>
        <p className={styles.emptyStateTitle}>No edges right now</p>
        <p>
          Edges show up here once tonight&apos;s sportsbook lines are pulled and the model
          has run predictions against them. Check back closer to first pitch.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className={styles.filters}>
        <div className={styles.filterGroup}>
          {statTypes.map((stat) => (
            <button
              key={stat}
              type="button"
              className={`${styles.chip} ${statFilter.has(stat) ? styles.chipActive : ""}`}
              aria-pressed={statFilter.has(stat)}
              onClick={() => toggleStat(stat)}
            >
              {statLabel(stat)}
            </button>
          ))}
        </div>
        <div className={styles.filterDivider} aria-hidden="true" />
        <div className={styles.filterGroup}>
          {(["over", "under"] as const).map((side) => (
            <button
              key={side}
              type="button"
              className={`${styles.chip} ${sideFilter.has(side) ? styles.chipActive : ""}`}
              aria-pressed={sideFilter.has(side)}
              onClick={() => toggleSide(side)}
            >
              {side === "over" ? "Over" : "Under"}
            </button>
          ))}
        </div>
        {hasActiveFilters && (
          <button type="button" className={styles.clearFilters} onClick={clearFilters}>
            Clear filters
          </button>
        )}
      </div>

      {sorted.length === 0 ? (
        <div className={styles.emptyState}>
          <p className={styles.emptyStateTitle}>No edges match these filters</p>
          <button type="button" className={styles.clearFilters} onClick={clearFilters}>
            Clear filters
          </button>
        </div>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th} scope="col">
                  Player
                </th>
                <th className={styles.th} scope="col">
                  Stat
                </th>
                <SortHeader label="Edge" sortableKey="edge" />
                <th className={styles.th} scope="col">
                  Side
                </th>
                {showDateColumn && (
                  <th className={styles.th} scope="col">
                    Date
                  </th>
                )}
                <SortHeader label="Line" sortableKey="line_value" />
                <SortHeader label="Odds" sortableKey="odds" />
                <SortHeader label="Model prob" sortableKey="model_prob" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((e) => (
                <tr
                  key={`${e.player_id}-${e.game_id}-${e.stat_type}-${e.side}`}
                  className={styles.row}
                >
                  <td className={styles.td}>
                    <div className={styles.playerCell}>
                      <Link href={`/players/${e.player_id}`} className={styles.playerLink}>
                        {e.player_name}
                      </Link>
                    </div>
                  </td>
                  <td className={`${styles.td} ${styles.statCell}`}>{statLabel(e.stat_type)}</td>
                  <td className={`${styles.td} ${styles.edgeValue}`}>{formatPercent(e.edge)}</td>
                  <td className={styles.td}>
                    <span className={styles.sideTag}>{e.side}</span>
                  </td>
                  {showDateColumn && (
                    <td className={`${styles.td} ${styles.dataMuted}`}>{e.date}</td>
                  )}
                  <td className={`${styles.td} ${styles.data}`}>{e.line_value}</td>
                  <td className={`${styles.td} ${styles.data}`}>{formatOdds(e.odds)}</td>
                  <td className={`${styles.td} ${styles.data}`}>{formatPercent(e.model_prob)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
