"""Actions acting on a person's library data: watched / unwatched / favorite."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import JellyfinError
from .browse_media import split_user
from .const import ATTR_JELLYFIN_USER, DOMAIN
from .users import resolve_user

ATTR_ITEM_ID = "item_id"
ATTR_CONFIG_ENTRY = "config_entry_id"
ATTR_FAVORITE = "favorite"

SERVICE_MARK_PLAYED = "mark_played"
SERVICE_MARK_UNPLAYED = "mark_unplayed"
SERVICE_SET_FAVORITE = "set_favorite"

_BASE = {
    vol.Required(ATTR_ITEM_ID): cv.string,
    vol.Optional(ATTR_JELLYFIN_USER): cv.string,
    vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
}


def _entry(hass: HomeAssistant, call: ServiceCall) -> Any:
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    wanted = call.data.get(ATTR_CONFIG_ENTRY)
    if wanted:
        entries = [e for e in entries if e.entry_id == wanted]
    if len(entries) != 1:
        raise ServiceValidationError(
            "Specify config_entry_id: no or several Jellyfin Plus servers are loaded"
        )
    return entries[0]


async def _call(hass: HomeAssistant, call: ServiceCall, action: str, value: bool) -> None:
    entry = _entry(hass, call)
    tagged_user, item_id = split_user(call.data[ATTR_ITEM_ID])
    user_id = tagged_user or resolve_user(
        entry,
        explicit=call.data.get(ATTR_JELLYFIN_USER),
        ha_user_id=call.context.user_id,
    )
    client = entry.runtime_data.client
    try:
        if action == "played":
            await client.set_played(item_id, value, user_id=user_id)
        else:
            await client.set_favorite(item_id, value, user_id=user_id)
    except JellyfinError as err:
        raise HomeAssistantError(f"Jellyfin: {err}") from err
    await entry.runtime_data.coordinator.async_request_refresh()


def async_register_services(hass: HomeAssistant) -> None:
    """Register once for all Jellyfin Plus entries."""
    if hass.services.has_service(DOMAIN, SERVICE_MARK_PLAYED):
        return

    async def mark_played(call: ServiceCall) -> None:
        await _call(hass, call, "played", True)

    async def mark_unplayed(call: ServiceCall) -> None:
        await _call(hass, call, "played", False)

    async def set_favorite(call: ServiceCall) -> None:
        await _call(hass, call, "favorite", call.data[ATTR_FAVORITE])

    hass.services.async_register(DOMAIN, SERVICE_MARK_PLAYED, mark_played, vol.Schema(_BASE))
    hass.services.async_register(DOMAIN, SERVICE_MARK_UNPLAYED, mark_unplayed, vol.Schema(_BASE))
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_FAVORITE,
        set_favorite,
        vol.Schema({**_BASE, vol.Optional(ATTR_FAVORITE, default=True): cv.boolean}),
    )
