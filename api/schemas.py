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
