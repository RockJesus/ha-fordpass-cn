"""The FordPass integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .api import FordPassClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FordPass from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create API client
    client = FordPassClient(
        username=entry.data["username"],
        password=entry.data["password"],
        base_url=entry.data.get("base_url", "https://api-connect.ford.com.cn/lbsp2c-app"),
    )

    # Test login
    try:
        await client.login()
    except Exception as err:
        _LOGGER.error("Failed to login: %s", err)
        return False

    hass.data[DOMAIN][entry.entry_id] = client

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        client: FordPassClient = hass.data[DOMAIN].pop(entry.entry_id)
        await client.async_close()

    return unload_ok
