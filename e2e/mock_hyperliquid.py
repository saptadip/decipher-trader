from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket

app = FastAPI()
FIXTURE = Path(__file__).parent / "fixtures" / "hyperliquid_ws.json"


@app.get("/info")
async def info() -> dict:
    return {"status": "ok"}


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    events = json.loads(FIXTURE.read_text())
    for ev in events:
        await sock.send_text(json.dumps(ev))
        await asyncio.sleep(0.05)
    while True:
        await asyncio.sleep(60)
