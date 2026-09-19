from __future__ import annotations

import logging

import httpx

from control_plane.config import get_settings

log = logging.getLogger(__name__)


def send(text: str) -> None:
    """Best-effort Telegram push. No-op when bot token / chat id are not configured.
    Never raises — must never break a request path."""
    settings = get_settings()
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        return
    try:
        with httpx.Client(timeout=5.0) as c:
            c.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
    except Exception as e:
        log.warning("telegram alert failed: %s", e)
