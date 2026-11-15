"""Diagnostics download and position trace."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.jellyfin_plus.api import JellyfinClient
from custom_components.jellyfin_plus.const import DOMAIN
from custom_components.jellyfin_plus.diagnostics import async_get_config_entry_diagnostics

from .test_init import DATA
from .test_playback_control import PLAYER, at


async def test_diagnostics(hass: HomeAssistant, fake_listener, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.jellyfin_plus")
    entry = MockConfigEntry(domain=DOMAIN, unique_id="srv1", data=DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    listener = fake_listener.instances[0]
    session = {**at(120), "UserName": "max", "UserId": "u1", "RemoteEndPoint": "192.168.1.5"}
    listener.push([session])
    listener.push([session])  # stale repeat
    await hass.async_block_till_done()
    with patch.object(JellyfinClient, "playstate", AsyncMock()):
        await hass.services.async_call(
            "media_player", "media_pause", {"entity_id": PLAYER}, blocking=True
        )

    diag = await async_get_config_entry_diagnostics(hass, entry)
    json.dumps(diag)  # must be serialisable

    # secrets and personal data are redacted
    assert diag["entry"]["token"] == "**REDACTED**"
    assert diag["entry"]["user_id"] == "**REDACTED**"
    s = diag["sessions"]["atv"]
    assert s["UserName"] == "**REDACTED**"
    assert s["RemoteEndPoint"] == "**REDACTED**"
    assert "max" not in json.dumps(diag["trace"])

    player = diag["players"][PLAYER]
    assert player["state"] == "paused"
    assert player["paused_override"] is True
    assert player["anchor_position"] is not None

    kinds = [e["kind"] for e in diag["trace"]]
    assert {"connection", "sessions", "position", "command"} <= set(kinds)
    decisions = [e["decision"] for e in diag["trace"] if e["kind"] == "position"]
    assert decisions[:2] == ["new item", "stale repeat ignored"]
    command = next(e for e in diag["trace"] if e["kind"] == "command")
    assert command["command"] == "Pause"

    # the debug log line explains the decision too
    assert "-> stale repeat ignored" in caplog.text
