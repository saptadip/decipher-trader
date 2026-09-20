import json
from datetime import datetime, timezone
from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# Native-epic 5 tests: Telegram alert on socket_disconnected audit action
# ---------------------------------------------------------------------------


def test_create_audit_socket_disconnect_fires_telegram(client):
    """POST audit with action=socket_disconnected must return 201 and fire telegram.send."""
    payload = {
        "strategy_id": 99,
        "state": "DISCONNECTED",
        "client_id": "HYPERLIQUID",
        "endpoint": "wss://api.hyperliquid.xyz/ws",
        "venue": "HYPERLIQUID",
    }
    sent = []
    with patch("control_plane.routers.audit.telegram") as mock_tg:
        mock_tg.send.side_effect = lambda msg: sent.append(msg)
        resp = client.post(
            "/audit",
            headers={"Authorization": "Bearer s"},
            json={
                "actor": "runner",
                "action": "socket_disconnected",
                "payload_json": json.dumps(payload),
            },
        )

    assert resp.status_code == 201
    assert len(sent) == 1, f"Expected telegram.send called once, got: {sent}"
    msg = sent[0]
    # I2 + M3: pin the message shape — strategy id, verb, state, and venue context.
    assert "strategy 99" in msg, msg
    assert "disconnected" in msg, msg
    assert "DISCONNECTED" in msg, msg
    assert "venue=HYPERLIQUID" in msg, msg
    assert "\U0001f50c" in msg, msg  # emoji present


def test_create_audit_socket_reconnect_fires_telegram(client):
    """POST audit with action=socket_reconnected must fire a distinct reconnect alert."""
    payload = {
        "strategy_id": 99,
        "state": "CONNECTED",
        "client_id": "HYPERLIQUID",
        "endpoint": "wss://api.hyperliquid.xyz/ws",
        "venue": "HYPERLIQUID",
    }
    sent = []
    with patch("control_plane.routers.audit.telegram") as mock_tg:
        mock_tg.send.side_effect = lambda msg: sent.append(msg)
        resp = client.post(
            "/audit",
            headers={"Authorization": "Bearer s"},
            json={
                "actor": "runner",
                "action": "socket_reconnected",
                "payload_json": json.dumps(payload),
            },
        )

    assert resp.status_code == 201
    assert len(sent) == 1
    msg = sent[0]
    assert "strategy 99" in msg
    assert "reconnected" in msg
    assert "CONNECTED" in msg
    assert "venue=HYPERLIQUID" in msg
    assert "✅" in msg  # reconnect emoji distinguishes from disconnect


def test_create_audit_non_socket_action_no_telegram(client):
    """POST audit with non-socket action must NOT fire telegram.send."""
    sent = []
    with patch("control_plane.routers.audit.telegram") as mock_tg:
        mock_tg.send.side_effect = lambda msg: sent.append(msg)
        resp = client.post(
            "/audit",
            headers={"Authorization": "Bearer s"},
            json={
                "actor": "runner",
                "action": "heartbeat",
                "payload_json": json.dumps({"strategy_id": 1}),
            },
        )

    assert resp.status_code == 201
    assert sent == [], f"telegram.send must not be called for non-socket actions, got: {sent}"
