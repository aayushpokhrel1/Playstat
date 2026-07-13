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

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, { cache: "no-store" });
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
  const res = await fetch(`${API_BASE_URL}/players/${playerId}`, { cache: "no-store" });
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
