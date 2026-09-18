from datetime import datetime, timezone

import httpx
import pytest
import respx

from nautilus_runner.control_plane_client import ControlPlaneClient


BASE = "http://cp.test"


@pytest.mark.asyncio
async def test_fetch_strategies_sends_status_csv():
    async with respx.mock(base_url=BASE, assert_all_called=True) as router:
        route = router.get("/strategies").mock(return_value=httpx.Response(200, json=[{"id": 1}]))
        client = ControlPlaneClient(BASE, "tok")
        rows = await client.fetch_strategies(["paper", "live"])
        assert rows == [{"id": 1}]
        assert route.calls[0].request.url.params["status"] == "paper,live"


@pytest.mark.asyncio
async def test_post_audit_sends_bearer():
    async with respx.mock(base_url=BASE) as router:
        router.post("/audit").mock(return_value=httpx.Response(201, json={"id": 1, "ts": "2026-01-01T00:00:00Z", "actor": "r", "action": "a", "payload_json": "{}"}))
        client = ControlPlaneClient(BASE, "tok")
        await client.post_audit("runner", "start", {"strategy_id": 1})
        req = router.calls[0].request
        assert req.headers["authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_post_metric_serializes_ts_iso():
    async with respx.mock(base_url=BASE) as router:
        router.post("/metrics/1").mock(return_value=httpx.Response(201, json={"strategy_id": 1, "ts": "2026-01-01T00:00:00Z", "pnl": 0, "sharpe": 0, "max_drawdown": 0, "n_trades": 0}))
        client = ControlPlaneClient(BASE, "tok")
        await client.post_metric(1, datetime(2026, 1, 1, tzinfo=timezone.utc), 0.0, 0.0, 0.0, 0)
        body = router.calls[0].request.content.decode()
        assert "2026-01-01T00:00:00+00:00" in body
