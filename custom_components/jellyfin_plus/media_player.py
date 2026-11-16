"""Media player per Jellyfin client device."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time
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
from .browse_media import async_browse, async_search, split_user
from .const import CONF_ADD_SESSION_USER, CONF_PEOPLE_FOLDER, TICKS_PER_SECOND
from .entity import (
    JellyfinDeviceEntity,
    async_setup_device_entities,
    command_error,
    supported_commands,
    supports_media_control,
)

from .sleep_timer import MODE_END_OF_ITEM, SleepTimer
from .users import resolve_user, user_map

if TYPE_CHECKING:
    from . import JellyfinConfigEntry

_LOGGER = logging.getLogger(__name__)

# Show the expected state right after a command until the app confirms it
OPTIMISTIC_SECONDS = 8
# A reported position this far off from our extrapolation counts as a real jump
POSITION_TOLERANCE = 2
# Resume protection: if an app restarts the item after "Unpause", seek back
RESUME_GUARD_SECONDS = 20
RESTART_THRESHOLD = 5
MIN_RESTORE_POSITION = 15

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
SERVICE_SLEEP_TIMER = "sleep_timer"
SERVICE_CANCEL_SLEEP_TIMER = "cancel_sleep_timer"
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
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SLEEP_TIMER,
        {
            vol.Exclusive("minutes", "when"): vol.All(
                vol.Coerce(float), vol.Range(min=0.1, max=720)
            ),
            vol.Exclusive("end_of_item", "when"): cv.boolean,
            vol.Optional("action", default="pause"): vol.In(["pause", "stop"]),
        },
        "async_set_sleep_timer",
    )
    platform.async_register_entity_service(
        SERVICE_CANCEL_SLEEP_TIMER, None, "async_cancel_sleep_timer"
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
        # Position (seconds) as of _position_updated_at; HA extrapolates while playing
        self._position: float | None = None
        self._position_updated_at: datetime | None = None
        self._last_item_id: str | None = None
        self._last_paused: bool | None = None
        self._last_ticks: int | float | None = None
        self._paused_override: bool | None = None
        self._override_until = 0.0
        # (item id, position, deadline) while watching for a restart after resume
        self._resume_guard: tuple[str, float, float] | None = None
        self._sleep: SleepTimer | None = None

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
            raise command_error(err, self._entry) from err

    async def _playstate(self, command: str, seek_ticks: int | None = None) -> None:
        session_id = self._session_id()
        _LOGGER.debug(
            "%s: -> %s%s (session %s)",
            self.entity_id,
            command,
            "" if seek_ticks is None else f" {seek_ticks / TICKS_PER_SECOND:.0f}s",
            session_id,
        )
        self._runtime.trace.add(
            "command",
            entity=self.entity_id,
            command=command,
            seek=None if seek_ticks is None else round(seek_ticks / TICKS_PER_SECOND, 2),
            session=session_id,
        )
        await self._run(self._runtime.client.playstate(session_id, command, seek_ticks))

    # ---- position / state bookkeeping --------------------------------------------
    @property
    def _paused(self) -> bool:
        if self._paused_override is not None and time.monotonic() < self._override_until:
            return self._paused_override
        return bool(self._play_state.get("IsPaused"))

    def _extrapolate(self, paused: bool | None) -> float | None:
        """Where playback is now, given the last anchor and play/pause state."""
        if self._position is None:
            return None
        if paused is False and self._position_updated_at is not None:
            elapsed = (dt_util.utcnow() - self._position_updated_at).total_seconds()
            return self._position + max(elapsed, 0)
        return self._position

    def _anchor(self, position: float | None) -> None:
        self._position = position
        self._position_updated_at = dt_util.utcnow() if position is not None else None

    @callback
    def _sync_from_session(self) -> None:
        item = self._item
        if not item:
            self._anchor(None)
            self._last_item_id = None
            self._last_paused = None
            self._last_ticks = None
            self._paused_override = None
            # Keep the resume guard: some players (e.g. Jellyfin Web) "resume" by
            # stopping and restarting the item, so it disappears for a moment.
            # The guard expires on its own and checks the item id.
            return
        play_state = self._play_state
        reported_paused = bool(play_state.get("IsPaused"))
        if self._paused_override is not None and (
            reported_paused == self._paused_override
            or time.monotonic() >= self._override_until
        ):
            self._paused_override = None

        ticks = play_state.get("PositionTicks")
        reported = ticks / TICKS_PER_SECOND if isinstance(ticks, (int, float)) else None
        item_changed = item.get("Id") != self._last_item_id
        # Jellyfin pushes the whole session list whenever *any* session changes,
        # repeating this player's position from its last own report. Only a
        # changed value is a fresh report; repeats are stale and must not move
        # the anchor (that made the position jump back).
        fresh = reported is not None and (item_changed or ticks != self._last_ticks)
        paused = self._paused
        expected = self._extrapolate(self._last_paused)
        deviation = (
            None if reported is None or expected is None else round(reported - expected, 2)
        )
        if reported is None:
            reason = "no position reported"
        elif item_changed or expected is None:
            self._anchor(reported)
            reason = "new item" if item_changed else "first position"
        elif paused != self._last_paused:
            # play/pause flipped: a fresh report is exact, else keep our estimate
            self._anchor(reported if fresh else expected)
            reason = "play/pause changed, " + ("took report" if fresh else "kept estimate")
        elif not fresh:
            reason = "stale repeat ignored"
        elif abs(reported - expected) > POSITION_TOLERANCE:
            self._anchor(reported)
            reason = f"corrected by {deviation:+}s"
        else:
            reason = "fresh report, within tolerance"
        if reported is not None:
            self._last_ticks = ticks

        shown = None if self._position is None else self._extrapolate(paused)
        _LOGGER.debug(
            "%s: <- item=%s paused=%s shown=%s reported=%s expected=%s -> %s (method %s, checkin %s)",
            self.entity_id,
            item.get("Name"),
            paused,
            None if shown is None else round(shown, 1),
            None if reported is None else round(reported, 1),
            None if expected is None else round(expected, 1),
            reason,
            play_state.get("PlayMethod"),
            (self.session or {}).get("LastPlaybackCheckIn"),
        )
        self._runtime.trace.add(
            "position",
            entity=self.entity_id,
            item=item.get("Name"),
            paused=paused,
            shown=None if shown is None else round(shown, 2),
            reported=None if reported is None else round(reported, 2),
            expected=None if expected is None else round(expected, 2),
            deviation=deviation,
            decision=reason,
        )
        self._last_item_id = item.get("Id")
        self._last_paused = paused
        if fresh:
            self._check_resume_guard(item.get("Id"), reported)

    @callback
    def _set_optimistic(self, paused: bool) -> None:
        if not self._item:
            return
        current = self._extrapolate(self._last_paused)
        self._paused_override = paused
        self._override_until = time.monotonic() + OPTIMISTIC_SECONDS
        if current is not None:
            self._anchor(current)
        self._last_paused = paused
        self.async_write_ha_state()

    def _check_resume_guard(self, item_id: str | None, reported: float) -> None:
        guard = self._resume_guard
        if guard is None:
            return
        guard_item, guard_position, deadline = guard
        if time.monotonic() > deadline or item_id != guard_item:
            self._resume_guard = None
        elif reported <= RESTART_THRESHOLD:
            self._resume_guard = None
            _LOGGER.warning(
                "%s restarted the item from the beginning after resume; seeking back to %ss",
                self.entity_id,
                round(guard_position),
            )
            self.hass.async_create_task(self._seek_back(guard_position))
        elif reported >= guard_position + 2:
            self._resume_guard = None  # resumed where it was: all good

    async def _seek_back(self, position: float) -> None:
        try:
            await self._playstate("Seek", int(position * TICKS_PER_SECOND))
        except HomeAssistantError as err:
            _LOGGER.warning("%s: could not seek back after restart: %s", self.entity_id, err)
            return
        self._anchor(position)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._runtime.players[self.entity_id] = self
        self.async_on_remove(lambda: self._runtime.players.pop(self.entity_id, None))
        self._sync_from_session()

    def debug_state(self) -> dict[str, Any]:
        """Internal state for the diagnostics download."""
        now = time.monotonic()
        guard = self._resume_guard
        return {
            "state": self.state,
            "session_id": (self.session or {}).get("Id"),
            "item": (self._item or {}).get("Name"),
            "anchor_position": self._position,
            "anchor_time": self._position_updated_at.isoformat()
            if self._position_updated_at
            else None,
            "shown_position_now": self._extrapolate(self._last_paused),
            "media_position": self.media_position,
            "media_position_updated_at": self.media_position_updated_at.isoformat()
            if self.media_position_updated_at
            else None,
            "last_reported": None
            if self._last_ticks is None
            else self._last_ticks / TICKS_PER_SECOND,
            "last_paused": self._last_paused,
            "paused_override": self._paused_override,
            "override_seconds_left": round(max(self._override_until - now, 0), 1)
            if self._paused_override is not None
            else None,
            "resume_guard": None
            if guard is None
            else {
                "item_id": guard[0],
                "position": guard[1],
                "seconds_left": round(max(guard[2] - now, 0), 1),
            },
        }

    @callback
    def _on_sessions(self) -> None:
        self._sync_from_session()
        self._update_sleep_timer()
        super()._on_sessions()

    # ---- sleep timer ------------------------------------------------------------
    def _remaining(self) -> float | None:
        position = self._extrapolate(self._paused)
        runtime = (self._item or {}).get("RunTimeTicks")
        if position is None or not runtime:
            return None
        return max(runtime / TICKS_PER_SECOND - position, 0)

    @callback
    def _update_sleep_timer(self) -> None:
        if self._sleep and self._sleep.mode == MODE_END_OF_ITEM:
            self._sleep.update(
                (self._item or {}).get("Id"),
                self._remaining(),
                self.state == MediaPlayerState.PLAYING,
            )

    async def async_set_sleep_timer(
        self,
        minutes: float | None = None,
        end_of_item: bool = False,
        action: str = "pause",
    ) -> None:
        """Pause/stop after some minutes, or when the current item ends."""
        if self._sleep is None:
            self._sleep = SleepTimer(self.hass, self._sleep_fire, self.async_write_ha_state)
        if end_of_item:
            item = self._item
            if not item:
                raise HomeAssistantError(f"Nothing is playing on {self.name or self.device_id}")
            self._sleep.start_end_of_item(
                item.get("Id"),
                self._remaining(),
                self.state == MediaPlayerState.PLAYING,
                action,
            )
        elif minutes:
            self._sleep.start_minutes(minutes, action)
        else:
            raise HomeAssistantError("Set either minutes or end_of_item")
        self._runtime.trace.add("sleep_timer", entity=self.entity_id, **self._sleep.attributes())

    async def async_cancel_sleep_timer(self) -> None:
        if self._sleep:
            self._sleep.cancel()

    async def _sleep_fire(self, action: str) -> None:
        if self.state not in (MediaPlayerState.PLAYING, MediaPlayerState.PAUSED):
            return
        if action == "stop":
            await self.async_media_stop()
        elif self.state == MediaPlayerState.PLAYING:
            await self.async_media_pause()

    async def async_will_remove_from_hass(self) -> None:
        if self._sleep:
            self._sleep.cancel(notify=False)
        await super().async_will_remove_from_hass()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self._sleep.attributes() if self._sleep and self._sleep.active else None

    # ---- state ------------------------------------------------------------
    @property
    def state(self) -> MediaPlayerState:
        if self.session is None:
            return MediaPlayerState.OFF
        if not self._item:
            return MediaPlayerState.IDLE
        if self._paused:
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
        return None if self._position is None else int(self._position)

    @property
    def media_position_updated_at(self) -> datetime | None:
        # media_position is a whole second; shift the reference time by the cut
        # fraction so HA's extrapolated position stays exact
        if self._position_updated_at is None or self._position is None:
            return self._position_updated_at
        return self._position_updated_at - timedelta(seconds=self._position % 1)

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
        position = self._extrapolate(self._last_paused)
        await self._playstate("Unpause")
        item = self._item
        if item and position is not None and position >= MIN_RESTORE_POSITION:
            self._resume_guard = (
                item.get("Id"),
                position,
                time.monotonic() + RESUME_GUARD_SECONDS,
            )
        self._set_optimistic(paused=False)

    async def async_media_pause(self) -> None:
        await self._playstate("Pause")
        self._set_optimistic(paused=True)

    async def async_media_play_pause(self) -> None:
        # Decided here instead of sending "PlayPause", so the shown state and
        # the resume protection stay consistent
        if self.state == MediaPlayerState.PLAYING:
            await self.async_media_pause()
        else:
            await self.async_media_play()

    async def async_media_stop(self) -> None:
        await self._playstate("Stop")

    async def async_media_next_track(self) -> None:
        await self._playstate("NextTrack")

    async def async_media_previous_track(self) -> None:
        await self._playstate("PreviousTrack")

    async def async_media_seek(self, position: float) -> None:
        await self._playstate("Seek", int(position * TICKS_PER_SECOND))
        self._anchor(position)
        self.async_write_ha_state()

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
        """Play a Jellyfin item; resumes where *the right person* left off.

        The person is the one picked in the People folder, else the linked
        Home Assistant user who triggered this, else the user logged in on
        the device.
        """
        session = self.session
        session_id = self._session_id()
        tagged_user, item_id = split_user(media_id)
        ha_user_id = self._context.user_id if self._context else None
        user_id = tagged_user or resolve_user(
            self._entry, ha_user_id=ha_user_id, session=session
        )
        command = _ENQUEUE.get(enqueue, "PlayNow") if enqueue else "PlayNow"
        start_ticks = None
        if command == "PlayNow":
            try:
                item = await self._runtime.client.item(item_id, user_id=user_id)
            except JellyfinError:
                item = None
            user_data = (item or {}).get("UserData") or {}
            ticks = user_data.get("PlaybackPositionTicks")
            if isinstance(ticks, int) and ticks > 0 and not user_data.get("Played"):
                start_ticks = ticks
        _LOGGER.debug(
            "%s: play %s for Jellyfin user %s (HA user %s, device user %s), start %s",
            self.entity_id,
            item_id,
            user_id,
            ha_user_id,
            (session or {}).get("UserId"),
            None if start_ticks is None else round(start_ticks / TICKS_PER_SECOND),
        )
        await self._maybe_add_session_user(session, session_id, user_id)
        await self._run(
            self._runtime.client.play_items(session_id, [item_id], command, start_ticks)
        )

    async def _maybe_add_session_user(
        self, session: dict[str, Any] | None, session_id: str, user_id: str | None
    ) -> None:
        """Shared device: also save progress for the person who started this."""
        if not user_id or not self._entry.options.get(CONF_ADD_SESSION_USER):
            return
        session = session or {}
        already = {session.get("UserId")} | {
            u.get("UserId") for u in session.get("AdditionalUsers") or []
        }
        if user_id in already:
            return
        try:
            await self._runtime.client.add_session_user(session_id, user_id)
        except JellyfinError as err:
            _LOGGER.warning(
                "%s: could not add Jellyfin user %s to the session: %s",
                self.entity_id,
                user_id,
                err,
            )

    async def async_send_message(
        self, message: str, title: str = "", timeout: int = 10, pause: bool = False
    ) -> None:
        """Show a message on the client's screen, optionally pausing playback."""
        session_id = self._session_id()
        if pause and self.state == MediaPlayerState.PLAYING:
            await self.async_media_pause()
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
        people = None
        if self._entry.options.get(CONF_PEOPLE_FOLDER):
            names = self._runtime.users
            people = {jf: names.get(jf, jf) for jf in sorted(set(user_map(self._entry).values()))}
        try:
            return await async_browse(
                self._runtime.client,
                media_content_id,
                self._thumb,
                user_id=self._device_user,
                people=people,
            )
        except JellyfinError as err:
            raise HomeAssistantError(f"Jellyfin browse failed: {err}") from err

    @property
    def _device_user(self) -> str | None:
        """Jellyfin user logged in on this device (browse/search default)."""
        return (self.session or {}).get("UserId")

    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        try:
            results = await async_search(
                self._runtime.client,
                query.search_query,
                self._thumb,
                user_id=self._device_user,
            )
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
