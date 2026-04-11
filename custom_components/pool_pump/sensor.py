"""Pool Pump sensors — status and temperature info."""

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
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
    entities: list[SensorEntity] = [PoolPumpStatus(coordinator, entry)]

    if coordinator._outside_temps:
        entities.append(OutsideTemperature(coordinator, entry))
    if coordinator._water_temp:
        entities.append(WaterTemperature(coordinator, entry))
    if coordinator._room_temp:
        entities.append(RoomTemperature(coordinator, entry))

    async_add_entities(entities)


class _Base(SensorEntity):
    """Common base for pool pump sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    async def async_added_to_hass(self) -> None:
        self._coordinator.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.remove_listener(self.async_write_ha_state)


class PoolPumpStatus(_Base):
    """Current pump status (text)."""

    _attr_name = "Status"
    _attr_icon = "mdi:information-outline"

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    def native_value(self) -> str:
        if not self._coordinator.running:
            return "stopped"
        return f"running ({self._coordinator.target_speed:.0f}%)"


class _TemperatureBase(_Base):
    """Shared properties for temperature sensors."""

    _attr_native_unit_of_measurement = "°C"
    _attr_device_class = SensorDeviceClass.TEMPERATURE


class OutsideTemperature(_TemperatureBase):
    """Minimum plausible outside temperature across configured sensors."""

    _attr_name = "Outside temperature"
    _attr_icon = "mdi:thermometer-low"

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_outside_temp"

    @property
    def native_value(self) -> float | None:
        return self._coordinator.outside_temperature


class WaterTemperature(_TemperatureBase):
    """Pool water temperature (for safety checks)."""

    _attr_name = "Water temperature"
    _attr_icon = "mdi:thermometer-water"

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_water_temp"

    @property
    def native_value(self) -> float | None:
        return self._coordinator.water_temperature


class RoomTemperature(_TemperatureBase):
    """Technical room temperature."""

    _attr_name = "Room temperature"
    _attr_icon = "mdi:home-thermometer"

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_room_temp"

    @property
    def native_value(self) -> float | None:
        return self._coordinator.room_temperature
