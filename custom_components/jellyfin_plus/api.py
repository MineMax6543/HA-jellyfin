"""Minimal Jellyfin REST + WebSocket client (aiohttp, no extra packages)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
from typing import Any

import aiohttp

from .const import (
    CLIENT_NAME,
    DEFAULT_KEEPALIVE_SECONDS,
    DEVICE_NAME,
    RECONNECT_MAX_SECONDS,
    RECONNECT_MIN_SECONDS,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


class JellyfinError(Exception):
    """Base error."""


class CannotConnect(JellyfinError):
    """Server not reachable or answered unexpectedly."""


class InvalidAuth(JellyfinError):
    """Credentials or token rejected."""


def normalize_url(url: str) -> str:
    """Strip whitespace and trailing slashes, default to http://."""
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = f"http://{url}"
    return url


def auth_header(device_id: str, token: str | None = None) -> dict[str, str]:
    """Jellyfin's MediaBrowser authorization header."""
    value = (
        f'MediaBrowser Client="{CLIENT_NAME}", Device="{DEVICE_NAME}", '
        f'DeviceId="{device_id}", Version="{VERSION}"'
    )
    if token:
        value += f', Token="{token}"'
    return {"Authorization": value}


async def _request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
) -> Any:
    try:
        async with session.request(
            method, url, headers=headers, json=json_body, timeout=REQUEST_TIMEOUT
        ) as resp:
            if resp.status in (401, 403):
                raise InvalidAuth(f"{resp.status} from {url}")
            if resp.status >= 400:
                raise CannotConnect(f"{resp.status} from {url}")
            if resp.content_type == "application/json":
                return await resp.json()
            return None
    except (aiohttp.ClientError, TimeoutError) as err:
        raise CannotConnect(str(err)) from err


async def async_authenticate(
    session: aiohttp.ClientSession,
    url: str,
    username: str,
    password: str,
    device_id: str,
) -> dict[str, Any]:
    """Log in; returns Jellyfin's auth result (AccessToken, User, ServerId)."""
    result = await _request(
        session,
        "POST",
        f"{url}/Users/AuthenticateByName",
        auth_header(device_id),
        {"Username": username, "Pw": password},
    )
    if not isinstance(result, dict) or not result.get("AccessToken"):
        raise CannotConnect("Unexpected login response")
    return result


async def async_get_system_info(
    session: aiohttp.ClientSession, url: str, token: str, device_id: str
) -> dict[str, Any]:
    """Authenticated server info (ServerName, Id, Version)."""
    result = await _request(
        session, "GET", f"{url}/System/Info", auth_header(device_id, token)
    )
    if not isinstance(result, dict):
        raise CannotConnect("Unexpected system info response")
    return result


async def async_get_sessions(
    session: aiohttp.ClientSession, url: str, token: str, device_id: str
) -> list[dict[str, Any]]:
    """Current sessions (baseline after (re)connecting)."""
    result = await _request(
        session, "GET", f"{url}/Sessions", auth_header(device_id, token)
    )
    return result if isinstance(result, list) else []


class JellyfinListener:
    """Keeps a WebSocket open and hands every session list to a callback.

    Reconnects with backoff. After each (re)connect it fetches the sessions
    once via REST, because Jellyfin only pushes on changes.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        token: str,
        device_id: str,
        on_sessions: Callable[[list[dict[str, Any]]], None],
        on_connection: Callable[[bool], None],
        on_auth_failed: Callable[[], None],
        create_task: Callable[[Awaitable[Any], str], asyncio.Task],
    ) -> None:
        self._session = session
        self._url = url
        self._token = token
        self._device_id = device_id
        self._on_sessions = on_sessions
        self._on_connection = on_connection
        self._on_auth_failed = on_auth_failed
        self._create_task = create_task
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.connected = False

    @property
    def ws_url(self) -> str:
        if self._url.startswith("https://"):
            return "wss://" + self._url[len("https://") :] + "/socket"
        return "ws://" + self._url.split("://", 1)[-1] + "/socket"

    def start(self) -> None:
        self._stopping = False
        self._task = self._create_task(self._run(), "jellyfin_plus websocket")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._set_connected(False)

    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            self._on_connection(connected)

    async def _run(self) -> None:
        delay = RECONNECT_MIN_SECONDS
        while not self._stopping:
            try:
                await self._connect_once()
                delay = RECONNECT_MIN_SECONDS  # clean close: retry quickly
            except InvalidAuth:
                _LOGGER.error("Jellyfin rejected the access token; re-authentication needed")
                self._set_connected(False)
                self._on_auth_failed()
                return
            except (CannotConnect, aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.warning("Jellyfin connection lost: %s (retrying in %ss)", err, delay)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error in Jellyfin listener")
            self._set_connected(False)
            if self._stopping:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_SECONDS)

    async def _connect_once(self) -> None:
        try:
            ws = await self._session.ws_connect(
                self.ws_url,
                headers=auth_header(self._device_id, self._token),
                heartbeat=None,
            )
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise InvalidAuth(str(err)) from err
            raise CannotConnect(str(err)) from err

        keepalive: asyncio.Task | None = None
        try:
            await ws.send_json({"MessageType": "SessionsStart", "Data": "0,1000"})
            keepalive = asyncio.ensure_future(self._keepalive(ws, DEFAULT_KEEPALIVE_SECONDS))
            # Baseline: Jellyfin only pushes on changes
            self._on_sessions(
                await async_get_sessions(self._session, self._url, self._token, self._device_id)
            )
            self._set_connected(True)

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break
                    continue
                try:
                    message = json.loads(msg.data)
                except ValueError:
                    continue
                kind = message.get("MessageType")
                if kind == "Sessions" and isinstance(message.get("Data"), list):
                    self._on_sessions(message["Data"])
                elif kind == "ForceKeepAlive":
                    seconds = message.get("Data")
                    if isinstance(seconds, (int, float)) and seconds > 0:
                        keepalive.cancel()
                        keepalive = asyncio.ensure_future(
                            self._keepalive(ws, max(seconds / 2, 5))
                        )
                elif kind in ("ServerRestarting", "ServerShuttingDown"):
                    _LOGGER.info("Jellyfin server is restarting/shutting down")
                    break
        finally:
            if keepalive:
                keepalive.cancel()
            await ws.close()

    @staticmethod
    async def _keepalive(ws: aiohttp.ClientWebSocketResponse, every: float) -> None:
        while not ws.closed:
            await asyncio.sleep(every)
            await ws.send_json({"MessageType": "KeepAlive"})
