"use client";

import { Fragment, useMemo, useState } from "react";
import Link from "next/link";
import type { Edge, EdgeDistribution } from "../lib/api";
import { statLabel } from "../lib/statLabels";
import styles from "./edges.module.css";

export type EdgeWithDistribution = Edge & { distribution: EdgeDistribution | null };

type SortKey = "edge" | "model_prob" | "odds" | "line_value";
type SortDirection = "asc" | "desc";

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

// Viewbox units per bar / overall chart geometry — kept in sync with the CSS
// classes below so the SVG scales cleanly at any width via `width: 100%`.
const BAR_UNIT = 28;
const BAR_GAP = 4;
const CHART_HEIGHT = 84;
const K_LABEL_HEIGHT = 16;
const PAD = 4;
// SVG user-space font size for the k-axis tick labels (a geometry attribute in
// viewBox units, scaled by the viewBox->viewport transform — not a CSS rem on
// the DESIGN.md type ramp; see .pmfKLabel).
const K_LABEL_FONT = 8;

function pmfLabelStep(n: number): number {
  if (n > 24) return 5;
  if (n > 12) return 2;
  return 1;
}

function PmfChart({
  distribution,
  side,
  statType,
}: {
  distribution: EdgeDistribution;
  side: "over" | "under";
  statType: string;
}) {
  const pmf = distribution.pmf;
  if (!pmf || pmf.length === 0) return null;

  // "Over" means X > line, i.e. X >= floor(line) + 1 — true for both
  // half-integer lines (2.5 -> X >= 3) and integer lines (3 -> X >= 4,
  // excluding the push value), matching modeling/distributions.prob_over_discrete.
  const boundaryK = Math.floor(distribution.line_value) + 1;
  const maxProb = Math.max(...pmf.map((p) => p.prob));
  const labelStep = pmfLabelStep(pmf.length);
  const width = pmf.length * BAR_UNIT;
  const height = PAD * 2 + CHART_HEIGHT + K_LABEL_HEIGHT;
  const boundaryX = boundaryK * BAR_UNIT;

  const ariaLabel =
    `Predicted distribution for ${statLabel(statType)}: mean ${distribution.predicted_mean.toFixed(2)}, ` +
    `line ${distribution.line_value}, P(over) ${formatPercent(distribution.prob_over)}, ` +
    `P(under) ${formatPercent(distribution.prob_under)}`;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={styles.pmfSvg}
      role="img"
      aria-label={ariaLabel}
      preserveAspectRatio="xMinYMid meet"
    >
      {pmf.map(({ k, prob }) => {
        const barHeight = maxProb > 0 ? (prob / maxProb) * CHART_HEIGHT : 0;
        const isOverBar = k >= boundaryK;
        const isWinningSide = (side === "over") === isOverBar;
        const x = k * BAR_UNIT + BAR_GAP / 2;
        const barWidth = BAR_UNIT - BAR_GAP;
        const y = PAD + (CHART_HEIGHT - barHeight);
        const showLabel = k % labelStep === 0 || k === pmf.length - 1;
        return (
          <g key={k}>
            <rect
              x={x}
              y={y}
              width={barWidth}
              height={Math.max(barHeight, prob > 0 ? 1 : 0)}
              className={isWinningSide ? styles.barWinning : styles.barMuted}
            />
            {showLabel && (
              <text
                x={x + barWidth / 2}
                y={PAD + CHART_HEIGHT + 12}
                textAnchor="middle"
                fontSize={K_LABEL_FONT}
                className={styles.pmfKLabel}
              >
                {k}
              </text>
            )}
          </g>
        );
      })}
      <line
        x1={boundaryX}
        y1={PAD}
        x2={boundaryX}
        y2={PAD + CHART_HEIGHT}
        className={styles.lineMarker}
      />
    </svg>
  );
}

function PmfPanel({ edge }: { edge: EdgeWithDistribution }) {
  const { distribution } = edge;

  if (!distribution || distribution.family !== "discrete" || !distribution.pmf) {
    return (
      <p className={styles.pmfUnavailable}>Distribution shown for MLB (discrete) stats.</p>
    );
  }

  return (
    <div className={styles.pmfPanel}>
      <div className={styles.pmfChartWrap}>
        <PmfChart distribution={distribution} side={edge.side} statType={edge.stat_type} />
      </div>
      <div className={styles.pmfMeta}>
        <span>
          Line <span className={styles.pmfMetaValue}>{distribution.line_value}</span>
        </span>
        <span>
          Mean <span className={styles.pmfMetaValue}>{distribution.predicted_mean.toFixed(2)}</span>
        </span>
        <span className={edge.side === "over" ? styles.pmfProbWinning : undefined}>
          P(over) {formatPercent(distribution.prob_over)}
        </span>
        <span className={edge.side === "under" ? styles.pmfProbWinning : undefined}>
          P(under) {formatPercent(distribution.prob_under)}
        </span>
      </div>
    </div>
  );
}

function sortAriaFor(sortKey: SortKey, sortDirection: SortDirection, key: SortKey): "ascending" | "descending" | "none" {
  if (sortKey !== key) return "none";
  return sortDirection === "asc" ? "ascending" : "descending";
}

function SortHeader({
  label,
  sortableKey,
  sortKey,
  sortDirection,
  onSort,
}: {
  label: string;
  sortableKey: SortKey;
  sortKey: SortKey;
  sortDirection: SortDirection;
  onSort: (key: SortKey) => void;
}) {
  const active = sortKey === sortableKey;
  return (
    <th className={styles.th} scope="col" aria-sort={sortAriaFor(sortKey, sortDirection, sortableKey)}>
      <button type="button" className={styles.thButton} onClick={() => onSort(sortableKey)}>
        {label}
        <span className={`${styles.sortIcon} ${active ? styles.sortIconActive : ""}`} aria-hidden="true">
          {active ? (sortDirection === "desc" ? "↓" : "↑") : "↕"}
        </span>
      </button>
    </th>
  );
}

export default function EdgesExplorer({ edges }: { edges: EdgeWithDistribution[] }) {
  const [statFilter, setStatFilter] = useState<Set<string>>(new Set());
  const [sideFilter, setSideFilter] = useState<Set<"over" | "under">>(new Set());
  const [sortKey, setSortKey] = useState<SortKey>("edge");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

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

  function toggleExpand(key: string) {
    setExpandedKey((prev) => (prev === key ? null : key));
  }

  const hasActiveFilters = statFilter.size > 0 || sideFilter.size > 0;

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
        <div className={styles.filterGroup} role="group" aria-label="Filter by stat">
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
        <div className={styles.filterGroup} role="group" aria-label="Filter by side">
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

      <div className={styles.srOnly} aria-live="polite">
        {sorted.length} of {edges.length} edges shown
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
                  <span className={styles.srOnly}>Expand distribution</span>
                </th>
                <th className={styles.th} scope="col">
                  Player
                </th>
                <th className={styles.th} scope="col">
                  Stat
                </th>
                <SortHeader
                  label="Edge"
                  sortableKey="edge"
                  sortKey={sortKey}
                  sortDirection={sortDirection}
                  onSort={handleSort}
                />
                <th className={styles.th} scope="col">
                  Side
                </th>
                {showDateColumn && (
                  <th className={styles.th} scope="col">
                    Date
                  </th>
                )}
                <SortHeader
                  label="Line"
                  sortableKey="line_value"
                  sortKey={sortKey}
                  sortDirection={sortDirection}
                  onSort={handleSort}
                />
                <SortHeader
                  label="Odds"
                  sortableKey="odds"
                  sortKey={sortKey}
                  sortDirection={sortDirection}
                  onSort={handleSort}
                />
                <SortHeader
                  label="Model prob"
                  sortableKey="model_prob"
                  sortKey={sortKey}
                  sortDirection={sortDirection}
                  onSort={handleSort}
                />
              </tr>
            </thead>
            <tbody>
              {sorted.map((e) => {
                const rowKey = `${e.player_id}-${e.game_id}-${e.stat_type}-${e.side}`;
                const isExpanded = expandedKey === rowKey;
                const panelId = `pmf-panel-${rowKey}`;
                const colCount = showDateColumn ? 9 : 8;
                return (
                  <Fragment key={rowKey}>
                    <tr className={styles.row}>
                      <td className={styles.td}>
                        <button
                          type="button"
                          className={styles.expandButton}
                          aria-expanded={isExpanded}
                          aria-controls={panelId}
                          onClick={() => toggleExpand(rowKey)}
                        >
                          <span
                            className={`${styles.expandIcon} ${isExpanded ? styles.expandIconOpen : ""}`}
                            aria-hidden="true"
                          >
                            ▸
                          </span>
                          <span className={styles.srOnly}>
                            {isExpanded ? "Collapse" : "Expand"} distribution for {e.player_name}{" "}
                            {statLabel(e.stat_type)}
                          </span>
                        </button>
                      </td>
                      <td className={styles.td}>
                        <div className={styles.playerCell}>
                          <Link
                            href={`/players/${e.player_id}`}
                            className={styles.playerLink}
                            title={e.player_name}
                          >
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
                    {isExpanded && (
                      <tr className={styles.expandRow}>
                        <td className={styles.expandCell} colSpan={colCount} id={panelId}>
                          <PmfPanel edge={e} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
