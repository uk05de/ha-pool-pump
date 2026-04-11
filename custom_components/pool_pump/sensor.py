"""Pool Pump sensor — status info."""

import logging

from homeassistant.components.sensor import SensorEntity
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
    entities = [PoolPumpStatus(coordinator, entry)]
    if coordinator._temp_sensors:
        entities.append(PoolPumpTemperature(coordinator, entry))
    async_add_entities(entities)


class PoolPumpStatus(SensorEntity):
    """Current pump status (text)."""

    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_icon = "mdi:information-outline"

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    async def async_added_to_hass(self) -> None:
        self._coordinator.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.remove_listener(self.async_write_ha_state)

    @property
    def native_value(self) -> str:
        if not self._coordinator.running:
            return "stopped"
        return f"running ({self._coordinator.target_speed:.0f}%)"


class PoolPumpTemperature(SensorEntity):
    """Average of configured temperature sensors."""

    _attr_has_entity_name = True
    _attr_name = "Reference temperature"
    _attr_icon = "mdi:thermometer"
    _attr_native_unit_of_measurement = "°C"
    _attr_device_class = "temperature"

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_temperature"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    @property
    def native_value(self) -> float | None:
        return self._coordinator.average_temperature
