"""Jellyfin in Home Assistant's Media panel: play it on any speaker or TV.

The media player entities control Jellyfin *apps*. This media source is the
other direction: Home Assistant fetches a stream URL from Jellyfin and hands
it to any media player (HomePod via AirPlay, Sonos, Chromecast, ...).
Audio is streamed as MP3 and video as HLS (H.264/AAC), which every one of
those players understands; Jellyfin transcodes when needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from homeassistant.components.media_player import BrowseError, BrowseMedia, MediaClass
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .api import JellyfinError
from .browse_media import _PLAYABLE_CONTAINERS, async_browse, split_user
from .const import CONF_TOKEN, DOMAIN

if TYPE_CHECKING:
    from . import JellyfinConfigEntry

AUDIO_MIME = "audio/mpeg"
HLS_MIME = "application/vnd.apple.mpegurl"


async def async_get_media_source(hass: HomeAssistant) -> JellyfinMediaSource:
    return JellyfinMediaSource(hass)


class JellyfinMediaSource(MediaSource):
    """Browse every loaded Jellyfin Plus server; identifier '<entry id>/<content id>'."""

    name = "Jellyfin"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _entries(self) -> list[JellyfinConfigEntry]:
        return self.hass.config_entries.async_loaded_entries(DOMAIN)

    def _entry(self, entry_id: str) -> JellyfinConfigEntry:
        for entry in self._entries():
            if entry.entry_id == entry_id:
                return entry
        raise Unresolvable(f"Jellyfin server {entry_id} is not loaded")

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        entry_id, _, content_id = (item.identifier or "").partition("/")
        entry = self._entry(entry_id)
        client = entry.runtime_data.client
        user_id, item_id = split_user(content_id)
        try:
            info = await client.item(item_id, user_id=user_id)
        except JellyfinError as err:
            raise Unresolvable(f"Jellyfin: {err}") from err
        if not info:
            raise Unresolvable(f"Jellyfin item {item_id} not found")
        token = entry.data[CONF_TOKEN]
        if info.get("MediaType") == "Audio":
            query = urlencode({"audioCodec": "mp3", "audioBitRate": 320000, "api_key": token})
            return PlayMedia(f"{client.url}/Audio/{item_id}/stream.mp3?{query}", AUDIO_MIME)
        if info.get("MediaType") == "Video":
            query = urlencode(
                {
                    "mediaSourceId": item_id,
                    "videoCodec": "h264",
                    "audioCodec": "aac",
                    "maxAudioChannels": 6,
                    "api_key": token,
                }
            )
            return PlayMedia(f"{client.url}/Videos/{item_id}/main.m3u8?{query}", HLS_MIME)
        raise Unresolvable(f"{info.get('Name')} is not a single song or video")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        entries = self._entries()
        if not item.identifier:
            if len(entries) == 1:
                return await self._browse(entries[0], None)
            return BrowseMediaSource(
                domain=DOMAIN,
                identifier=None,
                media_class=MediaClass.DIRECTORY,
                media_content_type="",
                title=self.name,
                can_play=False,
                can_expand=True,
                children=[
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=f"{entry.entry_id}/",
                        media_class=MediaClass.DIRECTORY,
                        media_content_type="",
                        title=entry.title,
                        can_play=False,
                        can_expand=True,
                    )
                    for entry in entries
                ],
                children_media_class=MediaClass.DIRECTORY,
            )
        entry_id, _, content_id = item.identifier.partition("/")
        return await self._browse(self._entry(entry_id), content_id or None)

    async def _browse(
        self, entry: JellyfinConfigEntry, content_id: str | None
    ) -> BrowseMediaSource:
        client = entry.runtime_data.client

        def thumb(item_id: str, tag: str) -> str:
            # Jellyfin serves images without a login
            return client.image_url(item_id, tag, max_height=300)

        try:
            node = await async_browse(client, content_id, thumb)
        except JellyfinError as err:
            raise BrowseError(f"Jellyfin browse failed: {err}") from err
        return _convert(entry.entry_id, node)


def _convert(entry_id: str, node: BrowseMedia) -> BrowseMediaSource:
    children: Any = node.children
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=f"{entry_id}/{node.media_content_id}",
        media_class=node.media_class,
        media_content_type=node.media_content_type,
        title=node.title,
        # A stream URL is one file: albums/playlists/seasons aren't playable here
        can_play=node.can_play and node.media_content_type not in _PLAYABLE_CONTAINERS,
        can_expand=node.can_expand,
        thumbnail=node.thumbnail,
        children=[_convert(entry_id, c) for c in children] if children is not None else None,
        children_media_class=node.children_media_class,
    )
