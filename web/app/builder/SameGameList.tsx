import type { BuilderLeg, SavedBuilderParlay } from "../lib/api";
import { nickname } from "../lib/teamNames";
import styles from "./builder.module.css";

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

// Same-game legs are always team markets (NRFI / F5), so the matchup is the
// plain `{away} @ {home}` form -- no player side to resolve. Mirrors
// ConstructionList's LegMatchup for the team-leg branch.
function GameMatchup({ leg }: { leg: BuilderLeg }) {
  const home = nickname(leg.home_team);
  const away = nickname(leg.away_team);
  if (home === null && away === null) return null;
  return (
    <span className={styles.legMatchup}>
      {away} @ {home}
    </span>
  );
}

/**
 * Same-game combos (README §15.9 item 1) -- the deliberate, labelled exception
 * to the across-game-only rule.
 *
 * Two honesty rules drive this layout:
 *  1. The lift-adjusted joint leads, because it IS the risk. It is deliberately
 *     NOT styled with the signal-green `safe` class: green is reserved for the
 *     >=75% joint-prob rule, and these cards sit far below that.
 *  2. The payout is a REFERENCE only -- a book reprices or restricts correlated
 *     same-game legs -- so it is captioned rather than shown as a headline price.
 */
export default function SameGameList({
  constructions,
  emptyTitle,
  emptyBody,
}: {
  constructions: SavedBuilderParlay[];
  emptyTitle: string;
  emptyBody: string;
}) {
  if (constructions.length === 0) {
    return (
      <div className={styles.emptyState}>
        <p className={styles.emptyStateTitle}>{emptyTitle}</p>
        <p>{emptyBody}</p>
      </div>
    );
  }

  return (
    <div className={styles.constructionList}>
      {constructions.map((c) => (
        <div key={c.parlay_id} className={styles.constructionCard}>
          <div className={styles.constructionHeader}>
            <span className={styles.neutral}>
              <span className={styles.jointProb}>
                ≈ {(c.joint_prob * 100).toFixed(1)}% both hit
              </span>
            </span>
            <span className={styles.constructionMeta}>
              {typeof c.lift === "number" && (
                <span className={styles.constructionMetaPrimary}>
                  correlation ×{c.lift.toFixed(2)}
                </span>
              )}
              {typeof c.lift_n === "number" && (
                <span>based on {c.lift_n.toLocaleString()} games</span>
              )}
            </span>
          </div>

          <div className={styles.legList}>
            {c.legs.map((leg, j) => (
              <div key={j} className={styles.legRow}>
                <span className={styles.legName}>
                  <GameMatchup leg={leg} />
                  <span className={styles.legLabel}>{leg.label}</span>
                  <span className={styles.legSide}>{leg.side}</span>
                </span>
                <span className={styles.legData}>
                  <span className={styles.legLine}>{leg.line}</span>
                  <span>
                    {formatOdds(leg.odds)}
                    {leg.book ? ` · ${leg.book}` : ""}
                  </span>
                  <span className={styles.legMarketProb}>{formatPercent(leg.market_prob)}</span>
                </span>
              </div>
            ))}
          </div>

          <p className={styles.tierNote}>
            Reference payout {c.combined_odds.toFixed(2)}x — not a placeable price. This is what
            the two legs would pay if they were independent bets; a sportsbook reprices or
            restricts correlated same-game legs.
          </p>

          {c.small_sample && (
            <p className={styles.tierNote}>
              Small sample — this correlation is measured over under a season of shared history,
              so treat it as provisional.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
