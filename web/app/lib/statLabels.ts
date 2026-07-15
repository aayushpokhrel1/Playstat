export const STAT_LABELS: Record<string, string> = {
  hits: "Hits",
  rbis: "RBIs",
  runs: "Runs",
  walks: "Walks",
  total_bases: "Total bases",
  batter_strikeouts: "Batter Ks",
  pitcher_strikeouts: "Pitcher Ks",
  home_runs: "Home runs",
  stolen_bases: "Stolen bases",
  earned_runs: "Earned runs",
  walks_allowed: "Walks allowed",
  hits_allowed: "Hits allowed",
  outs_recorded: "Outs recorded",
};

export function statLabel(statType: string): string {
  return STAT_LABELS[statType] ?? statType.replace(/_/g, " ");
}
