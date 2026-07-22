"use client";

import { useState } from "react";
import type { BuilderConstruction, BuilderSearchResult, SavedBuilderParlay } from "../lib/api";
import ConstructionList from "./ConstructionList";
import styles from "./builder.module.css";

type Mode = "payout" | "prob";

const DEFAULT_PAYOUT = 2.0;
const DEFAULT_PROB = 0.75;

export default function BuilderControls({ initial }: { initial: SavedBuilderParlay[] }) {
  const [mode, setMode] = useState<Mode>("payout");
  const [payout, setPayout] = useState(DEFAULT_PAYOUT);
  const [prob, setProb] = useState(DEFAULT_PROB);
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<BuilderSearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleBuild() {
    setPending(true);
    setError(null);

    const q = new URLSearchParams();
    if (mode === "payout") {
      q.set("target_payout", String(payout));
    } else {
      q.set("min_prob", String(prob));
    }

    try {
      const res = await fetch(`/api/builder-search?${q.toString()}`);
      const data = await res.json();
      if (!res.ok) {
        setError(typeof data?.error === "string" ? data.error : "Search failed.");
        setResult(null);
      } else {
        setResult(data as BuilderSearchResult);
      }
    } catch {
      setError("Search failed.");
      setResult(null);
    } finally {
      setPending(false);
    }
  }

  const constructions: BuilderConstruction[] | null = result ? result.constructions : null;

  return (
    <div>
      <div className={styles.controls}>
        <div className={styles.modeToggle} role="radiogroup" aria-label="Search mode">
          <button
            type="button"
            className={mode === "payout" ? styles.modeButtonActive : styles.modeButton}
            aria-pressed={mode === "payout"}
            onClick={() => setMode("payout")}
          >
            I want a payout of…
          </button>
          <button
            type="button"
            className={mode === "prob" ? styles.modeButtonActive : styles.modeButton}
            aria-pressed={mode === "prob"}
            onClick={() => setMode("prob")}
          >
            I want a hit chance of at least…
          </button>
        </div>

        <div className={styles.controlsRow}>
          {mode === "payout" ? (
            <>
              <label className={styles.controlField}>
                <span className={styles.controlLabel}>Target payout</span>
                <input
                  type="number"
                  className={styles.controlInput}
                  min={1.1}
                  step={0.1}
                  value={payout}
                  onChange={(e) => setPayout(Number(e.target.value))}
                />
              </label>
              <span className={styles.controlDerived}>
                hit chance ≈ the builder finds the rest
              </span>
            </>
          ) : (
            <>
              <label className={styles.controlField}>
                <span className={styles.controlLabel}>Minimum hit chance</span>
                <input
                  type="number"
                  className={styles.controlInput}
                  min={0.01}
                  max={0.99}
                  step={0.01}
                  value={prob}
                  onChange={(e) => setProb(Number(e.target.value))}
                />
              </label>
              <span className={styles.controlDerived}>
                payout ≈ the builder finds the rest
              </span>
            </>
          )}

          <button
            type="button"
            className={styles.buildButton}
            disabled={pending}
            onClick={handleBuild}
          >
            Build
          </button>
        </div>
      </div>

      {pending && (
        <div className={styles.pending}>
          Searching constructions — this takes a few seconds.
        </div>
      )}

      {!pending && error && (
        <div className={styles.errorState}>
          <p className={styles.emptyStateTitle}>{error}</p>
        </div>
      )}

      {!pending && !error && result && (
        <>
          {result.truncated && (
            <div className={styles.truncated}>
              Showing the best constructions found within the search budget; not proven optimal.
            </div>
          )}
          {constructions && constructions.length === 0 ? (
            <div className={styles.emptyState}>
              <p className={styles.emptyStateTitle}>
                Nothing on tonight&apos;s slate reaches that floor — try relaxing it.
              </p>
            </div>
          ) : (
            <ConstructionList constructions={constructions ?? []} />
          )}
        </>
      )}

      {!pending && !error && !result && <ConstructionList constructions={initial} />}
    </div>
  );
}
