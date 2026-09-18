from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from control_plane.config import Settings, get_settings
from control_plane.db import get_engine, get_session, get_sessionmaker
from control_plane.main import app
from control_plane.models import AuditEntry, Base, MetricsSnapshot, Strategy, StrategyStatus


@pytest.fixture()
def env(tmp_path):
    db_path = tmp_path / "t.sqlite3"
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    def _session():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret", db_path=db_path, paper_forward_min_days=14)
    yield Session, TestClient(app)
    app.dependency_overrides.clear()


AUTH = {"Authorization": "Bearer s3cret"}


def _seed(Session, *, status: StrategyStatus, paper_started_at=None, max_daily_loss=10.0):
    with Session() as s:
        row = Strategy(
            name="toy",
            code_path="p",
            status=status,
            capital_weight=1.0,
            max_notional=100.0,
            max_daily_loss=max_daily_loss,
            max_position=1.0,
            paper_started_at=paper_started_at,
        )
        s.add(row)
        s.commit()
        return row.id


def test_promote_rejects_non_paper(env):
    Session, client = env
    sid = _seed(Session, status=StrategyStatus.draft)
    resp = client.post(f"/strategies/{sid}/promote", headers=AUTH)
    assert resp.status_code == 409


def test_promote_rejects_too_recent_paper(env):
    Session, client = env
    started = datetime.now(timezone.utc) - timedelta(days=5)
    sid = _seed(Session, status=StrategyStatus.paper, paper_started_at=started)
    resp = client.post(f"/strategies/{sid}/promote", headers=AUTH)
    assert resp.status_code == 409
    assert "14" in resp.json()["detail"]


def test_promote_rejects_fatal_drawdown(env):
    Session, client = env
    started = datetime.now(timezone.utc) - timedelta(days=20)
    sid = _seed(Session, status=StrategyStatus.paper, paper_started_at=started, max_daily_loss=10.0)
    with Session() as s:
        s.add(
            MetricsSnapshot(
                strategy_id=sid,
                ts=datetime.now(timezone.utc) - timedelta(days=3),
                pnl=-50,
                sharpe=0.0,
                max_drawdown=20.0,
                n_trades=1,
            )
        )
        s.commit()
    resp = client.post(f"/strategies/{sid}/promote", headers=AUTH)
    assert resp.status_code == 409
    assert "drawdown" in resp.json()["detail"]


def test_promote_success(env):
    Session, client = env
    started = datetime.now(timezone.utc) - timedelta(days=20)
    sid = _seed(Session, status=StrategyStatus.paper, paper_started_at=started)
    resp = client.post(f"/strategies/{sid}/promote", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "live"
    assert body["promoted_by"] == "operator"

    with Session() as s:
        rows = s.scalars(select(AuditEntry).where(AuditEntry.action == "promote")).all()
        assert len(rows) == 1


def test_demote_flips_live_to_paper(env):
    Session, client = env
    started = datetime.now(timezone.utc) - timedelta(days=20)
    sid = _seed(Session, status=StrategyStatus.live, paper_started_at=started)
    resp = client.post(f"/strategies/{sid}/demote", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "paper"


def test_start_paper_from_draft(env):
    Session, client = env
    sid = _seed(Session, status=StrategyStatus.draft)
    resp = client.post(f"/strategies/{sid}/start_paper", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "paper"
    assert body["paper_started_at"] is not None


def test_start_paper_rejects_non_draft(env):
    Session, client = env
    started = datetime.now(timezone.utc) - timedelta(days=1)
    sid = _seed(Session, status=StrategyStatus.paper, paper_started_at=started)
    resp = client.post(f"/strategies/{sid}/start_paper", headers=AUTH)
    assert resp.status_code == 409
