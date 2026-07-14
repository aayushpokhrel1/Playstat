import Link from "next/link";
import { getEdges, getParlayRecommendations } from "../lib/api";
import EdgesExplorer from "./EdgesExplorer";
import ParlaySection from "./ParlaySection";
import RetryButton from "./RetryButton";
import styles from "./edges.module.css";

export default async function EdgesPage() {
  let edges;
  let parlays;
  let fetchError: string | null = null;

  try {
    [edges, parlays] = await Promise.all([getEdges(), getParlayRecommendations()]);
  } catch {
    fetchError = "Can't reach the Playstat API at localhost:8000. Make sure the service is running.";
  }

  const gameCount = edges ? new Set(edges.map((e) => e.game_id)).size : 0;

  return (
    <main className={styles.root}>
      <div className={styles.container}>
        <Link href="/" className={styles.back}>
          ← Back
        </Link>

        <div className={styles.header}>
          <h1 className={styles.title}>Tonight&apos;s Edges</h1>
          {edges && edges.length > 0 && (
            <span className={styles.meta}>
              {edges.length} edge{edges.length === 1 ? "" : "s"} · {gameCount} game
              {gameCount === 1 ? "" : "s"}
            </span>
          )}
        </div>

        {fetchError ? (
          <section className={styles.section} aria-label="Tonight's edges">
            <div className={styles.errorState}>
              <p className={styles.emptyStateTitle}>{fetchError}</p>
              <RetryButton />
            </div>
          </section>
        ) : (
          <>
            <section className={styles.section} aria-label="Tonight's edges">
              <EdgesExplorer edges={edges ?? []} />
            </section>

            <section className={styles.section}>
              <div className={styles.sectionHeader}>
                <h2 className={styles.sectionTitle}>Suggested parlays</h2>
              </div>
              <ParlaySection parlays={parlays ?? []} />
            </section>
          </>
        )}
      </div>
    </main>
  );
}
