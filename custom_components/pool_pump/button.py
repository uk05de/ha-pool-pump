"""Pool Pump buttons — backwash counter reset."""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PoolPumpCoordinator

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolPumpCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        BackwashResetButton(coordinator, entry),
        NotausButton(coordinator, entry),
    ])


class _BaseButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}


class BackwashResetButton(_BaseButton):
    """Reset the backwash counter to today."""

    _attr_name = "Backwash reset"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_backwash_reset"

    async def async_press(self) -> None:
        await self._coordinator.async_reset_backwash_counter()


class NotausButton(_BaseButton):
    """Emergency stop — cancel program/nachlauf, hard-stop pool + HP.

    Skips the heat-pump nachlauf delay. The HP may go into E3 (flow loss)
    on push, which is an accepted trade-off for an immediate stop.
    """

    _attr_name = "Notaus"
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_notaus"

    async def async_press(self) -> None:
        await self._coordinator.async_emergency_stop()
