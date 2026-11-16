"""Config flow for Jellyfin Plus."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    CannotConnect,
    Forbidden,
    InvalidAuth,
    async_authenticate,
    async_get_system_info,
    async_get_users,
    normalize_url,
)
from .const import (
    AUTH_API_KEY,
    AUTH_PASSWORD,
    CONF_API_KEY,
    CONF_AUTH_TYPE,
    CONF_ADD_SESSION_USER,
    CONF_PEOPLE_FOLDER,
    CONF_USER_MAP,
    CONF_DEVICE_ID,
    CONF_IS_ADMIN,
    CONF_SERVER_ID,
    CONF_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

API_KEY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_API_KEY): str,
    }
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_USERNAME): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
    }
)


NOT_LINKED = "__none__"
LIBRARY_USER = "library_user"


def _user_selector(names: dict[str, str]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=user_id, label=name)
                for user_id, name in sorted(names.items(), key=lambda i: i[1].casefold())
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


class JellyfinPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Log in to a Jellyfin server."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return JellyfinPlusOptionsFlow()

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
        """Choose how to log in."""
        return self.async_show_menu(step_id="user", menu_options=["password", "api_key"])

    async def async_step_password(
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
                        CONF_AUTH_TYPE: AUTH_PASSWORD,
                    },
                )
        return self.async_show_form(
            step_id="password",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input or {}),
            errors=errors,
        )

    # ---- API key ---------------------------------------------------------------
    async def _check_api_key(
        self, url: str, api_key: str, device_id: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, str]]:
        session = async_get_clientsession(self.hass)
        try:
            info = await async_get_system_info(session, url, api_key, device_id)
            users = await async_get_users(session, url, api_key, device_id)
        except (InvalidAuth, Forbidden):
            return None, [], {"base": "invalid_api_key"}
        except CannotConnect:
            return None, [], {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error checking the Jellyfin API key")
            return None, [], {"base": "unknown"}
        if not users:
            return None, [], {"base": "no_users"}
        return info, users, {}

    async def async_step_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in with an API key (Jellyfin dashboard -> API Keys)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            api_key = user_input[CONF_API_KEY].strip()
            device_id = uuid.uuid4().hex
            info, users, errors = await self._check_api_key(url, api_key, device_id)
            if info is not None:
                server_id = info.get("Id")
                if server_id:
                    await self.async_set_unique_id(server_id)
                    self._abort_if_unique_id_configured(updates={CONF_URL: url})
                self._api_login = {
                    "url": url,
                    "api_key": api_key,
                    "device_id": device_id,
                    "server_id": server_id,
                    "server_name": info.get("ServerName") or "Jellyfin",
                    "users": users,
                }
                return await self.async_step_api_user()
        return self.async_show_form(
            step_id="api_key",
            data_schema=self.add_suggested_values_to_schema(API_KEY_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_api_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the Jellyfin user the integration uses for its own library data."""
        login = self._api_login
        names = {u["Id"]: u.get("Name") or u["Id"] for u in login["users"] if u.get("Id")}
        if user_input is not None:
            user_id = user_input[CONF_USER_ID]
            return self.async_create_entry(
                title=f"Jellyfin ({login['server_name']})",
                data={
                    CONF_URL: login["url"],
                    CONF_USERNAME: names.get(user_id, user_id),
                    CONF_DEVICE_ID: login["device_id"],
                    CONF_TOKEN: login["api_key"],
                    CONF_USER_ID: user_id,
                    CONF_SERVER_ID: login["server_id"],
                    CONF_IS_ADMIN: True,  # API keys may do everything
                    CONF_AUTH_TYPE: AUTH_API_KEY,
                },
            )
        return self.async_show_form(
            step_id="api_user",
            data_schema=vol.Schema(
                {vol.Required(CONF_USER_ID): _user_selector(names)}
            ),
        )

    # ---- re-authentication --------------------------------------------------------
    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        if entry_data.get(CONF_AUTH_TYPE) == AUTH_API_KEY:
            return await self.async_step_reauth_api_key()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_api_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            info, _users, errors = await self._check_api_key(
                entry.data[CONF_URL], api_key, entry.data[CONF_DEVICE_ID]
            )
            if info is not None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: api_key}
                )
        return self.async_show_form(
            step_id="reauth_api_key",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
            description_placeholders={"url": entry.data[CONF_URL]},
        )

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


class JellyfinPlusOptionsFlow(OptionsFlow):
    """Link Home Assistant users to Jellyfin users."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None:
            return self.async_abort(reason="not_loaded")

        ha_users = [
            u
            for u in await self.hass.auth.async_get_users()
            if u.is_active and not u.system_generated
        ]
        # HA can't translate dynamic field names, so the user's name is the label
        labels: dict[str, str] = {}
        for user in sorted(ha_users, key=lambda u: (u.name or "").casefold()):
            label = user.name or user.id
            while label in labels:
                label += " "
            labels[label] = user.id

        current = dict(entry.options.get(CONF_USER_MAP) or {})
        api_key_login = entry.data.get(CONF_AUTH_TYPE) == AUTH_API_KEY
        if user_input is not None:
            new_user = user_input.get(LIBRARY_USER)
            if api_key_login and new_user and new_user != entry.data.get(CONF_USER_ID):
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_USER_ID: new_user,
                        CONF_USERNAME: runtime.users.get(new_user, new_user),
                    },
                )
            user_map = {
                labels[label]: value
                for label, value in user_input.items()
                if label in labels and value and value != NOT_LINKED
            }
            return self.async_create_entry(
                data={
                    CONF_USER_MAP: user_map,
                    CONF_PEOPLE_FOLDER: bool(user_input.get(CONF_PEOPLE_FOLDER)),
                    CONF_ADD_SESSION_USER: bool(user_input.get(CONF_ADD_SESSION_USER)),
                }
            )

        choices = [SelectOptionDict(value=NOT_LINKED, label="Not linked")] + [
            SelectOptionDict(value=user_id, label=name)
            for user_id, name in sorted(runtime.users.items(), key=lambda i: i[1].casefold())
        ]
        fields: dict[Any, Any] = {}
        if api_key_login:
            fields[
                vol.Required(LIBRARY_USER, default=entry.data.get(CONF_USER_ID))
            ] = _user_selector(runtime.users)
        for label, ha_id in labels.items():
            default = current.get(ha_id, NOT_LINKED)
            if default not in runtime.users:
                default = NOT_LINKED
            fields[vol.Optional(label, default=default)] = SelectSelector(
                SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN)
            )
        fields[
            vol.Optional(CONF_PEOPLE_FOLDER, default=bool(entry.options.get(CONF_PEOPLE_FOLDER)))
        ] = BooleanSelector()
        fields[
            vol.Optional(
                CONF_ADD_SESSION_USER, default=bool(entry.options.get(CONF_ADD_SESSION_USER))
            )
        ] = BooleanSelector()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "note": ""
                if runtime.is_admin
                else "\n\n**Note:** the Jellyfin account of this integration is not an "
                "administrator, so only that account can be linked. Enable *Allow this "
                "user to manage the server* for it in Jellyfin (Dashboard -> Users); "
                "*Allow remote control of other users* alone is not enough. Jellyfin "
                "Plus notices the change within 5 minutes."
            },
        )
