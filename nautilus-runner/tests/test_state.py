from datetime import datetime, timezone
from unittest.mock import patch

import httpx

from nautilus_runner.state import AuditWriter, MetricsWriter


def test_metrics_writer_swallows_connection_error():
    """MetricsWriter.post_metric must not propagate exceptions from the HTTP layer."""
    writer = MetricsWriter(base_url="http://localhost:9999", token="tok")

    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.side_effect = httpx.ConnectError(
            "refused"
        )
        # Must not raise — best-effort fire-and-forget
        writer.post_metric(
            strategy_id=1,
            ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
            pnl=-5.0,
            sharpe=0.0,
            max_drawdown=5.0,
            n_trades=3,
        )


def test_audit_writer_swallows_error():
    """AuditWriter.post must not propagate exceptions from the HTTP layer."""
    writer = AuditWriter(base_url="http://localhost:9999", token="tok")

    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.side_effect = httpx.ConnectError(
            "refused"
        )
        # Must not raise — best-effort fire-and-forget
        writer.post(
            actor="runner",
            action="position_drift",
            payload={"strategy_id": 1, "drift": 0.5},
        )
