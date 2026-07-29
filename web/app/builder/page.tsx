import Link from "next/link";
import { getBuilderRecord, getBuilderRecordDaily, getSavedBuilderParlays } from "../lib/api";
import BuilderControls from "./BuilderControls";
import ConstructionList from "./ConstructionList";
import RecordPanel from "./RecordPanel";
import RetryButton from "./RetryButton";
import styles from "./builder.module.css";

export default async function BuilderPage() {
  let saved;
  let teamSaved;
  let builderRecord;
  let builderRecordDaily;
  let fetchError: string | null = null;

  try {
    [saved, teamSaved, builderRecord, builderRecordDaily] = await Promise.all([
      getSavedBuilderParlays(10, "player"),
      getSavedBuilderParlays(10, "team"),
      getBuilderRecord(),
      getBuilderRecordDaily(),
    ]);
  } catch {
    fetchError = "Can't reach the Playstat API at localhost:8000. Make sure the service is running.";
  }

  // Scope both tiers to the SAME, most-recent slate present in the data. The
  // team tier is saved sparsely, so without this it can show a days-old slate
  // under a "Tonight's" heading while the player tier shows today's — the exact
  // confusion this fixes. Keying off the latest slate PRESENT (not the wall
  // clock) keeps the freshly-built card visible instead of blanking the page in
  // the pre-dawn window before the next build runs. created_at is a timestamptz
  // whose date prefix is ET (-04:00); a plain string compare on that prefix is
  // correct and needs no timezone math. (The endpoint still returns newest-N
  // regardless of date — Budgerr relies on that, so this stays client-side.)
  const slateOf = (p: { created_at: string }) => p.created_at.slice(0, 10);
  const latestSlate = [...(saved ?? []), ...(teamSaved ?? [])].reduce(
    (mx, p) => (slateOf(p) > mx ? slateOf(p) : mx),
    "",
  );
  const onLatestSlate = (p: { created_at: string }) => slateOf(p) === latestSlate;
  const savedLatest = (saved ?? []).filter(onLatestSlate);
  const teamSavedLatest = (teamSaved ?? []).filter(onLatestSlate);
  const slateLabel = latestSlate
    ? new Date(`${latestSlate}T12:00:00`).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        timeZone: "America/New_York",
      })
    : null;

  return (
    <main className={styles.root}>
      <div className={styles.container}>
        <Link href="/" className={styles.back}>
          ← Back
        </Link>

        <div className={styles.header}>
          <h1 className={styles.title}>Parlay Builder</h1>
          {slateLabel && savedLatest.length > 0 && (
            <span className={styles.meta}>
              {slateLabel} slate · {savedLatest.length} saved
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
              <RecordPanel rows={builderRecord ?? []} daily={builderRecordDaily ?? []} />
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
              <BuilderControls initial={savedLatest} />
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
                constructions={teamSavedLatest}
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
