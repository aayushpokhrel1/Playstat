from pydantic import BaseModel


class TeamOut(BaseModel):
    team_id: int
    name: str
    sport: str = "nba"


class PlayerOut(BaseModel):
    player_id: int
    name: str
    team_id: int | None
    position: str | None
    sport: str = "nba"


class GameOut(BaseModel):
    game_id: int
    sport: str
    date: str
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    status: str | None


class GameLogEntry(BaseModel):
    game_id: int
    date: str
    points: int | None
    rebounds: int | None
    assists: int | None
    minutes: float | None


class PredictionOut(BaseModel):
    game_id: int
    date: str
    stat_type: str
    predicted_mean: float
    predicted_std: float
    model_version: str
    actual: int | None


class BoxScoreOut(BaseModel):
    player_id: int
    player_name: str
    game_id: int
    date: str
    # NBA-era top-level fields, kept for backward compatibility (Budgerr's
    # auto-settlement reads these); null for non-NBA players.
    points: int | None
    rebounds: int | None
    assists: int | None
    # Full per-sport stat map, e.g. {"hits": 2.0, "total_bases": 5.0, ...}.
    stats: dict[str, float] = {}
    sport: str = "nba"


class ModelPerformanceOut(BaseModel):
    stat_type: str
    mae: float
    n: int


class GamePredictionOut(BaseModel):
    game_id: int
    date: str
    sport: str
    home_team: str
    away_team: str
    market: str
    line_value: float
    predicted_mean: float
    prob_under: float
    prob_over: float
    model_version: str
    # Latest book line for the same market, when one has been ingested.
    book_line_value: float | None = None
    book_over_odds: int | None = None
    book_under_odds: int | None = None


class EdgeOut(BaseModel):
    player_id: int
    player_name: str
    team_id: int | None
    game_id: int
    date: str
    stat_type: str
    side: str
    line_value: float
    odds: int
    model_prob: float
    edge: float


class PmfPoint(BaseModel):
    k: int
    prob: float


class EdgeDistributionOut(BaseModel):
    """Full predictive PMF for a current positive edge (README §14.5) — lets
    the dashboard draw the whole distribution behind an edge's model_prob,
    not just the single number. `family='gaussian'` (NBA) rows carry
    `pmf=None`; the bar chart is only meaningful for discrete (MLB) stats.
    """

    player_id: int
    game_id: int
    stat_type: str
    side: str
    family: str
    line_value: float
    predicted_mean: float
    prob_over: float
    prob_under: float
    pmf: list[PmfPoint] | None


class ParlayLeg(BaseModel):
    # player_id/stat_type are Optional, not required: the dormant kind='team'
    # parlay_recommendations shape (optimizer/team_parlay.py) carries neither
    # (it has `market` instead) -- required ints/strs here would raise a
    # pydantic ValidationError (a 500) the moment a team row entered
    # /parlay-recommendations' result window, same failure class as README
    # §15.10 bug #5. Additive/widening only: every existing (player-kind)
    # consumer still gets both fields populated exactly as before.
    player_id: int | None = None
    player_name: str | None = None  # resolved at read time, not stored in legs JSONB
    game_id: int
    stat_type: str | None = None
    side: str
    model_prob: float
    odds: int


class ParlayRecommendationOut(BaseModel):
    parlay_id: int
    created_at: str
    target_payout: float
    joint_prob: float
    combined_odds: float
    legs: list[ParlayLeg]


class ClvSummaryOut(BaseModel):
    stat_type: str
    n: int
    avg_clv: float
    pct_positive: float


class BetPerformanceOut(BaseModel):
    bet_type: str
    n: int
    wins: int
    losses: int
    pushes: int
    total_staked: float
    total_pnl: float
    roi: float


class BacktestRunOut(BaseModel):
    run_id: int
    run_at: str
    stat_type: str
    model_version: str
    n_test_games: int | None
    mae: float | None
    coverage_16: float | None
    coverage_84: float | None


class BuilderLegOut(BaseModel):
    game_id: int
    kind: str
    label: str
    player_id: int | None = None
    stat_type: str | None = None
    market: str | None = None
    side: str
    line: float
    odds: int
    market_prob: float
    # Shown for context only — never used to rank or filter (README §15.3).
    model_prob: float | None = None
    # Best-price bookmaker for this leg's shopped odds (line shopping, README
    # §15.9 item 3). None when unshopped (consensus price) or for legacy rows
    # predating the field. Additive/defaulted — Budgerr-safe.
    book: str | None = None
    # Additive team-name context (docs/superpowers/plans/2026-07-28-
    # leg-team-names.md) — resolved from game_id via a batched games+teams
    # join, never required for a leg to be valid. Optional with defaults so
    # every existing shape (Budgerr, etc.) still validates unchanged.
    home_team: str | None = None   # full name, e.g. "Oakland Athletics"
    away_team: str | None = None   # full name
    # "home" | "away" | None — which side the LEG'S player's (latest-pull)
    # team_id matches. None for team-market legs, and for a traded player
    # whose stored team_id matches neither side (README §15.10 NBA note).
    player_team_side: str | None = None


class BuilderParlayOut(BaseModel):
    legs: list[BuilderLegOut]
    combined_odds: float
    joint_prob: float
    n_legs: int


class BuilderSearchOut(BaseModel):
    constructions: list[BuilderParlayOut]
    # Whether the search hit its node budget and returned partial results.
    # exhaustive is the inverse, surfaced positively for call-site clarity.
    truncated: bool
    nodes_searched: int
    exhaustive: bool


class SavedBuilderParlayOut(BuilderParlayOut):
    parlay_id: int
    created_at: str
    target_payout: float
    # Same-game combos (README §15.9 item 1) — correlation metadata read from the
    # legs JSONB wrapper. `lift` is the empirically-measured same-game dependence
    # (observed / independent-product) at the pair's actual lines+sides, `lift_n`
    # the games it was measured over, `both_n` the joint "both hit" cell, and
    # small_sample flags a lift backed by under ~a season of shared history.
    # Additive/defaulted: None/False for every other class, so Budgerr's
    # (default) player-tier consumption is byte-unchanged.
    lift: float | None = None
    lift_n: int | None = None
    both_n: int | None = None
    small_sample: bool = False


class BuilderRecordOut(BaseModel):
    """Paper-trading builder record split by tier + target payout (README
    §15) — dashboard-only; /bet-performance and BetPerformanceOut are
    unchanged and still feed web/app/clv."""

    tier: str            # "player" for across_game, "team" for team_tier
    target_payout: float
    n: int
    wins: int
    losses: int
    pushes: int
    staked: float = 0.0  # sum of Kelly stakes (README §15.9 item 4); additive
    pnl: float
    roi: float           # pnl / staked (stake-weighted); 0.0 when staked == 0


class BuilderRecordDailyOut(BaseModel):
    """Per-day drill-down of the builder record (README §15 follow-on):
    same settled-builder data as BuilderRecordOut, grouped by slate date
    (date(pr.created_at)) instead of tier/target_payout. Newest date first.
    """

    date: str
    n: int
    wins: int
    losses: int
    pushes: int
    staked: float = 0.0  # sum of Kelly stakes (README §15.9 item 4); additive
    pnl: float
    roi: float           # pnl / staked (stake-weighted); 0.0 when staked == 0


class DailyParlayLegOut(BaseModel):
    """One leg of a settled builder parlay in the per-day drill-down: label +
    team context from the recommendation, result/actual from the settlement
    audit (README per-day drill-down spec)."""

    label: str | None = None
    side: str | None = None
    line: float | None = None
    actual: float | None = None
    result: str | None = None       # hit/won -> ✓, miss/lost -> ✗, void -> –, None -> pending
    odds: int | None = None
    book: str | None = None
    home_team: str | None = None
    away_team: str | None = None


class DailyParlayOut(BaseModel):
    """One settled builder parlay for a slate date (dashboard-only drill-down)."""

    parlay_id: int
    result: str                     # win | loss | push
    tier: str                       # player | team | game
    target_payout: float
    combined_odds: float
    stake: float
    pnl: float
    legs: list[DailyParlayLegOut]


class LineMovementLegOut(BaseModel):
    player_id: int | None = None
    game_id: int | None = None
    stat_type: str | None = None
    side: str | None = None
    # Nullable: home/away markets (moneyline) carry no line.
    line: float | None = None
    build_prob: float
    close_prob: float
    movement_pp: float


class LineMovementOut(BaseModel):
    """Line movement between a card's build price and its last pre-start price.

    NOT the closing line and NOT an edge/value claim (README §15.8 #2): the last
    snapshot lands a median ~100 min before first pitch. `coverage` is the share
    of legs comparable at an UNCHANGED line — a low value is itself the finding.
    """
    n_legs: int
    n_compared: int
    coverage: float
    mean_movement_pp: float | None = None
    n_toward: int = 0
    n_against: int = 0
    legs: list[LineMovementLegOut] = []
