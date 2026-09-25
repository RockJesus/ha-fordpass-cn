"""Config flow for the FordPass China integration.

Two-step SMS login:
  1. user enters the phone number -> app sends an SMS passcode;
  2. user enters the 6-digit passcode -> integration exchanges it for JWTs.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .api import FordPassApi, FordPassApiError
from .const import (
    CONF_PHONE,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required("phone"): str,
    }
)
STEP_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("passcode"): str,
    }
)
STEP_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL_SECONDS): vol.All(
            vol.Coerce(int), vol.Range(min=60, max=3600)
        ),
        vol.Optional("track_location", default=False): bool,
    }
)


class FordPassConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._phone: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._phone = user_input["phone"].strip()
            await self.async_set_unique_id(self._phone)
            self._abort_if_unique_id_configured()
            session = self.hass.helpers.aiohttp_client.async_get_clientsession()
            api = FordPassApi(session, _LOGGER)
            try:
                await api.generate_passcode(self._phone)
            except FordPassApiError as err:
                errors["base"] = "passcode_failed"
                _LOGGER.warning("send passcode failed: %s", err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("unexpected error sending passcode")
                errors["base"] = "unknown"
            if not errors:
                return self.async_show_form(
                    step_id="code",
                    data_schema=STEP_CODE_SCHEMA,
                    description_placeholders={"phone": self._phone},
                )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = self.hass.helpers.aiohttp_client.async_get_clientsession()
            api = FordPassApi(session, _LOGGER)
            try:
                tokens = await api.passcode_login(self._phone or "", user_input["passcode"].strip())
            except FordPassApiError as err:
                errors["base"] = "login_failed"
                _LOGGER.warning("login failed: %s", err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("unexpected error during login")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=self._phone or DOMAIN,
                    data={
                        CONF_PHONE: self._phone,
                        "access_token": tokens["access_token"],
                        "refresh_token": tokens["refresh_token"],
                    },
                    options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_SECONDS, "track_location": False},
                )
        return self.async_show_form(step_id="code", data_schema=STEP_CODE_SCHEMA, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return FordPassOptionsFlow(config_entry)


class FordPassOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        data = self._config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS),
                    ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
                    vol.Optional("track_location", default=data.get("track_location", False)): bool,
                }
            ),
        )
