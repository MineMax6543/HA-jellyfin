"""Experimental multi-user support."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.jellyfin_plus.api import JellyfinClient
from custom_components.jellyfin_plus.browse_media import async_browse
from custom_components.jellyfin_plus.const import DOMAIN
from custom_components.jellyfin_plus.users import resolve_user

from .test_init import DATA
from .test_media_player import atv
from .test_sessions import MOVIE

PLAYER = "media_player.living_room_apple_tv"


async def _setup(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="srv1", data=DATA, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def people(hass: HomeAssistant):
    max_ = await hass.auth.async_create_user("Max", group_ids=["system-users"])
    kai = await hass.auth.async_create_user("Kai", group_ids=["system-users"])
    return max_, kai


async def test_resolution_order(hass: HomeAssistant, fake_listener, people) -> None:
    max_, kai = people
    entry = await _setup(hass, {"user_map": {kai.id: "u2"}})
    device_session = {"UserId": "u1"}
    assert resolve_user(entry, explicit="KAI") == "u2"  # by name, any case
    assert resolve_user(entry, ha_user_id=kai.id, session=device_session) == "u2"
    assert resolve_user(entry, ha_user_id=max_.id, session=device_session) == "u1"  # not linked
    assert resolve_user(entry) == "u1"  # login user (DATA user_id)


async def test_options_flow_links_users(hass: HomeAssistant, fake_listener, people) -> None:
    max_, kai = people
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    fields = [str(k) for k in result["data_schema"].schema]
    assert {"Max", "Kai", "people_folder", "add_session_user"} <= set(fields)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"Max": "u1", "Kai": "u2", "people_folder": True, "add_session_user": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["user_map"] == {max_.id: "u1", kai.id: "u2"}

    # per-person entities after the reload
    reg = er.async_get(hass)
    for platform, key in [("sensor", "next_up"), ("sensor", "watching"), ("calendar", "upcoming")]:
        assert reg.async_get_entity_id(platform, DOMAIN, f"srv1_person_u2_{key}"), key


async def test_play_resumes_for_triggering_person(
    hass: HomeAssistant, fake_listener, people
) -> None:
    _max, kai = people
    await _setup(hass, {"user_map": {kai.id: "u2"}, "add_session_user": True})
    fake_listener.instances[0].push([{**atv(), "UserId": "u1"}])  # Apple TV logged in as max
    await hass.async_block_till_done()

    kai_progress = {"Id": "m1", "UserData": {"PlaybackPositionTicks": 6_000_000_000}}
    with (
        patch.object(JellyfinClient, "item", AsyncMock(return_value=kai_progress)) as item,
        patch.object(JellyfinClient, "add_session_user", AsyncMock()) as add_user,
        patch.object(JellyfinClient, "play_items", AsyncMock()) as play,
    ):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": PLAYER, "media_content_type": "movie", "media_content_id": "m1"},
            blocking=True,
            context=Context(user_id=kai.id),
        )
    assert item.call_args.kwargs["user_id"] == "u2"  # Kai's progress, not the device's
    add_user.assert_awaited_once_with("s1", "u2")  # Kai's progress gets saved too
    assert play.call_args.args == ("s1", ["m1"], "PlayNow", 6_000_000_000)


async def test_play_without_link_uses_device_user(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass, {"add_session_user": True})
    fake_listener.instances[0].push([{**atv(), "UserId": "u2"}])
    await hass.async_block_till_done()
    with (
        patch.object(JellyfinClient, "item", AsyncMock(return_value={})) as item,
        patch.object(JellyfinClient, "add_session_user", AsyncMock()) as add_user,
        patch.object(JellyfinClient, "play_items", AsyncMock()),
    ):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": PLAYER, "media_content_type": "movie", "media_content_id": "m1"},
            blocking=True,
        )
    assert item.call_args.kwargs["user_id"] == "u2"
    add_user.assert_not_awaited()  # it's already that user's session


class PeopleClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def user_views(self, user_id=None):
        self.calls.append(("views", user_id))
        return []

    async def resume_items(self, user_id=None):
        self.calls.append(("resume", user_id))
        return [{**MOVIE, "MediaType": "Video"}]

    async def next_up(self, user_id=None):
        return []


async def test_people_folder() -> None:
    client = PeopleClient()
    thumb = lambda item_id, tag: ""  # noqa: E731
    root = await async_browse(client, None, thumb, user_id="u1", people={"u2": "kai"})
    assert "People" in [c.title for c in root.children]
    assert client.calls[0] == ("views", "u1")  # device user by default

    folder = await async_browse(client, "people", thumb, people={"u2": "kai"})
    assert [c.media_content_id for c in folder.children] == ["person:u2"]
    resume = await async_browse(client, "person:u2:resume", thumb, people={"u2": "kai"})
    assert resume.title == "kai: Continue watching"
    assert resume.children[0].media_content_id == "u:u2:m1"  # tagged with the person
    assert client.calls[-1] == ("resume", "u2")


async def test_mark_played_for_person(hass: HomeAssistant, fake_listener, people) -> None:
    _max, kai = people
    await _setup(hass, {"user_map": {kai.id: "u2"}})
    with patch.object(JellyfinClient, "set_played", AsyncMock()) as played:
        await hass.services.async_call(
            DOMAIN, "mark_played", {"item_id": "m1"}, blocking=True, context=Context(user_id=kai.id)
        )
        await hass.services.async_call(
            DOMAIN, "mark_unplayed", {"item_id": "u:u1:m2"}, blocking=True
        )
        await hass.services.async_call(
            DOMAIN, "mark_played", {"item_id": "m3", "jellyfin_user": "max"}, blocking=True
        )
    calls = [(c.args, c.kwargs["user_id"]) for c in played.call_args_list]
    assert calls == [(("m1", True), "u2"), (("m2", False), "u1"), (("m3", True), "u1")]


async def test_watching_sensor_and_event_ha_user(
    hass: HomeAssistant, fake_listener, people
) -> None:
    _max, kai = people
    events = async_capture_events(hass, "jellyfin_plus_playback")
    await _setup(hass, {"user_map": {kai.id: "u2"}})
    fake_listener.instances[0].push([{**atv(item=MOVIE), "UserId": "u2"}])
    await hass.async_block_till_done()

    watching = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "srv1_person_u2_watching")
    state = hass.states.get(watching)
    assert state.state == "Dune"
    assert state.attributes["streams"][0]["device"] == "Living Room Apple TV"
    assert events[0].data["ha_user_id"] == kai.id



async def test_api_key_entry_is_admin_and_can_change_library_user(
    hass: HomeAssistant, fake_listener
) -> None:
    from custom_components.jellyfin_plus.api import JellyfinClient as Client

    data = {**DATA, "auth_type": "api_key", "is_admin": False}
    entry = MockConfigEntry(domain=DOMAIN, unique_id="srv1", data=data)
    entry.add_to_hass(hass)
    with patch.object(Client, "me", AsyncMock(side_effect=AssertionError("no /Users/Me"))):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.runtime_data.is_admin is True  # API keys may do everything
        assert entry.data["is_admin"] is True

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert "library_user" in [str(k) for k in result["data_schema"].schema]
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"library_user": "u2"}
        )
        await hass.async_block_till_done()
    assert entry.data["user_id"] == "u2"
    assert entry.data["username"] == "kai"
