from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane import telegram
from control_plane.auth import require_operator
from control_plane.db import get_session
from control_plane.events import broadcaster
from control_plane.models import AuditEntry, MetricsSnapshot, Strategy, StrategyStatus
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

    # Auto-demote: after committing the metric, reload the strategy with a fresh
    # get to pick up any concurrent status changes, then check breach condition.
    # We only act on live strategies; paper strategies can exceed the cap while
    # being tuned — the operator decides their fate.
    strategy = session.get(Strategy, strategy_id)
    if strategy is not None and strategy.status is StrategyStatus.live and snap.max_drawdown >= strategy.max_daily_loss:
        now = datetime.now(timezone.utc)
        strategy.status = StrategyStatus.paper
        session.add(
            AuditEntry(
                ts=now,
                actor="control-plane",
                action="auto_demote_drawdown",
                payload_json=json.dumps({
                    "strategy_id": strategy.id,
                    "max_drawdown": snap.max_drawdown,
                    "max_daily_loss": strategy.max_daily_loss,
                    "metric_id": snap.id,
                }),
            )
        )
        session.commit()

        # Broadcast kill_all so the runner's kill_listener_loop halts immediately.
        # We reuse the kill_all event type (not a new auto_demote type) because the
        # runner already reacts to it and Phase 1.5 treats any auto-demote as a
        # full stop — the safest posture while the autonomous-safety loop is new.
        await broadcaster.broadcast({"type": "kill_all", "ts": now.isoformat(), "demoted": [strategy.id]})
        telegram.send(
            f"⚠️ Auto-demoted {strategy.name} (id={strategy.id})"
            f" — drawdown ${snap.max_drawdown} ≥ max_daily_loss ${strategy.max_daily_loss}"
        )

    return snap
