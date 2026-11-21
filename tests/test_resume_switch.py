"""Apps that start a different item (or the same one over) after "Unpause"."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.jellyfin_plus.api import JellyfinClient
from custom_components.jellyfin_plus.const import DOMAIN
from custom_components.jellyfin_plus.update import current_plugins

from .conftest import PACKAGES
from .test_media_player import atv
from .test_playback_control import PLAYER, TICKS, _call, _setup, clock  # noqa: F401

CASUALTY = {"Id": "a1", "Name": "Casualty", "Type": "Audio", "MediaType": "Audio",
            "RunTimeTicks": 140 * TICKS}
GOOD_THINGS = {"Id": "a2", "Name": "Good Things Go", "Type": "Audio", "MediaType": "Audio",
               "RunTimeTicks": 220 * TICKS}
IGYEIH = {"Id": "a3", "Name": "IGYEIH", "Type": "Audio", "MediaType": "Audio",
          "RunTimeTicks": 210 * TICKS}
QUEUE = [{"Id": "a1"}, {"Id": "a2"}, {"Id": "a3"}]


def song(item: dict, seconds: float, paused: bool = False) -> dict:
    return {**atv(item=item, paused=paused, PositionTicks=int(seconds * TICKS)),
            "NowPlayingQueue": QUEUE}


async def test_web_player_jumps_to_first_song_after_resume(
    hass: HomeAssistant, fake_listener, clock, caplog  # noqa: F811
) -> None:
    """Replays the log: pause IGYEIH at 0.5 s, Unpause, the web player starts Casualty."""
    listener = await _setup(hass, fake_listener)
    listener.push([song(IGYEIH, 0.5, paused=True)])
    await hass.async_block_till_done()

    with (
        patch.object(JellyfinClient, "playstate", AsyncMock()),
        patch.object(JellyfinClient, "play_items", AsyncMock()) as play,
    ):
        await _call(hass, "media_play")
        clock[0] += 0.2
        listener.push([song(CASUALTY, 0)])
        await hass.async_block_till_done()

    # back to IGYEIH, inside the same queue (no seek needed below 1 s)
    assert play.call_args.args == ("s1", ["a1", "a2", "a3"], "PlayNow", None, 2)
    assert "switched to another item after resume" in caplog.text


async def test_other_song_restored_at_paused_position(
    hass: HomeAssistant, fake_listener, clock  # noqa: F811
) -> None:
    listener = await _setup(hass, fake_listener)
    listener.push([song(GOOD_THINGS, 95, paused=True)])
    await hass.async_block_till_done()
    with (
        patch.object(JellyfinClient, "playstate", AsyncMock()),
        patch.object(JellyfinClient, "play_items", AsyncMock()) as play,
    ):
        await _call(hass, "media_play")
        listener.push([song(CASUALTY, 0)])
        await hass.async_block_till_done()
    assert play.call_args.args == ("s1", ["a1", "a2", "a3"], "PlayNow", 95 * TICKS, 1)


async def test_early_restart_of_same_song_is_corrected(
    hass: HomeAssistant, fake_listener, clock  # noqa: F811
) -> None:
    """Paused at 4 s (below the old 15 s limit), app starts the song over."""
    listener = await _setup(hass, fake_listener)
    listener.push([song(CASUALTY, 4, paused=True)])
    await hass.async_block_till_done()
    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await _call(hass, "media_play")
        listener.push([song(CASUALTY, 0)])
        await hass.async_block_till_done()
    assert [c.args[1] for c in playstate.call_args_list] == ["Unpause", "Seek"]


async def test_resume_just_after_start_is_left_alone(
    hass: HomeAssistant, fake_listener, clock  # noqa: F811
) -> None:
    """Paused at 2.4 s: a restart from 0 isn't worth a seek."""
    listener = await _setup(hass, fake_listener)
    listener.push([song(CASUALTY, 2.4, paused=True)])
    await hass.async_block_till_done()
    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await _call(hass, "media_play")
        listener.push([song(CASUALTY, 0)])
        await hass.async_block_till_done()
    assert [c.args[1] for c in playstate.call_args_list] == ["Unpause"]


@pytest.mark.parametrize(
    ("action", "paused_at", "later"),
    [
        ("next", 50, 0),  # user skipped on purpose
        ("none", 135, 0),  # song was ending anyway
        ("none", 50, 6),  # switched long after resuming: the user did it on the app
    ],
)
async def test_intended_song_changes_are_kept(
    hass: HomeAssistant, fake_listener, clock, action, paused_at, later  # noqa: F811
) -> None:
    listener = await _setup(hass, fake_listener)
    listener.push([song(CASUALTY, paused_at, paused=True)])
    await hass.async_block_till_done()
    with (
        patch.object(JellyfinClient, "playstate", AsyncMock()),
        patch.object(JellyfinClient, "play_items", AsyncMock()) as play,
    ):
        await _call(hass, "media_play")
        if action == "next":
            await _call(hass, "media_next_track")
        clock[0] += later
        listener.push([song(GOOD_THINGS, 0)])
        await hass.async_block_till_done()
    play.assert_not_awaited()


async def test_not_connected_error_names_the_device(
    hass: HomeAssistant, fake_listener, clock  # noqa: F811
) -> None:
    listener = await _setup(hass, fake_listener)
    listener.push([atv()])
    await hass.async_block_till_done()
    listener.push([])  # app closed
    await hass.async_block_till_done()
    with pytest.raises(HomeAssistantError, match="^Living Room Apple TV is not connected"):
        await _call(hass, "media_play_pause")


def test_duplicate_plugin_versions() -> None:
    plugins = [
        {"Id": "dlna", "Name": "DLNA", "Version": "10.0.0.0", "Status": "Superceded"},
        {"Id": "dlna", "Name": "DLNA", "Version": "10.1.0.0", "Status": "Active"},
        {"Id": "tmdb", "Name": "TMDb", "Version": "2.0.0.0", "Status": "Active"},
        {"Id": "tmdb", "Name": "TMDb", "Version": "3.0.0.0", "Status": "Restart"},
    ]
    result = current_plugins(plugins)
    assert result["dlna"]["Version"] == "10.1.0.0"
    assert result["tmdb"]["Version"] == "3.0.0.0"  # installed, waiting for a restart


async def test_duplicate_plugins_give_one_entity(
    hass: HomeAssistant, fake_listener, clock, caplog  # noqa: F811
) -> None:
    plugins = [
        {"Id": "intro-skipper-guid", "Name": "Intro Skipper", "Version": "1.10.11.2",
         "Status": "Superceded"},
        {"Id": "intro-skipper-guid", "Name": "Intro Skipper", "Version": "1.10.11.3",
         "Status": "Active"},
    ]
    caplog.set_level(logging.ERROR)
    with patch.object(JellyfinClient, "plugins", AsyncMock(return_value=plugins)):
        await _setup(hass, fake_listener)
    assert "does not generate unique IDs" not in caplog.text
    entity_id = er.async_get(hass).async_get_entity_id(
        "update", DOMAIN, "srv1_plugin_intro-skipper-guid"
    )
    state = hass.states.get(entity_id)
    assert state.attributes["installed_version"] == "1.10.11.3"
    assert state.attributes["latest_version"] == PACKAGES[0]["versions"][0]["version"]
