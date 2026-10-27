"""Config flow for Newport News Waterworks."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_CODE, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import voluptuous as vol

from .api import (
    DEFAULT_USER_AGENT,
    CannotConnect,
    InvalidAuth,
    InvalidCode,
    NNWWClient,
    VerificationRequired,
)
from .const import CONF_USER_AGENT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})
STEP_CODE_SCHEMA = vol.Schema({vol.Required(CONF_CODE): str})
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class NNWWConfigFlow(ConfigFlow, domain=DOMAIN):
    """Log in, verify the emailed code if asked, then create the entry."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._client: NNWWClient | None = None
        self._username = ""
        self._password = ""
        self._user_agent = DEFAULT_USER_AGENT

    async def _async_login(self) -> ConfigFlowResult | dict[str, str]:
        """Log in with the stored credentials; returns errors or the next step."""
        # The emailed code is tied to this session's cookies, so the same client
        # (and cookie jar) is kept for the verify step.
        self._client = NNWWClient(
            async_create_clientsession(self.hass, cookie_jar=aiohttp.CookieJar()),
            self._username,
            self._password,
            self._user_agent,
        )
        try:
            await self._client.login()
        except VerificationRequired:
            try:
                await self._client.send_verification_code()
            except CannotConnect:
                return {"base": "cannot_connect"}
            return await self.async_step_verify()
        except InvalidAuth:
            return {"base": "invalid_auth"}
        except CannotConnect:
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error logging in")
            return {"base": "unknown"}
        return await self._async_finish()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the portal login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._password = user_input[CONF_PASSWORD]
            await self.async_set_unique_id(self._username.lower())
            self._abort_if_unique_id_configured()
            result = await self._async_login()
            if "type" in result:
                return result  # type: ignore[return-value]
            errors = result  # type: ignore[assignment]
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, {CONF_USERNAME: self._username}
            ),
            errors=errors,
        )

    async def async_step_verify(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the code the portal emailed."""
        errors: dict[str, str] = {}
        if user_input is not None and self._client is not None:
            try:
                await self._client.verify_code(user_input[CONF_CODE])
            except InvalidCode:
                errors[CONF_CODE] = "invalid_code"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error verifying code")
                errors["base"] = "unknown"
            else:
                return await self._async_finish()
        return self.async_show_form(step_id="verify", data_schema=STEP_CODE_SCHEMA, errors=errors)

    async def _async_finish(self) -> ConfigFlowResult:
        data = {
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_USER_AGENT: self._user_agent,
        }
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
        return self.async_create_entry(title=self._username, data=data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauth when the portal wants a new code or the password changed."""
        self._username = entry_data[CONF_USERNAME]
        self._password = entry_data[CONF_PASSWORD]
        self._user_agent = entry_data.get(CONF_USER_AGENT, DEFAULT_USER_AGENT)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the password, then log in (sending a new code if needed)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            result = await self._async_login()
            if "type" in result:
                return result  # type: ignore[return-value]
            errors = result  # type: ignore[assignment]
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_REAUTH_SCHEMA, {CONF_PASSWORD: self._password}
            ),
            description_placeholders={"username": self._username},
            errors=errors,
        )
