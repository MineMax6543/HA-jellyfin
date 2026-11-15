"""Setup, entities and bus events with a simulated Jellyfin connection."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.jellyfin_plus.api import CannotConnect, InvalidAuth
from custom_components.jellyfin_plus.const import DOMAIN, EVENT_PLAYBACK

from .test_sessions import MOVIE, session

DATA = {
    "url": "http://jellyfin.local:8096",
    "username": "max",
    "device_id": "ha-device",
    "token": "tok",
    "user_id": "u1",
    "server_id": "srv1",
    "is_admin": True,
}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="srv1", data=DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _event_entity(hass: HomeAssistant, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("event", DOMAIN, unique_id)


async def test_playback_events(hass: HomeAssistant, fake_listener) -> None:
    bus_events = async_capture_events(hass, EVENT_PLAYBACK)
    entry = await _setup(hass)
    listener = fake_listener.instances[0]
    assert listener.token == "tok"

    all_clients = _event_entity(hass, "srv1_all_clients")
    assert all_clients is not None
    assert hass.states.get(all_clients).state != "unavailable"

    # first playback on a new client creates its entity and fires there too
    listener.push([session(item=MOVIE)])
    await hass.async_block_till_done()
    client = _event_entity(hass, "srv1_atv")
    assert client is not None
    state = hass.states.get(client)
    assert state.attributes["event_type"] == "playback_started"
    assert state.attributes["title"] == "Dune"
    assert hass.states.get(all_clients).attributes["event_type"] == "playback_started"
    assert bus_events[-1].data["action"] == "playback_started"
    assert bus_events[-1].data["device_name"] == "Living Room Apple TV"
    assert bus_events[-1].data["server_name"] == "Media Server"

    listener.push([session(item=MOVIE, paused=True)])
    await hass.async_block_till_done()
    assert hass.states.get(client).attributes["event_type"] == "playback_paused"

    listener.push([session()])
    await hass.async_block_till_done()
    assert hass.states.get(client).attributes["event_type"] == "playback_stopped"
    assert [e.data["action"] for e in bus_events] == [
        "playback_started",
        "playback_paused",
        "playback_stopped",
    ]

    # connection loss makes entities unavailable
    listener.connected = False
    listener.on_connection(False)
    await hass.async_block_till_done()
    assert hass.states.get(client).state == "unavailable"

    # client entities come back after a reload, before any new playback
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(client) is not None
    assert hass.states.get(client).state != "unavailable"


async def test_started_and_paused_in_one_update(hass: HomeAssistant, fake_listener) -> None:
    """A new client's entity must not lose the second event of its first update."""
    await _setup(hass)
    fake_listener.instances[0].push([session(item=MOVIE, paused=True)])
    await hass.async_block_till_done()
    client = _event_entity(hass, "srv1_atv")
    assert hass.states.get(client).attributes["event_type"] == "playback_paused"


async def test_setup_retries_when_unreachable(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.jellyfin_plus.async_get_system_info",
        AsyncMock(side_effect=CannotConnect("down")),
    ):
        entry = MockConfigEntry(domain=DOMAIN, unique_id="srv1", data=DATA)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_starts_reauth_on_bad_token(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.jellyfin_plus.async_get_system_info",
        AsyncMock(side_effect=InvalidAuth("401")),
    ):
        entry = MockConfigEntry(domain=DOMAIN, unique_id="srv1", data=DATA)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert flows and flows[0]["context"]["source"] == "reauth"
