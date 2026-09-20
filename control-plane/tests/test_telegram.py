from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from control_plane.config import Settings
from control_plane import telegram


def _settings(**kwargs) -> Settings:
    defaults = dict(operator_token="tok", db_path="/tmp/x.sqlite3")
    defaults.update(kwargs)
    return Settings(**defaults)


def test_send_noop_when_token_missing():
    settings = _settings(telegram_bot_token=None, telegram_chat_id="123")
    with patch("control_plane.telegram.get_settings", return_value=settings):
        with patch("httpx.Client") as mock_client:
            telegram.send("hi")
            mock_client.assert_not_called()


def test_send_noop_when_chat_id_missing():
    settings = _settings(telegram_bot_token="mytoken", telegram_chat_id=None)
    with patch("control_plane.telegram.get_settings", return_value=settings):
        with patch("httpx.Client") as mock_client:
            telegram.send("hi")
            mock_client.assert_not_called()


def test_send_posts_when_configured():
    settings = _settings(telegram_bot_token="mytoken", telegram_chat_id="456")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_post = MagicMock(return_value=mock_response)
    mock_client_instance = MagicMock()
    mock_client_instance.post = mock_post
    mock_client_ctx = MagicMock()
    mock_client_ctx.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_ctx.__exit__ = MagicMock(return_value=False)

    with patch("control_plane.telegram.get_settings", return_value=settings):
        with patch("httpx.Client", return_value=mock_client_ctx):
            telegram.send("hello world")

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs.kwargs.get("url", call_kwargs[0][0])
    assert "mytoken" in url
    body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert body["chat_id"] == "456"
    assert body["text"] == "hello world"


def test_send_swallows_http_error():
    settings = _settings(telegram_bot_token="tok", telegram_chat_id="789")

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    mock_client_instance = MagicMock()
    mock_client_instance.post = _raise
    mock_client_ctx = MagicMock()
    mock_client_ctx.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_ctx.__exit__ = MagicMock(return_value=False)

    with patch("control_plane.telegram.get_settings", return_value=settings):
        with patch("httpx.Client", return_value=mock_client_ctx):
            # Must not raise
            result = telegram.send("hi")
            assert result is None


def test_send_logs_non_2xx_response():
    """A 4xx/5xx from Telegram (bad token, wrong chat_id, bot blocked) must log a WARNING
    with the status code and body so misconfig surfaces in `docker logs`. httpx does not
    raise on non-2xx by default; without this log the failure would be silent."""
    settings = _settings(telegram_bot_token="badtoken", telegram_chat_id="1")
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = '{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}'
    mock_post = MagicMock(return_value=mock_response)
    mock_client_instance = MagicMock()
    mock_client_instance.post = mock_post
    mock_client_ctx = MagicMock()
    mock_client_ctx.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_ctx.__exit__ = MagicMock(return_value=False)

    with patch("control_plane.telegram.get_settings", return_value=settings):
        with patch("httpx.Client", return_value=mock_client_ctx):
            with patch("control_plane.telegram.log") as mock_log:
                telegram.send("hi")

    mock_log.warning.assert_called_once()
    args = mock_log.warning.call_args[0]
    fmt = args[0]
    assert "non-2xx" in fmt
    assert 400 in args
    assert any("chat not found" in str(a) for a in args)


def test_send_no_warning_on_2xx():
    """A 200 response must NOT log a warning."""
    settings = _settings(telegram_bot_token="goodtoken", telegram_chat_id="1")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"ok":true}'
    mock_client_instance = MagicMock()
    mock_client_instance.post = MagicMock(return_value=mock_response)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_ctx.__exit__ = MagicMock(return_value=False)

    with patch("control_plane.telegram.get_settings", return_value=settings):
        with patch("httpx.Client", return_value=mock_client_ctx):
            with patch("control_plane.telegram.log") as mock_log:
                telegram.send("hi")

    mock_log.warning.assert_not_called()
