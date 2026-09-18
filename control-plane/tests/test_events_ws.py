import json
import pytest
from fastapi.testclient import TestClient

from control_plane.config import Settings, get_settings
from control_plane.db import get_engine, get_session, get_sessionmaker
from control_plane.main import app
from control_plane.models import Base, Strategy, StrategyStatus


@pytest.fixture()
def client(tmp_path):
    engine = get_engine(tmp_path / "t.sqlite3")
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    def _s():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _s
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s", db_path=tmp_path / "t.sqlite3")
    with Session() as s:
        s.add(Strategy(id=1, name="t", code_path="p", status=StrategyStatus.live, capital_weight=0, max_notional=1, max_daily_loss=1, max_position=1))
        s.commit()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_kill_all_broadcasts(client):
    with client.websocket_connect("/events") as ws:
        resp = client.post("/kill_all", headers={"Authorization": "Bearer s"})
        assert resp.status_code == 200
        msg = ws.receive_text()
        payload = json.loads(msg)
        assert payload["type"] == "kill_all"
