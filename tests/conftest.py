"""Fixtures for Jellyfin Plus tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.jellyfin_plus.api import JellyfinClient
from custom_components.jellyfin_plus.coordinator import JellyfinCoordinator

SYSTEM_INFO = {"Id": "srv1", "ServerName": "Media Server", "Version": "10.11.3"}
STORAGE = {
    "ProgramDataFolder": {"Path": "/config", "FreeSpace": 50_000_000_000, "UsedSpace": 10_000_000_000},
    "Libraries": [
        {"Id": "lib-movies", "Name": "Movies",
         "Folders": [{"Path": "/media/movies", "FreeSpace": 2_000_000_000_000,
                      "UsedSpace": 6_000_000_000_000}]},
    ],
}
PLUGINS = [
    {"Id": "intro-skipper-guid", "Name": "Intro Skipper", "Version": "1.10.11.3", "Status": "Active"},
    {"Id": "local-only", "Name": "Local Plugin", "Version": "1.0.0", "Status": "Active"},
]
PACKAGES = [
    {"name": "Intro Skipper", "guid": "intro-skipper-guid", "versions": [
        {"version": "1.10.11.4", "targetAbi": "10.11.0.0", "changelog": "Bug fixes",
         "repositoryUrl": "https://repo.example"},
        {"version": "2.0.0.0", "targetAbi": "10.12.0.0", "changelog": "Needs newer Jellyfin"},
    ]},
]
UPCOMING = [
    {"Id": "up1", "Name": "The Next One", "SeriesName": "Severance", "ParentIndexNumber": 2,
     "IndexNumber": 5, "PremiereDate": "2026-11-20T02:00:00Z"},
]
NEXT_UP = [{"Id": "nx1", "Name": "Pilot", "SeriesName": "Severance", "ParentIndexNumber": 1,
            "IndexNumber": 1}]
TASKS = [{"Id": "t1", "Name": "Scan Media Library", "State": "Idle"}]
COUNTS = {"MovieCount": 412, "SeriesCount": 37, "EpisodeCount": 1503, "SongCount": 0,
          "AlbumCount": 0, "ArtistCount": 0, "BoxSetCount": 12, "BookCount": 0}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom_components."""
    return


class FakeListener:
    """Stands in for the WebSocket listener; tests push data by hand."""

    instances: list[FakeListener] = []

    def __init__(self, session, url, token, device_id, on_sessions, on_connection,
                 on_auth_failed, create_task, on_message=None, subscriptions=None,
                 on_connected=None) -> None:
        self.url = url
        self.token = token
        self.device_id = device_id
        self.on_sessions = on_sessions
        self.on_connection = on_connection
        self.on_auth_failed = on_auth_failed
        self.on_message = on_message
        self.subscriptions = subscriptions
        self.on_connected = on_connected
        self.connected = False
        FakeListener.instances.append(self)

    def start(self) -> None:
        self.connected = True
        self.on_connection(True)

    async def stop(self) -> None:
        self.connected = False

    def push(self, sessions: list[dict[str, Any]]) -> None:
        self.on_sessions(sessions)

    def message(self, kind: str, data: Any) -> None:
        self.on_message(kind, data)


@pytest.fixture
def fake_listener() -> Generator[type[FakeListener]]:
    FakeListener.instances.clear()
    with (
        patch("custom_components.jellyfin_plus.JellyfinListener", FakeListener),
        patch(
            "custom_components.jellyfin_plus.async_get_system_info",
            AsyncMock(return_value=SYSTEM_INFO),
        ),
        patch.object(JellyfinClient, "system_info", AsyncMock(return_value=SYSTEM_INFO)),
        patch.object(JellyfinClient, "storage", AsyncMock(return_value=STORAGE)),
        patch.object(JellyfinClient, "plugins", AsyncMock(return_value=PLUGINS)),
        patch.object(JellyfinClient, "packages", AsyncMock(return_value=PACKAGES)),
        patch.object(JellyfinClient, "upcoming", AsyncMock(return_value=UPCOMING)),
        patch.object(JellyfinClient, "next_up", AsyncMock(return_value=NEXT_UP)),
        patch.object(JellyfinClient, "scheduled_tasks", AsyncMock(return_value=TASKS)),
        patch.object(JellyfinClient, "item_counts", AsyncMock(return_value=COUNTS)),
        patch.object(JellyfinClient, "media_segments", AsyncMock(return_value=[])),
        patch.object(
            JellyfinCoordinator,
            "_latest_release",
            AsyncMock(return_value={"version": "10.11.5", "url": "https://github.com/jellyfin/jellyfin/releases/tag/v10.11.5"}),
        ),
    ):
        yield FakeListener
