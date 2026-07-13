from pydantic import BaseModel


class TeamOut(BaseModel):
    team_id: int
    name: str


class PlayerOut(BaseModel):
    player_id: int
    name: str
    team_id: int | None
    position: str | None


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


class ModelPerformanceOut(BaseModel):
    stat_type: str
    mae: float
    n: int


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
