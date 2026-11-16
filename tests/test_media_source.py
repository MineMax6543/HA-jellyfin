"""Jellyfin in the Media panel, playable on any media player."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.components import media_source
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.jellyfin_plus.api import JellyfinClient
from custom_components.jellyfin_plus.const import DOMAIN

from .test_media_player import _setup

VIEWS = [{"Id": "music", "Name": "Music", "Type": "CollectionFolder", "IsFolder": True}]
ALBUM = {"Id": "al1", "Name": "Random Access Memories", "Type": "MusicAlbum", "IsFolder": True}
SONG = {"Id": "s1", "Name": "Contact", "Type": "Audio", "MediaType": "Audio"}
MOVIE = {"Id": "m1", "Name": "Dune", "Type": "Movie", "MediaType": "Video"}


@pytest.fixture
async def setup(hass: HomeAssistant, fake_listener):
    assert await async_setup_component(hass, "media_source", {})
    return await _setup(hass)


async def test_browse(hass: HomeAssistant, setup) -> None:
    entry = setup
    with patch.object(JellyfinClient, "user_views", AsyncMock(return_value=VIEWS)):
        root = await media_source.async_browse_media(hass, f"media-source://{DOMAIN}")
    assert root.title == "Jellyfin"
    titles = [c.title for c in root.children]
    assert titles == ["Continue watching", "Next up", "Music"]
    assert root.children[2].media_content_id == f"media-source://{DOMAIN}/{entry.entry_id}/music"

    with (
        patch.object(JellyfinClient, "item", AsyncMock(return_value=ALBUM)),
        patch.object(JellyfinClient, "items", AsyncMock(return_value=[SONG])),
    ):
        album = await media_source.async_browse_media(
            hass, f"media-source://{DOMAIN}/{entry.entry_id}/al1"
        )
    assert not album.can_play  # a whole album isn't one stream
    assert album.children[0].can_play
    assert album.children[0].title == "Contact"


async def test_resolve(hass: HomeAssistant, setup) -> None:
    entry = setup
    with patch.object(JellyfinClient, "item", AsyncMock(side_effect=[SONG, MOVIE, ALBUM])):
        song = await media_source.async_resolve_media(
            hass, f"media-source://{DOMAIN}/{entry.entry_id}/s1", None
        )
        movie = await media_source.async_resolve_media(
            hass, f"media-source://{DOMAIN}/{entry.entry_id}/m1", None
        )
        with pytest.raises(media_source.Unresolvable):
            await media_source.async_resolve_media(
                hass, f"media-source://{DOMAIN}/{entry.entry_id}/al1", None
            )
    assert song.url.startswith("http://jellyfin.local:8096/Audio/s1/stream.mp3?")
    assert "api_key=tok" in song.url
    assert song.mime_type == "audio/mpeg"
    assert movie.url.startswith("http://jellyfin.local:8096/Videos/m1/main.m3u8?")
    assert movie.mime_type == "application/vnd.apple.mpegurl"
