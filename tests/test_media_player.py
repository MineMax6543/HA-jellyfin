"""Media player, remote and browsing."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.media_player import BrowseMedia
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.jellyfin_plus.api import JellyfinClient
from custom_components.jellyfin_plus.browse_media import async_browse, async_search
from custom_components.jellyfin_plus.const import DOMAIN

from .test_init import DATA
from .test_sessions import EPISODE, MOVIE

COMMANDS = ["MoveUp", "MoveDown", "MoveLeft", "MoveRight", "Select", "Back",
            "GoHome", "SetVolume", "Mute", "Unmute", "VolumeUp", "VolumeDown"]


def atv(session_id: str = "s1", item: dict | None = None, paused: bool = False,
        **play_state: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "Id": session_id,
        "DeviceId": "atv",
        "DeviceName": "Living Room Apple TV",
        "Client": "Swiftfin",
        "UserName": "max",
        "SupportsMediaControl": True,
        "SupportedCommands": COMMANDS,
        "LastActivityDate": "2026-11-15T20:00:00Z",
        "PlayState": {"IsPaused": paused, "PositionTicks": 1_200_000_000,
                      "VolumeLevel": 40, "IsMuted": False, **play_state},
    }
    if item is not None:
        data["NowPlayingItem"] = {**item, "ImageTags": {"Primary": "abc123"}}
    return data


BROWSER = {"Id": "s9", "DeviceId": "ff", "DeviceName": "Firefox", "Client": "Jellyfin Web",
           "SupportsMediaControl": False, "SupportedCommands": []}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="srv1", data=DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _eid(hass, domain: str, device_id: str = "atv") -> str | None:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"srv1_{device_id}")


async def test_media_player_states_and_stable_entity(hass: HomeAssistant, fake_listener) -> None:
    entry = await _setup(hass)
    listener = fake_listener.instances[0]
    listener.push([atv(), BROWSER])
    await hass.async_block_till_done()

    player = _eid(hass, "media_player")
    assert player == "media_player.living_room_apple_tv"
    assert _eid(hass, "media_player", "ff") is None  # not controllable -> no player
    assert hass.states.get(player).state == "idle"

    listener.push([atv(item=MOVIE)])
    await hass.async_block_till_done()
    state = hass.states.get(player)
    assert state.state == "playing"
    assert state.attributes["media_title"] == "Dune"
    assert state.attributes["media_content_type"] == "movie"
    assert state.attributes["media_duration"] == 9360
    assert state.attributes["media_position"] == 120
    assert state.attributes["volume_level"] == 0.4
    assert "entity_picture" in state.attributes

    listener.push([atv(item=EPISODE, paused=True)])
    await hass.async_block_till_done()
    state = hass.states.get(player)
    assert state.state == "paused"
    assert state.attributes["media_series_title"] == "Severance"
    assert state.attributes["media_episode"] == "1"

    # app closed: off, not unavailable
    listener.push([])
    await hass.async_block_till_done()
    assert hass.states.get(player).state == "off"

    # app restarted -> NEW session id, SAME entity
    listener.push([atv(session_id="s2", item=MOVIE)])
    await hass.async_block_till_done()
    assert hass.states.get(player).state == "playing"
    assert len([e for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
                if e.domain == "media_player"]) == 1
    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await hass.services.async_call(
            "media_player", "media_pause", {"entity_id": player}, blocking=True
        )
        await hass.services.async_call(
            "media_player", "media_seek", {"entity_id": player, "seek_position": 90}, blocking=True
        )
    assert playstate.call_args_list[0].args == ("s2", "Pause", None)
    assert playstate.call_args_list[1].args == ("s2", "Seek", 900_000_000)

    # server connection lost: unavailable; after reload the player exists right away
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(player).state == "off"


async def test_volume_and_play_media_resume(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    fake_listener.instances[0].push([atv()])
    await hass.async_block_till_done()
    player = _eid(hass, "media_player")

    with patch.object(JellyfinClient, "general_command", AsyncMock()) as cmd:
        await hass.services.async_call(
            "media_player", "volume_set", {"entity_id": player, "volume_level": 0.55}, blocking=True
        )
    assert cmd.call_args.args == ("s1", "SetVolume", {"Volume": 55})

    resume = {"Id": "m1", "UserData": {"PlaybackPositionTicks": 30_000_000_000, "Played": False}}
    with (
        patch.object(JellyfinClient, "item", AsyncMock(return_value=resume)),
        patch.object(JellyfinClient, "play_items", AsyncMock()) as play,
    ):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": player, "media_content_type": "movie", "media_content_id": "m1"},
            blocking=True,
        )
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": player, "media_content_type": "movie", "media_content_id": "m2",
             "enqueue": "add"},
            blocking=True,
        )
    assert play.call_args_list[0].args == ("s1", ["m1"], "PlayNow", 30_000_000_000)
    assert play.call_args_list[1].args == ("s1", ["m2"], "PlayLast", None)


async def test_remote(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    fake_listener.instances[0].push([atv()])
    await hass.async_block_till_done()
    remote = _eid(hass, "remote")
    assert remote == "remote.living_room_apple_tv_remote"
    assert hass.states.get(remote).state == "on"

    with patch.object(JellyfinClient, "general_command", AsyncMock()) as cmd:
        await hass.services.async_call(
            "remote", "send_command",
            {"entity_id": remote, "command": ["up", "Select"], "num_repeats": 2},
            blocking=True,
        )
    assert [c.args[1] for c in cmd.call_args_list] == ["MoveUp", "Select", "MoveUp", "Select"]

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "remote", "send_command", {"entity_id": remote, "command": "ChannelUp"}, blocking=True
        )


class FakeClient:
    async def user_views(self):
        return [{"Id": "lib-movies", "Name": "Movies", "Type": "CollectionFolder",
                 "IsFolder": True, "CollectionType": "movies"},
                {"Id": "lib-books", "Name": "Books", "Type": "CollectionFolder",
                 "IsFolder": True, "CollectionType": "books"}]

    async def item(self, item_id):
        return {"Id": item_id, "Name": "Severance", "Type": "Series", "IsFolder": True}

    async def items(self, **params):
        if "searchTerm" in params:
            return [{**MOVIE, "MediaType": "Video"}]
        return [{"Id": "season1", "Name": "Season 1", "Type": "Season", "IsFolder": True,
                 "ImageTags": {"Primary": "t1"}}]

    async def resume_items(self):
        return [{**EPISODE, "MediaType": "Video", "SeriesId": "ser", "SeriesPrimaryImageTag": "st"}]

    async def next_up(self):
        return []


async def test_browse_and_search() -> None:
    thumb = lambda item_id, tag: f"/img/{item_id}/{tag}"  # noqa: E731
    client = FakeClient()
    root = await async_browse(client, None, thumb)
    assert [c.title for c in root.children] == ["Continue watching", "Next up", "Movies", "Books"]

    resume = await async_browse(client, "resume", thumb)
    episode = resume.children[0]
    assert episode.title == "Severance – S01E01 Pilot"
    assert episode.can_play and not episode.can_expand
    assert episode.thumbnail == "/img/ser/st"  # falls back to the series poster

    series = await async_browse(client, "ser", thumb)
    assert isinstance(series, BrowseMedia)
    assert series.can_expand and series.children[0].title == "Season 1"
    assert series.children[0].can_play  # a season can be queued as a whole

    found = await async_search(client, "dune", thumb)
    assert found[0].title == "Dune (2021)"


async def test_remote_turn_off_stops_playback(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    listener = fake_listener.instances[0]
    listener.push([atv(item=MOVIE)])
    await hass.async_block_till_done()
    remote = _eid(hass, "remote")

    with patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate:
        await hass.services.async_call("remote", "turn_off", {"entity_id": remote}, blocking=True)
        # turn_on and turn_off while idle no longer raise NotImplementedError
        await hass.services.async_call("remote", "turn_on", {"entity_id": remote}, blocking=True)
        listener.push([atv()])
        await hass.async_block_till_done()
        await hass.services.async_call("remote", "turn_off", {"entity_id": remote}, blocking=True)
    assert [c.args[:2] for c in playstate.call_args_list] == [("s1", "Stop")]
