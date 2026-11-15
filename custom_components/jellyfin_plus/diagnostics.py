"""Download diagnostics: current state plus the recent trace."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN, CONF_USER_ID, VERSION

if TYPE_CHECKING:
    from . import JellyfinConfigEntry

TO_REDACT = {
    CONF_TOKEN,
    CONF_USER_ID,
    "password",
    "AccessToken",
    "UserId",
    "UserName",
    "RemoteEndPoint",
    "UserPrimaryImageTag",
    "AdditionalUsers",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: JellyfinConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    data = runtime.coordinator.data or {}
    return {
        "integration_version": VERSION,
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "server": {
            "name": runtime.server_name,
            "version": runtime.server_version,
            "is_admin": runtime.is_admin,
            "connected": runtime.connected,
        },
        "players": {
            entity_id: player.debug_state() for entity_id, player in runtime.players.items()
        },
        "sessions": async_redact_data(runtime.sessions, TO_REDACT),
        "tasks": [
            {"name": t.get("Name"), "state": t.get("State")} for t in runtime.tasks
        ],
        "polled": {
            "last_update_success": runtime.coordinator.last_update_success,
            "upcoming": len(data.get("upcoming") or []),
            "next_up": len(data.get("next_up") or []),
            "plugins": len(data.get("plugins") or []),
            "latest_release": data.get("latest_release"),
        },
        "trace": runtime.trace.as_list(),
    }
