from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from control_plane.heartbeat_monitor import _check_once, should_alert
from control_plane.config import Settings
from control_plane.db import get_engine, get_sessionmaker
from control_plane.models import AuditEntry, Base


# ---------------------------------------------------------------------------
# Pure logic tests — no I/O
# ---------------------------------------------------------------------------


def test_should_alert_stale_no_debounce():
    now = datetime.now(timezone.utc)
    latest_ts = now - timedelta(seconds=120)
    assert should_alert(latest_ts, now, None, 90) is True


def test_should_alert_fresh_returns_false():
    now = datetime.now(timezone.utc)
    latest_ts = now - timedelta(seconds=10)
    assert should_alert(latest_ts, now, None, 90) is False


def test_should_alert_debounce_suppresses_repeat():
    now = datetime.now(timezone.utc)
    latest_ts = now - timedelta(seconds=120)
    # debounce_marker == latest_ts means we already alerted for this gap
    assert should_alert(latest_ts, now, latest_ts, 90) is False


def test_should_alert_after_fresh_heartbeat_clears_debounce():
    """Fresh heartbeat should allow a new alert (debounce_marker older than latest_ts)."""
    now = datetime.now(timezone.utc)
    # latest_ts is newer than debounce_marker — gap just became stale again after recovery
    debounce_marker = now - timedelta(seconds=200)
    latest_ts = now - timedelta(seconds=120)
    # latest_ts > debounce_marker means a new heartbeat came in since our last alert,
    # so debounce should be considered cleared.
    # should_alert: latest_ts > debounce_marker means NOT suppressed
    assert should_alert(latest_ts, now, debounce_marker, 90) is True


# ---------------------------------------------------------------------------
# Integration tests — use real SQLite via tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session(tmp_path):
    engine = get_engine(tmp_path / "test.sqlite3")
    Base.metadata.create_all(engine)
    Session = get_sessionmaker(engine)
    return Session


def _seed_heartbeat(Session, age_seconds: int) -> None:
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    with Session() as s:
        s.add(AuditEntry(ts=ts, actor="runner", action="heartbeat", payload_json="{}"))
        s.commit()


@pytest.mark.asyncio
async def test_stale_heartbeat_fires_alert(db_session, tmp_path):
    _seed_heartbeat(db_session, age_seconds=300)  # 5 minutes old — stale
    calls = []
    with patch("control_plane.heartbeat_monitor.get_engine") as mock_engine, \
         patch("control_plane.heartbeat_monitor.get_sessionmaker") as mock_sm, \
         patch("control_plane.telegram.get_settings",
               return_value=Settings(operator_token="t", db_path=tmp_path / "test.sqlite3",
                                     telegram_bot_token="tok", telegram_chat_id="123")):
        # Wire the real db_session
        engine = get_engine(tmp_path / "test.sqlite3")
        mock_engine.return_value = engine
        mock_sm.return_value = db_session

        sent_messages = []
        with patch("control_plane.heartbeat_monitor.telegram") as mock_telegram:
            mock_telegram.send = lambda msg: sent_messages.append(msg)
            result = await _check_once(None)

    assert any("heartbeat" in m.lower() or "stale" in m.lower() for m in sent_messages), \
        f"Expected stale heartbeat alert, got: {sent_messages}"
    assert result is not None  # debounce_marker was set


@pytest.mark.asyncio
async def test_fresh_heartbeat_no_alert(db_session, tmp_path):
    _seed_heartbeat(db_session, age_seconds=5)  # 5 seconds old — fresh
    engine = get_engine(tmp_path / "test.sqlite3")
    with patch("control_plane.heartbeat_monitor.get_engine", return_value=engine), \
         patch("control_plane.heartbeat_monitor.get_sessionmaker", return_value=db_session):

        sent_messages = []
        with patch("control_plane.heartbeat_monitor.telegram") as mock_telegram:
            mock_telegram.send = lambda msg: sent_messages.append(msg)
            result = await _check_once(None)

    assert sent_messages == [], f"Expected no alert, got: {sent_messages}"
    assert result is None  # debounce_marker unchanged


@pytest.mark.asyncio
async def test_debounce_prevents_duplicate_alerts(db_session, tmp_path):
    _seed_heartbeat(db_session, age_seconds=300)
    engine = get_engine(tmp_path / "test.sqlite3")
    with patch("control_plane.heartbeat_monitor.get_engine", return_value=engine), \
         patch("control_plane.heartbeat_monitor.get_sessionmaker", return_value=db_session):

        sent_messages = []
        with patch("control_plane.heartbeat_monitor.telegram") as mock_telegram:
            mock_telegram.send = lambda msg: sent_messages.append(msg)

            # First tick: should alert
            debounce = await _check_once(None)
            assert len(sent_messages) == 1, "Expected one alert on first tick"

            # Second tick: same stale heartbeat, debounce_marker set
            debounce = await _check_once(debounce)
            assert len(sent_messages) == 1, "Expected no additional alert on second tick"
