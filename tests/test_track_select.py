"""Audio and subtitle track selects."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.jellyfin_plus.api import JellyfinClient
from custom_components.jellyfin_plus.const import DOMAIN

from .test_media_player import COMMANDS, _setup, atv
from .test_sessions import MOVIE

STREAMS = [
    {"Type": "Video", "Index": 0, "DisplayTitle": "4K HEVC"},
    {"Type": "Audio", "Index": 1, "DisplayTitle": "English - Dolby Atmos"},
    {"Type": "Audio", "Index": 2, "DisplayTitle": "German - AC3 5.1"},
    {"Type": "Subtitle", "Index": 3, "DisplayTitle": "English - SRT"},
    {"Type": "Subtitle", "Index": 4, "DisplayTitle": "English - SRT"},
]
TRACK_COMMANDS = [*COMMANDS, "SetAudioStreamIndex", "SetSubtitleStreamIndex"]


def _session(**play_state):
    return {
        **atv(item={**MOVIE, "MediaStreams": STREAMS}, **play_state),
        "SupportedCommands": TRACK_COMMANDS,
    }


async def test_track_selects(hass: HomeAssistant, fake_listener) -> None:
    entry = await _setup(hass)
    listener = fake_listener.instances[0]
    listener.push([_session(AudioStreamIndex=1, SubtitleStreamIndex=-1)])
    await hass.async_block_till_done()

    reg = er.async_get(hass)
    audio = reg.async_get_entity_id("select", DOMAIN, "srv1_atv_audio_track")
    subs = reg.async_get_entity_id("select", DOMAIN, "srv1_atv_subtitle_track")
    assert audio == "select.living_room_apple_tv_audio_track"
    state = hass.states.get(audio)
    assert state.state == "English - Dolby Atmos"
    assert state.attributes["options"] == ["English - Dolby Atmos", "German - AC3 5.1"]
    state = hass.states.get(subs)
    assert state.state == "Off"
    assert state.attributes["options"] == ["Off", "English - SRT", "English - SRT (#4)"]

    with patch.object(JellyfinClient, "general_command", AsyncMock()) as command:
        await hass.services.async_call(
            "select", "select_option",
            {"entity_id": audio, "option": "German - AC3 5.1"}, blocking=True,
        )
        await hass.services.async_call(
            "select", "select_option",
            {"entity_id": subs, "option": "English - SRT (#4)"}, blocking=True,
        )
    assert [c.args for c in command.call_args_list] == [
        ("s1", "SetAudioStreamIndex", {"Index": 2}),
        ("s1", "SetSubtitleStreamIndex", {"Index": 4}),
    ]

    # nothing playing -> unavailable; entities survive a reload
    listener.push([{**atv(), "SupportedCommands": TRACK_COMMANDS}])
    await hass.async_block_till_done()
    assert hass.states.get(audio).state == "unavailable"
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get_entity_id("select", DOMAIN, "srv1_atv_audio_track") == audio
    assert len([e for e in er.async_entries_for_config_entry(reg, entry.entry_id)
                if e.domain == "select"]) == 2


async def test_no_select_without_track_commands(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    fake_listener.instances[0].push([atv(item=MOVIE)])
    await hass.async_block_till_done()
    assert not hass.states.async_entity_ids("select")
