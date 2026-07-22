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

export type Prediction = {
  game_id: number;
  date: string;
  stat_type: string;
  predicted_mean: number;
  predicted_std: number;
  model_version: string;
  actual: number | null;
};

export type Edge = {
  player_id: number;
  player_name: string;
  team_id: number | null;
  game_id: number;
  date: string;
  stat_type: string;
  side: "over" | "under";
  line_value: number;
  odds: number;
  model_prob: number;
  edge: number;
};

export type PmfPoint = {
  k: number;
  prob: number;
};

export type EdgeDistribution = {
  player_id: number;
  game_id: number;
  stat_type: string;
  side: "over" | "under";
  family: "discrete" | "gaussian";
  line_value: number;
  predicted_mean: number;
  prob_over: number;
  prob_under: number;
  pmf: PmfPoint[] | null;
};

export type ParlayLeg = {
  player_id: number;
  player_name: string | null;
  game_id: number;
  stat_type: string;
  side: "over" | "under";
  model_prob: number;
  odds: number;
};

export type ParlayRecommendation = {
  parlay_id: number;
  created_at: string;
  target_payout: number;
  joint_prob: number;
  combined_odds: number;
  legs: ParlayLeg[];
};

export type ClvSummary = {
  stat_type: string;
  n: number;
  avg_clv: number;
  pct_positive: number;
};

export type BetPerformance = {
  bet_type: string;
  n: number;
  wins: number;
  losses: number;
  pushes: number;
  total_staked: number;
  total_pnl: number;
  roi: number;
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

export type BuilderSearchParams = {
  target_payout?: number;
  min_prob?: number;
  max_legs?: number;
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

export function getPlayerPredictions(playerId: number) {
  return apiGet<Prediction[]>(`/players/${playerId}/predictions`);
}

export function getEdges() {
  return apiGet<Edge[]>("/edges");
}

export function getEdgeDistributions() {
  return apiGet<EdgeDistribution[]>("/edge-distributions");
}

export function getParlayRecommendations() {
  return apiGet<ParlayRecommendation[]>("/parlay-recommendations");
}

export function getClvSummary() {
  return apiGet<ClvSummary[]>("/clv-summary");
}

export function getBetPerformance() {
  return apiGet<BetPerformance[]>("/bet-performance");
}

export function getSavedBuilderParlays(limit = 10) {
  return apiGet<SavedBuilderParlay[]>(`/parlay-builder/saved?limit=${limit}`);
}

export function searchBuilder(params: BuilderSearchParams) {
  const q = new URLSearchParams();
  if (params.target_payout != null) q.set("target_payout", String(params.target_payout));
  if (params.min_prob != null) q.set("min_prob", String(params.min_prob));
  if (params.max_legs != null) q.set("max_legs", String(params.max_legs));
  return apiGet<BuilderSearchResult>(`/parlay-builder?${q.toString()}`);
}
