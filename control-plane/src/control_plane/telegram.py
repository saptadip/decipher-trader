from __future__ import annotations

import logging

import httpx

from control_plane.config import get_settings

log = logging.getLogger(__name__)


def send(text: str) -> None:
    """Best-effort Telegram push. No-op when bot token / chat id are not configured.
    Never raises — must never break a request path.

    The entire body is wrapped in try/except: not just the HTTP call. This guards
    against any exception from `get_settings()` (e.g. a future pydantic validator
    change) leaking to a router endpoint and turning an alert failure into a 500.
    """
    try:
        settings = get_settings()
        token = settings.telegram_bot_token
        chat_id = settings.telegram_chat_id
        if not token or not chat_id:
            return
        with httpx.Client(timeout=5.0) as c:
            resp = c.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
        # httpx doesn't raise on 4xx/5xx by default; log so a silent-drop misconfig
        # (bad token, wrong chat_id, bot blocked by user) surfaces in `docker logs`
        # rather than vanishing.
        if resp.status_code >= 300:
            log.warning(
                "telegram alert non-2xx: status=%d body=%s",
                resp.status_code,
                resp.text[:200],
            )
    except Exception as e:
        log.warning("telegram alert failed: %s", e)
