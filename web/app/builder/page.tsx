import Link from "next/link";
import { getBetPerformance, getSavedBuilderParlays } from "../lib/api";
import BuilderControls from "./BuilderControls";
import RetryButton from "./RetryButton";
import styles from "./builder.module.css";

export default async function BuilderPage() {
  let saved;
  let betPerformance;
  let fetchError: string | null = null;

  try {
    [saved, betPerformance] = await Promise.all([getSavedBuilderParlays(), getBetPerformance()]);
  } catch {
    fetchError = "Can't reach the Playstat API at localhost:8000. Make sure the service is running.";
  }

  const builderRecord = betPerformance?.find((row) => row.bet_type === "parlay_builder") ?? null;

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
              <div className={styles.recordStrip}>
                {builderRecord ? (
                  <>
                    <span className={styles.recordFigure}>
                      {builderRecord.wins}-{builderRecord.losses}-{builderRecord.pushes} · ROI{" "}
                      {(builderRecord.roi * 100).toFixed(1)}% · n={builderRecord.n}
                    </span>
                  </>
                ) : (
                  <>
                    <span className={styles.recordFigure}>0-0-0 · n=0</span>
                    <span className={styles.recordCopy}>
                      No settled builder parlays yet — the record starts once tonight&apos;s
                      slate finishes.
                    </span>
                  </>
                )}
              </div>
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
          </>
        )}
      </div>
    </main>
  );
}
