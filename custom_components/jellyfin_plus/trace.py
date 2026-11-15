"""In-memory trace of what Jellyfin Plus received and sent.

Always on and cheap: a ring buffer of compact entries that ends up in the
"Download diagnostics" file, so a problem can be inspected right after it
happened without having enabled debug logging beforehand.
"""

from __future__ import annotations

from collections import deque
from typing import Any

import homeassistant.util.dt as dt_util

from .const import TICKS_PER_SECOND

TRACE_SIZE = 300


class Trace:
    """Ring buffer of timestamped entries."""

    def __init__(self, size: int = TRACE_SIZE) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=size)

    def add(self, kind: str, **data: Any) -> None:
        self._entries.append(
            {"time": dt_util.utcnow().isoformat(timespec="milliseconds"), "kind": kind, **data}
        )

    def as_list(self) -> list[dict[str, Any]]:
        return list(self._entries)


def session_summary(session: dict[str, Any]) -> dict[str, Any]:
    """The parts of a session that matter for playback debugging."""
    item = session.get("NowPlayingItem") or {}
    play_state = session.get("PlayState") or {}
    ticks = play_state.get("PositionTicks")
    return {
        "device": session.get("DeviceName"),
        "client": session.get("Client"),
        "session": session.get("Id"),
        "item": item.get("Name"),
        "paused": play_state.get("IsPaused"),
        "position": round(ticks / TICKS_PER_SECOND, 2) if isinstance(ticks, (int, float)) else None,
        "method": play_state.get("PlayMethod"),
        "last_checkin": session.get("LastPlaybackCheckIn"),
    }
