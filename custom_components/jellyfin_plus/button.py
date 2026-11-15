"""Buttons: library scan, backup, restart (admin only)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import JellyfinClient, JellyfinError
from .const import DOMAIN

if TYPE_CHECKING:
    from . import JellyfinConfigEntry, JellyfinRuntime


@dataclass(frozen=True, kw_only=True)
class JellyfinButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[JellyfinClient], Awaitable[Any]]


BUTTONS = (
    JellyfinButtonDescription(
        key="library_scan",
        translation_key="library_scan",
        press_fn=lambda client: client.library_refresh(),
    ),
    JellyfinButtonDescription(
        key="create_backup",
        translation_key="create_backup",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda client: client.create_backup(),
    ),
    JellyfinButtonDescription(
        key="restart",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda client: client.restart(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if runtime.is_admin:
        async_add_entities(JellyfinButton(runtime, desc) for desc in BUTTONS)


class JellyfinButton(ButtonEntity):
    _attr_has_entity_name = True
    entity_description: JellyfinButtonDescription

    def __init__(self, runtime: JellyfinRuntime, desc: JellyfinButtonDescription) -> None:
        self.entity_description = desc
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.server_id}_{desc.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, runtime.server_id)})

    @property
    def available(self) -> bool:
        return self._runtime.connected

    async def async_press(self) -> None:
        try:
            await self.entity_description.press_fn(self._runtime.client)
        except JellyfinError as err:
            raise HomeAssistantError(f"Jellyfin: {err}") from err
