"""Jellyfin Plus: a push-based, device-stable Jellyfin integration."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    JellyfinClient,
    JellyfinError,
    JellyfinListener,
    async_get_system_info,
)
from .const import (
    ACTION_ITEM_ADDED,
    ANNOUNCED_ITEM_TYPES,
    CONF_DEVICE_ID,
    CONF_IS_ADMIN,
    CONF_TOKEN,
    CONF_USER_ID,
    DOMAIN,
    EVENT_ACTIVITY,
    EVENT_LIBRARY,
    EVENT_PLAYBACK,
    SIGNAL_ACTIVITY,
    SIGNAL_CONNECTION,
    SIGNAL_LIBRARY,
    SIGNAL_PLAYBACK,
    SIGNAL_SESSIONS,
    SIGNAL_TASKS,
)
from .coordinator import JellyfinCoordinator
from .segments import SegmentWatcher
from .sessions import PlaybackEvent, SessionTracker

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
    Platform.SENSOR,
    Platform.UPDATE,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
MAX_ANNOUNCED_PER_BATCH = 50


@dataclass
class JellyfinRuntime:
    """Everything a loaded entry needs."""

    server_id: str
    server_name: str
    server_version: str | None
    is_admin: bool
    tracker: SessionTracker
    client: JellyfinClient
    coordinator: JellyfinCoordinator
    listener: JellyfinListener | None = None
    segments: SegmentWatcher | None = None
    # Latest session per client device (device_id -> Jellyfin session dict)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Scheduled tasks as pushed by the server (admin only)
    tasks: list[dict[str, Any]] = field(default_factory=list)

    def session_for(self, device_id: str) -> dict[str, Any] | None:
        return self.sessions.get(device_id)

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
    is_admin = bool(entry.data.get(CONF_IS_ADMIN))

    try:
        info = await async_get_system_info(session, url, token, device_id)
    except InvalidAuth as err:
        raise ConfigEntryAuthFailed("Jellyfin rejected the access token") from err
    except CannotConnect as err:
        raise ConfigEntryNotReady(f"Cannot reach Jellyfin at {url}: {err}") from err

    client = JellyfinClient(session, url, token, device_id, entry.data.get(CONF_USER_ID))
    coordinator = JellyfinCoordinator(hass, entry, client, is_admin)
    runtime = JellyfinRuntime(
        server_id=info.get("Id") or entry.unique_id or entry.entry_id,
        server_name=info.get("ServerName") or "Jellyfin",
        server_version=info.get("Version"),
        is_admin=is_admin,
        tracker=SessionTracker(own_device_id=device_id),
        client=client,
        coordinator=coordinator,
    )
    entry.runtime_data = runtime
    await coordinator.async_config_entry_first_refresh()

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, runtime.server_id)},
        name=runtime.server_name,
        manufacturer="Jellyfin",
        model="Jellyfin server",
        sw_version=runtime.server_version,
        configuration_url=url,
    )

    @callback
    def _emit(event: PlaybackEvent) -> None:
        _handle_playback_event(hass, entry, runtime, event)

    runtime.segments = SegmentWatcher(hass, client, _emit)
    entry.async_on_unload(runtime.segments.shutdown)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _on_sessions(sessions: list[dict[str, Any]]) -> None:
        runtime.sessions = _latest_by_device(sessions, device_id)
        async_dispatcher_send(hass, SIGNAL_SESSIONS.format(entry.entry_id))
        for event in runtime.tracker.update(sessions):
            _emit(event)
        runtime.segments.update(runtime.sessions)

    @callback
    def _on_message(kind: str, data: Any) -> None:
        if kind == "LibraryChanged" and isinstance(data, dict):
            added = [i for i in data.get("ItemsAdded") or [] if isinstance(i, str)]
            if added:
                entry.async_create_background_task(
                    hass, _announce_items(hass, entry, runtime, added), "jellyfin_plus library"
                )
        elif kind == "ActivityLogEntry":
            entries = data if isinstance(data, list) else [data]
            for item in entries:
                if isinstance(item, dict):
                    _handle_activity(hass, entry, runtime, item)
        elif kind == "ScheduledTasksInfo" and isinstance(data, list):
            runtime.tasks = data
            async_dispatcher_send(hass, SIGNAL_TASKS.format(entry.entry_id))

    async def _on_connected() -> None:
        if is_admin:
            runtime.tasks = await client.scheduled_tasks()
            async_dispatcher_send(hass, SIGNAL_TASKS.format(entry.entry_id))

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
        on_message=_on_message,
        # Tasks and the activity log are only sent to administrators
        subscriptions=["ScheduledTasksInfoStart", "ActivityLogEntryStart"] if is_admin else [],
        on_connected=_on_connected,
    )
    runtime.listener.start()
    return True


def _latest_by_device(
    sessions: list[dict[str, Any]], own_device_id: str
) -> dict[str, dict[str, Any]]:
    """One session per device: the most recently active one."""
    result: dict[str, dict[str, Any]] = {}
    for session in sessions:
        device_id = session.get("DeviceId")
        if not device_id or device_id == own_device_id:
            continue
        current = result.get(device_id)
        if current is None or (session.get("LastActivityDate") or "") >= (
            current.get("LastActivityDate") or ""
        ):
            result[device_id] = session
    return result


@callback
def _handle_playback_event(
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


def library_item_data(runtime: JellyfinRuntime, item: dict[str, Any]) -> dict[str, Any]:
    """Event data for a newly added library item."""
    tag = (item.get("ImageTags") or {}).get("Primary")
    image_id = item.get("Id")
    if not tag and item.get("SeriesId") and item.get("SeriesPrimaryImageTag"):
        image_id, tag = item["SeriesId"], item["SeriesPrimaryImageTag"]
    return {
        "item_id": item.get("Id"),
        "title": item.get("Name"),
        "item_type": item.get("Type"),
        "series_name": item.get("SeriesName"),
        "season_number": item.get("ParentIndexNumber"),
        "episode_number": item.get("IndexNumber"),
        "year": item.get("ProductionYear"),
        "image_url": runtime.client.image_url(image_id, tag) if tag else None,
    }


async def _announce_items(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    runtime: JellyfinRuntime,
    item_ids: list[str],
) -> None:
    """Look up newly added items and fire one event per announceable item."""
    try:
        items = await runtime.client.items(
            ids=",".join(item_ids[:MAX_ANNOUNCED_PER_BATCH]),
            fields="PremiereDate",
        )
    except JellyfinError as err:
        _LOGGER.debug("Could not look up added items: %s", err)
        return
    for item in items:
        if item.get("Type") not in ANNOUNCED_ITEM_TYPES:
            continue
        data = library_item_data(runtime, item)
        hass.bus.async_fire(
            EVENT_LIBRARY,
            {"action": ACTION_ITEM_ADDED, "server_name": runtime.server_name, **data},
        )
        async_dispatcher_send(hass, SIGNAL_LIBRARY.format(entry.entry_id), data)


@callback
def _handle_activity(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    runtime: JellyfinRuntime,
    item: dict[str, Any],
) -> None:
    data = {
        "type": item.get("Type"),
        "name": item.get("Name"),
        "overview": item.get("ShortOverview") or item.get("Overview"),
        "severity": item.get("Severity"),
        "user_id": item.get("UserId"),
        "item_id": item.get("ItemId"),
        "date": item.get("Date"),
    }
    hass.bus.async_fire(EVENT_ACTIVITY, {"server_name": runtime.server_name, **data})
    async_dispatcher_send(hass, SIGNAL_ACTIVITY.format(entry.entry_id), data)


async def async_unload_entry(hass: HomeAssistant, entry: JellyfinConfigEntry) -> bool:
    """Stop listening."""
    runtime: JellyfinRuntime | None = getattr(entry, "runtime_data", None)
    if runtime is None:  # setup never completed
        return True
    if runtime.listener:
        await runtime.listener.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
