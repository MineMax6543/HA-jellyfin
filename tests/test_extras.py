"""B + C: segments, TV messages, library/activity events, sensors, calendar,
updates and buttons."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import homeassistant.util.dt as dt_util

from custom_components.jellyfin_plus import segments as segments_module
from custom_components.jellyfin_plus.api import JellyfinClient
from custom_components.jellyfin_plus.const import DOMAIN

from .test_init import DATA
from .test_media_player import atv
from .test_sessions import MOVIE

SEGMENTS = [
    {"Type": "Intro", "StartTicks": 0, "EndTicks": 300_000_000},  # 0-30 s
    {"Type": "Outro", "StartTicks": 1_000_000_000, "EndTicks": 2_000_000_000},  # 100-200 s
]


async def _setup(hass: HomeAssistant, admin: bool = True) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="srv1", data={**DATA, "is_admin": admin})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _eid(hass: HomeAssistant, domain: str, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"srv1_{key}")


def _at(seconds: float) -> dict:
    return atv(item=MOVIE, PositionTicks=int(seconds * 10_000_000))


@pytest.fixture
def clock(monkeypatch):
    """Controllable monotonic clock for position extrapolation."""
    now = [1000.0]
    monkeypatch.setattr(segments_module.time, "monotonic", lambda: now[0])
    return now


async def test_credits_and_intro_events(hass: HomeAssistant, fake_listener, clock) -> None:
    events = async_capture_events(hass, "jellyfin_plus_playback")
    with patch.object(JellyfinClient, "media_segments", AsyncMock(return_value=SEGMENTS)):
        await _setup(hass)
        listener = fake_listener.instances[0]
        listener.push([_at(10)])  # inside the intro
        await hass.async_block_till_done()
        actions = [e.data["action"] for e in events]
        assert actions == ["playback_started", "intro_started"]

        listener.push([_at(40)])  # left the intro
        await hass.async_block_till_done()
        assert events[-1].data["action"] == "intro_ended"

        # 95 s, still playing: the timer must fire credits_started at 100 s
        # without any new position report from the client
        listener.push([_at(95)])
        await hass.async_block_till_done()
        clock[0] += 6
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done()
        assert events[-1].data["action"] == "credits_started"
        assert events[-1].data["segment"] == "credits"
        assert events[-1].data["segment_start"] == 100

    client = er.async_get(hass).async_get_entity_id("event", DOMAIN, "srv1_atv")
    assert hass.states.get(client).attributes["event_type"] == "credits_started"


async def test_paused_does_not_extrapolate(hass: HomeAssistant, fake_listener, clock) -> None:
    events = async_capture_events(hass, "jellyfin_plus_playback")
    with patch.object(JellyfinClient, "media_segments", AsyncMock(return_value=SEGMENTS)):
        await _setup(hass)
        fake_listener.instances[0].push([atv(item=MOVIE, paused=True, PositionTicks=950_000_000)])
        await hass.async_block_till_done()
        clock[0] += 60
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
        await hass.async_block_till_done()
    assert "credits_started" not in [e.data["action"] for e in events]


async def test_send_message(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    fake_listener.instances[0].push([atv(item=MOVIE)])
    await hass.async_block_till_done()
    with (
        patch.object(JellyfinClient, "playstate", AsyncMock()) as playstate,
        patch.object(JellyfinClient, "send_message", AsyncMock()) as message,
    ):
        await hass.services.async_call(
            DOMAIN,
            "send_message",
            {"entity_id": "media_player.living_room_apple_tv", "message": "Doorbell!",
             "title": "Front door", "timeout": 8, "pause": True},
            blocking=True,
        )
    assert playstate.call_args.args == ("s1", "Pause", None)
    assert message.call_args.args == ("s1", "Front door", "Doorbell!", 8000)


async def test_library_and_activity_events(hass: HomeAssistant, fake_listener) -> None:
    library_events = async_capture_events(hass, "jellyfin_plus_library")
    activity_events = async_capture_events(hass, "jellyfin_plus_activity")
    await _setup(hass)
    listener = fake_listener.instances[0]
    assert listener.subscriptions == ["ScheduledTasksInfoStart", "ActivityLogEntryStart"]

    added = [
        {**MOVIE, "ImageTags": {"Primary": "p1"}},
        {"Id": "season1", "Name": "Season 1", "Type": "Season"},  # not announced
    ]
    with patch.object(JellyfinClient, "items", AsyncMock(return_value=added)) as items:
        listener.message("LibraryChanged", {"ItemsAdded": ["m1", "season1"]})
        await hass.async_block_till_done()
    assert items.call_args.kwargs["ids"] == "m1,season1"
    assert len(library_events) == 1
    assert library_events[0].data["title"] == "Dune"
    assert library_events[0].data["image_url"].endswith("tag=p1")
    library = _eid(hass, "event", "library")
    assert hass.states.get(library).attributes["event_type"] == "item_added"

    listener.message(
        "ActivityLogEntry",
        [
            {"Type": "AuthenticationFailed", "Name": "Failed login from 10.0.0.9",
             "Severity": "Error"},
            {"Type": "SessionStarted", "Name": "max is online"},
        ],
    )
    await hass.async_block_till_done()
    assert [e.data["type"] for e in activity_events] == ["AuthenticationFailed", "SessionStarted"]
    activity = _eid(hass, "event", "activity")
    state = hass.states.get(activity)
    assert state.attributes["event_type"] == "login_failed"  # SessionStarted kept off
    assert state.attributes["name"] == "Failed login from 10.0.0.9"


async def test_sensors(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    listener = fake_listener.instances[0]
    transcoding = {**atv(session_id="s5", item=MOVIE), "DeviceId": "ipad", "DeviceName": "iPad",
                   "TranscodingInfo": {"Bitrate": 8_000_000}}
    idle = {**atv(session_id="s6"), "DeviceId": "web", "DeviceName": "Firefox"}
    listener.push([atv(item=MOVIE), transcoding, idle])
    listener.message("ScheduledTasksInfo", [
        {"Name": "Scan Media Library", "State": "Running", "CurrentProgressPercentage": 41.6},
        {"Name": "Clean Cache", "State": "Idle"},
    ])
    await hass.async_block_till_done()

    def value(key):
        return hass.states.get(_eid(hass, "sensor", key))

    assert value("active_streams").state == "2"
    assert value("transcoding_streams").state == "1"
    assert value("connected_clients").state == "3"
    assert float(value("streaming_bandwidth").state) == pytest.approx(8.0)
    tasks = value("running_tasks")
    assert tasks.state == "1"
    assert tasks.attributes["tasks"] == [{"name": "Scan Media Library", "progress": 42}]
    assert value("next_up").state == "1"
    assert value("next_up").attributes["episodes"][0]["series"] == "Severance"
    movies = value("storage_lib-movies")
    assert float(movies.state) == pytest.approx(2000.0)  # GB
    assert movies.attributes["used_percent"] == 75.0
    assert value("storage_server_data") is not None


async def test_calendar(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    calendar = _eid(hass, "calendar", "upcoming")
    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": calendar, "start_date_time": "2026-11-01T00:00:00",
         "end_date_time": "2026-12-01T00:00:00"},
        blocking=True,
        return_response=True,
    )
    events = result[calendar]["events"]
    assert len(events) == 1
    assert events[0]["summary"] == "Severance S02E05 – The Next One"


async def test_updates_and_buttons(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    server = hass.states.get(_eid(hass, "update", "server_update"))
    assert server.state == "on"
    assert server.attributes["installed_version"] == "10.11.3"
    assert server.attributes["latest_version"] == "10.11.5"

    plugin_id = _eid(hass, "update", "plugin_intro-skipper-guid")
    plugin = hass.states.get(plugin_id)
    assert plugin.attributes["latest_version"] == "1.10.11.4"  # 2.0 needs newer Jellyfin
    assert _eid(hass, "update", "plugin_local-only") is None  # not in the catalog
    with patch.object(JellyfinClient, "install_package", AsyncMock()) as install:
        await hass.services.async_call(
            "update", "install", {"entity_id": plugin_id}, blocking=True
        )
    assert install.call_args.args == (
        "Intro Skipper", "intro-skipper-guid", "1.10.11.4", "https://repo.example"
    )

    with patch.object(JellyfinClient, "library_refresh", AsyncMock()) as scan:
        await hass.services.async_call(
            "button", "press", {"entity_id": _eid(hass, "button", "library_scan")}, blocking=True
        )
    scan.assert_awaited_once()


async def test_non_admin_gets_no_admin_entities(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass, admin=False)
    assert fake_listener.instances[0].subscriptions == []
    for domain, key in [
        ("button", "library_scan"),
        ("button", "restart"),
        ("update", "server_update"),
        ("event", "activity"),
        ("sensor", "running_tasks"),
        ("sensor", "storage_lib-movies"),
    ]:
        assert _eid(hass, domain, key) is None, key
    # everyone gets these
    assert _eid(hass, "sensor", "active_streams") is not None
    assert _eid(hass, "calendar", "upcoming") is not None
    assert _eid(hass, "event", "library") is not None
