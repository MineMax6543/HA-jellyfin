"""Shared base for per-client entities (media player, remote).

Entities are tied to the Jellyfin *device* id, not the session id. Sessions
come and go every time an app restarts; the device stays the same, so the
entity (and every automation using it) stays the same too.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SIGNAL_CONNECTION, SIGNAL_SESSIONS

if TYPE_CHECKING:
    from . import JellyfinConfigEntry, JellyfinRuntime


def client_unique_id(server_id: str, device_id: str) -> str:
    return f"{server_id}_{device_id}"


def supports_media_control(session: dict[str, Any]) -> bool:
    caps = session.get("Capabilities") or {}
    return bool(session.get("SupportsMediaControl") or caps.get("SupportsMediaControl"))


def supported_commands(session: dict[str, Any] | None) -> set[str]:
    if not session:
        return set()
    caps = session.get("Capabilities") or {}
    return set(session.get("SupportedCommands") or caps.get("SupportedCommands") or [])


class JellyfinDeviceEntity(Entity):
    """Base for entities bound to one Jellyfin client device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: JellyfinConfigEntry,
        runtime: JellyfinRuntime,
        device_id: str,
        device_name: str,
        client: str | None,
    ) -> None:
        self._entry = entry
        self._runtime = runtime
        self.device_id = device_id
        self._attr_unique_id = client_unique_id(runtime.server_id, device_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=device_name,
            manufacturer="Jellyfin",
            model=client,
            via_device=(DOMAIN, runtime.server_id),
        )

    @property
    def session(self) -> dict[str, Any] | None:
        """Current session of this device, None if the app isn't connected."""
        return self._runtime.session_for(self.device_id)

    @property
    def available(self) -> bool:
        # Server reachable = available. A closed app is "off", not unavailable.
        return self._runtime.connected

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_SESSIONS.format(self._entry.entry_id), self._on_sessions
            )
        )
        # Entities are added before the WebSocket connects: follow availability
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CONNECTION.format(self._entry.entry_id),
                self._on_connection,
            )
        )

    @callback
    def _on_connection(self, _connected: bool) -> None:
        self.async_write_ha_state()

    @callback
    def _on_sessions(self) -> None:
        self.async_write_ha_state()


@callback
def async_setup_device_entities(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    platform: str,
    wanted: Callable[[dict[str, Any]], bool],
    factory: Callable[[str, str, str | None], JellyfinDeviceEntity],
) -> None:
    """Restore known devices, then add entities for new matching clients."""
    runtime = entry.runtime_data
    known: set[str] = set()
    prefix = f"{runtime.server_id}_"
    dev_reg = dr.async_get(hass)

    restored = []
    for reg_entry in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if reg_entry.domain != platform or not reg_entry.unique_id.startswith(prefix):
            continue
        device_id = reg_entry.unique_id[len(prefix) :]
        device = dev_reg.async_get_device(identifiers={(DOMAIN, reg_entry.unique_id)})
        known.add(device_id)
        restored.append(
            factory(
                device_id,
                device.name if device else device_id,
                device.model if device else None,
            )
        )
    if restored:
        async_add_entities(restored)

    @callback
    def _on_sessions() -> None:
        new = []
        for device_id, session in runtime.sessions.items():
            if device_id in known or not wanted(session):
                continue
            known.add(device_id)
            new.append(
                factory(
                    device_id,
                    session.get("DeviceName") or device_id,
                    session.get("Client"),
                )
            )
        if new:
            async_add_entities(new)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_SESSIONS.format(entry.entry_id), _on_sessions)
    )
    _on_sessions()
