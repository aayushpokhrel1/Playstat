import Link from "next/link";
import { getBuilderRecord, getSavedBuilderParlays, type BuilderRecord } from "../lib/api";
import BuilderControls from "./BuilderControls";
import ConstructionList from "./ConstructionList";
import RetryButton from "./RetryButton";
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

export default async function BuilderPage() {
  let saved;
  let teamSaved;
  let builderRecord;
  let fetchError: string | null = null;

  try {
    [saved, teamSaved, builderRecord] = await Promise.all([
      getSavedBuilderParlays(10, "player"),
      getSavedBuilderParlays(10, "team"),
      getBuilderRecord(),
    ]);
  } catch {
    fetchError = "Can't reach the Playstat API at localhost:8000. Make sure the service is running.";
  }

  const builderRecordRows = builderRecord ?? [];
  const hasBuilderRecord = builderRecordRows.length > 0;
  const hasTeamRows = builderRecordRows.some((row) => row.tier === "team");

  return (
    <main className={styles.root}>
      <div className={styles.container}>
        <Link href="/" className={styles.back}>
          ← Back
        </Link>

        <div className={styles.header}>
          <h1 className={styles.title}>Parlay Builder</h1>
          {saved && saved.length > 0 && (
            <span className={styles.meta}>
              {saved.length} saved
            </span>
          )}
        </div>

        {fetchError ? (
          <section className={styles.section} aria-label="Parlay builder">
            <div className={styles.errorState}>
              <p className={styles.emptyStateTitle}>{fetchError}</p>
              <RetryButton />
            </div>
          </section>
        ) : (
          <>
            <section className={styles.section} aria-label="Betting record (paper)">
              {hasBuilderRecord ? (
                <div className={styles.recordTable}>
                  {builderRecordRows.map((row) => (
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
                      <span className={styles.recordCopy}>
                        Team — no settled team parlays yet —
                      </span>
                    </div>
                  )}
                  <p className={styles.recordCaption}>Paper trading only — not a real bet.</p>
                </div>
              ) : (
                <div className={styles.recordStrip}>
                  <span className={styles.recordFigure}>0-0-0 · n=0</span>
                  <span className={styles.recordCopy}>
                    No settled builder parlays yet — the record starts once tonight&apos;s
                    slate finishes.
                  </span>
                </div>
              )}
            </section>

            <section className={styles.section} aria-label="How to read this">
              <div className={styles.framing}>
                <p>
                  <strong>Joint probability</strong> is the chance the whole parlay hits — every
                  leg has to land. A parlay priced around 2x is roughly a coin flip once you
                  multiply the legs together.
                </p>
                <p>
                  Each additional leg adds another vig bite, so at a fixed payout, fewer legs is
                  the safer construction.
                </p>
                <p>
                  Rankings here are on the book&apos;s de-vigged market price, not our model. The
                  model probability shown per leg is context only — it never decides ranking.
                </p>
                <p>This is paper only. Nothing here is a real bet or a recommendation to place one.</p>
              </div>
            </section>

            <section className={styles.section} aria-label="Tonight's low-risk parlays">
              <div className={styles.sectionHeader}>
                <h2 className={styles.sectionTitle}>Tonight&apos;s low-risk parlays</h2>
              </div>
              <BuilderControls initial={saved ?? []} />
            </section>

            <section className={styles.section} aria-label="Team-market parlays">
              <div className={styles.sectionHeader}>
                <h2 className={styles.sectionTitle}>Team-market parlays</h2>
              </div>
              <p className={styles.tierNote}>
                NRFI / F5 team markets price close to a coin flip, so this tier is
                higher-variance than the player-prop tier above — and it may come up
                empty on any given night. That&apos;s expected, not a bug.
              </p>
              <ConstructionList
                constructions={teamSaved ?? []}
                emptyTitle="No team-market parlays tonight"
                emptyBody="NRFI/F5 lines rarely clear the safety floor, so an empty night here is normal — check back tomorrow, or after the next nightly build."
              />
            </section>
          </>
        )}
      </div>
    </main>
  );
}
