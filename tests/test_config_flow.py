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


async def _start(hass, method: str = "password"):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": method}
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


PATCH_INFO = "custom_components.jellyfin_plus.config_flow.async_get_system_info"
PATCH_USERS = "custom_components.jellyfin_plus.config_flow.async_get_users"
USERS = [{"Id": "u1", "Name": "max"}, {"Id": "u2", "Name": "kai"}]


async def test_api_key_flow(hass: HomeAssistant) -> None:
    result = await _start(hass, "api_key")
    assert result["step_id"] == "api_key"
    with (
        patch(PATCH_INFO, AsyncMock(return_value={"Id": "srv1", "ServerName": "Media Server"})),
        patch(PATCH_USERS, AsyncMock(return_value=USERS)) as users,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "jelly.local:8096/", "api_key": " key123 "}
        )
    assert result["step_id"] == "api_user"
    assert users.call_args.args[2] == "key123"  # stripped key used as token
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"user_id": "u2"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Jellyfin (Media Server)"
    data = result["data"]
    assert data["auth_type"] == "api_key"
    assert data["token"] == "key123"
    assert data["user_id"] == "u2"
    assert data["username"] == "kai"
    assert data["is_admin"] is True
    assert data["url"] == "http://jelly.local:8096"
    assert result["result"].unique_id == "srv1"


async def test_api_key_rejected(hass: HomeAssistant) -> None:
    result = await _start(hass, "api_key")
    with patch(PATCH_INFO, AsyncMock(side_effect=InvalidAuth("401"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "http://j:8096", "api_key": "bad"}
        )
    assert result["errors"] == {"base": "invalid_api_key"}


async def test_reauth_api_key(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="srv1",
        data={"url": "http://j:8096", "username": "kai", "device_id": "d",
              "token": "old", "auth_type": "api_key"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_api_key"
    with (
        patch(PATCH_INFO, AsyncMock(return_value={"Id": "srv1"})),
        patch(PATCH_USERS, AsyncMock(return_value=USERS)),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"api_key": "new-key"}
        )
    assert result["reason"] == "reauth_successful"
    assert entry.data["token"] == "new-key"
