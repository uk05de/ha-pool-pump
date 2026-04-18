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
        PoolPumpSpeed(coordinator, entry),
        PoolPumpMode(coordinator, entry),
        TestModeSensor(coordinator, entry),
        BackwashCountdown(coordinator, entry),
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


class PoolPumpSpeed(_Base):
    """Aktuelle Drehzahl als Text (`Aus` oder `45 %`).

    Text-State, damit HA im History-Graph Farbbänder statt Linie rendert.
    Zum Ändern der Drehzahl dient `number.pool_pump_speed`.
    """

    _attr_name = "Speed"
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_speed_current"

    @property
    def native_value(self) -> str:
        if not self._coordinator.running:
            return "Aus"
        return f"{int(self._coordinator.target_speed)} %"


class PoolPumpMode(_Base):
    """Aktiver Modus — einheitliche, gut visualisierbare Werte."""

    _attr_name = "Mode"
    _attr_icon = "mdi:auto-fix"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_mode"

    @property
    def native_value(self) -> str:
        prog = self._coordinator.active_program
        if prog is None:
            return "Manuell"
        if prog == MODE_AUTOMATIK:
            if self._coordinator.auto_sub_mode == "frost_protection":
                return "Frostschutz"
            return "Automatik"
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


class BackwashCountdown(_Base):
    """Days since last backwash / days remaining until due."""

    _attr_name = "Backwash"
    _attr_icon = "mdi:filter-outline"
    _attr_native_unit_of_measurement = "days"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_backwash_countdown"

    @property
    def native_value(self) -> int | None:
        days = self._coordinator.days_since_backwash
        if days is None:
            return None
        remaining = self._coordinator.backwash_interval_days - days
        return remaining

    @property
    def icon(self) -> str:
        if self._coordinator.backwash_overdue:
            return "mdi:filter-remove"
        return "mdi:filter-outline"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "days_since_backwash": self._coordinator.days_since_backwash,
            "interval_days": self._coordinator.backwash_interval_days,
            "overdue": self._coordinator.backwash_overdue,
            "last_backwash": self._coordinator._last_backwash_date,
        }


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
