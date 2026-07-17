import Link from "next/link";
import type { Edge, EdgeDistribution } from "../lib/api";
import { getEdgeDistributions, getEdges, getParlayRecommendations } from "../lib/api";
import EdgesExplorer from "./EdgesExplorer";
import ParlaySection from "./ParlaySection";
import RetryButton from "./RetryButton";
import styles from "./edges.module.css";

function distributionKey(e: { player_id: number; game_id: number; stat_type: string }): string {
  return `${e.player_id}-${e.game_id}-${e.stat_type}`;
}

// Merges each edge's full PMF (from /edge-distributions) into the edge it
// belongs to by (player_id, game_id, stat_type) — the two endpoints share
// that key but /edge-distributions is additive, so an edge simply has no
// `distribution` if the join finds nothing (e.g. transient timing between
// the two queries).
function mergeDistributions(edges: Edge[], distributions: EdgeDistribution[]) {
  const byKey = new Map(distributions.map((d) => [distributionKey(d), d]));
  return edges.map((e) => ({ ...e, distribution: byKey.get(distributionKey(e)) ?? null }));
}

export default async function EdgesPage() {
  let edges;
  let distributions;
  let parlays;
  let fetchError: string | null = null;

  try {
    [edges, distributions, parlays] = await Promise.all([
      getEdges(),
      getEdgeDistributions(),
      getParlayRecommendations(),
    ]);
  } catch {
    fetchError = "Can't reach the Playstat API at localhost:8000. Make sure the service is running.";
  }

  const edgesWithDistributions = edges ? mergeDistributions(edges, distributions ?? []) : [];

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
              <EdgesExplorer edges={edgesWithDistributions} />
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
