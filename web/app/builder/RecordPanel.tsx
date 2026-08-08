"use client";

import { useState } from "react";
import type {
  BuilderRecord,
  BuilderRecordDaily,
  DailyParlay,
  DailyParlayLeg,
} from "../lib/api";
import styles from "./builder.module.css";

const TIER_LABELS: Record<BuilderRecord["tier"], string> = {
  player: "Player",
  team: "Team",
  game: "Game",
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

// Sum of ¼-Kelly stakes, always ≥ 0 — plain units, no sign (this is exposure,
// not P&L). ROI above is pnl/staked, so a low-staked row is a small real bet.
function formatStaked(value: number): string {
  return `${value.toFixed(2)}u staked`;
}

function tierTargetLabel(row: BuilderRecord): string {
  return `${TIER_LABELS[row.tier] ?? row.tier} ${row.target_payout.toFixed(1)}x`;
}

// Per-leg settlement outcome as a monochrome glyph — no signal-green (reserved
// for the ≥75% joint-prob rule); the ✓/✗/– shapes carry the meaning.
function resultGlyph(result: string | null): string {
  if (result === "hit" || result === "won") return "✓";
  if (result === "miss" || result === "lost") return "✗";
  if (result === "void") return "–";
  return "·"; // pending / unknown
}

function LegRow({ leg }: { leg: DailyParlayLeg }) {
  const actual = leg.actual === null ? "—" : leg.actual;
  const line = leg.line === null ? "—" : leg.line;
  return (
    <div className={styles.legRow}>
      <span className={styles.legGlyph} aria-hidden="true">
        {resultGlyph(leg.result)}
      </span>
      <span className={styles.legLabel}>{leg.label ?? "—"}</span>
      <span className={styles.recordMeta}>
        {actual} / {line}
      </span>
    </div>
  );
}

function ParlayRow({ parlay }: { parlay: DailyParlay }) {
  const [open, setOpen] = useState(false);
  const panelId = `parlay-${parlay.parlay_id}`;
  return (
    <div className={styles.parlayRow}>
      <button
        type="button"
        className={styles.parlayToggle}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={styles.parlayResult}>{parlay.result.toUpperCase()}</span>
        <span className={styles.recordLabel}>
          {TIER_LABELS[parlay.tier] ?? parlay.tier} {parlay.target_payout.toFixed(1)}x
        </span>
        <span className={styles.recordFigure}>{formatUnits(parlay.pnl)}</span>
        <span className={styles.recordMeta}>
          {parlay.stake.toFixed(2)}u @ {parlay.combined_odds.toFixed(2)}x
        </span>
      </button>
      {open && (
        <div id={panelId} className={styles.legList}>
          {parlay.legs.map((leg, i) => (
            <LegRow key={i} leg={leg} />
          ))}
        </div>
      )}
    </div>
  );
}

function DayRow({ day, parlays }: { day: BuilderRecordDaily; parlays: DailyParlay[] }) {
  const [open, setOpen] = useState(false);
  const panelId = `day-${day.date}`;
  const hasParlays = parlays.length > 0;
  return (
    <div className={styles.dailyRow}>
      <button
        type="button"
        className={styles.dayToggle}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        disabled={!hasParlays}
      >
        <span className={styles.recordLabel}>{day.date}</span>
        <span className={styles.recordFigure}>
          {day.wins}-{day.losses}-{day.pushes}
        </span>
        <span className={styles.recordFigure}>{formatUnits(day.pnl)}</span>
        <span className={styles.recordFigure}>{formatRoi(day.roi)}</span>
        <span className={styles.recordMeta}>
          n={day.n} · {formatStaked(day.staked)}
        </span>
      </button>
      {open && hasParlays && (
        <div id={panelId} className={styles.parlayGroup}>
          {parlays.map((parlay) => (
            <ParlayRow key={parlay.parlay_id} parlay={parlay} />
          ))}
        </div>
      )}
    </div>
  );
}

type RecordPanelProps = {
  rows: BuilderRecord[];
  daily: BuilderRecordDaily[];
  parlaysByDate: Record<string, DailyParlay[]>;
};

export default function RecordPanel({ rows, daily, parlaysByDate }: RecordPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const hasBuilderRecord = rows.length > 0;
  const hasTeamRows = rows.some((row) => row.tier === "team");
  const hasDaily = daily.length > 0;

  if (!hasBuilderRecord) {
    return (
      <div className={styles.recordStrip}>
        <span className={styles.recordFigure}>0-0-0 · n=0</span>
        <span className={styles.recordCopy}>
          No settled builder parlays yet — the record starts once tonight&apos;s slate finishes.
        </span>
      </div>
    );
  }

  return (
    <div className={styles.recordTable}>
      {rows.map((row) => (
        <div key={`${row.tier}-${row.target_payout}`} className={styles.recordRow}>
          <span className={styles.recordLabel}>{tierTargetLabel(row)}</span>
          <span className={styles.recordFigure}>
            {row.wins}-{row.losses}-{row.pushes}
          </span>
          <span className={styles.recordFigure}>{formatUnits(row.pnl)}</span>
          <span className={styles.recordFigure}>{formatRoi(row.roi)}</span>
          <span className={styles.recordMeta}>n={row.n}</span>
          <span className={styles.recordMeta}>{formatStaked(row.staked)}</span>
        </div>
      ))}
      {!hasTeamRows && (
        <div className={styles.recordRowEmpty}>
          <span className={styles.recordCopy}>Team — no settled team parlays yet —</span>
        </div>
      )}

      <div className={styles.recordFooter}>
        <p className={styles.recordCaption}>
          Paper trading only — not a real bet. Stakes are ¼-Kelly sized on the
          shopped price with a nightly exposure cap; a card with no price edge is
          staked 0, so ROI is per unit staked.
        </p>
        {hasDaily && (
          <button
            type="button"
            className={styles.recordToggle}
            aria-expanded={expanded}
            aria-controls="builder-record-daily"
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "Hide" : "Show"} per-day breakdown ({daily.length}{" "}
            {daily.length === 1 ? "day" : "days"})
          </button>
        )}
      </div>

      {expanded && hasDaily && (
        <div id="builder-record-daily" className={styles.dailyPanel}>
          <p className={styles.dailyCaption}>
            Early slate dates are small samples — a handful of settled parlays can swing
            W-L-P and ROI a lot day to day (README §15 calibration note). Read these as
            noisy until the sample builds up. Expand a day to see each parlay, and a
            parlay to see which leg landed (✓) or missed (✗).
          </p>
          <div className={styles.dailyList}>
            {daily.map((day) => (
              <DayRow key={day.date} day={day} parlays={parlaysByDate[day.date] ?? []} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
