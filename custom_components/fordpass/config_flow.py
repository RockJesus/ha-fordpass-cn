"""Config flow for FordPass integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, DEFAULT_BASE_URL
from .api import FordPassClient, FordPassAuthError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    client = FordPassClient(
        username=data["username"],
        password=data["password"],
        base_url=DEFAULT_BASE_URL,
    )

    try:
        await client.login()
    except FordPassAuthError as err:
        raise ValueError("invalid_auth") from err
    except Exception as err:
        _LOGGER.exception("Unexpected exception")
        raise ValueError("unknown") from err
    finally:
        await client.async_close()

    return {"title": f"福特派 ({data['username']})"}


class FordPassConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FordPass."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except ValueError:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
