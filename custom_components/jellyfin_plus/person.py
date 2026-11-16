"""Shared bits for per-person (linked Jellyfin user) entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.device_registry import DeviceInfo

from .entity import client_device_info
from .users import user_map

if TYPE_CHECKING:
    from . import JellyfinConfigEntry, JellyfinRuntime


def linked_people(entry: JellyfinConfigEntry) -> dict[str, str]:
    """Linked Jellyfin users: id -> display name."""
    names = entry.runtime_data.users
    return {jf: names.get(jf, jf) for jf in sorted(set(user_map(entry).values()))}


def person_unique_id(runtime: JellyfinRuntime, user_id: str, key: str) -> str:
    return f"{runtime.server_id}_person_{user_id}_{key}"


def person_device_info(runtime: JellyfinRuntime, user_id: str, name: str) -> DeviceInfo:
    """One device per linked person, under the server device."""
    return client_device_info(
        runtime, f"{runtime.server_id}_person_{user_id}", f"Jellyfin {name}", "Jellyfin user"
    )


def person_data(runtime: JellyfinRuntime, user_id: str) -> dict[str, Any]:
    """Polled next up / upcoming lists for one person."""
    return ((runtime.coordinator.data or {}).get("people") or {}).get(user_id) or {}


def sessions_of(runtime: JellyfinRuntime, user_id: str) -> list[dict[str, Any]]:
    """Sessions this person is watching in (as owner or added user)."""
    return [
        s
        for s in runtime.sessions.values()
        if s.get("UserId") == user_id
        or any(u.get("UserId") == user_id for u in s.get("AdditionalUsers") or [])
    ]
