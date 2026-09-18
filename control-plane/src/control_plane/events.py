from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, event: dict[str, Any]) -> None:
        payload = json.dumps(event)
        async with self._lock:
            stale: list[WebSocket] = []
            for client in self._clients:
                try:
                    await client.send_text(payload)
                except Exception:
                    stale.append(client)
            for s in stale:
                self._clients.discard(s)


broadcaster = Broadcaster()
