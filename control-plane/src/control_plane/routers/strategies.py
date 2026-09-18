from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.auth import require_operator
from control_plane.db import get_session
from control_plane.models import Strategy, StrategyStatus
from control_plane.schemas import StrategyCreate, StrategyOut

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategyOut])
async def list_strategies(
    status_csv: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_session),
) -> list[Strategy]:
    stmt = select(Strategy)
    if status_csv:
        wanted = [StrategyStatus(s.strip()) for s in status_csv.split(",") if s.strip()]
        stmt = stmt.where(Strategy.status.in_(wanted))
    return list(session.scalars(stmt).all())


@router.post("", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    payload: StrategyCreate,
    session: Session = Depends(get_session),
    _actor: str = Depends(require_operator),
) -> Strategy:
    existing = session.scalar(select(Strategy).where(Strategy.name == payload.name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="strategy name already exists")

    strategy = Strategy(
        name=payload.name,
        code_path=payload.code_path,
        status=StrategyStatus.draft,
        capital_weight=payload.capital_weight,
        max_notional=payload.max_notional,
        max_daily_loss=payload.max_daily_loss,
        max_position=payload.max_position,
        created_at=datetime.now(timezone.utc),
    )
    session.add(strategy)
    session.commit()
    session.refresh(strategy)
    return strategy
