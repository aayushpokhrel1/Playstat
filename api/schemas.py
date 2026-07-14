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


class ParlayLeg(BaseModel):
    player_id: int
    player_name: str | None = None  # resolved at read time, not stored in legs JSONB
    game_id: int
    stat_type: str
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


class BacktestRunOut(BaseModel):
    run_id: int
    run_at: str
    stat_type: str
    model_version: str
    n_test_games: int | None
    mae: float | None
    coverage_16: float | None
    coverage_84: float | None
