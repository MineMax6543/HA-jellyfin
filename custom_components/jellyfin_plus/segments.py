"""Intro / credits / recap / preview / commercial events from Jellyfin media segments.

Jellyfin knows where segments start and end (filled by a segment provider
plugin such as Intro Skipper). Clients only report their position every few
seconds, so besides checking on every update we also set a timer for the next
segment boundary, extrapolating the position while playing.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import time
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .api import JellyfinClient, JellyfinError
from .const import SEGMENT_NAMES, TICKS_PER_SECOND
from .sessions import PlaybackEvent, describe

_LOGGER = logging.getLogger(__name__)

CACHE_SIZE = 50
TIMER_SLACK = 0.25  # seconds past the boundary, so we're clearly inside/outside


@dataclass(frozen=True)
class Segment:
    name: str  # intro, credits, ...
    start: float  # seconds
    end: float


@dataclass
class _DeviceState:
    item_id: str
    position: float = 0.0  # seconds, as last reported
    reported_at: float = field(default_factory=time.monotonic)
    paused: bool = False
    active: set[Segment] = field(default_factory=set)
    timer: CALLBACK_TYPE | None = None

    def position_now(self) -> float:
        if self.paused:
            return self.position
        return self.position + (time.monotonic() - self.reported_at)


def parse_segments(raw: list[dict[str, Any]]) -> list[Segment]:
    result = []
    for seg in raw:
        name = SEGMENT_NAMES.get(seg.get("Type"))
        start, end = seg.get("StartTicks"), seg.get("EndTicks")
        if name and isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            result.append(Segment(name, start / TICKS_PER_SECOND, end / TICKS_PER_SECOND))
    return result


class SegmentWatcher:
    """Turns playback position + segments into <segment>_started/_ended events."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: JellyfinClient,
        emit: Callable[[PlaybackEvent], None],
    ) -> None:
        self._hass = hass
        self._client = client
        self._emit = emit
        self._cache: OrderedDict[str, list[Segment]] = OrderedDict()
        self._loading: set[str] = set()
        self._devices: dict[str, _DeviceState] = {}
        self._sessions: dict[str, dict[str, Any]] = {}

    @callback
    def update(self, sessions_by_device: dict[str, dict[str, Any]]) -> None:
        """Call with the latest session per device after every push."""
        self._sessions = sessions_by_device
        for device_id in list(self._devices):
            item = (sessions_by_device.get(device_id) or {}).get("NowPlayingItem")
            if not item or item.get("Id") != self._devices[device_id].item_id:
                self._forget(device_id)

        for device_id, session in sessions_by_device.items():
            item = session.get("NowPlayingItem") or {}
            item_id = item.get("Id")
            if not item_id:
                continue
            play_state = session.get("PlayState") or {}
            ticks = play_state.get("PositionTicks")
            state = self._devices.get(device_id)
            if state is None:
                state = self._devices[device_id] = _DeviceState(item_id)
            if isinstance(ticks, (int, float)):
                state.position = ticks / TICKS_PER_SECOND
                state.reported_at = time.monotonic()
            state.paused = bool(play_state.get("IsPaused"))
            if item_id in self._cache:
                self._cache.move_to_end(item_id)
                self._evaluate(device_id)
            elif item_id not in self._loading:
                self._loading.add(item_id)
                self._hass.async_create_background_task(
                    self._load(item_id), f"jellyfin_plus segments {item_id}"
                )

    async def _load(self, item_id: str) -> None:
        try:
            segments = parse_segments(await self._client.media_segments(item_id))
        except JellyfinError as err:
            _LOGGER.debug("Could not load segments for %s: %s", item_id, err)
            segments = []
        finally:
            self._loading.discard(item_id)
        self._cache[item_id] = segments
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        for device_id, state in list(self._devices.items()):
            if state.item_id == item_id:
                self._evaluate(device_id)

    @callback
    def _evaluate(self, device_id: str) -> None:
        state = self._devices.get(device_id)
        session = self._sessions.get(device_id)
        if state is None or session is None:
            return
        if state.timer:
            state.timer()
            state.timer = None
        segments = self._cache.get(state.item_id) or []
        if not segments:
            return
        position = state.position_now()
        inside = {s for s in segments if s.start <= position < s.end}
        data = describe(session)
        for seg in sorted(state.active - inside, key=lambda s: s.start):
            self._emit(PlaybackEvent(f"{seg.name}_ended", device_id, self._data(data, seg)))
        for seg in sorted(inside - state.active, key=lambda s: s.start):
            self._emit(PlaybackEvent(f"{seg.name}_started", device_id, self._data(data, seg)))
        state.active = inside

        if state.paused:
            return
        upcoming = [
            edge - position
            for seg in segments
            for edge in (seg.start, seg.end)
            if edge > position
        ]
        if upcoming:

            @callback
            def _on_timer(_now: Any) -> None:
                state.timer = None
                self._evaluate(device_id)

            state.timer = async_call_later(
                self._hass, min(upcoming) + TIMER_SLACK, _on_timer
            )

    @staticmethod
    def _data(base: dict[str, Any], seg: Segment) -> dict[str, Any]:
        return {
            **base,
            "segment": seg.name,
            "segment_start": round(seg.start),
            "segment_end": round(seg.end),
        }

    @callback
    def _forget(self, device_id: str) -> None:
        state = self._devices.pop(device_id, None)
        if state and state.timer:
            state.timer()

    @callback
    def shutdown(self) -> None:
        for device_id in list(self._devices):
            self._forget(device_id)
