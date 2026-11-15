"""Instant (optimistic) state, position anchoring and resume protection."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.jellyfin_plus import media_player as mp_module
from custom_components.jellyfin_plus.api import JellyfinClient
from custom_components.jellyfin_plus.const import DOMAIN

from .test_init import DATA
from .test_media_player import atv
from .test_sessions import MOVIE

PLAYER = "media_player.living_room_apple_tv"
TICKS = 10_000_000


def at(seconds: float, paused: bool = False) -> dict:
    return atv(item=MOVIE, paused=paused, PositionTicks=int(seconds * TICKS))


@pytest.fixture
def clock(monkeypatch):
    now = [5000.0]
    monkeypatch.setattr(mp_module.time, "monotonic", lambda: now[0])
    return now


async def _setup(hass: HomeAssistant, fake_listener):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="srv1", data=DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return fake_listener.instances[0]


async def _call(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        "media_player", service, {"entity_id": PLAYER, **data}, blocking=True
    )


async def test_pause_is_instant_and_waits_for_confirmation(
    hass: HomeAssistant, fake_listener, clock
) -> None:
    listener = await _setup(hass, fake_listener)
    listener.push([at(600)])
    await hass.async_block_till_done()

    with patch.object(JellyfinClient, "playstate", AsyncMock()):
        await _call(hass, "media_pause")
    # no confirmation from the app yet, HA already shows paused
    assert hass.states.get(PLAYER).state == "paused"

    # a late update from the app still says "playing": keep the expected state
    listener.push([at(601)])
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).state == "paused"

    # app never confirms: after the grace period the reported state wins
    clock[0] += 9
    listener.push([at(602)])
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).state == "playing"


async def test_resume_from_paused_position(hass: HomeAssistant, fake_listener, clock) -> None:
    listener = await _setup(hass, fake_listener)
    listener.push([at(600, paused=True)])
    await hass.async_block_till_done()
    paused_state = hass.states.get(PLAYER)
    assert paused_state.attributes["media_position"] == 600

    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await _call(hass, "media_play")
    assert playstate.call_args.args == ("s1", "Unpause", None)
    state = hass.states.get(PLAYER)
    assert state.state == "playing"
    assert state.attributes["media_position"] == 600
    # anchor re-set on resume, so HA's progress bar continues from 600 s now
    assert (
        state.attributes["media_position_updated_at"]
        > paused_state.attributes["media_position_updated_at"]
    )


async def test_restart_after_resume_is_corrected(
    hass: HomeAssistant, fake_listener, clock, caplog
) -> None:
    listener = await _setup(hass, fake_listener)
    listener.push([at(600, paused=True)])
    await hass.async_block_till_done()

    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await _call(hass, "media_play")
        # the app misbehaves: same item, back at the start
        listener.push([at(1)])
        await hass.async_block_till_done()

    seek = playstate.call_args_list[-1].args
    assert seek[1] == "Seek"
    assert abs(seek[2] / TICKS - 600) < 2
    assert "restarted the item from the beginning" in caplog.text
    assert hass.states.get(PLAYER).attributes["media_position"] == 600


async def test_normal_resume_disarms_protection(hass: HomeAssistant, fake_listener, clock) -> None:
    listener = await _setup(hass, fake_listener)
    listener.push([at(600, paused=True)])
    await hass.async_block_till_done()

    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await _call(hass, "media_play")
        listener.push([at(603)])  # resumed properly
        await hass.async_block_till_done()
        listener.push([at(1)])  # user restarted on purpose afterwards
        await hass.async_block_till_done()
    assert [c.args[1] for c in playstate.call_args_list] == ["Unpause"]  # no seek


async def test_play_pause_toggle_and_debug_log(
    hass: HomeAssistant, fake_listener, clock, caplog
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.jellyfin_plus")
    listener = await _setup(hass, fake_listener)
    listener.push([at(100)])
    await hass.async_block_till_done()
    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await _call(hass, "media_play_pause")
        await _call(hass, "media_play_pause")
    assert [c.args[1] for c in playstate.call_args_list] == ["Pause", "Unpause"]
    assert "-> Pause" in caplog.text
    assert "<- item=Dune paused=False" in caplog.text


async def test_web_player_stop_restart_on_resume(
    hass: HomeAssistant, fake_listener, clock, caplog
) -> None:
    """Replays the Jellyfin Web log: resume -> item vanishes -> item back at 0 s."""
    listener = await _setup(hass, fake_listener)
    listener.push([at(39)])
    await hass.async_block_till_done()

    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await _call(hass, "media_pause")
        listener.push([at(39, paused=True)])
        await hass.async_block_till_done()
        await _call(hass, "media_play")
        listener.push([atv()])  # session without a playing item for a moment
        await hass.async_block_till_done()
        listener.push([at(0)])  # web player started the movie over
        await hass.async_block_till_done()

    commands = [c.args[1] for c in playstate.call_args_list]
    assert commands == ["Pause", "Unpause", "Seek"]
    assert abs(playstate.call_args_list[-1].args[2] / TICKS - 39) < 2
    assert "restarted the item from the beginning" in caplog.text


async def test_stale_position_repeats_do_not_pull_back(
    hass: HomeAssistant, fake_listener, clock, freezer
) -> None:
    """Pushes caused by other sessions repeat this player's old position."""
    listener = await _setup(hass, fake_listener)
    listener.push([at(100)])
    await hass.async_block_till_done()
    first = hass.states.get(PLAYER).attributes["media_position_updated_at"]

    freezer.tick(6)  # 6 s later another device changes -> same old 100 s again
    listener.push([at(100)])
    await hass.async_block_till_done()
    state = hass.states.get(PLAYER)
    assert state.attributes["media_position"] == 100
    assert state.attributes["media_position_updated_at"] == first  # still extrapolating

    # a fresh report that really differs (user seeked on the device) wins
    freezer.tick(1)
    listener.push([at(300)])
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER).attributes["media_position"] == 300


async def test_fractional_position_is_exact(hass: HomeAssistant, fake_listener, clock) -> None:
    listener = await _setup(hass, fake_listener)
    listener.push([at(42.75)])
    await hass.async_block_till_done()
    attrs = hass.states.get(PLAYER).attributes
    assert attrs["media_position"] == 42
    entity = hass.data["entity_components"]["media_player"].get_entity(PLAYER)
    shift = entity._position_updated_at - attrs["media_position_updated_at"]
    assert abs(shift.total_seconds() - 0.75) < 0.01
