from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.auth import require_operator
from control_plane.db import get_session
from control_plane.models import MetricsSnapshot, Strategy
from control_plane.schemas import MetricsSnapshotIn, MetricsSnapshotOut

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/{strategy_id}", response_model=list[MetricsSnapshotOut])
async def list_metrics(
    strategy_id: int,
    since: datetime | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[MetricsSnapshot]:
    stmt = select(MetricsSnapshot).where(MetricsSnapshot.strategy_id == strategy_id).order_by(MetricsSnapshot.ts.asc())
    if since is not None:
        stmt = stmt.where(MetricsSnapshot.ts >= since)
    return list(session.scalars(stmt).all())


@router.post("/{strategy_id}", response_model=MetricsSnapshotOut, status_code=status.HTTP_201_CREATED)
async def add_metric(
    strategy_id: int,
    payload: MetricsSnapshotIn,
    session: Session = Depends(get_session),
    _actor: str = Depends(require_operator),
) -> MetricsSnapshot:
    if session.get(Strategy, strategy_id) is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    snap = MetricsSnapshot(
        strategy_id=strategy_id,
        ts=payload.ts,
        pnl=payload.pnl,
        sharpe=payload.sharpe,
        max_drawdown=payload.max_drawdown,
        n_trades=payload.n_trades,
    )
    session.add(snap)
    session.commit()
    session.refresh(snap)
    return snap
