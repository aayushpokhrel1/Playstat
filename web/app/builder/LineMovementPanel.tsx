"use client";

import type { LineMovement } from "../lib/api";
import styles from "./builder.module.css";

// Mean movement in percentage points, always one decimal. Signed with a
// monochrome +/− glyph — no signal-green (reserved for the ≥75% joint-prob
// rule); the direction is carried by the sign, not a color.
function formatMovement(pp: number | null): string {
  if (pp === null) return "—";
  if (pp === 0) return "0.0pp";
  const sign = pp > 0 ? "+" : "−";
  return `${sign}${Math.abs(pp).toFixed(1)}pp`;
}

function formatCoverage(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export default function LineMovementPanel({ data }: { data: LineMovement | null }) {
  if (!data || data.n_compared === 0) {
    return (
      <div className={styles.recordStrip}>
        <span className={styles.recordCopy}>Not enough matched lines yet.</span>
      </div>
    );
  }

  return (
    <>
      <div className={styles.sectionHeader}>
        <h2 className={styles.sectionTitle}>Line movement (paper)</h2>
      </div>
      <div className={styles.recordTable}>
        <div className={styles.recordRow}>
          <span className={styles.recordLabel}>Mean movement</span>
          <span className={styles.recordFigure}>{formatMovement(data.mean_movement_pp)}</span>
          <span className={styles.recordMeta}>
            {data.n_compared} of {data.n_legs} legs
          </span>
        </div>
        <div className={styles.recordRow}>
          <span className={styles.recordLabel}>Toward / against</span>
          <span className={styles.recordFigure}>
            {data.n_toward} / {data.n_against}
          </span>
        </div>
        <div className={styles.recordRow}>
          <span className={styles.recordLabel}>Coverage</span>
          <span className={styles.recordFigure}>{formatCoverage(data.coverage)}</span>
        </div>
        <div className={styles.recordFooter}>
          <p className={styles.recordCaption}>
            Build price vs the last snapshot before first pitch — median ~100 min out, not the closing line. Legs whose line moved are excluded; coverage shows how many were comparable.
          </p>
        </div>
      </div>
    </>
  );
}
