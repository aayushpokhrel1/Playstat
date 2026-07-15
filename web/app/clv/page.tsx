import Link from "next/link";
import { getClvSummary } from "../lib/api";
import { statLabel } from "../lib/statLabels";
import RetryButton from "./RetryButton";
import styles from "./clv.module.css";

function formatClv(value: number): string {
  const pct = value * 100;
  if (pct === 0) return "0.0%";
  const sign = pct > 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export default async function ClvPage() {
  let summary;
  let fetchError: string | null = null;

  try {
    summary = await getClvSummary();
  } catch {
    fetchError = "Can't reach the Playstat API at localhost:8000. Make sure the service is running.";
  }

  const totalRecords = summary ? summary.reduce((sum, row) => sum + row.n, 0) : 0;

  return (
    <main className={styles.root}>
      <div className={styles.container}>
        <Link href="/" className={styles.back}>
          ← Back
        </Link>

        <div className={styles.header}>
          <h1 className={styles.title}>Model performance</h1>
          {summary && summary.length > 0 && (
            <span className={styles.meta}>
              {totalRecords} record{totalRecords === 1 ? "" : "s"} · {summary.length} stat
              {summary.length === 1 ? "" : "s"}
            </span>
          )}
        </div>

        <p className={styles.intro}>
          Closing-line value (CLV) tracks whether the market keeps moving toward our flagged
          positions after we flag them — the leading indicator of whether an edge is real.
          Positive CLV means we&apos;re ahead of the closing line; negative means we&apos;re
          being picked off.
        </p>

        <section className={styles.section} aria-label="CLV by stat type">
          {fetchError ? (
            <div className={styles.errorState}>
              <p className={styles.emptyStateTitle}>{fetchError}</p>
              <RetryButton />
            </div>
          ) : summary && summary.length > 0 ? (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th className={styles.th} scope="col">
                      Stat
                    </th>
                    <th className={styles.th} scope="col">
                      N
                    </th>
                    <th className={styles.th} scope="col">
                      Avg CLV
                    </th>
                    <th className={styles.th} scope="col">
                      % positive
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {summary.map((row) => (
                    <tr key={row.stat_type} className={styles.row}>
                      <td className={`${styles.td} ${styles.statCell}`}>{statLabel(row.stat_type)}</td>
                      <td className={`${styles.td} ${styles.data}`}>{row.n}</td>
                      <td
                        className={`${styles.td} ${row.avg_clv >= 0 ? styles.clvPositive : styles.clvNegative}`}
                      >
                        {formatClv(row.avg_clv)}
                      </td>
                      <td className={`${styles.td} ${styles.data}`}>{formatPercent(row.pct_positive)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className={styles.emptyState}>
              <p className={styles.emptyStateTitle}>No CLV records yet</p>
              <p>
                CLV compares our flagged lines against the closing line to check whether edges
                hold up once the market has finished moving. The first records land the morning
                after the first slate with flagged edges finishes — check back after Sat, Jul
                18.
              </p>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
