from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable

from nautilus_runner.control_plane_client import ControlPlaneClient


async def heartbeat_loop(
    client: ControlPlaneClient,
    interval_secs: float,
    miss_limit: int,
    stop_event: asyncio.Event,
    on_miss: Callable[[], None],
) -> None:
    consecutive_misses = 0
    while not stop_event.is_set():
        try:
            await client.post_audit(
                "runner", "heartbeat", {"ts": datetime.now(timezone.utc).isoformat()}
            )
            consecutive_misses = 0
        except Exception:
            consecutive_misses += 1
            if consecutive_misses >= miss_limit:
                on_miss()
                consecutive_misses = 0
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_secs)
        except asyncio.TimeoutError:
            continue
