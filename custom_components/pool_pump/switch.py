"""Pool Pump switches — pump, program switches, winter override."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_PROGRAMS, CONF_WINTER_OVERRIDE, DEFAULT_PROGRAMS, MODE_AUTOMATIK
from .coordinator import PoolPumpCoordinator

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolPumpCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PumpSwitch(coordinator, entry),
        AutomatikProgramSwitch(coordinator, entry),
        WinterOverrideSwitch(coordinator, entry),
    ]

    if coordinator.freshwater_available:
        entities.append(FreshwaterSwitch(coordinator, entry))

    # Create a switch for each user-defined program
    for prog in coordinator.programs:
        entities.append(TimedProgramSwitch(coordinator, entry, prog))

    async_add_entities(entities)


class _BaseSwitch(SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._entry = entry
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_added_to_hass(self) -> None:
        self._coordinator.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.remove_listener(self.async_write_ha_state)


class PumpSwitch(_BaseSwitch):
    """Manual pump on/off. Turning off cancels any active program."""

    _attr_name = "Pump"
    _attr_icon = "mdi:pump"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_switch"
        # Set device info with name here (primary entity)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Pool Pump",
            "manufacturer": "DAB",
        }

    @property
    def is_on(self) -> bool:
        return self._coordinator.running

    async def async_turn_on(self, **kwargs) -> None:
        speed = self._coordinator.target_speed or self._coordinator.normal_speed
        await self._coordinator.async_ensure_running(speed)

    async def async_turn_off(self, **kwargs) -> None:
        await self._coordinator.async_pump_switch_off()


class AutomatikProgramSwitch(_BaseSwitch):
    """Activates the automatic scheduler (normal + frost protection)."""

    _attr_name = "Programm Automatik"
    _attr_icon = "mdi:robot"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_prog_automatik"

    @property
    def is_on(self) -> bool:
        return self._coordinator.is_program_active(MODE_AUTOMATIK)

    async def async_turn_on(self, **kwargs) -> None:
        await self._coordinator.async_activate_program(MODE_AUTOMATIK)

    async def async_turn_off(self, **kwargs) -> None:
        await self._coordinator.async_deactivate_program(MODE_AUTOMATIK)


class TimedProgramSwitch(_BaseSwitch):
    """Switch for a user-defined timed program (backwash, rinse, etc.)."""

    def __init__(self, coordinator, entry, program: dict):
        super().__init__(coordinator, entry)
        self._program_name = program["name"]
        self._attr_name = f"Programm {program['name']}"
        self._attr_icon = "mdi:playlist-play"
        # Slug for unique_id
        slug = program["name"].lower().replace(" ", "_").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
        self._attr_unique_id = f"{entry.entry_id}_prog_{slug}"

    @property
    def is_on(self) -> bool:
        return self._coordinator.is_program_active(self._program_name)

    async def async_turn_on(self, **kwargs) -> None:
        await self._coordinator.async_activate_program(self._program_name)

    async def async_turn_off(self, **kwargs) -> None:
        await self._coordinator.async_deactivate_program(self._program_name)


class FreshwaterSwitch(_BaseSwitch):
    """Open/close freshwater valve with auto-timer."""

    _attr_name = "Frischwasser"
    _attr_icon = "mdi:water-plus"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_freshwater"

    @property
    def is_on(self) -> bool:
        return self._coordinator.freshwater_running

    async def async_turn_on(self, **kwargs) -> None:
        await self._coordinator.async_start_freshwater()

    async def async_turn_off(self, **kwargs) -> None:
        await self._coordinator.async_stop_freshwater()


class WinterOverrideSwitch(_BaseSwitch):
    """Force frost protection regardless of temperatures."""

    _attr_name = "Wintermodus erzwingen"
    _attr_icon = "mdi:snowflake-alert"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_winter_override"

    @property
    def is_on(self) -> bool:
        return self._coordinator.winter_override

    async def async_turn_on(self, **kwargs) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_WINTER_OVERRIDE: True}
        )

    async def async_turn_off(self, **kwargs) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_WINTER_OVERRIDE: False}
        )
