import Link from "next/link";
import { getBetPerformance, getClvSummary } from "../lib/api";
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

function formatUnits(value: number): string {
  if (value === 0) return "0.00u";
  const sign = value > 0 ? "+" : "−";
  return `${sign}${Math.abs(value).toFixed(2)}u`;
}

const BET_TYPE_LABEL: Record<string, string> = {
  parlay: "Parlay",
  edge: "Edge",
  all: "All",
};

export default async function ClvPage() {
  let summary;
  let fetchError: string | null = null;

  try {
    summary = await getClvSummary();
  } catch {
    fetchError = "Can't reach the Playstat API at localhost:8000. Make sure the service is running.";
  }

  let betPerformance;
  let betPerformanceError: string | null = null;

  try {
    betPerformance = await getBetPerformance();
  } catch {
    betPerformanceError = "Can't reach the Playstat API at localhost:8000. Make sure the service is running.";
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
          The paper-trading ledger records whether every recommended parlay and every flagged
          edge (≥3%) would have won, at the odds quoted when it was recommended — the
          project&apos;s real report card, complementing CLV below (which only measures market
          agreement, not profit).
        </p>

        <section className={styles.section} aria-label="Betting record (paper)">
          <h2 className={styles.sectionTitle}>Betting record (paper)</h2>
          {betPerformanceError ? (
            <div className={styles.errorState}>
              <p className={styles.emptyStateTitle}>{betPerformanceError}</p>
              <RetryButton />
            </div>
          ) : betPerformance && betPerformance.length > 0 ? (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th className={styles.th} scope="col">
                      Bet type
                    </th>
                    <th className={styles.th} scope="col">
                      Record (W-L-P)
                    </th>
                    <th className={styles.th} scope="col">
                      P&amp;L
                    </th>
                    <th className={styles.th} scope="col">
                      ROI
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {betPerformance.map((row) => (
                    <tr key={row.bet_type} className={styles.row}>
                      <td className={`${styles.td} ${styles.statCell}`}>
                        {BET_TYPE_LABEL[row.bet_type] ?? row.bet_type}
                      </td>
                      <td className={`${styles.td} ${styles.data}`}>
                        {row.wins}-{row.losses}-{row.pushes}
                      </td>
                      <td
                        className={`${styles.td} ${row.total_pnl >= 0 ? styles.clvPositive : styles.clvNegative}`}
                      >
                        {formatUnits(row.total_pnl)}
                      </td>
                      <td
                        className={`${styles.td} ${row.roi >= 0 ? styles.clvPositive : styles.clvNegative}`}
                      >
                        {formatClv(row.roi)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className={styles.emptyState}>
              <p className={styles.emptyStateTitle}>No settled bets yet</p>
              <p>
                The first settlements land the morning after the first slate with recommended
                bets finishes (after Sat, Jul 18).
              </p>
            </div>
          )}
        </section>

        <section className={styles.section} aria-label="CLV by stat type">
          <h2 className={styles.sectionTitle}>Closing-line value</h2>
          <p className={styles.intro}>
            Closing-line value (CLV) tracks whether the market keeps moving toward our flagged
            positions after we flag them — the leading indicator of whether an edge is real.
            Positive CLV means we&apos;re ahead of the closing line; negative means we&apos;re
            being picked off.
          </p>
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
