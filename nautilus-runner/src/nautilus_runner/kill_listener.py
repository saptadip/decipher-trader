from __future__ import annotations

import asyncio
import json
from typing import Callable

import websockets


async def kill_listener_loop(
    control_plane_ws_url: str,
    on_kill: Callable[[], None],
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            async with websockets.connect(control_plane_ws_url) as ws:
                while not stop_event.is_set():
                    recv_task = asyncio.create_task(ws.recv())
                    stop_task = asyncio.create_task(stop_event.wait())
                    done, pending = await asyncio.wait(
                        {recv_task, stop_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                    if stop_task in done:
                        return
                    msg = recv_task.result()
                    try:
                        data = json.loads(msg)
                    except Exception:
                        continue
                    if data.get("type") == "kill_all":
                        on_kill()
        except Exception:
            await asyncio.sleep(1.0)
