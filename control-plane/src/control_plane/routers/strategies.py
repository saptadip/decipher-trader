from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.auth import require_operator
from control_plane.config import Settings, get_settings
from control_plane.db import get_session
from control_plane.models import AuditEntry, MetricsSnapshot, Strategy, StrategyStatus
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


def _write_audit(session: Session, actor: str, action: str, payload: dict) -> None:
    session.add(
        AuditEntry(
            ts=datetime.now(timezone.utc),
            actor=actor,
            action=action,
            payload_json=json.dumps(payload, default=str),
        )
    )


@router.post("/{strategy_id}/promote", response_model=StrategyOut)
async def promote_strategy(
    strategy_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    actor: str = Depends(require_operator),
) -> Strategy:
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    if strategy.status is not StrategyStatus.paper:
        _write_audit(session, actor, "promote_refused", {"strategy_id": strategy_id, "reason": "wrong_status"})
        session.commit()
        raise HTTPException(status_code=409, detail=f"strategy in status {strategy.status.value}, not paper")
    if strategy.paper_started_at is None:
        _write_audit(session, actor, "promote_refused", {"strategy_id": strategy_id, "reason": "missing_paper_started_at"})
        session.commit()
        raise HTTPException(status_code=409, detail="paper_started_at not set")

    now = datetime.now(timezone.utc)
    min_delta = timedelta(days=settings.paper_forward_min_days)
    paper_started = strategy.paper_started_at
    if paper_started.tzinfo is None:
        paper_started = paper_started.replace(tzinfo=timezone.utc)
    if now - paper_started < min_delta:
        _write_audit(session, actor, "promote_refused", {"strategy_id": strategy_id, "reason": "insufficient_paper_days"})
        session.commit()
        raise HTTPException(
            status_code=409,
            detail=f"must run in paper for at least {settings.paper_forward_min_days} days",
        )

    window_start = now - min_delta
    fatal = session.scalar(
        select(MetricsSnapshot)
        .where(MetricsSnapshot.strategy_id == strategy.id)
        .where(MetricsSnapshot.ts >= window_start)
        .where(MetricsSnapshot.max_drawdown > strategy.max_daily_loss)
        .limit(1)
    )
    if fatal is not None:
        _write_audit(session, actor, "promote_refused", {"strategy_id": strategy_id, "reason": "fatal_drawdown"})
        session.commit()
        raise HTTPException(status_code=409, detail="fatal drawdown snapshot in review window; refuse to promote")

    strategy.status = StrategyStatus.live
    strategy.promoted_at = now
    strategy.promoted_by = actor
    _write_audit(session, actor, "promote", {"strategy_id": strategy.id})
    session.commit()
    session.refresh(strategy)
    from control_plane.events import broadcaster
    await broadcaster.broadcast({"type": "promote", "strategy_id": strategy.id, "ts": now.isoformat()})
    return strategy


@router.post("/{strategy_id}/demote", response_model=StrategyOut)
async def demote_strategy(
    strategy_id: int,
    session: Session = Depends(get_session),
    actor: str = Depends(require_operator),
) -> Strategy:
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    if strategy.status is not StrategyStatus.live:
        raise HTTPException(status_code=409, detail=f"strategy in status {strategy.status.value}, not live")

    strategy.status = StrategyStatus.paper
    _write_audit(session, actor, "demote", {"strategy_id": strategy.id})
    session.commit()
    session.refresh(strategy)
    from control_plane.events import broadcaster
    await broadcaster.broadcast({"type": "demote", "strategy_id": strategy.id, "ts": datetime.now(timezone.utc).isoformat()})
    return strategy


@router.post("/{strategy_id}/start_paper", response_model=StrategyOut)
async def start_paper(
    strategy_id: int,
    session: Session = Depends(get_session),
    actor: str = Depends(require_operator),
) -> Strategy:
    strategy = session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    if strategy.status is not StrategyStatus.draft:
        raise HTTPException(status_code=409, detail=f"strategy in status {strategy.status.value}, not draft")
    now = datetime.now(timezone.utc)
    strategy.status = StrategyStatus.paper
    strategy.paper_started_at = now
    _write_audit(session, actor, "start_paper", {"strategy_id": strategy.id})
    session.commit()
    session.refresh(strategy)
    return strategy
