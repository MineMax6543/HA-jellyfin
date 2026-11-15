"""Media player per Jellyfin client device."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEnqueue,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    SearchMedia,
    SearchMediaQuery,
)
import voluptuous as vol

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
import homeassistant.util.dt as dt_util

from .api import JellyfinError
from .browse_media import async_browse, async_search
from .const import TICKS_PER_SECOND
from .entity import (
    JellyfinDeviceEntity,
    async_setup_device_entities,
    supported_commands,
    supports_media_control,
)

if TYPE_CHECKING:
    from . import JellyfinConfigEntry

_BASE_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.MEDIA_ENQUEUE
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.SEARCH_MEDIA
)
_CONTENT_TYPE = {
    "Movie": MediaType.MOVIE,
    "Episode": MediaType.TVSHOW,
    "Audio": MediaType.MUSIC,
    "MusicVideo": MediaType.VIDEO,
    "TvChannel": MediaType.CHANNEL,
}
SERVICE_SEND_MESSAGE = "send_message"
_ENQUEUE = {
    MediaPlayerEnqueue.ADD: "PlayLast",
    MediaPlayerEnqueue.NEXT: "PlayNext",
    MediaPlayerEnqueue.PLAY: "PlayNow",
    MediaPlayerEnqueue.REPLACE: "PlayNow",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_SEND_MESSAGE,
        {
            vol.Required("message"): cv.string,
            vol.Optional("title", default=""): cv.string,
            vol.Optional("timeout", default=10): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=600)
            ),
            vol.Optional("pause", default=False): cv.boolean,
        },
        "async_send_message",
    )
    async_setup_device_entities(
        hass,
        entry,
        async_add_entities,
        "media_player",
        supports_media_control,
        lambda device_id, name, client: JellyfinMediaPlayer(
            entry, runtime, device_id, name, client
        ),
    )


class JellyfinMediaPlayer(JellyfinDeviceEntity, MediaPlayerEntity):
    """Stable media player for one Jellyfin client."""

    _attr_name = None  # use the device name
    _attr_media_image_remotely_accessible = False

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._position: int | None = None
        self._position_updated_at: datetime | None = None

    # ---- helpers ----------------------------------------------------------
    @property
    def _item(self) -> dict[str, Any] | None:
        session = self.session
        return (session or {}).get("NowPlayingItem")

    @property
    def _play_state(self) -> dict[str, Any]:
        return (self.session or {}).get("PlayState") or {}

    def _session_id(self) -> str:
        session = self.session
        if not session or not session.get("Id"):
            raise HomeAssistantError(f"{self.name or self.device_id} is not connected to Jellyfin")
        return session["Id"]

    async def _run(self, coro) -> None:
        try:
            await coro
        except JellyfinError as err:
            raise HomeAssistantError(f"Jellyfin command failed: {err}") from err

    def _update_position(self) -> None:
        ticks = self._play_state.get("PositionTicks") if self._item else None
        position = int(ticks // TICKS_PER_SECOND) if isinstance(ticks, (int, float)) else None
        if position != self._position:
            self._position = position
            self._position_updated_at = dt_util.utcnow()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._update_position()

    @callback
    def _on_sessions(self) -> None:
        self._update_position()
        super()._on_sessions()

    # ---- state ------------------------------------------------------------
    @property
    def state(self) -> MediaPlayerState:
        if self.session is None:
            return MediaPlayerState.OFF
        if not self._item:
            return MediaPlayerState.IDLE
        if self._play_state.get("IsPaused"):
            return MediaPlayerState.PAUSED
        return MediaPlayerState.PLAYING

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = _BASE_FEATURES
        commands = supported_commands(self.session)
        if "SetVolume" in commands:
            features |= MediaPlayerEntityFeature.VOLUME_SET
        if commands & {"Mute", "Unmute", "ToggleMute"}:
            features |= MediaPlayerEntityFeature.VOLUME_MUTE
        if commands & {"VolumeUp", "VolumeDown"}:
            features |= MediaPlayerEntityFeature.VOLUME_STEP
        return features

    @property
    def media_content_id(self) -> str | None:
        return (self._item or {}).get("Id")

    @property
    def media_content_type(self) -> str | None:
        item = self._item
        if not item:
            return None
        return _CONTENT_TYPE.get(item.get("Type"), MediaType.VIDEO)

    @property
    def media_title(self) -> str | None:
        return (self._item or {}).get("Name")

    @property
    def media_series_title(self) -> str | None:
        return (self._item or {}).get("SeriesName")

    @property
    def media_season(self) -> str | None:
        value = (self._item or {}).get("ParentIndexNumber")
        return str(value) if value is not None else None

    @property
    def media_episode(self) -> str | None:
        value = (self._item or {}).get("IndexNumber")
        return str(value) if value is not None else None

    @property
    def media_artist(self) -> str | None:
        artists = (self._item or {}).get("Artists")
        return ", ".join(artists) if artists else None

    @property
    def media_album_name(self) -> str | None:
        return (self._item or {}).get("Album")

    @property
    def media_album_artist(self) -> str | None:
        return (self._item or {}).get("AlbumArtist")

    @property
    def media_duration(self) -> int | None:
        ticks = (self._item or {}).get("RunTimeTicks")
        return int(ticks // TICKS_PER_SECOND) if isinstance(ticks, (int, float)) else None

    @property
    def media_position(self) -> int | None:
        return self._position

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self._position_updated_at

    @property
    def volume_level(self) -> float | None:
        level = self._play_state.get("VolumeLevel")
        return level / 100 if isinstance(level, (int, float)) else None

    @property
    def is_volume_muted(self) -> bool | None:
        if not self.session:
            return None
        return bool(self._play_state.get("IsMuted"))

    @property
    def media_image_url(self) -> str | None:
        item = self._item
        if not item:
            return None
        tag = (item.get("ImageTags") or {}).get("Primary")
        if tag:
            return self._runtime.client.image_url(item["Id"], tag)
        for id_key, tag_key in (("SeriesId", "SeriesPrimaryImageTag"), ("AlbumId", "AlbumPrimaryImageTag")):
            if item.get(id_key) and item.get(tag_key):
                return self._runtime.client.image_url(item[id_key], item[tag_key])
        return None

    @property
    def media_image_hash(self) -> str | None:
        url = self.media_image_url
        return url.rsplit("tag=", 1)[-1][:16] if url else None

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        url = self.media_image_url
        if not url:
            return None, None
        return await self._runtime.client.fetch_image(url)

    # ---- controls -----------------------------------------------------------
    async def async_media_play(self) -> None:
        await self._run(self._runtime.client.playstate(self._session_id(), "Unpause"))

    async def async_media_pause(self) -> None:
        await self._run(self._runtime.client.playstate(self._session_id(), "Pause"))

    async def async_media_play_pause(self) -> None:
        await self._run(self._runtime.client.playstate(self._session_id(), "PlayPause"))

    async def async_media_stop(self) -> None:
        await self._run(self._runtime.client.playstate(self._session_id(), "Stop"))

    async def async_media_next_track(self) -> None:
        await self._run(self._runtime.client.playstate(self._session_id(), "NextTrack"))

    async def async_media_previous_track(self) -> None:
        await self._run(self._runtime.client.playstate(self._session_id(), "PreviousTrack"))

    async def async_media_seek(self, position: float) -> None:
        await self._run(
            self._runtime.client.playstate(
                self._session_id(), "Seek", int(position * TICKS_PER_SECOND)
            )
        )

    async def async_set_volume_level(self, volume: float) -> None:
        await self._run(
            self._runtime.client.general_command(
                self._session_id(), "SetVolume", {"Volume": round(volume * 100)}
            )
        )

    async def async_mute_volume(self, mute: bool) -> None:
        await self._run(
            self._runtime.client.general_command(self._session_id(), "Mute" if mute else "Unmute")
        )

    async def async_volume_up(self) -> None:
        await self._run(self._runtime.client.general_command(self._session_id(), "VolumeUp"))

    async def async_volume_down(self) -> None:
        await self._run(self._runtime.client.general_command(self._session_id(), "VolumeDown"))

    async def async_play_media(
        self,
        media_type: str,
        media_id: str,
        enqueue: MediaPlayerEnqueue | None = None,
        **kwargs: Any,
    ) -> None:
        """Play a Jellyfin item; resumes where you left off when starting fresh."""
        session_id = self._session_id()
        command = _ENQUEUE.get(enqueue, "PlayNow") if enqueue else "PlayNow"
        start_ticks = None
        if command == "PlayNow":
            try:
                item = await self._runtime.client.item(media_id)
            except JellyfinError:
                item = None
            user_data = (item or {}).get("UserData") or {}
            ticks = user_data.get("PlaybackPositionTicks")
            if isinstance(ticks, int) and ticks > 0 and not user_data.get("Played"):
                start_ticks = ticks
        await self._run(
            self._runtime.client.play_items(session_id, [media_id], command, start_ticks)
        )

    async def async_send_message(
        self, message: str, title: str = "", timeout: int = 10, pause: bool = False
    ) -> None:
        """Show a message on the client's screen, optionally pausing playback."""
        session_id = self._session_id()
        if pause and self.state == MediaPlayerState.PLAYING:
            await self._run(self._runtime.client.playstate(session_id, "Pause"))
        await self._run(
            self._runtime.client.send_message(
                session_id, title or "Home Assistant", message, timeout * 1000 if timeout else None
            )
        )

    # ---- browsing -------------------------------------------------------------
    def _thumb(self, item_id: str, tag: str) -> str:
        return self.get_browse_image_url("image", item_id, tag)

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        try:
            return await async_browse(self._runtime.client, media_content_id, self._thumb)
        except JellyfinError as err:
            raise HomeAssistantError(f"Jellyfin browse failed: {err}") from err

    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        try:
            results = await async_search(self._runtime.client, query.search_query, self._thumb)
        except JellyfinError as err:
            raise HomeAssistantError(f"Jellyfin search failed: {err}") from err
        return SearchMedia(result=results)

    async def async_get_browse_image(
        self,
        media_content_type: str,
        media_content_id: str,
        media_image_id: str | None = None,
    ) -> tuple[bytes | None, str | None]:
        url = self._runtime.client.image_url(media_content_id, media_image_id, max_height=300)
        return await self._runtime.client.fetch_image(url)
