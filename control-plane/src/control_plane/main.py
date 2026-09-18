from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from control_plane.events import broadcaster
from control_plane.routers import audit, health, kill, metrics, strategies

app = FastAPI(title="decipher-trader control-plane")
app.include_router(health.router)
app.include_router(strategies.router)
app.include_router(kill.router)
app.include_router(metrics.router)
app.include_router(audit.router)


@app.websocket("/events")
async def events_ws(ws: WebSocket) -> None:
    await broadcaster.register(ws)
    try:
        while True:
            await ws.receive_text()  # any client message keeps the socket alive
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(ws)
