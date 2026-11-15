"""Event entities: per client, all clients, library additions and activity log."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ACTION_ITEM_ADDED,
    ACTIONS,
    ACTIVITY_IGNORED_ON_ENTITY,
    ACTIVITY_OTHER,
    ACTIVITY_TYPES,
    DOMAIN,
    SIGNAL_ACTIVITY,
    SIGNAL_CONNECTION,
    SIGNAL_LIBRARY,
    SIGNAL_PLAYBACK,
)
from .entity import client_device_info, client_unique_id, find_device
from .sessions import PlaybackEvent

if TYPE_CHECKING:
    from . import JellyfinConfigEntry, JellyfinRuntime


# Suffixes of server-level event entities; everything else is a client
SERVER_EVENT_KEYS = {"all_clients", "library", "activity"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the server entity, restore known clients, add new ones on the fly."""
    runtime = entry.runtime_data
    server_id = runtime.server_id
    clients: dict[str, JellyfinClientEvent] = {}
    entities: list[EventEntity] = [
        JellyfinAllClientsEvent(entry, runtime),
        JellyfinLibraryEvent(entry, runtime),
    ]
    if runtime.is_admin:
        entities.append(JellyfinActivityEvent(entry, runtime))

    # Restore clients seen before, so their entities exist right after a restart
    prefix = f"{server_id}_"
    for reg_entry in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if (
            reg_entry.domain != "event"
            or not reg_entry.unique_id.startswith(prefix)
            or reg_entry.unique_id[len(prefix) :] in SERVER_EVENT_KEYS
        ):
            continue
        device_id = reg_entry.unique_id[len(prefix) :]
        device = find_device(hass, entry, reg_entry.unique_id)
        clients[device_id] = JellyfinClientEvent(
            entry,
            runtime,
            device_id,
            device.name if device else device_id,
            device.model if device else None,
        )
    entities.extend(clients.values())
    async_add_entities(entities)

    @callback
    def _on_playback(event: PlaybackEvent) -> None:
        if event.device_id in clients:
            clients[event.device_id].handle(event)
            return
        # First event from a new client: create its entity and let it fire
        # this event once it has been added.
        entity = JellyfinClientEvent(
            entry,
            runtime,
            event.device_id,
            event.data.get("device_name") or event.device_id,
            event.data.get("client"),
            pending=event,
        )
        clients[event.device_id] = entity
        async_add_entities([entity])

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_PLAYBACK.format(entry.entry_id), _on_playback)
    )


class _JellyfinEventBase(EventEntity):
    """Shared behaviour: availability follows the WebSocket connection."""

    _attr_has_entity_name = True
    _attr_event_types = ACTIONS

    def __init__(self, entry: JellyfinConfigEntry, runtime: JellyfinRuntime) -> None:
        self._entry = entry
        self._runtime = runtime
        self._queued: list[PlaybackEvent] = []

    @property
    def available(self) -> bool:
        return self._runtime.connected

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CONNECTION.format(self._entry.entry_id),
                self._on_connection,
            )
        )
        queued, self._queued = self._queued, []
        for event in queued:
            self.handle(event)

    @callback
    def _on_connection(self, _connected: bool) -> None:
        self.async_write_ha_state()

    @callback
    def handle(self, event: PlaybackEvent) -> None:
        if self.hass is None:  # not added yet: keep it for async_added_to_hass
            self._queued.append(event)
            return
        self._trigger_event(event.action, dict(event.data))
        self.async_write_ha_state()


class JellyfinAllClientsEvent(_JellyfinEventBase):
    """Fires for playback on any client of the server."""

    _attr_translation_key = "all_clients"

    def __init__(self, entry: JellyfinConfigEntry, runtime: JellyfinRuntime) -> None:
        super().__init__(entry, runtime)
        self._attr_unique_id = f"{runtime.server_id}_all_clients"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, runtime.server_id)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_PLAYBACK.format(self._entry.entry_id), self.handle
            )
        )


class JellyfinClientEvent(_JellyfinEventBase):
    """Fires for playback on one client device (e.g. the Apple TV)."""

    _attr_translation_key = "playback"

    def __init__(
        self,
        entry: JellyfinConfigEntry,
        runtime: JellyfinRuntime,
        device_id: str,
        device_name: str,
        client: str | None,
        pending: PlaybackEvent | None = None,
    ) -> None:
        super().__init__(entry, runtime)
        self.device_id = device_id
        if pending is not None:
            self._queued.append(pending)
        unique_id = client_unique_id(runtime.server_id, device_id)
        self._attr_unique_id = unique_id
        self._attr_device_info = client_device_info(runtime, unique_id, device_name, client)


class _ServerEvent(_JellyfinEventBase):
    """Event entity on the server device fed by a dispatcher signal."""

    _signal: str

    def __init__(self, entry: JellyfinConfigEntry, runtime: JellyfinRuntime, key: str) -> None:
        super().__init__(entry, runtime)
        self._attr_unique_id = f"{runtime.server_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, runtime.server_id)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._signal.format(self._entry.entry_id), self._on_data
            )
        )

    @callback
    def _on_data(self, data: dict[str, Any]) -> None:
        raise NotImplementedError


class JellyfinLibraryEvent(_ServerEvent):
    """Fires when a movie, episode, series or album is added to the library."""

    _attr_translation_key = "library"
    _attr_event_types = [ACTION_ITEM_ADDED]
    _signal = SIGNAL_LIBRARY

    def __init__(self, entry: JellyfinConfigEntry, runtime: JellyfinRuntime) -> None:
        super().__init__(entry, runtime, "library")

    @callback
    def _on_data(self, data: dict[str, Any]) -> None:
        self._trigger_event(ACTION_ITEM_ADDED, dict(data))
        self.async_write_ha_state()


class JellyfinActivityEvent(_ServerEvent):
    """Fires for Jellyfin activity log entries (failed logins, plugin updates, …)."""

    _attr_translation_key = "activity"
    _attr_event_types = sorted({*ACTIVITY_TYPES.values(), ACTIVITY_OTHER})
    _signal = SIGNAL_ACTIVITY

    def __init__(self, entry: JellyfinConfigEntry, runtime: JellyfinRuntime) -> None:
        super().__init__(entry, runtime, "activity")

    @callback
    def _on_data(self, data: dict[str, Any]) -> None:
        if data.get("type") in ACTIVITY_IGNORED_ON_ENTITY:
            return
        event_type = ACTIVITY_TYPES.get(data.get("type") or "", ACTIVITY_OTHER)
        self._trigger_event(event_type, dict(data))
        self.async_write_ha_state()
