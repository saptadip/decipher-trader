from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.auth import require_operator
from control_plane.db import get_session
from control_plane.models import AuditEntry, Strategy, StrategyStatus

router = APIRouter(tags=["kill"])


@router.post("/kill_all")
async def kill_all(
    session: Session = Depends(get_session),
    actor: str = Depends(require_operator),
) -> dict[str, list[int]]:
    live_rows = list(session.scalars(select(Strategy).where(Strategy.status == StrategyStatus.live)))
    demoted: list[int] = []
    now = datetime.now(timezone.utc)
    for row in live_rows:
        row.status = StrategyStatus.paper
        demoted.append(row.id)
        session.add(
            AuditEntry(
                ts=now,
                actor=actor,
                action="demote",
                payload_json=json.dumps({"strategy_id": row.id, "reason": "kill_all"}),
            )
        )
    session.add(
        AuditEntry(
            ts=now,
            actor=actor,
            action="kill_all",
            payload_json=json.dumps({"demoted": demoted}),
        )
    )
    session.commit()
    return {"demoted": demoted}
