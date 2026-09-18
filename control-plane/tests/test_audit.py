from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from control_plane.config import Settings, get_settings
from control_plane.db import get_engine, get_session, get_sessionmaker
from control_plane.main import app
from control_plane.models import Base


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
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_post_and_filter(client):
    for i, action in enumerate(["order_intent", "fill", "order_intent"]):
        resp = client.post(
            "/audit",
            headers={"Authorization": "Bearer s"},
            json={"actor": "runner", "action": action, "payload_json": f"{{\"i\":{i}}}"},
        )
        assert resp.status_code == 201

    resp = client.get("/audit?action=order_intent")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp = client.get("/audit?limit=1")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
