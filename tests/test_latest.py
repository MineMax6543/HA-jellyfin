"""Recently added sensor."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.jellyfin_plus.const import DOMAIN

from .test_media_player import _setup


async def test_recently_added(hass: HomeAssistant, fake_listener) -> None:
    await _setup(hass)
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "srv1_latest")
    state = hass.states.get(entity_id)
    assert state.state == "Severance S01E07"
    first, second = state.attributes["items"]
    assert first["item_id"] == "ep9"
    assert first["added"] == "2026-11-16T08:00:00Z"
    assert "/Items/sev/Images/Primary" in first["image_url"]  # series poster
    assert second["label"] == "Arrival"
    assert second["year"] == 2016
