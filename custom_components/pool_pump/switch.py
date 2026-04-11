"""Pool Pump switch — master on/off with sequence handling."""

import logging

from homeassistant.components.switch import SwitchEntity
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
    async_add_entities([PoolPumpSwitch(coordinator, entry)])


class PoolPumpSwitch(SwitchEntity):
    """Master switch — runs the sequence via the coordinator."""

    _attr_has_entity_name = True
    _attr_name = "Pool pump"
    _attr_icon = "mdi:pump"

    def __init__(self, coordinator: PoolPumpCoordinator, entry: ConfigEntry):
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_switch"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Pool pump",
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
        speed = self._coordinator.target_speed or 30
        await self._coordinator.async_ensure_running(speed)

    async def async_turn_off(self, **kwargs) -> None:
        await self._coordinator.async_ensure_stopped()
