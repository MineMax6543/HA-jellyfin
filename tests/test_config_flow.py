"""Config flow tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.jellyfin_plus.api import CannotConnect, InvalidAuth
from custom_components.jellyfin_plus.const import DOMAIN

LOGIN = {
    "AccessToken": "tok",
    "ServerId": "srv1",
    "User": {"Id": "u1", "Name": "max", "Policy": {"IsAdministrator": True}},
}
PATCH_AUTH = "custom_components.jellyfin_plus.config_flow.async_authenticate"


@pytest.fixture(autouse=True)
def no_setup():
    with patch("custom_components.jellyfin_plus.async_setup_entry", return_value=True):
        yield


async def _start(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with patch(PATCH_AUTH, AsyncMock(return_value=LOGIN)) as auth:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"url": " 192.168.1.10:8096/ ", "username": "max", "password": "pw"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Jellyfin (max)"
    data = result["data"]
    assert data["url"] == "http://192.168.1.10:8096"
    assert data["token"] == "tok"
    assert data["is_admin"] is True
    assert "password" not in data  # only the token is stored
    assert result["result"].unique_id == "srv1"
    assert auth.call_args.args[1] == "http://192.168.1.10:8096"


@pytest.mark.parametrize(
    ("error", "key"),
    [(InvalidAuth("401"), "invalid_auth"), (CannotConnect("x"), "cannot_connect")],
)
async def test_user_flow_errors(hass: HomeAssistant, error, key) -> None:
    result = await _start(hass)
    with patch(PATCH_AUTH, AsyncMock(side_effect=error)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "http://j:8096", "username": "max", "password": "x"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}


async def test_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="srv1", data={"url": "http://old:8096"}).add_to_hass(hass)
    result = await _start(hass)
    with patch(PATCH_AUTH, AsyncMock(return_value=LOGIN)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "http://j:8096", "username": "max", "password": "pw"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="srv1",
        data={"url": "http://j:8096", "username": "max", "device_id": "d", "token": "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(PATCH_AUTH, AsyncMock(return_value={**LOGIN, "AccessToken": "new"})):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "pw"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["token"] == "new"
