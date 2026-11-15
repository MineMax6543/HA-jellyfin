"""Fixtures for Jellyfin Plus tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

SYSTEM_INFO = {"Id": "srv1", "ServerName": "Media Server", "Version": "10.11.3"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom_components."""
    return


class FakeListener:
    """Stands in for the WebSocket listener; tests push sessions by hand."""

    instances: list[FakeListener] = []

    def __init__(self, session, url, token, device_id, on_sessions, on_connection,
                 on_auth_failed, create_task) -> None:
        self.url = url
        self.token = token
        self.device_id = device_id
        self.on_sessions = on_sessions
        self.on_connection = on_connection
        self.on_auth_failed = on_auth_failed
        self.connected = False
        FakeListener.instances.append(self)

    def start(self) -> None:
        self.connected = True
        self.on_connection(True)

    async def stop(self) -> None:
        self.connected = False

    def push(self, sessions: list[dict[str, Any]]) -> None:
        self.on_sessions(sessions)


@pytest.fixture
def fake_listener() -> Generator[type[FakeListener]]:
    FakeListener.instances.clear()
    with (
        patch("custom_components.jellyfin_plus.JellyfinListener", FakeListener),
        patch(
            "custom_components.jellyfin_plus.async_get_system_info",
            AsyncMock(return_value=SYSTEM_INFO),
        ),
    ):
        yield FakeListener
