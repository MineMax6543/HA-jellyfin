"""Jellyfin Plus: instant Jellyfin playback events for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import (
    CannotConnect,
    InvalidAuth,
    JellyfinListener,
    async_get_system_info,
)
from .const import (
    CONF_DEVICE_ID,
    CONF_TOKEN,
    DOMAIN,
    EVENT_PLAYBACK,
    SIGNAL_CONNECTION,
    SIGNAL_PLAYBACK,
)
from .sessions import PlaybackEvent, SessionTracker

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.EVENT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class JellyfinRuntime:
    """Everything a loaded entry needs."""

    server_id: str
    server_name: str
    server_version: str | None
    tracker: SessionTracker
    listener: JellyfinListener | None = None

    @property
    def connected(self) -> bool:
        return bool(self.listener and self.listener.connected)


type JellyfinConfigEntry = ConfigEntry[JellyfinRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: JellyfinConfigEntry) -> bool:
    """Connect to the server and start listening."""
    session = async_get_clientsession(hass)
    url = entry.data[CONF_URL]
    token = entry.data[CONF_TOKEN]
    device_id = entry.data[CONF_DEVICE_ID]

    try:
        info = await async_get_system_info(session, url, token, device_id)
    except InvalidAuth as err:
        raise ConfigEntryAuthFailed("Jellyfin rejected the access token") from err
    except CannotConnect as err:
        raise ConfigEntryNotReady(f"Cannot reach Jellyfin at {url}: {err}") from err

    runtime = JellyfinRuntime(
        server_id=info.get("Id") or entry.unique_id or entry.entry_id,
        server_name=info.get("ServerName") or "Jellyfin",
        server_version=info.get("Version"),
        tracker=SessionTracker(own_device_id=device_id),
    )
    entry.runtime_data = runtime

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, runtime.server_id)},
        name=runtime.server_name,
        manufacturer="Jellyfin",
        model="Jellyfin server",
        sw_version=runtime.server_version,
        configuration_url=url,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _on_sessions(sessions: list[dict[str, Any]]) -> None:
        for event in runtime.tracker.update(sessions):
            _handle_event(hass, entry, runtime, event)

    @callback
    def _on_connection(connected: bool) -> None:
        async_dispatcher_send(hass, SIGNAL_CONNECTION.format(entry.entry_id), connected)

    @callback
    def _on_auth_failed() -> None:
        entry.async_start_reauth(hass)

    runtime.listener = JellyfinListener(
        session,
        url,
        token,
        device_id,
        on_sessions=_on_sessions,
        on_connection=_on_connection,
        on_auth_failed=_on_auth_failed,
        create_task=lambda coro, name: entry.async_create_background_task(hass, coro, name),
    )
    runtime.listener.start()
    return True


@callback
def _handle_event(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    runtime: JellyfinRuntime,
    event: PlaybackEvent,
) -> None:
    """Fire the bus event and hand the event to the entities."""
    payload = {
        "action": event.action,
        "server_name": runtime.server_name,
        "config_entry_id": entry.entry_id,
        **event.data,
    }
    hass.bus.async_fire(EVENT_PLAYBACK, payload)
    async_dispatcher_send(hass, SIGNAL_PLAYBACK.format(entry.entry_id), event)


async def async_unload_entry(hass: HomeAssistant, entry: JellyfinConfigEntry) -> bool:
    """Stop listening."""
    if entry.runtime_data.listener:
        await entry.runtime_data.listener.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
