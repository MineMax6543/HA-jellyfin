"""Audio and subtitle track of what a Jellyfin app is playing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import JellyfinError
from .entity import (
    JellyfinDeviceEntity,
    async_setup_device_entities,
    command_error,
    supported_commands,
)

if TYPE_CHECKING:
    from . import JellyfinConfigEntry

SUBTITLES_OFF = "Off"

# kind -> (stream Type, PlayState field, command, unique id suffix)
KINDS = {
    "audio_track": ("Audio", "AudioStreamIndex", "SetAudioStreamIndex", "_audio_track"),
    "subtitle_track": (
        "Subtitle",
        "SubtitleStreamIndex",
        "SetSubtitleStreamIndex",
        "_subtitle_track",
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    for kind, (_type, _field, command, suffix) in KINDS.items():
        async_setup_device_entities(
            hass,
            entry,
            async_add_entities,
            "select",
            lambda session, command=command: command in supported_commands(session),
            lambda device_id, name, client, kind=kind: JellyfinTrackSelect(
                entry, runtime, device_id, name, client, kind=kind
            ),
            suffix=suffix,
        )


def track_options(streams: list[dict[str, Any]], stream_type: str) -> dict[str, int]:
    """Option label -> stream index, labels made unique."""
    options: dict[str, int] = {}
    for stream in streams:
        if stream.get("Type") != stream_type or not isinstance(stream.get("Index"), int):
            continue
        label = stream.get("DisplayTitle") or stream.get("Language") or f"Track {stream['Index']}"
        if label in options:
            label = f"{label} (#{stream['Index']})"
        options[label] = stream["Index"]
    return options


class JellyfinTrackSelect(JellyfinDeviceEntity, SelectEntity):
    """Pick the audio or subtitle track of the playing item."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, *args: Any, kind: str) -> None:
        super().__init__(*args)
        self._kind = kind
        self._type, self._field, self._command, suffix = KINDS[kind]
        self._attr_unique_id = f"{self._attr_unique_id}{suffix}"
        self._attr_translation_key = kind

    def _choices(self) -> dict[str, int]:
        item = (self.session or {}).get("NowPlayingItem") or {}
        choices = track_options(item.get("MediaStreams") or [], self._type)
        if self._type == "Subtitle" and choices:
            choices = {SUBTITLES_OFF: -1, **choices}
        return choices

    @property
    def available(self) -> bool:
        # Only meaningful while something with tracks is playing
        return super().available and bool(self._choices())

    @property
    def options(self) -> list[str]:
        return list(self._choices())

    @property
    def current_option(self) -> str | None:
        index = ((self.session or {}).get("PlayState") or {}).get(self._field)
        if index is None:
            return None
        for label, idx in self._choices().items():
            if idx == index:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        choices = self._choices()
        session = self.session
        if option not in choices or not session or not session.get("Id"):
            raise HomeAssistantError(f"Track {option} is not available")
        try:
            await self._runtime.client.general_command(
                session["Id"], self._command, {"Index": choices[option]}
            )
        except JellyfinError as err:
            raise command_error(err, self._entry) from err
