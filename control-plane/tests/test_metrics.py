from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from control_plane.config import Settings, get_settings
from control_plane.db import get_engine, get_session, get_sessionmaker
from control_plane.main import app
from control_plane.models import Base, Strategy, StrategyStatus


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
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_post_and_get_metrics(env):
    client = env
    ts = datetime.now(timezone.utc)
    resp = client.post(
        "/metrics/1",
        headers={"Authorization": "Bearer s"},
        json={"ts": ts.isoformat(), "pnl": 1.0, "sharpe": 0.5, "max_drawdown": 0.1, "n_trades": 3},
    )
    assert resp.status_code == 201

    resp = client.get("/metrics/1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["pnl"] == 1.0
