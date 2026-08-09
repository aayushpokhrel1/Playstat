const API_BASE_URL = "http://localhost:8000";

export type Team = {
  team_id: number;
  name: string;
};

export type Player = {
  player_id: number;
  name: string;
  team_id: number | null;
  position: string | null;
};

export type GameLogEntry = {
  game_id: number;
  date: string;
  points: number | null;
  rebounds: number | null;
  assists: number | null;
  minutes: number | null;
};

export type BuilderLeg = {
  game_id: number;
  kind: "player" | "team";
  label: string;
  player_id: number | null;
  stat_type: string | null;
  market: string | null;
  side: "over" | "under";
  line: number;
  odds: number;
  market_prob: number;
  model_prob: number | null;
  // Best-price bookmaker for the shopped odds (line shopping, §15.9 item 3);
  // null when unshopped (consensus price) or for legacy rows.
  book: string | null;
  home_team: string | null;
  away_team: string | null;
  player_team_side: "home" | "away" | null;
};

export type BuilderConstruction = {
  legs: BuilderLeg[];
  combined_odds: number;
  joint_prob: number;
  n_legs: number;
};

export type BuilderSearchResult = {
  constructions: BuilderConstruction[];
  truncated: boolean;
  nodes_searched: number;
  exhaustive: boolean;
};

export type SavedBuilderParlay = BuilderConstruction & {
  parlay_id: number;
  created_at: string;
  target_payout: number;
  // Same-game combos (README §15.9 item 1). Present only on the same_game tier;
  // null/false everywhere else. `lift` is the measured same-game dependence
  // (observed / independent product) at the pair's actual lines+sides, `lift_n`
  // the games behind it, `both_n` the joint "both hit" count, and small_sample
  // flags under ~a season of shared history.
  lift?: number | null;
  lift_n?: number | null;
  both_n?: number | null;
  small_sample?: boolean;
};

export type BuilderRecord = {
  tier: "player" | "team" | "game";
  target_payout: number;
  n: number;
  wins: number;
  losses: number;
  pushes: number;
  staked: number; // sum of ¼-Kelly stakes (README §15.9 item 4)
  pnl: number;
  roi: number; // pnl / staked (stake-weighted)
};

export type BuilderRecordDaily = {
  date: string;
  n: number;
  wins: number;
  losses: number;
  pushes: number;
  staked: number; // sum of ¼-Kelly stakes (README §15.9 item 4)
  pnl: number;
  roi: number; // pnl / staked (stake-weighted)
};

export type DailyParlayLeg = {
  label: string | null;
  side: string | null;
  line: number | null;
  actual: number | null;
  result: string | null; // hit/won -> ✓, miss/lost -> ✗, void -> –, null -> pending
  odds: number | null;
  book: string | null;
  home_team: string | null;
  away_team: string | null;
};

export type DailyParlay = {
  parlay_id: number;
  result: "win" | "loss" | "push";
  tier: "player" | "team" | "game";
  target_payout: number;
  combined_odds: number;
  stake: number;
  pnl: number;
  legs: DailyParlayLeg[];
};

export type BuilderSearchParams = {
  target_payout?: number;
  min_prob?: number;
  max_legs?: number;
  sport?: string;
};

// Server-only: apiGet is called exclusively from server components, so the
// key never reaches the browser.
function apiHeaders(): HeadersInit | undefined {
  const key = process.env.PLAYSTAT_API_KEY;
  return key ? { "X-API-Key": key } : undefined;
}

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, { cache: "no-store", headers: apiHeaders() });
  if (!res.ok) {
    throw new Error(`API request failed: ${path} (${res.status})`);
  }
  return res.json();
}

export function getTeams() {
  return apiGet<Team[]>("/teams");
}

export function getPlayers() {
  return apiGet<Player[]>("/players");
}

export async function getPlayer(playerId: number): Promise<Player | null> {
  const res = await fetch(`${API_BASE_URL}/players/${playerId}`, {
    cache: "no-store",
    headers: apiHeaders(),
  });
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    throw new Error(`API request failed: /players/${playerId} (${res.status})`);
  }
  return res.json();
}

export function getPlayerStats(playerId: number) {
  return apiGet<GameLogEntry[]>(`/players/${playerId}/stats`);
}

// Dashboard-only builder record split by tier + target payout (README §15).
// sport is additive (default "mlb", NFL builder chain #4a/#4b) — existing
// callers passing no sport keep getting exactly MLB rows, unchanged.
export function getBuilderRecord(sport = "mlb") {
  return apiGet<BuilderRecord[]>(`/parlay-builder/record?sport=${sport}`);
}

// Per-day drill-down of the same settled-builder data (README §15 follow-on).
export function getBuilderRecordDaily(sport = "mlb") {
  return apiGet<BuilderRecordDaily[]>(`/parlay-builder/record/daily?sport=${sport}`);
}

// Settled builder parlays for one slate date, each with per-leg result — the
// day -> parlay -> leg autopsy (per-day parlay drill-down spec). Dashboard-only.
export function getDailyParlays(date: string, sport = "mlb") {
  return apiGet<DailyParlay[]>(
    `/parlay-builder/record/daily/parlays?date=${date}&sport=${sport}`,
  );
}

// tier is additive (README §15 Change 3): "player" is the default and
// matches today's exact saved shape (the mixed player+team across-game
// tier); "team" is the new dedicated, higher-variance NRFI/F5-only tier,
// which may legitimately be empty on any given slate; "game" is the NFL
// game-market tier (#4a/#4b); "all" skips the class filter server-side.
// sport is additive (default "mlb") — existing callers unchanged.
export function getSavedBuilderParlays(
  limit = 10,
  tier: "player" | "team" | "game" | "same_game" | "all" = "player",
  sport = "mlb",
) {
  return apiGet<SavedBuilderParlay[]>(`/parlay-builder/saved?limit=${limit}&tier=${tier}&sport=${sport}`);
}

export function searchBuilder(params: BuilderSearchParams) {
  const q = new URLSearchParams();
  if (params.target_payout != null) q.set("target_payout", String(params.target_payout));
  if (params.min_prob != null) q.set("min_prob", String(params.min_prob));
  if (params.max_legs != null) q.set("max_legs", String(params.max_legs));
  if (params.sport != null) q.set("sport", params.sport);
  return apiGet<BuilderSearchResult>(`/parlay-builder?${q.toString()}`);
}

export type LineMovementLeg = {
  player_id: number | null;
  game_id: number | null;
  stat_type: string | null;
  side: string | null;
  line: number | null;
  build_prob: number;
  close_prob: number;
  movement_pp: number;
};

export type LineMovement = {
  n_legs: number;
  n_compared: number;
  coverage: number;
  mean_movement_pp: number | null;
  n_toward: number;
  n_against: number;
  legs: LineMovementLeg[];
};

export async function fetchLineMovement(sport = "mlb"): Promise<LineMovement | null> {
  try {
    return await apiGet<LineMovement>(`/parlay-builder/line-movement?sport=${sport}`);
  } catch {
    // A missing measurement must never break the builder page.
    return null;
  }
}
