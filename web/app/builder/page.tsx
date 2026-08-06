import Link from "next/link";
import { getBuilderRecord, getBuilderRecordDaily, getSavedBuilderParlays } from "../lib/api";
import BuilderControls from "./BuilderControls";
import ConstructionList from "./ConstructionList";
import RecordPanel from "./RecordPanel";
import RetryButton from "./RetryButton";
import SportTabs from "./SportTabs";
import styles from "./builder.module.css";

// Per-sport tier-2 (the non-player tier) + copy. MLB = NRFI/F5 team markets;
// NFL = full-game total/spread/moneyline (NFL builder chain #4a/#4b).
const SPORT_CFG = {
  mlb: {
    tier2: "team" as const,
    playerHeading: "Tonight's low-risk parlays",
    tier2Heading: "Team-market parlays",
    tier2Note:
      "NRFI / F5 team markets price close to a coin flip, so this tier is higher-variance than the player-prop tier above — and it may come up empty on any given night. That's expected, not a bug.",
    tier2Empty: {
      title: "No team-market parlays tonight",
      body: "NRFI/F5 lines rarely clear the safety floor, so an empty night here is normal — check back tomorrow, or after the next nightly build.",
    },
    emptyAll: null as null | { title: string; body: string },
  },
  nfl: {
    tier2: "game" as const,
    playerHeading: "This week's low-risk parlays",
    tier2Heading: "Game-market parlays",
    tier2Note:
      "Full-game total / spread / moneyline. Moneyline favorites can clear the safety floor; totals and spreads price near a coin flip, so this tier is higher-variance and may be empty.",
    tier2Empty: {
      title: "No game-market parlays this week",
      body: "Spreads and totals rarely clear the safety floor and moneyline favorites are picked sparingly — an empty week here is normal.",
    },
    emptyAll: {
      title: "No NFL parlays yet",
      body: "The weekly NFL card builds Thursday mornings once preseason odds open (~August). Check back then.",
    },
  },
  nba: {
    tier2: "game" as const,
    playerHeading: "Tonight's low-risk parlays",
    tier2Heading: "Game-market parlays",
    tier2Note:
      "Full-game total / spread / moneyline. Moneyline favorites can clear the safety floor; totals and spreads price near a coin flip, so this tier is higher-variance and may be empty.",
    tier2Empty: {
      title: "No game-market parlays tonight",
      body: "Spreads and totals rarely clear the safety floor and moneyline favorites are picked sparingly — an empty night here is normal.",
    },
    emptyAll: {
      title: "No NBA parlays yet",
      body: "The nightly NBA card builds once the season opens (~October). Check back then.",
    },
  },
  mls: {
    tier2: "game" as const,
    playerHeading: "Today's low-risk parlays",
    tier2Heading: "Match-total parlays",
    tier2Note:
      "Match total goals (over/under). Overs on high-scoring matchups can clear the safety floor; most price near a coin flip, so this tier is higher-variance and may be empty.",
    tier2Empty: {
      title: "No match-total parlays today",
      body: "Match totals rarely clear the safety floor — an empty day here is normal.",
    },
    emptyAll: {
      title: "No MLS parlays yet",
      body: "MLS parlays build daily during the season once live odds + settlement data are connected.",
    },
  },
  ucl: {
    tier2: "game" as const,
    playerHeading: "Today's low-risk parlays",
    tier2Heading: "Match-total parlays",
    tier2Note:
      "Match total goals (over/under). Overs on high-scoring matchups can clear the safety floor; most price near a coin flip, so this tier is higher-variance and may be empty.",
    tier2Empty: {
      title: "No match-total parlays today",
      body: "Match totals rarely clear the safety floor — an empty day here is normal.",
    },
    emptyAll: {
      title: "No Champions League parlays yet",
      body: "UCL parlays build on matchdays during the season once live odds + settlement data are connected.",
    },
  },
} as const;

export default async function BuilderPage({
  searchParams,
}: {
  searchParams: Promise<{ sport?: string }>;
}) {
  const sportParam = (await searchParams).sport;
  const sport: "mlb" | "nfl" | "nba" | "mls" | "ucl" =
    sportParam === "nfl" ? "nfl" : sportParam === "nba" ? "nba" : sportParam === "mls" ? "mls" : sportParam === "ucl" ? "ucl" : "mlb";
  const cfg = SPORT_CFG[sport];

  let saved;
  let tier2Saved;
  let builderRecord;
  let builderRecordDaily;
  let fetchError: string | null = null;

  try {
    [saved, tier2Saved, builderRecord, builderRecordDaily] = await Promise.all([
      getSavedBuilderParlays(10, "player", sport),
      getSavedBuilderParlays(10, cfg.tier2, sport),
      getBuilderRecord(sport),
      getBuilderRecordDaily(sport),
    ]);
  } catch {
    fetchError = "Can't reach the Playstat API at localhost:8000. Make sure the service is running.";
  }

  // Scope both tiers to the SAME, most-recent slate present in the data. The
  // tier-2 market is saved sparsely, so without this it can show a days-old
  // slate under a "Tonight's"/"This week's" heading while the player tier
  // shows today's/this week's — the exact confusion this fixes. Keying off
  // the latest slate PRESENT (not the wall clock) keeps the freshly-built
  // card visible instead of blanking the page in the pre-dawn window before
  // the next build runs. created_at is a timestamptz whose date prefix is ET
  // (-04:00); a plain string compare on that prefix is correct and needs no
  // timezone math. (The endpoint still returns newest-N regardless of date —
  // Budgerr relies on that, so this stays client-side.)
  const slateOf = (p: { created_at: string }) => p.created_at.slice(0, 10);
  const latestSlate = [...(saved ?? []), ...(tier2Saved ?? [])].reduce(
    (mx, p) => (slateOf(p) > mx ? slateOf(p) : mx),
    "",
  );
  const onLatestSlate = (p: { created_at: string }) => slateOf(p) === latestSlate;
  const savedLatest = (saved ?? []).filter(onLatestSlate);
  const tier2Latest = (tier2Saved ?? []).filter(onLatestSlate);
  const slateLabel = latestSlate
    ? new Date(`${latestSlate}T12:00:00`).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        timeZone: "America/New_York",
      })
    : null;

  const noParlaysAtAll = !fetchError && savedLatest.length === 0 && tier2Latest.length === 0;

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

        <SportTabs active={sport} />

        {fetchError ? (
          <section className={styles.section} aria-label="Parlay builder">
            <div className={styles.errorState}>
              <p className={styles.emptyStateTitle}>{fetchError}</p>
              <RetryButton />
            </div>
          </section>
        ) : cfg.emptyAll && noParlaysAtAll ? (
          <section className={styles.section} aria-label="Parlay builder">
            <div className={styles.errorState}>
              <p className={styles.emptyStateTitle}>{cfg.emptyAll.title}</p>
              <p className={styles.tierNote}>{cfg.emptyAll.body}</p>
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

            <section className={styles.section} aria-label={cfg.playerHeading}>
              <div className={styles.sectionHeader}>
                <h2 className={styles.sectionTitle}>{cfg.playerHeading}</h2>
              </div>
              <BuilderControls initial={savedLatest} sport={sport} />
            </section>

            <section className={styles.section} aria-label={cfg.tier2Heading}>
              <div className={styles.sectionHeader}>
                <h2 className={styles.sectionTitle}>{cfg.tier2Heading}</h2>
              </div>
              <p className={styles.tierNote}>{cfg.tier2Note}</p>
              <ConstructionList
                constructions={tier2Latest}
                emptyTitle={cfg.tier2Empty.title}
                emptyBody={cfg.tier2Empty.body}
              />
            </section>
          </>
        )}
      </div>
    </main>
  );
}
