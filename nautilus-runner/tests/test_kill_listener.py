from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nautilus_runner.kill_listener import kill_listener_loop


class _BlockingWS:
    """A fake WebSocket whose recv() blocks until the instance's gate is set."""

    def __init__(self) -> None:
        self._gate = asyncio.Event()

    async def recv(self) -> str:
        await self._gate.wait()
        return "{}"

    async def __aenter__(self) -> "_BlockingWS":
        return self

    async def __aexit__(self, *_: object) -> None:
        pass


@pytest.mark.asyncio
async def test_stop_event_unblocks_recv() -> None:
    """kill_listener_loop must return promptly when stop_event is set while recv() blocks."""
    blocking_ws = _BlockingWS()

    @asynccontextmanager
    async def _fake_connect(_url: str, **_kw: object):  # type: ignore[override]
        yield blocking_ws

    stop_event = asyncio.Event()

    with patch("nautilus_runner.kill_listener.websockets.connect", _fake_connect):
        task = asyncio.create_task(
            kill_listener_loop("ws://fake", lambda: None, stop_event)
        )
        # Give the loop time to block on recv()
        await asyncio.sleep(0.05)
        stop_event.set()
        # Must finish within 500 ms; TimeoutError would mean the fix is missing
        await asyncio.wait_for(task, timeout=0.5)


@pytest.mark.asyncio
async def test_kill_all_calls_on_kill() -> None:
    """kill_listener_loop must invoke on_kill when a kill_all message arrives."""
    kill_all_msg = json.dumps({"type": "kill_all"})
    call_count = 0

    def _on_kill() -> None:
        nonlocal call_count
        call_count += 1

    message_gate = asyncio.Event()
    stop_event = asyncio.Event()
    delivered = asyncio.Event()

    class _OneShotWS:
        async def recv(self) -> str:
            await message_gate.wait()
            delivered.set()
            # Block forever afterwards so we control when the loop exits
            await asyncio.Event().wait()
            return ""  # unreachable

        async def __aenter__(self) -> "_OneShotWS":
            return self

        async def __aexit__(self, *_: object) -> None:
            pass

    @asynccontextmanager
    async def _fake_connect(_url: str, **_kw: object):  # type: ignore[override]
        yield _OneShotWS()

    with patch("nautilus_runner.kill_listener.websockets.connect", _fake_connect):
        # Override recv to return the kill_all message once then block
        async def _patched_recv(self: _OneShotWS) -> str:  # type: ignore[misc]
            if not delivered.is_set():
                delivered.set()
                return kill_all_msg
            await asyncio.Event().wait()
            return ""

        _OneShotWS.recv = _patched_recv  # type: ignore[method-assign]

        task = asyncio.create_task(
            kill_listener_loop("ws://fake", _on_kill, stop_event)
        )
        await asyncio.sleep(0.1)
        stop_event.set()
        await asyncio.wait_for(task, timeout=0.5)

    assert call_count == 1
