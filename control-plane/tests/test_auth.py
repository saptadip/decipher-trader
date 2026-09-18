from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from control_plane.auth import require_operator
from control_plane.config import get_settings, Settings


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/needs-auth")
    async def needs_auth(actor: str = Depends(require_operator)) -> dict[str, str]:
        return {"actor": actor}

    return app


def test_missing_token_returns_401():
    app = _make_app()
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret")
    client = TestClient(app)
    resp = client.get("/needs-auth")
    assert resp.status_code == 401


def test_wrong_token_returns_401():
    app = _make_app()
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret")
    client = TestClient(app)
    resp = client.get("/needs-auth", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_correct_token_returns_actor():
    app = _make_app()
    app.dependency_overrides[get_settings] = lambda: Settings(operator_token="s3cret")
    client = TestClient(app)
    resp = client.get("/needs-auth", headers={"Authorization": "Bearer s3cret"})
    assert resp.status_code == 200
    assert resp.json() == {"actor": "operator"}
