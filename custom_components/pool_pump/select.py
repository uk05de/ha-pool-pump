"""Pool Pump select — current program."""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ALL_PROGRAMS
from .coordinator import PoolPumpCoordinator

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolPumpCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PoolPumpProgram(coordinator, entry)])


class PoolPumpProgram(SelectEntity):
    """Current program selector."""

    _attr_has_entity_name = True
    _attr_name = "Program"
    _attr_icon = "mdi:playlist-play"
    _attr_options = ALL_PROGRAMS

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_program"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    async def async_added_to_hass(self) -> None:
        self._coordinator.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.remove_listener(self.async_write_ha_state)

    @property
    def current_option(self) -> str:
        return self._coordinator.program

    async def async_select_option(self, option: str) -> None:
        await self._coordinator.async_set_program(option)
