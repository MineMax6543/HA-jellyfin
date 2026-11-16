"""Sleep timer for a media player: pause or stop after N minutes or at the end of the item."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import logging

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
import homeassistant.util.dt as dt_util

_LOGGER = logging.getLogger(__name__)

MODE_MINUTES = "minutes"
MODE_END_OF_ITEM = "end_of_item"
# Fire this long before the item's end, so the app doesn't start the next one
END_MARGIN_SECONDS = 2


class SleepTimer:
    """One timer per media player.

    *Minutes* is a plain countdown. *End of item* follows playback: it is
    re-aimed at every position update, waits while paused, and also fires
    when the item changes (the app moved on before we caught the end).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        fire: Callable[[str], Awaitable[None]],
        on_change: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._fire = fire
        self._on_change = on_change
        self.mode: str | None = None
        self.action = "pause"
        self.ends_at: datetime | None = None
        self._item_id: str | None = None
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def active(self) -> bool:
        return self.mode is not None

    def attributes(self) -> dict[str, str | None]:
        if not self.active:
            return {}
        return {
            "sleep_timer": self.mode,
            "sleep_timer_action": self.action,
            "sleep_timer_ends": self.ends_at.isoformat() if self.ends_at else None,
        }

    @callback
    def start_minutes(self, minutes: float, action: str) -> None:
        self.cancel(notify=False)
        self.mode, self.action = MODE_MINUTES, action
        self._schedule(minutes * 60)
        self._on_change()

    @callback
    def start_end_of_item(
        self, item_id: str, remaining: float | None, playing: bool, action: str
    ) -> None:
        self.cancel(notify=False)
        self.mode, self.action, self._item_id = MODE_END_OF_ITEM, action, item_id
        self.update(item_id, remaining, playing)

    @callback
    def update(self, item_id: str | None, remaining: float | None, playing: bool) -> None:
        """Called on every session update of the player."""
        if self.mode != MODE_END_OF_ITEM:
            return
        if item_id != self._item_id:
            # Next episode started (or playback ended): act now
            self._trigger()
            return
        if not playing or remaining is None:
            self._unschedule()
            self.ends_at = None
        else:
            self._schedule(max(remaining - END_MARGIN_SECONDS, 0))
        self._on_change()

    @callback
    def cancel(self, notify: bool = True) -> None:
        self._unschedule()
        self.mode = None
        self.ends_at = None
        self._item_id = None
        if notify:
            self._on_change()

    def _schedule(self, seconds: float) -> None:
        self._unschedule()
        self.ends_at = dt_util.utcnow() + timedelta(seconds=seconds)
        self._unsub = async_call_later(self._hass, seconds, self._elapsed)

    def _unschedule(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _elapsed(self, _now: datetime) -> None:
        self._unsub = None
        self._trigger()

    @callback
    def _trigger(self) -> None:
        action = self.action
        self.cancel()
        _LOGGER.debug("Sleep timer elapsed: %s", action)
        self._hass.async_create_task(self._fire(action), "jellyfin_plus sleep timer")
