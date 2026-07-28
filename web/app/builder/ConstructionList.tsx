import type { BuilderConstruction, BuilderLeg } from "../lib/api";
import { nickname } from "../lib/teamNames";
import styles from "./builder.module.css";

function formatOdds(odds: number): string {
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

// Matchup line rendered above leg.label (docs/superpowers/plans/
// 2026-07-28-leg-team-names.md):
// - Player leg with a resolved player_team_side: player's own team first and
//   bold, then the opponent -- `**{team}** vs {opponent}`.
// - Team leg, or a player leg whose side didn't resolve (traded/unknown):
//   plain matchup -- `{away} @ {home}`.
// - Both team names null (unresolved) -> render nothing.
function LegMatchup({ leg }: { leg: BuilderLeg }) {
  const home = nickname(leg.home_team);
  const away = nickname(leg.away_team);
  if (home === null && away === null) return null;

  if (leg.player_team_side !== null) {
    const playerTeam = leg.player_team_side === "home" ? home : away;
    const oppTeam = leg.player_team_side === "home" ? away : home;
    return (
      <span className={styles.legMatchup}>
        <span className={styles.legMatchupTeam}>{playerTeam}</span> vs {oppTeam}
      </span>
    );
  }

  return (
    <span className={styles.legMatchup}>
      {away} @ {home}
    </span>
  );
}

export default function ConstructionList({
  constructions,
  emptyTitle = "No parlays recorded yet",
  emptyBody = "The nightly builder search runs once per slate and saves its lowest-risk results here. Check back after tonight's search completes.",
}: {
  constructions: BuilderConstruction[];
  emptyTitle?: string;
  emptyBody?: string;
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
                  <LegMatchup leg={leg} />
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
