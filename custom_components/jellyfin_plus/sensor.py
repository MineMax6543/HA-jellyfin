"""Server sensors: streams, transcodes, bandwidth, tasks, next up, storage."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfDataRate, UnitOfInformation
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SIGNAL_CONNECTION, SIGNAL_SESSIONS, SIGNAL_TASKS

if TYPE_CHECKING:
    from . import JellyfinConfigEntry, JellyfinRuntime


# ---- helpers on the session list ------------------------------------------------
def _playing(runtime: JellyfinRuntime) -> list[dict[str, Any]]:
    return [s for s in runtime.sessions.values() if s.get("NowPlayingItem")]


def _is_transcoding(session: dict[str, Any]) -> bool:
    return bool(session.get("TranscodingInfo")) or (
        (session.get("PlayState") or {}).get("PlayMethod") == "Transcode"
    )


def _bitrate(session: dict[str, Any]) -> int:
    """Best effort: transcode bitrate, else the source streams' bitrates."""
    transcoding = session.get("TranscodingInfo") or {}
    if isinstance(transcoding.get("Bitrate"), int):
        return transcoding["Bitrate"]
    item = session.get("NowPlayingItem") or {}
    return sum(
        s.get("BitRate") or 0
        for s in item.get("MediaStreams") or []
        if s.get("Type") in ("Video", "Audio") and isinstance(s.get("BitRate"), int)
    )


def _stream_list(runtime: JellyfinRuntime) -> list[dict[str, Any]]:
    return [
        {
            "device": s.get("DeviceName"),
            "user": s.get("UserName"),
            "title": (s.get("NowPlayingItem") or {}).get("Name"),
            "transcoding": _is_transcoding(s),
        }
        for s in _playing(runtime)
    ]


@dataclass(frozen=True, kw_only=True)
class PushSensorDescription(SensorEntityDescription):
    value_fn: Callable[[JellyfinRuntime], Any]
    attrs_fn: Callable[[JellyfinRuntime], dict[str, Any]] | None = None
    signal: str = SIGNAL_SESSIONS
    admin_only: bool = False


PUSH_SENSORS: tuple[PushSensorDescription, ...] = (
    PushSensorDescription(
        key="active_streams",
        translation_key="active_streams",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda rt: len(_playing(rt)),
        attrs_fn=lambda rt: {"streams": _stream_list(rt)},
    ),
    PushSensorDescription(
        key="transcoding_streams",
        translation_key="transcoding_streams",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda rt: sum(1 for s in _playing(rt) if _is_transcoding(s)),
    ),
    PushSensorDescription(
        key="connected_clients",
        translation_key="connected_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda rt: len(rt.sessions),
        attrs_fn=lambda rt: {
            "clients": sorted(s.get("DeviceName") or "?" for s in rt.sessions.values())
        },
    ),
    PushSensorDescription(
        key="streaming_bandwidth",
        translation_key="streaming_bandwidth",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.BITS_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        suggested_display_precision=1,
        value_fn=lambda rt: sum(_bitrate(s) for s in _playing(rt)),
    ),
    PushSensorDescription(
        key="running_tasks",
        translation_key="running_tasks",
        state_class=SensorStateClass.MEASUREMENT,
        signal=SIGNAL_TASKS,
        admin_only=True,
        value_fn=lambda rt: sum(1 for t in rt.tasks if t.get("State") == "Running"),
        attrs_fn=lambda rt: {
            "tasks": [
                {"name": t.get("Name"), "progress": round(t.get("CurrentProgressPercentage") or 0)}
                for t in rt.tasks
                if t.get("State") == "Running"
            ]
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        JellyfinPushSensor(entry, runtime, desc)
        for desc in PUSH_SENSORS
        if runtime.is_admin or not desc.admin_only
    ]
    entities.append(JellyfinNextUpSensor(entry, runtime))
    storage = (runtime.coordinator.data or {}).get("storage") or {}
    if runtime.is_admin:
        if storage.get("ProgramDataFolder"):
            entities.append(JellyfinStorageSensor(entry, runtime, "server_data", None))
        for library in storage.get("Libraries") or []:
            if library.get("Id") and library.get("Folders"):
                entities.append(
                    JellyfinStorageSensor(entry, runtime, library["Id"], library.get("Name"))
                )
    async_add_entities(entities)


def _server_device(runtime: JellyfinRuntime) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, runtime.server_id)})


class JellyfinPushSensor(SensorEntity):
    """Sensor computed from pushed data (sessions or tasks)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: PushSensorDescription

    def __init__(
        self, entry: JellyfinConfigEntry, runtime: JellyfinRuntime, desc: PushSensorDescription
    ) -> None:
        self.entity_description = desc
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.server_id}_{desc.key}"
        self._attr_device_info = _server_device(runtime)

    @property
    def available(self) -> bool:
        return self._runtime.connected

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._runtime)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self._runtime) if fn else None

    async def async_added_to_hass(self) -> None:
        for signal in (self.entity_description.signal, SIGNAL_CONNECTION):
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, signal.format(self._entry.entry_id), self._refresh
                )
            )

    @callback
    def _refresh(self, *_args: Any) -> None:
        self.async_write_ha_state()


class _CoordinatorSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, runtime: JellyfinRuntime, key: str) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.server_id}_{key}"
        self._attr_device_info = _server_device(runtime)


class JellyfinNextUpSensor(_CoordinatorSensor):
    """Number of episodes in 'Next up', with the list as attribute."""

    _attr_translation_key = "next_up"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: JellyfinConfigEntry, runtime: JellyfinRuntime) -> None:
        super().__init__(runtime, "next_up")

    @property
    def _items(self) -> list[dict[str, Any]]:
        return (self.coordinator.data or {}).get("next_up") or []

    @property
    def native_value(self) -> int:
        return len(self._items)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "episodes": [
                {
                    "series": item.get("SeriesName"),
                    "season": item.get("ParentIndexNumber"),
                    "episode": item.get("IndexNumber"),
                    "title": item.get("Name"),
                    "item_id": item.get("Id"),
                }
                for item in self._items[:20]
            ]
        }


class JellyfinStorageSensor(_CoordinatorSensor):
    """Free space where a library (or Jellyfin's own data) lives."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        entry: JellyfinConfigEntry,
        runtime: JellyfinRuntime,
        library_id: str,
        library_name: str | None,
    ) -> None:
        super().__init__(runtime, f"storage_{library_id}")
        self._library_id = library_id
        if library_name is None:
            self._attr_translation_key = "server_data_free"
        else:
            self._attr_translation_key = "library_free"
            self._attr_translation_placeholders = {"library": library_name}

    @property
    def _folder(self) -> dict[str, Any] | None:
        storage = (self.coordinator.data or {}).get("storage") or {}
        if self._library_id == "server_data":
            return storage.get("ProgramDataFolder")
        for library in storage.get("Libraries") or []:
            if library.get("Id") == self._library_id:
                folders = library.get("Folders") or []
                return folders[0] if folders else None
        return None

    @property
    def available(self) -> bool:
        return super().available and self._folder is not None

    @property
    def native_value(self) -> int | None:
        free = (self._folder or {}).get("FreeSpace")
        return free if isinstance(free, int) and free >= 0 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        folder = self._folder or {}
        free, used = folder.get("FreeSpace"), folder.get("UsedSpace")
        attrs: dict[str, Any] = {"path": folder.get("Path")}
        if isinstance(free, int) and isinstance(used, int) and free + used > 0:
            attrs["used_percent"] = round(used / (free + used) * 100, 1)
        return attrs
