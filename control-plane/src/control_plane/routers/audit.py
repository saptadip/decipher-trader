from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.auth import require_operator
from control_plane.db import get_session
from control_plane import telegram
from control_plane.models import AuditEntry
from control_plane.schemas import AuditEntryIn, AuditEntryOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEntryOut])
async def list_audit(
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> list[AuditEntry]:
    stmt = select(AuditEntry).order_by(AuditEntry.ts.desc())
    if actor:
        stmt = stmt.where(AuditEntry.actor == actor)
    if action:
        stmt = stmt.where(AuditEntry.action == action)
    if since:
        stmt = stmt.where(AuditEntry.ts >= since)
    stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


@router.post("", response_model=AuditEntryOut, status_code=status.HTTP_201_CREATED)
async def add_audit(
    payload: AuditEntryIn,
    session: Session = Depends(get_session),
    _actor: str = Depends(require_operator),
) -> AuditEntry:
    entry = AuditEntry(
        ts=datetime.now(timezone.utc),
        actor=payload.actor,
        action=payload.action,
        payload_json=payload.payload_json,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    # Immediate Telegram alerts on venue-socket transitions — provides sub-interval
    # visibility that the stale-heartbeat detector (absence-of-heartbeat) cannot give.
    # Fires after commit so the audit row is durable before the alert.
    if payload.action in ("socket_disconnected", "socket_reconnected"):
        try:
            inner = json.loads(payload.payload_json)
            strategy_id = inner.get("strategy_id", "unknown")
            state = inner.get("state", "unknown")
            venue = inner.get("venue")
            endpoint = inner.get("endpoint", "")
            context = f" venue={venue}" if venue else f" endpoint={endpoint}"
            emoji = "\U0001f50c" if payload.action == "socket_disconnected" else "✅"
            verb = (
                "disconnected" if payload.action == "socket_disconnected" else "reconnected"
            )
            telegram.send(
                f"{emoji} Venue socket {verb} ({state}) — strategy {strategy_id}{context}"
            )
        except Exception:
            pass  # best-effort; must never break the response path
    return entry
