import type { ParlayRecommendation } from "../lib/api";
import styles from "./edges.module.css";

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export default function ParlaySection({ parlays }: { parlays: ParlayRecommendation[] }) {
  if (parlays.length === 0) {
    return (
      <div className={styles.emptyState}>
        <p className={styles.emptyStateTitle}>No parlay recommendations yet</p>
        <p>
          The optimizer needs a full slate of calibrated edges across multiple games before
          it can search for a joint-probability combination worth suggesting. It&apos;ll
          populate here once tonight&apos;s edges cover more than one game.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.parlayList}>
      {parlays.map((p) => (
        <div key={p.parlay_id} className={styles.parlayCard}>
          <div className={styles.parlayCardHeader}>
            <span className={styles.parlayTarget}>{p.target_payout}x target</span>
            <span className={styles.parlayProb}>{formatPercent(p.joint_prob)} joint prob</span>
          </div>
          <div className={styles.parlayLegs}>
            {p.legs.map((leg, i) => (
              <div key={i} className={styles.parlayLeg}>
                <span className={styles.parlayLegName}>
                  {leg.player_name ?? `Player ${leg.player_id}`} — {leg.stat_type.replace(/_/g, " ")}{" "}
                  {leg.side}
                </span>
                <span>{formatOdds(leg.odds)}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
