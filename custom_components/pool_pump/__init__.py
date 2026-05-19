"""Pool Pump integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import PoolPumpCoordinator

log = logging.getLogger(__name__)

PLATFORMS = ["switch", "number", "sensor", "binary_sensor", "button"]
# select platform removed — mode is now a read-only sensor


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Pool Pump from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Pre-load platform modules in the executor so the subsequent
    # async_forward_entry_setups doesn't do a blocking importlib call
    # in the event loop (HA 2024.6+ warns about this).
    integration = await async_get_integration(hass, DOMAIN)
    await integration.async_get_platforms(PLATFORMS)

    coordinator = PoolPumpCoordinator(hass, entry)
    await coordinator.async_setup()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok


async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — reload."""
    await hass.config_entries.async_reload(entry.entry_id)
