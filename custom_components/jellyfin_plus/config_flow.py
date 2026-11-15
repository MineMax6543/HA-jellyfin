"""Config flow for Jellyfin Plus."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CannotConnect, InvalidAuth, async_authenticate, normalize_url
from .const import (
    CONF_DEVICE_ID,
    CONF_IS_ADMIN,
    CONF_SERVER_ID,
    CONF_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
    }
)


class JellyfinPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Log in to a Jellyfin server."""

    VERSION = 1

    async def _login(
        self, url: str, username: str, password: str, device_id: str
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        try:
            result = await async_authenticate(
                async_get_clientsession(self.hass), url, username, password, device_id
            )
        except InvalidAuth:
            return None, {"base": "invalid_auth"}
        except CannotConnect:
            return None, {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error logging in to Jellyfin")
            return None, {"base": "unknown"}
        return result, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            device_id = uuid.uuid4().hex
            result, errors = await self._login(
                url, user_input[CONF_USERNAME], user_input[CONF_PASSWORD], device_id
            )
            if result is not None:
                server_id = result.get("ServerId")
                if server_id:
                    await self.async_set_unique_id(server_id)
                    self._abort_if_unique_id_configured(updates={CONF_URL: url})
                user = result.get("User") or {}
                policy = user.get("Policy") or {}
                return self.async_create_entry(
                    title=f"Jellyfin ({user.get('Name') or user_input[CONF_USERNAME]})",
                    data={
                        CONF_URL: url,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_DEVICE_ID: device_id,
                        CONF_TOKEN: result["AccessToken"],
                        CONF_USER_ID: user.get("Id"),
                        CONF_SERVER_ID: server_id,
                        CONF_IS_ADMIN: bool(policy.get("IsAdministrator")),
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            result, errors = await self._login(
                entry.data[CONF_URL],
                entry.data[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                entry.data[CONF_DEVICE_ID],
            )
            if result is not None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: result["AccessToken"]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_PASSWORD, default=""): str}),
            errors=errors,
            description_placeholders={
                "username": entry.data[CONF_USERNAME],
                "url": entry.data[CONF_URL],
            },
        )
