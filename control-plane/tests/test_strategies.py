import pytest
from fastapi.testclient import TestClient

from control_plane.config import Settings, get_settings
from control_plane.db import get_session, get_engine, get_sessionmaker
from control_plane.main import app
from control_plane.models import Base


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)

    def _session():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret", db_path=db_path)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer s3cret"}


def test_list_strategies_empty(client):
    resp = client.get("/strategies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_requires_auth(client):
    resp = client.post("/strategies", json={"name": "x", "code_path": "y", "max_notional": 1, "max_daily_loss": 1, "max_position": 1})
    assert resp.status_code == 401


def test_create_returns_draft(client):
    resp = client.post(
        "/strategies",
        json={"name": "toy", "code_path": "strategies/toy/strategy.py", "max_notional": 100, "max_daily_loss": 10, "max_position": 1},
        headers=_auth(),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "toy"
    assert body["status"] == "draft"

    resp2 = client.get("/strategies?status=draft")
    assert resp2.status_code == 200
    assert len(resp2.json()) == 1
