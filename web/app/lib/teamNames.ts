// Static full-name -> nickname map for MLB (30) and NBA (30) teams
// (NFL legs fall back to full names — no NFL block here yet; harmless, NFL offseason)
// (docs/superpowers/plans/2026-07-28-leg-team-names.md).
//
// Naive last-token splitting is WRONG: it collapses "Boston Red Sox" and
// "Chicago White Sox" to the same "Sox", and turns "Toronto Blue Jays" into
// just "Jays". A full-name lookup table is the only correct approach for a
// fixed 30-team league.
const TEAM_NICKNAMES: Record<string, string> = {
  "Arizona Diamondbacks": "Diamondbacks",
  "Atlanta Braves": "Braves",
  "Baltimore Orioles": "Orioles",
  "Boston Red Sox": "Red Sox",
  "Chicago Cubs": "Cubs",
  "Chicago White Sox": "White Sox",
  "Cincinnati Reds": "Reds",
  "Cleveland Guardians": "Guardians",
  "Colorado Rockies": "Rockies",
  "Detroit Tigers": "Tigers",
  "Houston Astros": "Astros",
  "Kansas City Royals": "Royals",
  "Los Angeles Angels": "Angels",
  "Los Angeles Dodgers": "Dodgers",
  "Miami Marlins": "Marlins",
  "Milwaukee Brewers": "Brewers",
  "Minnesota Twins": "Twins",
  "New York Mets": "Mets",
  "New York Yankees": "Yankees",
  "Oakland Athletics": "Athletics",
  "Philadelphia Phillies": "Phillies",
  "Pittsburgh Pirates": "Pirates",
  "San Diego Padres": "Padres",
  "San Francisco Giants": "Giants",
  "Seattle Mariners": "Mariners",
  "St. Louis Cardinals": "Cardinals",
  "Tampa Bay Rays": "Rays",
  "Texas Rangers": "Rangers",
  "Toronto Blue Jays": "Blue Jays",
  "Washington Nationals": "Nationals",
  "Atlanta Hawks": "Hawks",
  "Boston Celtics": "Celtics",
  "Brooklyn Nets": "Nets",
  "Charlotte Hornets": "Hornets",
  "Chicago Bulls": "Bulls",
  "Cleveland Cavaliers": "Cavaliers",
  "Dallas Mavericks": "Mavericks",
  "Denver Nuggets": "Nuggets",
  "Detroit Pistons": "Pistons",
  "Golden State Warriors": "Warriors",
  "Houston Rockets": "Rockets",
  "Indiana Pacers": "Pacers",
  "Los Angeles Clippers": "Clippers",
  "Los Angeles Lakers": "Lakers",
  "Memphis Grizzlies": "Grizzlies",
  "Miami Heat": "Heat",
  "Milwaukee Bucks": "Bucks",
  "Minnesota Timberwolves": "Timberwolves",
  "New Orleans Pelicans": "Pelicans",
  "New York Knicks": "Knicks",
  "Oklahoma City Thunder": "Thunder",
  "Orlando Magic": "Magic",
  "Philadelphia 76ers": "76ers",
  "Phoenix Suns": "Suns",
  "Portland Trail Blazers": "Trail Blazers",
  "Sacramento Kings": "Kings",
  "San Antonio Spurs": "Spurs",
  "Toronto Raptors": "Raptors",
  "Utah Jazz": "Jazz",
  "Washington Wizards": "Wizards",
};

// Map lookup, fallback to the full name for anything unmapped (never wrong,
// just longer) and null-safe (a null/missing team name renders as null so
// callers can skip the line gracefully).
export function nickname(fullName: string | null): string | null {
  if (fullName === null) return null;
  return TEAM_NICKNAMES[fullName] ?? fullName;
}
