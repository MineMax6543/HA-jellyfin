"""Multi-user: which Jellyfin user's data (progress, watched, next up) to use.

Order:
1. an explicitly given Jellyfin user (id or name),
2. the Home Assistant user who triggered the action, if linked,
3. the Jellyfin user logged in on the target device,
4. the account Jellyfin Plus logs in with.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import ServiceValidationError

from .const import CONF_USER_MAP

if TYPE_CHECKING:
    from . import JellyfinConfigEntry


def user_map(entry: JellyfinConfigEntry) -> dict[str, str]:
    """HA user id -> Jellyfin user id."""
    return dict(entry.options.get(CONF_USER_MAP) or {})


def ha_user_for(entry: JellyfinConfigEntry, jellyfin_user_id: str | None) -> str | None:
    """Reverse lookup: the HA user linked to a Jellyfin user."""
    if not jellyfin_user_id:
        return None
    for ha_user, jf_user in user_map(entry).items():
        if jf_user == jellyfin_user_id:
            return ha_user
    return None


def lookup_jellyfin_user(users: dict[str, str], value: str) -> str:
    """Accept a Jellyfin user id or (case-insensitive) name."""
    if value in users:
        return value
    for user_id, name in users.items():
        if name.casefold() == value.casefold():
            return user_id
    raise ServiceValidationError(f"Unknown Jellyfin user: {value}")


def resolve_user(
    entry: JellyfinConfigEntry,
    *,
    explicit: str | None = None,
    ha_user_id: str | None = None,
    session: dict[str, Any] | None = None,
) -> str | None:
    """The Jellyfin user id to read/write progress, watched state etc. for."""
    runtime = entry.runtime_data
    if explicit:
        return lookup_jellyfin_user(runtime.users, explicit)
    mapped = user_map(entry).get(ha_user_id or "")
    if mapped:
        return mapped
    if session and session.get("UserId"):
        return session["UserId"]
    return runtime.client.user_id
