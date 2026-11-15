"""The real WebSocket listener against a fake Jellyfin server."""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import WSMsgType, web
import pytest

from custom_components.jellyfin_plus import api
from custom_components.jellyfin_plus.api import JellyfinListener

from .test_sessions import MOVIE, session


class FakeJellyfin:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.auth_headers: list[str] = []
        self.sockets: list[web.WebSocketResponse] = []
        self.sessions: list[dict[str, Any]] = [session()]
        self.reject = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/socket", self.socket)
        app.router.add_get("/Sessions", self.get_sessions)
        return app

    async def get_sessions(self, request: web.Request) -> web.Response:
        return web.json_response(self.sessions)

    async def socket(self, request: web.Request) -> web.WebSocketResponse:
        self.auth_headers.append(request.headers.get("Authorization", ""))
        if self.reject:
            raise web.HTTPUnauthorized
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        await ws.send_json({"MessageType": "ForceKeepAlive", "Data": 10})
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                self.received.append(msg.json())
        return ws


async def _wait_for(predicate, timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


@pytest.fixture
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(api, "RECONNECT_MIN_SECONDS", 0.05)


async def test_listener_end_to_end(aiohttp_client, fast_reconnect) -> None:
    server = FakeJellyfin()
    client = await aiohttp_client(server.app())
    base = str(client.make_url("")).rstrip("/")
    updates: list[list[dict]] = []
    connection: list[bool] = []

    listener = JellyfinListener(
        client.session,
        base,
        "tok",
        "ha-device",
        on_sessions=updates.append,
        on_connection=connection.append,
        on_auth_failed=lambda: None,
        create_task=lambda coro, name: asyncio.ensure_future(coro),
    )
    assert listener.ws_url.startswith("ws://") and listener.ws_url.endswith("/socket")
    listener.start()
    await _wait_for(lambda: connection == [True])

    assert 'Token="tok"' in server.auth_headers[0]
    assert 'DeviceId="ha-device"' in server.auth_headers[0]
    await _wait_for(lambda: server.received)
    assert server.received[0] == {"MessageType": "SessionsStart", "Data": "0,1000"}
    assert updates[0] == [session()]  # REST baseline after connecting

    # server pushes a change
    await server.sockets[0].send_json({"MessageType": "Sessions", "Data": [session(item=MOVIE)]})
    await _wait_for(lambda: len(updates) == 2)
    assert updates[1][0]["NowPlayingItem"]["Name"] == "Dune"

    # server restarts: listener reconnects and re-subscribes
    await server.sockets[0].send_json({"MessageType": "ServerRestarting"})
    await _wait_for(lambda: len(server.sockets) == 2 and connection[-1] is True)
    assert connection == [True, False, True]
    await _wait_for(lambda: len(server.received) >= 2)
    assert server.received[-1]["MessageType"] == "SessionsStart"

    await listener.stop()
    assert connection[-1] is False


async def test_listener_reports_auth_failure(aiohttp_client, fast_reconnect) -> None:
    server = FakeJellyfin()
    server.reject = True
    client = await aiohttp_client(server.app())
    failed = asyncio.Event()
    listener = JellyfinListener(
        client.session,
        str(client.make_url("")).rstrip("/"),
        "expired",
        "ha-device",
        on_sessions=lambda s: None,
        on_connection=lambda c: None,
        on_auth_failed=failed.set,
        create_task=lambda coro, name: asyncio.ensure_future(coro),
    )
    listener.start()
    async with asyncio.timeout(5):
        await failed.wait()
    await listener.stop()


def test_url_helpers() -> None:
    assert api.normalize_url(" jelly.local:8096/ ") == "http://jelly.local:8096"
    listener = JellyfinListener(
        None, "https://jf.example.com/base", "t", "d",
        on_sessions=None, on_connection=None, on_auth_failed=None, create_task=None,
    )
    assert listener.ws_url == "wss://jf.example.com/base/socket"
