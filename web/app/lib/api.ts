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
};

export type BuilderRecord = {
  tier: "player" | "team" | "game";
  target_payout: number;
  n: number;
  wins: number;
  losses: number;
  pushes: number;
  pnl: number;
  roi: number;
};

export type BuilderRecordDaily = {
  date: string;
  n: number;
  wins: number;
  losses: number;
  pushes: number;
  pnl: number;
  roi: number;
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

// tier is additive (README §15 Change 3): "player" is the default and
// matches today's exact saved shape (the mixed player+team across-game
// tier); "team" is the new dedicated, higher-variance NRFI/F5-only tier,
// which may legitimately be empty on any given slate; "game" is the NFL
// game-market tier (#4a/#4b); "all" skips the class filter server-side.
// sport is additive (default "mlb") — existing callers unchanged.
export function getSavedBuilderParlays(
  limit = 10,
  tier: "player" | "team" | "game" | "all" = "player",
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
