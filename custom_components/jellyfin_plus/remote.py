"""Remote per Jellyfin client device (navigation commands)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from homeassistant.components.remote import ATTR_DELAY_SECS, ATTR_NUM_REPEATS, RemoteEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import JellyfinError
from .entity import (
    JellyfinDeviceEntity,
    async_setup_device_entities,
    command_error,
    supported_commands,
)

if TYPE_CHECKING:
    from . import JellyfinConfigEntry

# Friendly aliases -> Jellyfin GeneralCommand names
ALIASES = {
    "up": "MoveUp",
    "down": "MoveDown",
    "left": "MoveLeft",
    "right": "MoveRight",
    "select": "Select",
    "ok": "Select",
    "enter": "Select",
    "back": "Back",
    "home": "GoHome",
    "menu": "ToggleContextMenu",
    "osd": "ToggleOsd",
    "page_up": "PageUp",
    "page_down": "PageDown",
    "search": "GoToSearch",
    "settings": "GoToSettings",
    "fullscreen": "ToggleFullscreen",
    "volume_up": "VolumeUp",
    "volume_down": "VolumeDown",
    "mute": "ToggleMute",
}
# A client is "remote-controllable" if it accepts navigation commands
_NAV_COMMANDS = {"MoveUp", "MoveDown", "Select", "Back"}


def _wants_remote(session: dict[str, Any]) -> bool:
    return bool(supported_commands(session) & _NAV_COMMANDS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    async_setup_device_entities(
        hass,
        entry,
        async_add_entities,
        "remote",
        _wants_remote,
        lambda device_id, name, client: JellyfinRemote(entry, runtime, device_id, name, client),
    )


class JellyfinRemote(JellyfinDeviceEntity, RemoteEntity):
    """Send navigation commands (MoveUp, Select, Back, …) to a client."""

    _attr_translation_key = "remote"

    @property
    def is_on(self) -> bool:
        return self.session is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"supported_commands": sorted(supported_commands(self.session))}

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        session = self.session
        if not session or not session.get("Id"):
            raise HomeAssistantError(f"{self.device_id} is not connected to Jellyfin")
        repeats = int(kwargs.get(ATTR_NUM_REPEATS) or 1)
        delay = float(kwargs.get(ATTR_DELAY_SECS) or 0)
        names = [ALIASES.get(c.strip().lower(), c.strip()) for c in command]
        allowed = supported_commands(session)
        unknown = [n for n in names if allowed and n not in allowed]
        if unknown:
            raise ServiceValidationError(
                f"{', '.join(unknown)} not supported by this client. "
                f"Supported: {', '.join(sorted(allowed))}"
            )
        try:
            for _ in range(repeats):
                for name in names:
                    await self._runtime.client.general_command(session["Id"], name)
                    if delay:
                        await asyncio.sleep(delay)
        except JellyfinError as err:
            raise command_error(err, self._entry) from err
