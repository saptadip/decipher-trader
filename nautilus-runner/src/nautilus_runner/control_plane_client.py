from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx


class ControlPlaneClient:
    def __init__(self, base_url: str, token: str, timeout_secs: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout_secs

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base, headers=self._headers, timeout=self._timeout)

    async def fetch_strategies(self, statuses: list[str]) -> list[dict[str, Any]]:
        async with await self._client() as c:
            resp = await c.get("/strategies", params={"status": ",".join(statuses)} if statuses else None)
            resp.raise_for_status()
            return resp.json()

    async def post_audit(self, actor: str, action: str, payload: dict[str, Any]) -> None:
        import json as _json

        async with await self._client() as c:
            resp = await c.post("/audit", json={"actor": actor, "action": action, "payload_json": _json.dumps(payload, default=str)})
            resp.raise_for_status()

    async def post_metric(
        self,
        strategy_id: int,
        ts: datetime,
        pnl: float,
        sharpe: float,
        max_drawdown: float,
        n_trades: int,
    ) -> None:
        async with await self._client() as c:
            resp = await c.post(
                f"/metrics/{strategy_id}",
                json={
                    "ts": ts.isoformat(),
                    "pnl": pnl,
                    "sharpe": sharpe,
                    "max_drawdown": max_drawdown,
                    "n_trades": n_trades,
                },
            )
            resp.raise_for_status()
