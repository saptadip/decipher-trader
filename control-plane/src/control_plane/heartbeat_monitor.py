from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from control_plane import telegram
from control_plane.db import get_engine, get_sessionmaker
from control_plane.models import AuditEntry

log = logging.getLogger(__name__)

_POLL_INTERVAL_SECS = 30
_STALE_HEARTBEAT_MULTIPLIER = 3


def should_alert(
    latest_ts: datetime,
    now: datetime,
    debounce_marker: datetime | None,
    threshold_secs: int,
) -> bool:
    """Return True when the gap is stale and we have not already alerted for this gap."""
    age_secs = (now - latest_ts).total_seconds()
    if age_secs <= threshold_secs:
        return False
    # We have a stale gap. Only alert if we haven't already alerted for this gap.
    if debounce_marker is not None and latest_ts <= debounce_marker:
        return False
    return True


async def _check_once(debounce_marker: datetime | None) -> datetime | None:
    """Poll the audit log once. Returns the updated debounce_marker."""
    try:
        engine = get_engine()
        Session = get_sessionmaker(engine)
        with Session() as session:
            latest_entry = session.scalar(
                select(AuditEntry)
                .where(AuditEntry.action == "heartbeat")
                .where(AuditEntry.actor == "runner")
                .order_by(AuditEntry.ts.desc())
                .limit(1)
            )

        if latest_entry is None:
            return debounce_marker

        latest_ts: datetime = latest_entry.ts
        if latest_ts.tzinfo is None:
            latest_ts = latest_ts.replace(tzinfo=timezone.utc)

        # If a fresh heartbeat arrived after our last alert, reset debounce so
        # the next gap re-triggers an alert.
        if debounce_marker is not None and latest_ts > debounce_marker:
            debounce_marker = None

        now = datetime.now(timezone.utc)
        threshold_secs = _POLL_INTERVAL_SECS * _STALE_HEARTBEAT_MULTIPLIER

        if should_alert(latest_ts, now, debounce_marker, threshold_secs):
            age = int((now - latest_ts).total_seconds())
            telegram.send(
                f"⚠️ Stale heartbeat — last runner heartbeat was {age}s ago "
                f"(threshold: {threshold_secs}s)"
            )
            log.warning("stale heartbeat detected: last seen %s (%ds ago)", latest_ts, age)
            return latest_ts  # debounce_marker set to latest_ts to prevent repeat alerts

    except Exception as e:
        log.warning("heartbeat monitor poll failed: %s", e)

    return debounce_marker


async def run() -> None:
    """Background task: poll forever, alerting on stale heartbeats."""
    log.info("heartbeat monitor started (poll=%ds, threshold=%dx)", _POLL_INTERVAL_SECS, _STALE_HEARTBEAT_MULTIPLIER)
    debounce_marker: datetime | None = None
    while True:
        debounce_marker = await _check_once(debounce_marker)
        await asyncio.sleep(_POLL_INTERVAL_SECS)
