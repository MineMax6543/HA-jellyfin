"""Device registry: new API (HA >= 2026.8) with fallback for older versions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.jellyfin_plus import entity as entity_module
from custom_components.jellyfin_plus.const import DOMAIN

from .test_init import DATA
from .test_media_player import atv


def test_client_device_info_new_api() -> None:
    runtime = SimpleNamespace(server_id="srv1", server_device_id="dev-123")
    with patch.object(entity_module, "NEW_DEVICE_API", True):
        info = entity_module.client_device_info(runtime, "srv1_atv", "Apple TV", "Swiftfin")
    assert info["via_device_id"] == "dev-123"
    assert "via_device" not in info


def test_client_device_info_old_api() -> None:
    runtime = SimpleNamespace(server_id="srv1", server_device_id="dev-123")
    with patch.object(entity_module, "NEW_DEVICE_API", False):
        info = entity_module.client_device_info(runtime, "srv1_atv", "Apple TV", "Swiftfin")
    assert info["via_device"] == (DOMAIN, "srv1")
    assert "via_device_id" not in info


def test_find_device_prefers_per_entry_lookup(hass: HomeAssistant) -> None:
    entry = SimpleNamespace(entry_id="entry1")
    registry = MagicMock()
    registry.async_get_device_by_identifier.return_value = "device"
    with patch.object(entity_module.dr, "async_get", return_value=registry):
        assert entity_module.find_device(hass, entry, "srv1_atv") == "device"
    registry.async_get_device_by_identifier.assert_called_once_with(
        (DOMAIN, "srv1_atv"), "entry1"
    )
    registry.async_get_device.assert_not_called()


async def test_clients_linked_to_server_device(hass: HomeAssistant, fake_listener) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="srv1", data=DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    fake_listener.instances[0].push([atv()])
    await hass.async_block_till_done()

    dev_reg = dr.async_get(hass)
    server = dev_reg.async_get_device(identifiers={(DOMAIN, "srv1")})
    client = dev_reg.async_get_device(identifiers={(DOMAIN, "srv1_atv")})
    assert entry.runtime_data.server_device_id == server.id
    assert client.via_device_id == server.id
    assert client.name == "Living Room Apple TV"
