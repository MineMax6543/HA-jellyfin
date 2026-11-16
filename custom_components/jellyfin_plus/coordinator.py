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
from .const import CONF_USER_MAP, GITHUB_LATEST_RELEASE, POLL_MINUTES

_LOGGER = logging.getLogger(__name__)


def is_admin_account(me: dict[str, Any]) -> bool:
    return bool((me.get("Policy") or {}).get("IsAdministrator"))

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
        # Admin rights can change at any time; the entry reloads when they do
        try:
            data["is_admin"] = is_admin_account(await self.client.me())
        except JellyfinError:
            data["is_admin"] = self.is_admin

        parts: dict[str, Any] = {
            "upcoming": self.client.upcoming,
            "next_up": self.client.next_up,
            "counts": self.client.item_counts,
        }
        if self.is_admin:
            parts |= {
                "storage": self.client.storage,
                "plugins": self.client.plugins,
                "packages": self.client.packages,
                "users": self.client.users,
            }
        for key, fetch in parts.items():
            try:
                data[key] = await fetch()
            except JellyfinError as err:
                _LOGGER.debug("Jellyfin %s refresh failed: %s", key, err)
                data[key] = previous.get(key)

        data["people"] = await self._people(previous.get("people") or {})
        data["latest_release"] = await self._latest_release()
        return data

    async def _people(self, previous: dict[str, Any]) -> dict[str, Any]:
        """Next up and upcoming episodes for every linked Jellyfin user."""
        people: dict[str, Any] = {}
        user_ids = set((self.config_entry.options.get(CONF_USER_MAP) or {}).values())
        for user_id in sorted(user_ids):
            try:
                people[user_id] = {
                    "next_up": await self.client.next_up(user_id=user_id),
                    "upcoming": await self.client.upcoming(user_id=user_id),
                }
            except JellyfinError as err:
                _LOGGER.debug("Jellyfin data for user %s failed: %s", user_id, err)
                if user_id in previous:
                    people[user_id] = previous[user_id]
        return people

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
