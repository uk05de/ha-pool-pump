"""Pool Pump switches — master on/off and winter override."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_AUTOMATION_ENABLED, MODE_MANUAL, MODE_OFF
from .coordinator import PoolPumpCoordinator

log = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PoolPumpCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        PoolPumpSwitch(coordinator, entry),
        AutomationSwitch(coordinator, entry),
        WinterOverrideSwitch(coordinator, entry),
    ])


class PoolPumpSwitch(SwitchEntity):
    """Master switch — manual on/off, bypasses scheduler."""

    _attr_has_entity_name = True
    _attr_name = "Pump"
    _attr_icon = "mdi:pump"

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_switch"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Pool Pump",
            "manufacturer": "DAB",
        }

    async def async_added_to_hass(self) -> None:
        self._coordinator.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.remove_listener(self.async_write_ha_state)

    @property
    def is_on(self) -> bool:
        return self._coordinator.running

    async def async_turn_on(self, **kwargs) -> None:
        speed = self._coordinator.target_speed or self._coordinator.normal_speed
        await self._coordinator.async_set_mode(MODE_MANUAL)
        await self._coordinator.async_ensure_running(speed)

    async def async_turn_off(self, **kwargs) -> None:
        await self._coordinator.async_ensure_stopped()
        await self._coordinator.async_set_mode(MODE_OFF)


class AutomationSwitch(SwitchEntity):
    """Enable/disable all automatic control."""

    _attr_has_entity_name = True
    _attr_name = "Automation"
    _attr_icon = "mdi:robot"

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_automation"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    async def async_added_to_hass(self) -> None:
        self._coordinator.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.remove_listener(self.async_write_ha_state)

    @property
    def is_on(self) -> bool:
        return self._coordinator.automation_enabled

    async def async_turn_on(self, **kwargs) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_AUTOMATION_ENABLED: True}
        )

    async def async_turn_off(self, **kwargs) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_AUTOMATION_ENABLED: False}
        )


class WinterOverrideSwitch(SwitchEntity):
    """Force frost protection regardless of temperatures."""

    _attr_has_entity_name = True
    _attr_name = "Winter override"
    _attr_icon = "mdi:snowflake-alert"

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_winter_override"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
        }

    async def async_added_to_hass(self) -> None:
        self._coordinator.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self._coordinator.remove_listener(self.async_write_ha_state)

    @property
    def is_on(self) -> bool:
        return self._coordinator.winter_override

    async def async_turn_on(self, **kwargs) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, "winter_override": True}
        )

    async def async_turn_off(self, **kwargs) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, "winter_override": False}
        )
