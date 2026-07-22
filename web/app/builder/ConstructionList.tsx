import type { BuilderConstruction } from "../lib/api";
import styles from "./builder.module.css";

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export default function ConstructionList({
  constructions,
}: {
  constructions: BuilderConstruction[];
}) {
  if (constructions.length === 0) {
    return (
      <div className={styles.emptyState}>
        <p className={styles.emptyStateTitle}>No parlays recorded yet</p>
        <p>
          The nightly builder search runs once per slate and saves its lowest-risk results here.
          Check back after tonight&apos;s search completes.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.constructionList}>
      {constructions.map((c, i) => (
        <div key={i} className={styles.constructionCard}>
          <div className={styles.constructionHeader}>
            <span className={c.joint_prob >= 0.75 ? styles.safe : styles.neutral}>
              <span className={styles.jointProb}>
                ≈ {(c.joint_prob * 100).toFixed(1)}% to hit
              </span>
            </span>
            <span className={styles.constructionMeta}>
              <span className={styles.constructionMetaPrimary}>
                {c.combined_odds.toFixed(2)}x
              </span>
              <span>
                {c.n_legs} leg{c.n_legs === 1 ? "" : "s"}
              </span>
            </span>
          </div>

          <div className={styles.legList}>
            {c.legs.map((leg, j) => (
              <div key={j} className={styles.legRow}>
                <span className={styles.legName}>
                  <span className={styles.legLabel}>{leg.label}</span>
                  <span className={styles.legSide}>{leg.side}</span>
                </span>
                <span className={styles.legData}>
                  <span className={styles.legLine}>{leg.line}</span>
                  <span>{formatOdds(leg.odds)}</span>
                  <span className={styles.legMarketProb}>{formatPercent(leg.market_prob)}</span>
                  <span className={styles.legModelProb}>
                    model: {leg.model_prob === null ? "—" : formatPercent(leg.model_prob)} (not
                    used for ranking)
                  </span>
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
