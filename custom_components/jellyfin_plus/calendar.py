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

if TYPE_CHECKING:
    from . import JellyfinConfigEntry, JellyfinRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([JellyfinUpcomingCalendar(entry.runtime_data)])


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

    def _events(self) -> list[CalendarEvent]:
        items = (self.coordinator.data or {}).get("upcoming") or []
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
