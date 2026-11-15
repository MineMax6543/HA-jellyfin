"""Turn Jellyfin session snapshots into playback events.

Jellyfin pushes the full session list whenever something changes. This module
keeps the previous state per client device and diffs it against the new one.
It has no Home Assistant dependencies so it can be tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    ACTION_PAUSED,
    ACTION_RESUMED,
    ACTION_STARTED,
    ACTION_STOPPED,
    TICKS_PER_SECOND,
)


@dataclass(frozen=True)
class PlaybackEvent:
    """One playback change on one client."""

    action: str
    device_id: str
    data: dict[str, Any]


@dataclass(frozen=True)
class _Snapshot:
    item_id: str
    paused: bool
    data: dict[str, Any]


def _seconds(ticks: Any) -> int | None:
    if isinstance(ticks, (int, float)):
        return int(ticks // TICKS_PER_SECOND)
    return None


def describe(session: dict[str, Any]) -> dict[str, Any]:
    """Flatten the interesting parts of a playing session for event data."""
    item = session.get("NowPlayingItem") or {}
    play_state = session.get("PlayState") or {}
    transcoding = session.get("TranscodingInfo") or {}
    return {
        "device_id": session.get("DeviceId"),
        "device_name": session.get("DeviceName"),
        "client": session.get("Client"),
        "user_id": session.get("UserId"),
        "user_name": session.get("UserName"),
        "item_id": item.get("Id"),
        "title": item.get("Name"),
        "item_type": item.get("Type"),
        "media_type": item.get("MediaType"),
        "series_name": item.get("SeriesName"),
        "season_number": item.get("ParentIndexNumber"),
        "episode_number": item.get("IndexNumber"),
        "year": item.get("ProductionYear"),
        "runtime": _seconds(item.get("RunTimeTicks")),
        "position": _seconds(play_state.get("PositionTicks")),
        "is_paused": bool(play_state.get("IsPaused")),
        "play_method": play_state.get("PlayMethod"),
        "is_transcoding": bool(transcoding) or play_state.get("PlayMethod") == "Transcode",
    }


class SessionTracker:
    """Remembers what each client was playing and reports changes."""

    def __init__(self, own_device_id: str | None = None) -> None:
        self._own_device_id = own_device_id
        self._playing: dict[str, _Snapshot] = {}
        self.devices: dict[str, dict[str, Any]] = {}  # device_id -> name/client

    @property
    def playing(self) -> dict[str, dict[str, Any]]:
        """Current playback per device (event data of the last known state)."""
        return {device_id: snap.data for device_id, snap in self._playing.items()}

    def update(self, sessions: list[dict[str, Any]]) -> list[PlaybackEvent]:
        """Apply a full session list; return the changes since the last one."""
        events: list[PlaybackEvent] = []
        current: dict[str, _Snapshot] = {}

        for session in sessions:
            device_id = session.get("DeviceId")
            if not device_id or device_id == self._own_device_id:
                continue
            self.devices[device_id] = {
                "device_name": session.get("DeviceName") or device_id,
                "client": session.get("Client"),
            }
            item = session.get("NowPlayingItem")
            if not item or not item.get("Id"):
                continue
            data = describe(session)
            current[device_id] = _Snapshot(str(item["Id"]), data["is_paused"], data)

        # Stopped: was playing, now gone or nothing playing
        for device_id, before in self._playing.items():
            after = current.get(device_id)
            if after is None or after.item_id != before.item_id:
                events.append(PlaybackEvent(ACTION_STOPPED, device_id, before.data))

        for device_id, after in current.items():
            before = self._playing.get(device_id)
            if before is None or before.item_id != after.item_id:
                events.append(PlaybackEvent(ACTION_STARTED, device_id, after.data))
                if after.paused:
                    events.append(PlaybackEvent(ACTION_PAUSED, device_id, after.data))
            elif after.paused and not before.paused:
                events.append(PlaybackEvent(ACTION_PAUSED, device_id, after.data))
            elif before.paused and not after.paused:
                events.append(PlaybackEvent(ACTION_RESUMED, device_id, after.data))

        self._playing = current
        return events
