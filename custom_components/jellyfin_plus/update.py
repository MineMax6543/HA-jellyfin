"""Update entities for the Jellyfin server and its plugins (admin only)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from awesomeversion import AwesomeVersion, AwesomeVersionException

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import JellyfinError
from .const import DOMAIN

if TYPE_CHECKING:
    from . import JellyfinConfigEntry, JellyfinRuntime


def _newer(a: str | None, b: str | None) -> bool:
    try:
        return a is not None and b is not None and AwesomeVersion(a) > AwesomeVersion(b)
    except AwesomeVersionException:
        return False


def latest_compatible(package: dict[str, Any], server_version: str | None) -> dict[str, Any] | None:
    """Newest package version whose targetAbi the server satisfies."""
    best: dict[str, Any] | None = None
    for version in package.get("versions") or []:
        abi = version.get("targetAbi")
        if abi and server_version and _newer(abi, server_version):
            continue  # needs a newer Jellyfin
        if best is None or _newer(version.get("version"), best.get("version")):
            best = version
    return best


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if not runtime.is_admin:
        return
    entities: list[UpdateEntity] = [JellyfinServerUpdate(runtime)]
    data = runtime.coordinator.data or {}
    packages = {p.get("guid"): p for p in data.get("packages") or []}
    for plugin in data.get("plugins") or []:
        if plugin.get("Id") in packages:
            entities.append(JellyfinPluginUpdate(runtime, plugin["Id"], plugin.get("Name")))
    async_add_entities(entities)


class _Base(CoordinatorEntity, UpdateEntity):
    _attr_has_entity_name = True

    def __init__(self, runtime: JellyfinRuntime, key: str) -> None:
        super().__init__(runtime.coordinator)
        self._runtime = runtime
        self._attr_unique_id = f"{runtime.server_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, runtime.server_id)})

    @property
    def _data(self) -> dict[str, Any]:
        return self.coordinator.data or {}

    @property
    def _server_version(self) -> str | None:
        return (self._data.get("system") or {}).get("Version")


class JellyfinServerUpdate(_Base):
    """Shows when a newer Jellyfin release exists (install it the usual way)."""

    _attr_translation_key = "server"
    _attr_title = "Jellyfin"

    def __init__(self, runtime: JellyfinRuntime) -> None:
        super().__init__(runtime, "server_update")

    @property
    def installed_version(self) -> str | None:
        return self._server_version

    @property
    def latest_version(self) -> str | None:
        latest = (self._data.get("latest_release") or {}).get("version")
        if latest and _newer(latest, self._server_version):
            return latest
        return self._server_version

    @property
    def release_url(self) -> str | None:
        return (self._data.get("latest_release") or {}).get("url")


class JellyfinPluginUpdate(_Base):
    """One installed plugin; installs updates through Jellyfin's catalog."""

    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, runtime: JellyfinRuntime, plugin_id: str, name: str | None) -> None:
        super().__init__(runtime, f"plugin_{plugin_id}")
        self._plugin_id = plugin_id
        self._attr_name = f"{name or plugin_id} plugin"
        self._attr_title = name

    @property
    def _plugin(self) -> dict[str, Any]:
        return next(
            (p for p in self._data.get("plugins") or [] if p.get("Id") == self._plugin_id), {}
        )

    @property
    def _package(self) -> dict[str, Any] | None:
        return next(
            (p for p in self._data.get("packages") or [] if p.get("guid") == self._plugin_id),
            None,
        )

    @property
    def _latest(self) -> dict[str, Any] | None:
        package = self._package
        return latest_compatible(package, self._server_version) if package else None

    @property
    def available(self) -> bool:
        return super().available and bool(self._plugin)

    @property
    def installed_version(self) -> str | None:
        return self._plugin.get("Version")

    @property
    def latest_version(self) -> str | None:
        latest = (self._latest or {}).get("version")
        if latest and _newer(latest, self.installed_version):
            return latest
        return self.installed_version

    @property
    def release_summary(self) -> str | None:
        if self._plugin.get("Status") == "Restart":
            return "Restart Jellyfin to finish the update."
        return ((self._latest or {}).get("changelog") or "")[:255] or None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        latest = self._latest
        if not latest:
            raise HomeAssistantError("No compatible version found in Jellyfin's catalog")
        try:
            await self._runtime.client.install_package(
                (self._package or {}).get("name") or self._attr_title or "",
                self._plugin_id,
                latest["version"],
                latest.get("repositoryUrl"),
            )
        except JellyfinError as err:
            raise HomeAssistantError(f"Plugin update failed: {err}") from err
        await self.coordinator.async_request_refresh()
