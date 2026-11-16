"""Calendar with upcoming episodes of the shows in your library."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .person import linked_people, person_data, person_device_info, person_unique_id

if TYPE_CHECKING:
    from . import JellyfinConfigEntry, JellyfinRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[CalendarEntity] = [JellyfinUpcomingCalendar(runtime)]
    entities.extend(
        JellyfinPersonUpcomingCalendar(runtime, user_id, name)
        for user_id, name in linked_people(entry).items()
    )
    async_add_entities(entities)


def episode_event(item: dict[str, Any]) -> CalendarEvent | None:
    """All-day event on the episode's air date."""
    premiere = dt_util.parse_datetime(item.get("PremiereDate") or "")
    if premiere is None:
        return None
    day = dt_util.as_local(premiere).date()
    season, episode = item.get("ParentIndexNumber"), item.get("IndexNumber")
    code = (
        f" S{season:02d}E{episode:02d}"
        if isinstance(season, int) and isinstance(episode, int)
        else ""
    )
    series = item.get("SeriesName") or ""
    title = item.get("Name") or ""
    summary = f"{series}{code}" + (f" – {title}" if title else "")
    return CalendarEvent(
        start=day,
        end=day + timedelta(days=1),
        summary=summary.strip() or "Episode",
        description=item.get("Overview"),
        uid=item.get("Id"),
    )


class JellyfinUpcomingCalendar(CoordinatorEntity, CalendarEntity):
    """Upcoming episodes (Jellyfin's 'Upcoming' list)."""

    _attr_has_entity_name = True
    _attr_translation_key = "upcoming"

    def __init__(self, runtime: JellyfinRuntime) -> None:
        super().__init__(runtime.coordinator)
        self._attr_unique_id = f"{runtime.server_id}_upcoming"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, runtime.server_id)})

    def _items(self) -> list[dict[str, Any]]:
        return (self.coordinator.data or {}).get("upcoming") or []

    def _events(self) -> list[CalendarEvent]:
        items = self._items()
        events = [e for e in (episode_event(i) for i in items) if e is not None]
        return sorted(events, key=lambda e: e.start)

    @property
    def event(self) -> CalendarEvent | None:
        today = dt_util.now().date()
        return next((e for e in self._events() if e.end > today), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        start: date = dt_util.as_local(start_date).date()
        end: date = dt_util.as_local(end_date).date()
        return [e for e in self._events() if e.end > start and e.start <= end]


class JellyfinPersonUpcomingCalendar(JellyfinUpcomingCalendar):
    """Upcoming episodes for one linked person."""

    def __init__(self, runtime: JellyfinRuntime, user_id: str, name: str) -> None:
        super().__init__(runtime)
        self._runtime = runtime
        self._user_id = user_id
        self._attr_unique_id = person_unique_id(runtime, user_id, "upcoming")
        self._attr_device_info = person_device_info(runtime, user_id, name)

    def _items(self) -> list[dict[str, Any]]:
        return person_data(self._runtime, self._user_id).get("upcoming") or []
