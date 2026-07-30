# NFL dashboard view + shelved-model cleanup (#4b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface NFL builder parlays on the dashboard via a URL-param sport selector, and remove the shelved model-performance + edges frontend.

**Architecture:** A small additive backend change exposes the NFL game tier by name (`tier=game`) and labels it in the record; `api.ts` builder fetchers gain an additive `sport`; the builder page becomes sport-parameterized (server-rendered `?sport=` with MLB|NFL tabs) reusing the existing layout; the `/edges` and `/clv` frontend routes + home links + their dead `api.ts` exports are deleted (backend endpoints untouched).

**Tech Stack:** Next.js **16.x** (breaking changes vs training data — read `web/node_modules/next/dist/docs/`), React server components, TypeScript, Python 3.11 / FastAPI / pytest.

**Spec:** [docs/superpowers/specs/2026-07-29-nfl-dashboard-view-design.md](../specs/2026-07-29-nfl-dashboard-view-design.md)

## Global Constraints

- **Read before writing UI:** `PRODUCT.md` + `DESIGN.md` (repo root) and `web/AGENTS.md`, then `web/node_modules/next/dist/docs/` for the Next 16 APIs you use. **`searchParams` is async in Next 16** — the page must `await` it; confirm the exact signature in the installed docs.
- **Design system:** near-black terminal surface, ONE signal-green accent **reserved for the existing ≥0.75 joint-prob rule** — the sport tabs + NFL sections use the muted terminal palette, NOT signal-green. Match `web/app/builder/builder.module.css` + `web/app/edges/` conventions.
- **No "+EV"/"edge"/"value"/"beat the market" language; no signal-green** beyond the existing rule (guardrail §15.8).
- **NO TEST DATABASE. `ingestion.db.get_engine()` is the LIVE production DB.** Backend tests are pure/fake-engine (monkeypatch `api.main.engine`); never open a real connection.
- **Additive-only API.** New `sport`/`tier=game` params default to today's behavior; `tier=player/team/all` and no-`sport` callers are unchanged (Budgerr consumes `tier=all` — do not disturb it).
- **Backend endpoints are NOT removed.** `/edges`, `/game-predictions`, `/parlay-recommendations`, `/bet-performance`, `/clv-summary` keep serving (frozen; Budgerr reads `/edges`). This sub-project deletes only FRONTEND routes.
- **DO NOT `git push`.** Commit in the worktree only; the architect reviews, kickstarts, browser-verifies, and merges.
- **Worktree:** `graphify-out/` gitignored/absent — read source directly. `graphify` rule is unfollowable here. Python interpreter: `/Users/aayushpokhrel/dev/playstat/.venv/bin/python`. In `web/`, run `npm install` first (node_modules not checked out), then `npx tsc --noEmit` and `npm run build`. Baseline backend suite: **345 passing**.

---

## Task 1: Backend tier plumbing for the NFL game tier

**Files:**
- Modify: `api/main.py` (`TIER_TO_CLASS`, `_CLASS_TO_TIER`, `_TIER_SORT_ORDER`)
- Test: `tests/test_builder_record_api.py`

**Interfaces:**
- Produces: `/parlay-builder/saved?tier=game` → `class='game_tier'` rows; `/parlay-builder/record` labels a `game_tier` row as tier `"game"`, sorted after `team`.

- [ ] **Step 1: Write failing tests** (`tests/test_builder_record_api.py`):

```python
def test_game_tier_maps_to_game_label():
    rows = [("game_tier", 1.4, 5, 3, 2, 0, 1.2)]
    out = api_main._shape_builder_record(rows)
    assert out[0].tier == "game"

def test_tier_sort_orders_player_team_game():
    rows = [
        ("game_tier", 1.4, 5, 3, 2, 0, 1.2),
        ("team_tier", 1.4, 5, 3, 2, 0, 1.2),
        ("across_game", 1.4, 5, 3, 2, 0, 1.2),
    ]
    out = api_main._shape_builder_record(rows)
    assert [r.tier for r in out] == ["player", "team", "game"]

def test_tier_to_class_has_game_entry():
    assert api_main.TIER_TO_CLASS["game"] == "game_tier"
```

- [ ] **Step 2: Run, verify fail.** `/.venv/bin/python -m pytest tests/test_builder_record_api.py -q -k "game_tier or tier_sort or game_entry"` → FAIL.

- [ ] **Step 3: Implement** (`api/main.py`). Add the `game` entries to the three maps:

```python
TIER_TO_CLASS = {"player": "across_game", "team": "team_tier", "game": "game_tier"}
```
```python
_CLASS_TO_TIER = {"across_game": "player", "team_tier": "team", "game_tier": "game"}
```
For `_TIER_SORT_ORDER` (currently `{"player": 0, "team": 1}`), add `"game": 2`:
```python
_TIER_SORT_ORDER = {"player": 0, "team": 1, "game": 2}
```

> Do not change the saved endpoint's `tier`-validation logic — adding `game` to `TIER_TO_CLASS` makes `tier=game` valid automatically (the guard is `tier != "all" and tier not in TIER_TO_CLASS`).

- [ ] **Step 4: Run, verify pass.** Same command → PASS.

- [ ] **Step 5: Full backend suite.** `/.venv/bin/python -m pytest -q` → 345 + 3 green.

- [ ] **Step 6: Commit.**
```bash
git add api/main.py tests/test_builder_record_api.py
git commit -m "feat(api): expose NFL game tier — tier=game maps to game_tier + record label"
```

---

## Task 2: `api.ts` — additive `sport` on builder fetchers

**Files:**
- Modify: `web/app/lib/api.ts`

**Interfaces:**
- Produces: `getSavedBuilderParlays(limit, tier, sport)`, `getBuilderRecord(sport)`, `getBuilderRecordDaily(sport)` — all `sport` defaulting to `"mlb"`; `BuilderRecord.tier` widened to include `"game"`; `getSavedBuilderParlays` tier type widened to include `"game"`.

- [ ] **Step 1: `npm install` in `web/`** (node_modules not checked out): `cd web && npm install`.

- [ ] **Step 2: Edit `web/app/lib/api.ts`.** Widen the record tier type:
```ts
export type BuilderRecord = {
  tier: "player" | "team" | "game";
  target_payout: number;
  n: number; wins: number; losses: number; pushes: number; pnl: number; roi: number;
};
```
Add `sport` to the three fetchers:
```ts
export function getBuilderRecord(sport = "mlb") {
  return apiGet<BuilderRecord[]>(`/parlay-builder/record?sport=${sport}`);
}

export function getBuilderRecordDaily(sport = "mlb") {
  return apiGet<BuilderRecordDaily[]>(`/parlay-builder/record/daily?sport=${sport}`);
}

export function getSavedBuilderParlays(
  limit = 10,
  tier: "player" | "team" | "game" | "all" = "player",
  sport = "mlb",
) {
  return apiGet<SavedBuilderParlay[]>(`/parlay-builder/saved?limit=${limit}&tier=${tier}&sport=${sport}`);
}
```

- [ ] **Step 3: Typecheck.** `cd web && npx tsc --noEmit` → clean (the existing no-`sport` callers still typecheck via defaults).

- [ ] **Step 4: Commit.**
```bash
git add web/app/lib/api.ts
git commit -m "feat(web): additive sport param on builder fetchers + game record tier"
```

---

## Task 3: Sport selector + per-sport builder page

**Files:**
- Create: `web/app/builder/SportTabs.tsx`
- Modify: `web/app/builder/page.tsx`
- Modify (styles): `web/app/builder/builder.module.css` (tab styles)

**Interfaces:**
- Consumes: Task 2's `getSavedBuilderParlays(limit, tier, sport)`, `getBuilderRecord(sport)`, `getBuilderRecordDaily(sport)`.

- [ ] **Step 1: Read the Next 16 docs** for `searchParams` in a server component page (`web/node_modules/next/dist/docs/`), confirming whether it is a Promise and the exact prop shape. Match what the installed version requires — the code below assumes the Next-16 async form; adjust if the docs differ.

- [ ] **Step 2: Create `web/app/builder/SportTabs.tsx`** — a server component (no client state), two links:

```tsx
import Link from "next/link";
import styles from "./builder.module.css";

const SPORTS: { key: string; label: string }[] = [
  { key: "mlb", label: "MLB" },
  { key: "nfl", label: "NFL" },
];

export default function SportTabs({ active }: { active: string }) {
  return (
    <nav className={styles.sportTabs} aria-label="Sport">
      {SPORTS.map((s) => (
        <Link
          key={s.key}
          href={`/builder?sport=${s.key}`}
          className={s.key === active ? styles.sportTabActive : styles.sportTab}
          aria-current={s.key === active ? "page" : undefined}
        >
          {s.label}
        </Link>
      ))}
    </nav>
  );
}
```

- [ ] **Step 3: Add tab styles to `web/app/builder/builder.module.css`.** Muted terminal palette (NOT signal-green); match the existing `.back`/`.meta` tone. Example (adapt to the file's tokens/variables):

```css
.sportTabs { display: flex; gap: 0.25rem; margin-bottom: 1.25rem; }
.sportTab, .sportTabActive {
  padding: 0.35rem 0.9rem; border-radius: 6px; font-size: 0.9rem;
  font-family: var(--font-mono, monospace); text-decoration: none;
  border: 1px solid var(--border, #2a2a2a); color: var(--text-muted, #888);
}
.sportTabActive { color: var(--text, #eee); background: var(--surface-raised, #1a1a1a); border-color: var(--border-strong, #444); }
.sportTab:hover { color: var(--text, #eee); }
```

> Use the actual CSS variables already defined in `builder.module.css`/`globals.css` — the values above are fallbacks. Do not introduce signal-green.

- [ ] **Step 4: Rewrite `web/app/builder/page.tsx`** to be sport-parameterized. Resolve the sport from `searchParams`, pick the per-sport tier-2 config, fetch, and render. Key structure (adapt the section JSX from the existing file — keep RecordPanel, framing, player section, tier-2 section; add SportTabs; make headings/copy/empty-state sport-aware):

```tsx
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
} as const;

export default async function BuilderPage({
  searchParams,
}: {
  searchParams: Promise<{ sport?: string }>;
}) {
  const sportParam = (await searchParams).sport;
  const sport: "mlb" | "nfl" = sportParam === "nfl" ? "nfl" : "mlb";
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
        month: "short", day: "numeric", timeZone: "America/New_York",
      })
    : null;

  const noParlaysAtAll = !fetchError && savedLatest.length === 0 && tier2Latest.length === 0;

  return (
    <main className={styles.root}>
      <div className={styles.container}>
        <Link href="/" className={styles.back}>← Back</Link>

        <div className={styles.header}>
          <h1 className={styles.title}>Parlay Builder</h1>
          {slateLabel && savedLatest.length > 0 && (
            <span className={styles.meta}>{slateLabel} slate · {savedLatest.length} saved</span>
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
                <p><strong>Joint probability</strong> is the chance the whole parlay hits — every leg has to land. A parlay priced around 2x is roughly a coin flip once you multiply the legs together.</p>
                <p>Each additional leg adds another vig bite, so at a fixed payout, fewer legs is the safer construction.</p>
                <p>Rankings here are on the book&apos;s de-vigged market price, not our model. The model probability shown per leg is context only — it never decides ranking.</p>
                <p>This is paper only. Nothing here is a real bet or a recommendation to place one.</p>
              </div>
            </section>

            <section className={styles.section} aria-label={cfg.playerHeading}>
              <div className={styles.sectionHeader}>
                <h2 className={styles.sectionTitle}>{cfg.playerHeading}</h2>
              </div>
              <BuilderControls initial={savedLatest} />
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
```

> Verify `BuilderControls`/`ConstructionList`/`RecordPanel` prop types accept these unchanged (they already render generic constructions + records; `RecordPanel` gets a `game`-tier row via the widened `BuilderRecord.tier`). If any prop type is narrower than needed, widen it minimally — do not redesign the components.

- [ ] **Step 5: Typecheck + build.** `cd web && npx tsc --noEmit && npm run build` → clean.

- [ ] **Step 6: Commit.**
```bash
git add web/app/builder/SportTabs.tsx web/app/builder/page.tsx web/app/builder/builder.module.css
git commit -m "feat(web): MLB|NFL sport selector on the builder page (server-rendered ?sport)"
```

---

## Task 4: Remove shelved-model frontend (edges + model performance)

**Files:**
- Modify: `web/app/page.tsx` (remove 2 links)
- Delete: `web/app/edges/` and `web/app/clv/` (entire dirs)
- Modify: `web/app/lib/api.ts` (remove now-dead exports, verified)

**Interfaces:** none produced/consumed.

- [ ] **Step 1: Verify no cross-imports** of the two route dirs before deleting:
```bash
cd web && grep -rn "app/edges\|app/clv\|/edges\|/clv" app --include='*.ts' --include='*.tsx' | grep -v "app/edges/\|app/clv/"
```
Expected: only the two `Link href` lines in `app/page.tsx`. If anything else imports from those dirs, stop and report.

- [ ] **Step 2: Remove the two links** in `web/app/page.tsx` — delete the `<Link href="/edges">View tonight's edges →</Link>` and `<Link href="/clv">View model performance →</Link>` lines (and any now-empty wrapper/surrounding copy that referenced "edges"/"model performance"). Keep `<Link href="/builder">Build a low-risk parlay →</Link>` as the primary CTA and the team/player browse.

- [ ] **Step 3: Delete the route dirs.**
```bash
git rm -r web/app/edges web/app/clv
```

- [ ] **Step 4: Remove now-dead `api.ts` exports.** For each of `getEdges`, `getEdgeDistributions`, `getParlayRecommendations`, `getClvSummary`, `getBetPerformance` and their now-unused types (`Edge`, `EdgeDistribution`, `PmfPoint`, `ParlayRecommendation`, `ParlayLeg`, `ClvSummary`, `BetPerformance`): grep the remaining `web/app` for any importer:
```bash
cd web && grep -rn "getEdges\|getEdgeDistributions\|getParlayRecommendations\|getClvSummary\|getBetPerformance" app
```
Remove ONLY the functions/types with zero remaining importers. Leave any still referenced. (If a type is still used by a kept function, keep it.)

- [ ] **Step 5: Typecheck + build.** `cd web && npx tsc --noEmit && npm run build` → clean (no dangling imports; deleted routes gone).

- [ ] **Step 6: Commit.**
```bash
git add -A web/app
git commit -m "feat(web): remove shelved model-performance + edges frontend (§16)"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** Component A → Task 1; B → Task 2; C → Task 3; D → Task 4. Verification (build/tsc + backend suite) in each task; the architect's kickstart + browser/login pass is the reserved-lane finish (noted in handoff). All covered.
- **Placeholder scan:** none — full code for backend maps, api.ts fetchers, SportTabs, the page, and exact delete/verify commands.
- **Type consistency:** `sport = "mlb"` default and `tier` union `"player"|"team"|"game"|"all"` identical across api.ts + page usage; `BuilderRecord.tier` widened to `"player"|"team"|"game"` matches the backend `_CLASS_TO_TIER` `game_tier→game`; `SPORT_CFG[sport].tier2` values (`"team"`/`"game"`) are valid `getSavedBuilderParlays` tiers.
- **Live-DB safety:** only Task 1 touches Python and is fake-engine/pure; frontend tasks never touch the DB.

## Execution Handoff

Architect dispatches a single worktree subagent for Tasks 1–4 (review diffs after; Task 1 is backend-TDD, Tasks 2–4 gate on `tsc`/`next build`), then performs the reserved-lane finish: kickstart `com.playstat.api` (Task 1's `api/main.py` change), log into the dashboard and browser-verify both sport tabs + the home page + `/edges`&`/clv` 404s (screenshots), then merge + README.
