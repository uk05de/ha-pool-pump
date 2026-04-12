"""Pool Pump sensors — status, mode, temperatures."""

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODE_AUTOMATIK
from .coordinator import PoolPumpCoordinator

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolPumpCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        PoolPumpStatus(coordinator, entry),
        PoolPumpMode(coordinator, entry),
        TestModeSensor(coordinator, entry),
        BufferedWaterTemp(coordinator, entry),
    ]

    if coordinator._outside_temps:
        entities.append(OutsideTemperature(coordinator, entry))
    if coordinator._water_temp_entity:
        entities.append(LiveWaterTemperature(coordinator, entry))
    if coordinator._room_temp_entity:
        entities.append(RoomTemperature(coordinator, entry))

    async_add_entities(entities)


class _Base(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_added_to_hass(self) -> None:
        self._coordinator.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.remove_listener(self.async_write_ha_state)


class PoolPumpStatus(_Base):
    _attr_name = "Status"
    _attr_icon = "mdi:information-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    def native_value(self) -> str:
        if not self._coordinator.running:
            return "stopped"
        return f"running ({self._coordinator.target_speed:.0f}%)"


class PoolPumpMode(_Base):
    """Shows active program or manual."""

    _attr_name = "Mode"
    _attr_icon = "mdi:auto-fix"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_mode"

    @property
    def native_value(self) -> str:
        prog = self._coordinator.active_program
        if prog is None:
            return "manual"
        if prog == MODE_AUTOMATIK:
            sub = self._coordinator.auto_sub_mode
            if sub:
                return f"automatik ({sub})"
            return "automatik"
        return prog


class TestModeSensor(_Base):
    _attr_name = "Test mode"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_test_mode"

    @property
    def native_value(self) -> str:
        return "active" if self._coordinator.test_mode else "inactive"

    @property
    def icon(self) -> str:
        return "mdi:test-tube" if self._coordinator.test_mode else "mdi:test-tube-off"


class _TempBase(_Base):
    _attr_native_unit_of_measurement = "°C"
    _attr_device_class = SensorDeviceClass.TEMPERATURE


class OutsideTemperature(_TempBase):
    _attr_name = "Outside temperature"
    _attr_icon = "mdi:thermometer-low"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_outside_temp"

    @property
    def native_value(self):
        return self._coordinator.outside_temperature


class LiveWaterTemperature(_TempBase):
    _attr_name = "Water temperature (live)"
    _attr_icon = "mdi:thermometer-water"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_water_temp_live"

    @property
    def native_value(self):
        return self._coordinator.water_temperature


class BufferedWaterTemp(_TempBase):
    _attr_name = "Water temperature (buffered)"
    _attr_icon = "mdi:thermometer-check"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_water_temp_buffered"

    @property
    def native_value(self):
        return self._coordinator.buffered_water_temp


class RoomTemperature(_TempBase):
    _attr_name = "Room temperature"
    _attr_icon = "mdi:home-thermometer"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_room_temp"

    @property
    def native_value(self):
        return self._coordinator.room_temperature
