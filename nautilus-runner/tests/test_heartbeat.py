import asyncio

import httpx
import pytest
import respx

from nautilus_runner.control_plane_client import ControlPlaneClient
from nautilus_runner.heartbeat import heartbeat_loop


@pytest.mark.asyncio
async def test_heartbeat_stops_after_miss_limit():
    misses = 0

    def _cb() -> None:
        nonlocal misses
        misses += 1

    with respx.mock(base_url="http://cp.test", assert_all_called=False) as router:
        router.post("/audit").mock(return_value=httpx.Response(500))
        client = ControlPlaneClient("http://cp.test", "tok", timeout_secs=0.1)
        stop = asyncio.Event()
        task = asyncio.create_task(heartbeat_loop(client, interval_secs=0.05, miss_limit=2, stop_event=stop, on_miss=_cb))
        await asyncio.sleep(0.25)
        stop.set()
        await task
        assert misses >= 1


@pytest.mark.asyncio
async def test_heartbeat_resets_after_success():
    responses = [httpx.Response(500), httpx.Response(201, json={"id": 1, "ts": "2026-01-01T00:00:00Z", "actor": "r", "action": "heartbeat", "payload_json": "{}"})]

    with respx.mock(base_url="http://cp.test") as router:
        route = router.post("/audit")
        route.side_effect = responses
        misses = 0

        def _cb() -> None:
            nonlocal misses
            misses += 1

        client = ControlPlaneClient("http://cp.test", "tok", timeout_secs=0.1)
        stop = asyncio.Event()
        task = asyncio.create_task(heartbeat_loop(client, interval_secs=0.05, miss_limit=3, stop_event=stop, on_miss=_cb))
        await asyncio.sleep(0.2)
        stop.set()
        await task
        assert misses == 0
