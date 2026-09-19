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
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret", db_path=tmp_path / "t.sqlite3")
    yield Session, TestClient(app)
    app.dependency_overrides.clear()


AUTH = {"Authorization": "Bearer s3cret"}


def test_kill_all_flips_only_live(env):
    Session, client = env
    with Session() as s:
        s.add(Strategy(name="a", code_path="a", status=StrategyStatus.live, capital_weight=1, max_notional=1, max_daily_loss=1, max_position=1))
        s.add(Strategy(name="b", code_path="b", status=StrategyStatus.live, capital_weight=1, max_notional=1, max_daily_loss=1, max_position=1))
        s.add(Strategy(name="c", code_path="c", status=StrategyStatus.paper, capital_weight=1, max_notional=1, max_daily_loss=1, max_position=1))
        s.commit()

    with patch("control_plane.routers.kill.telegram.send") as mock_send:
        resp = client.post("/kill_all", headers=AUTH)
    assert resp.status_code == 200
    assert set(resp.json()["demoted"]) == {1, 2}

    with Session() as s:
        statuses = {row.name: row.status for row in s.scalars(select(Strategy))}
        assert statuses["a"] is StrategyStatus.paper
        assert statuses["b"] is StrategyStatus.paper
        assert statuses["c"] is StrategyStatus.paper

        actions = [a.action for a in s.scalars(select(AuditEntry)).all()]
        assert actions.count("demote") == 2
        assert actions.count("kill_all") == 1

    mock_send.assert_called_once()
    msg = mock_send.call_args[0][0]
    assert "KILL ALL" in msg
    # Both demoted strategy IDs appear in the message
    assert "1" in msg
    assert "2" in msg
