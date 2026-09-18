from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from control_plane.models import StrategyStatus


class StrategyCreate(BaseModel):
    name: str
    code_path: str
    max_notional: float = Field(gt=0)
    max_daily_loss: float = Field(gt=0)
    max_position: float = Field(gt=0)
    capital_weight: float = 0.0


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code_path: str
    status: StrategyStatus
    capital_weight: float
    max_notional: float
    max_daily_loss: float
    max_position: float
    created_at: datetime
    paper_started_at: datetime | None
    promoted_at: datetime | None
    promoted_by: str | None
    retired_at: datetime | None


class PromoteRequest(BaseModel):
    confirm: bool = True


class MetricsSnapshotIn(BaseModel):
    ts: datetime
    pnl: float
    sharpe: float
    max_drawdown: float
    n_trades: int


class MetricsSnapshotOut(MetricsSnapshotIn):
    model_config = ConfigDict(from_attributes=True)
    strategy_id: int


class AuditEntryIn(BaseModel):
    actor: str
    action: str
    payload_json: str


class AuditEntryOut(AuditEntryIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ts: datetime
