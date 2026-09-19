from __future__ import annotations

import json
from datetime import datetime

import httpx


class MetricsWriter:
    """Sync wrapper around the control-plane POST /metrics/{id} endpoint.

    Best-effort: swallows all send errors so the strategy layer never crashes
    on a metrics write failure.
    """

    def __init__(self, base_url: str, token: str, timeout_s: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        self._timeout = timeout_s

    def post_metric(
        self,
        strategy_id: int,
        ts: datetime,
        pnl: float,
        sharpe: float,
        max_drawdown: float,
        n_trades: int,
    ) -> None:
        try:
            with httpx.Client(
                base_url=self._base,
                headers=self._headers,
                timeout=self._timeout,
            ) as c:
                c.post(
                    f"/metrics/{strategy_id}",
                    json={
                        "ts": ts.isoformat(),
                        "pnl": pnl,
                        "sharpe": sharpe,
                        "max_drawdown": max_drawdown,
                        "n_trades": n_trades,
                    },
                )
        except Exception:
            pass  # best-effort; strategy layer must never crash on metrics send


class AuditWriter:
    """Sync wrapper around the control-plane POST /audit endpoint.

    Best-effort: swallows all send errors so the strategy layer never crashes
    on an audit write failure.
    """

    def __init__(self, base_url: str, token: str, timeout_s: float = 5.0) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        self._timeout = timeout_s

    def post(self, actor: str, action: str, payload: dict) -> None:
        try:
            with httpx.Client(
                base_url=self._base,
                headers=self._headers,
                timeout=self._timeout,
            ) as c:
                c.post(
                    "/audit",
                    json={
                        "actor": actor,
                        "action": action,
                        "payload_json": json.dumps(payload, default=str),
                    },
                )
        except Exception:
            pass  # best-effort — must never break trading path


# Module-level singletons — initialized by main.py at boot.
metrics: MetricsWriter | None = None
audit_writer: AuditWriter | None = None
