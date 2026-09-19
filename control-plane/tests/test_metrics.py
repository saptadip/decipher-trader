import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from control_plane.config import Settings, get_settings
from control_plane.db import get_engine, get_session, get_sessionmaker
from control_plane.main import app
from control_plane.models import AuditEntry, Base, Strategy, StrategyStatus


@pytest.fixture()
def env(tmp_path):
    engine = get_engine(tmp_path / "t.sqlite3")
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    def _s():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _s
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s", db_path=tmp_path / "t.sqlite3")
    with Session() as s:
        s.add(Strategy(id=1, name="t", code_path="p", status=StrategyStatus.paper, capital_weight=0, max_notional=1, max_daily_loss=1, max_position=1))
        s.commit()
    yield Session, TestClient(app)
    app.dependency_overrides.clear()


AUTH = {"Authorization": "Bearer s"}


def test_post_and_get_metrics(env):
    _, client = env
    ts = datetime.now(timezone.utc)
    resp = client.post(
        "/metrics/1",
        headers=AUTH,
        json={"ts": ts.isoformat(), "pnl": 1.0, "sharpe": 0.5, "max_drawdown": 0.1, "n_trades": 3},
    )
    assert resp.status_code == 201

    resp = client.get("/metrics/1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["pnl"] == 1.0


# ---------------------------------------------------------------------------
# Auto-demote tests
# ---------------------------------------------------------------------------

def _seed_strategy(Session, *, status: StrategyStatus, max_daily_loss: float = 100.0) -> int:
    with Session() as s:
        row = Strategy(
            name="algo",
            code_path="path/to/algo.py",
            status=status,
            capital_weight=1.0,
            max_notional=10_000.0,
            max_daily_loss=max_daily_loss,
            max_position=1.0,
        )
        s.add(row)
        s.commit()
        return row.id


def _post_metric(client, strategy_id: int, max_drawdown: float) -> int:
    ts = datetime.now(timezone.utc).isoformat()
    resp = client.post(
        f"/metrics/{strategy_id}",
        headers=AUTH,
        json={"ts": ts, "pnl": -max_drawdown, "sharpe": -1.0, "max_drawdown": max_drawdown, "n_trades": 5},
    )
    return resp.status_code


def test_metric_auto_demotes_live_on_drawdown_breach(env):
    Session, client = env
    sid = _seed_strategy(Session, status=StrategyStatus.live, max_daily_loss=100.0)

    with (
        patch("control_plane.routers.metrics.telegram.send") as mock_tg,
        patch("control_plane.routers.metrics.broadcaster.broadcast") as mock_bc,
    ):
        code = _post_metric(client, sid, max_drawdown=150.0)

    assert code == 201

    # Strategy must be demoted to paper
    with Session() as s:
        strategy = s.get(Strategy, sid)
        assert strategy.status is StrategyStatus.paper

        # Audit entry must exist
        audit_rows = list(
            s.scalars(
                select(AuditEntry).where(
                    AuditEntry.action == "auto_demote_drawdown",
                    AuditEntry.actor == "control-plane",
                )
            )
        )
        assert len(audit_rows) == 1
        payload = json.loads(audit_rows[0].payload_json)
        assert payload["strategy_id"] == sid
        assert payload["max_drawdown"] == 150.0
        assert payload["max_daily_loss"] == 100.0
        # I1: audit payload must carry metric_id so the audit row is joinable to the
        # snapshot that triggered the demote. If future refactor drops this field the
        # audit-to-metric link is lost silently.
        assert "metric_id" in payload
        assert isinstance(payload["metric_id"], int)

    # Telegram called once with the full prescribed message format.
    # I2: assertions cover the design contract — ⚠️ prefix, "Auto-demoted", strategy id,
    # drawdown value, ≥ character, and max_daily_loss value. A silent reformat that drops
    # any of these must fail this test.
    mock_tg.assert_called_once()
    msg = mock_tg.call_args[0][0]
    assert "⚠️" in msg
    assert "Auto-demoted" in msg
    assert str(sid) in msg
    assert "150.0" in msg
    assert "≥" in msg
    assert "100.0" in msg

    # Broadcaster called once with kill_all type
    mock_bc.assert_called_once()
    broadcast_event = mock_bc.call_args[0][0]
    assert broadcast_event["type"] == "kill_all"
    assert sid in broadcast_event["demoted"]


def test_metric_on_live_below_cap_no_demote(env):
    Session, client = env
    sid = _seed_strategy(Session, status=StrategyStatus.live, max_daily_loss=100.0)

    with (
        patch("control_plane.routers.metrics.telegram.send") as mock_tg,
        patch("control_plane.routers.metrics.broadcaster.broadcast") as mock_bc,
    ):
        code = _post_metric(client, sid, max_drawdown=50.0)

    assert code == 201

    # Strategy must remain live
    with Session() as s:
        strategy = s.get(Strategy, sid)
        assert strategy.status is StrategyStatus.live

        audit_rows = list(
            s.scalars(
                select(AuditEntry).where(AuditEntry.action == "auto_demote_drawdown")
            )
        )
        assert len(audit_rows) == 0

    mock_tg.assert_not_called()
    mock_bc.assert_not_called()


def test_metric_on_paper_no_demote_even_on_breach(env):
    Session, client = env
    sid = _seed_strategy(Session, status=StrategyStatus.paper, max_daily_loss=100.0)

    with (
        patch("control_plane.routers.metrics.telegram.send") as mock_tg,
        patch("control_plane.routers.metrics.broadcaster.broadcast") as mock_bc,
    ):
        code = _post_metric(client, sid, max_drawdown=150.0)

    assert code == 201

    # Strategy must stay paper (it was paper; auto-demote is live-only)
    with Session() as s:
        strategy = s.get(Strategy, sid)
        assert strategy.status is StrategyStatus.paper

        audit_rows = list(
            s.scalars(
                select(AuditEntry).where(AuditEntry.action == "auto_demote_drawdown")
            )
        )
        assert len(audit_rows) == 0

    mock_tg.assert_not_called()
    mock_bc.assert_not_called()


def test_metric_auto_demote_boundary(env):
    """Exact equality (max_drawdown == max_daily_loss) must trigger demote (>= not >)."""
    Session, client = env
    sid = _seed_strategy(Session, status=StrategyStatus.live, max_daily_loss=100.0)

    with (
        patch("control_plane.routers.metrics.telegram.send") as mock_tg,
        patch("control_plane.routers.metrics.broadcaster.broadcast") as mock_bc,
    ):
        code = _post_metric(client, sid, max_drawdown=100.0)

    assert code == 201

    with Session() as s:
        strategy = s.get(Strategy, sid)
        assert strategy.status is StrategyStatus.paper

        audit_rows = list(
            s.scalars(
                select(AuditEntry).where(AuditEntry.action == "auto_demote_drawdown")
            )
        )
        assert len(audit_rows) == 1

    mock_tg.assert_called_once()
    mock_bc.assert_called_once()
