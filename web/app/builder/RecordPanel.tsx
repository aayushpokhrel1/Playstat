"use client";

import { useState } from "react";
import type { BuilderRecord, BuilderRecordDaily } from "../lib/api";
import styles from "./builder.module.css";

const TIER_LABELS: Record<BuilderRecord["tier"], string> = {
  player: "Player",
  team: "Team",
};

function formatUnits(value: number): string {
  if (value === 0) return "0.00u";
  const sign = value > 0 ? "+" : "−";
  return `${sign}${Math.abs(value).toFixed(2)}u`;
}

function formatRoi(value: number): string {
  const pct = value * 100;
  if (pct === 0) return "ROI 0.0%";
  const sign = pct > 0 ? "+" : "−";
  return `ROI ${sign}${Math.abs(pct).toFixed(1)}%`;
}

function tierTargetLabel(row: BuilderRecord): string {
  return `${TIER_LABELS[row.tier] ?? row.tier} ${row.target_payout.toFixed(1)}x`;
}

type RecordPanelProps = {
  rows: BuilderRecord[];
  daily: BuilderRecordDaily[];
};

export default function RecordPanel({ rows, daily }: RecordPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const hasBuilderRecord = rows.length > 0;
  const hasTeamRows = rows.some((row) => row.tier === "team");
  const hasDaily = daily.length > 0;

  if (!hasBuilderRecord) {
    return (
      <div className={styles.recordStrip}>
        <span className={styles.recordFigure}>0-0-0 · n=0</span>
        <span className={styles.recordCopy}>
          No settled builder parlays yet — the record starts once tonight&apos;s slate finishes.
        </span>
      </div>
    );
  }

  return (
    <div className={styles.recordTable}>
      {rows.map((row) => (
        <div key={`${row.tier}-${row.target_payout}`} className={styles.recordRow}>
          <span className={styles.recordLabel}>{tierTargetLabel(row)}</span>
          <span className={styles.recordFigure}>
            {row.wins}-{row.losses}-{row.pushes}
          </span>
          <span className={styles.recordFigure}>{formatUnits(row.pnl)}</span>
          <span className={styles.recordFigure}>{formatRoi(row.roi)}</span>
          <span className={styles.recordMeta}>n={row.n}</span>
        </div>
      ))}
      {!hasTeamRows && (
        <div className={styles.recordRowEmpty}>
          <span className={styles.recordCopy}>Team — no settled team parlays yet —</span>
        </div>
      )}

      <div className={styles.recordFooter}>
        <p className={styles.recordCaption}>Paper trading only — not a real bet.</p>
        {hasDaily && (
          <button
            type="button"
            className={styles.recordToggle}
            aria-expanded={expanded}
            aria-controls="builder-record-daily"
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "Hide" : "Show"} per-day breakdown ({daily.length}{" "}
            {daily.length === 1 ? "day" : "days"})
          </button>
        )}
      </div>

      {expanded && hasDaily && (
        <div id="builder-record-daily" className={styles.dailyPanel}>
          <p className={styles.dailyCaption}>
            Early slate dates are small samples — a handful of settled parlays can swing
            W-L-P and ROI a lot day to day (README §15 calibration note). Read these as
            noisy until the sample builds up.
          </p>
          <div className={styles.dailyList}>
            {daily.map((day) => (
              <div key={day.date} className={styles.dailyRow}>
                <span className={styles.recordLabel}>{day.date}</span>
                <span className={styles.recordFigure}>
                  {day.wins}-{day.losses}-{day.pushes}
                </span>
                <span className={styles.recordFigure}>{formatUnits(day.pnl)}</span>
                <span className={styles.recordFigure}>{formatRoi(day.roi)}</span>
                <span className={styles.recordMeta}>n={day.n}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
