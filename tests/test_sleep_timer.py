"""Sleep timer on the media player."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant
import homeassistant.util.dt as dt_util

from custom_components.jellyfin_plus.api import JellyfinClient
from custom_components.jellyfin_plus.const import DOMAIN

from .test_media_player import _setup, atv
from .test_sessions import EPISODE, MOVIE

PLAYER = "media_player.living_room_apple_tv"


async def test_minutes_pauses(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    fake_listener.instances[0].push([atv(item=MOVIE)])
    await hass.async_block_till_done()

    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await hass.services.async_call(
            DOMAIN, "sleep_timer", {"entity_id": PLAYER, "minutes": 30}, blocking=True
        )
        state = hass.states.get(PLAYER)
        assert state.attributes["sleep_timer"] == "minutes"
        assert state.attributes["sleep_timer_ends"]

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=29))
        await hass.async_block_till_done()
        playstate.assert_not_awaited()

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=31))
        await hass.async_block_till_done()
    assert playstate.call_args.args == ("s1", "Pause", None)
    assert "sleep_timer" not in hass.states.get(PLAYER).attributes


async def test_end_of_item_follows_playback(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    listener = fake_listener.instances[0]
    # 9360 s movie at 9300 s -> 60 s left
    listener.push([atv(item=MOVIE, PositionTicks=93_000_000_000)])
    await hass.async_block_till_done()

    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await hass.services.async_call(
            DOMAIN,
            "sleep_timer",
            {"entity_id": PLAYER, "end_of_item": True, "action": "stop"},
            blocking=True,
        )
        assert hass.states.get(PLAYER).attributes["sleep_timer"] == "end_of_item"

        # paused: the timer waits
        listener.push([atv(item=MOVIE, paused=True, PositionTicks=93_100_000_000)])
        await hass.async_block_till_done()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5))
        await hass.async_block_till_done()
        playstate.assert_not_awaited()
        assert hass.states.get(PLAYER).attributes["sleep_timer_ends"] is None

        # app moved on to another item before we caught the end
        listener.push([atv(item=EPISODE)])
        await hass.async_block_till_done()
    assert playstate.call_args.args == ("s1", "Stop", None)


async def test_cancel(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    fake_listener.instances[0].push([atv(item=MOVIE)])
    await hass.async_block_till_done()
    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await hass.services.async_call(
            DOMAIN, "sleep_timer", {"entity_id": PLAYER, "minutes": 1}, blocking=True
        )
        await hass.services.async_call(
            DOMAIN, "cancel_sleep_timer", {"entity_id": PLAYER}, blocking=True
        )
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
        await hass.async_block_till_done()
    playstate.assert_not_awaited()
    assert "sleep_timer" not in hass.states.get(PLAYER).attributes
