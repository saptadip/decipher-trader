import pytest
from fastapi.testclient import TestClient

from control_plane.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)
