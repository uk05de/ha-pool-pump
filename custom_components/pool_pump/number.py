"""Pool Pump number — target speed."""

import logging

from homeassistant.components.number import NumberEntity, NumberMode
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
    async_add_entities([PoolPumpSpeed(coordinator, entry)])


class PoolPumpSpeed(NumberEntity):
    """Target speed percentage. Writes through to the Shelly Dimmer."""

    _attr_has_entity_name = True
    _attr_name = "Speed"
    _attr_icon = "mdi:speedometer"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_speed"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    async def async_added_to_hass(self) -> None:
        self._coordinator.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.remove_listener(self.async_write_ha_state)

    @property
    def native_value(self) -> float:
        return self._coordinator.target_speed

    async def async_set_native_value(self, value: float) -> None:
        if self._coordinator.running:
            await self._coordinator.async_set_speed(value)
        else:
            # Not running — just remember the value for next start
            self._coordinator._target_speed = value
            self._coordinator._notify()
