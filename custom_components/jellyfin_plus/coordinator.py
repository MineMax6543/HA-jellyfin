"""Polled data: server info, updates, storage, upcoming episodes, next up."""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import JellyfinClient, JellyfinError
from .const import GITHUB_LATEST_RELEASE, POLL_MINUTES

_LOGGER = logging.getLogger(__name__)

GITHUB_EVERY_SECONDS = 6 * 3600


class JellyfinCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Everything that doesn't come over the WebSocket.

    Parts fail independently: a broken plugin repository must not take the
    calendar or the storage sensors down with it.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: JellyfinClient,
        is_admin: bool,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Jellyfin Plus",
            update_interval=timedelta(minutes=POLL_MINUTES),
        )
        self.client = client
        self.is_admin = is_admin
        self._github: dict[str, Any] = {}
        self._github_checked = 0.0

    async def _async_update_data(self) -> dict[str, Any]:
        previous = self.data or {}
        data: dict[str, Any] = {}
        try:
            data["system"] = await self.client.system_info()
        except JellyfinError as err:
            raise UpdateFailed(f"Jellyfin unreachable: {err}") from err

        parts: dict[str, Any] = {
            "upcoming": self.client.upcoming,
            "next_up": self.client.next_up,
        }
        if self.is_admin:
            parts |= {
                "storage": self.client.storage,
                "plugins": self.client.plugins,
                "packages": self.client.packages,
            }
        for key, fetch in parts.items():
            try:
                data[key] = await fetch()
            except JellyfinError as err:
                _LOGGER.debug("Jellyfin %s refresh failed: %s", key, err)
                data[key] = previous.get(key)

        data["latest_release"] = await self._latest_release()
        return data

    async def _latest_release(self) -> dict[str, Any]:
        """Latest Jellyfin server release from GitHub (checked every 6 h)."""
        if time.monotonic() - self._github_checked < GITHUB_EVERY_SECONDS and self._github:
            return self._github
        self._github_checked = time.monotonic()
        try:
            async with self.client.session.get(
                GITHUB_LATEST_RELEASE,
                headers={"Accept": "application/vnd.github+json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    release = await resp.json()
                    self._github = {
                        "version": str(release.get("tag_name", "")).lstrip("v"),
                        "url": release.get("html_url"),
                        "notes": (release.get("body") or "")[:255],
                    }
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Could not check the latest Jellyfin release: %s", err)
        return self._github
