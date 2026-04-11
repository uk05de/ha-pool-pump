"""Pool Pump coordinator — owns state and drives the Shellys."""

import asyncio
import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_POWER_SWITCH,
    CONF_SPEED_NUMBER,
    CONF_START_SWITCH,
    CONF_TEMP_SENSORS,
    POWER_ON_DELAY,
    STOP_DELAY,
    PROGRAM_OFF,
    PROGRAM_MANUAL,
)

log = logging.getLogger(__name__)


class PoolPumpCoordinator:
    """Central brain — knows the target state, drives the Shellys."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self._power_switch = entry.data[CONF_POWER_SWITCH]
        self._speed_number = entry.data[CONF_SPEED_NUMBER]
        self._start_switch = entry.data[CONF_START_SWITCH]
        self._temp_sensors = entry.data.get(CONF_TEMP_SENSORS, [])

        self._program = PROGRAM_OFF
        self._target_speed = 0
        self._running = False
        self._listeners: list = []
        self._lock = asyncio.Lock()

    async def async_setup(self) -> None:
        """Called during integration setup."""
        log.info("Pool Pump coordinator starting")

    async def async_shutdown(self) -> None:
        """Called during integration unload."""
        log.info("Pool Pump coordinator stopping")

    # --- State accessors ---

    @property
    def program(self) -> str:
        return self._program

    @property
    def target_speed(self) -> float:
        return self._target_speed

    @property
    def running(self) -> bool:
        return self._running

    @property
    def average_temperature(self) -> float | None:
        """Average of all configured temperature sensors."""
        values = []
        for sensor_id in self._temp_sensors:
            state = self.hass.states.get(sensor_id)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    values.append(float(state.state))
                except ValueError:
                    pass
        return sum(values) / len(values) if values else None

    # --- Core operations ---

    async def async_ensure_running(self, speed: float) -> None:
        """Idempotently bring the pump up at a given speed."""
        async with self._lock:
            log.info("ensure_running at %.0f%%", speed)

            # 1. Power on if needed
            power_state = self.hass.states.get(self._power_switch)
            if not power_state or power_state.state != "on":
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._power_switch}, blocking=True
                )
                await asyncio.sleep(POWER_ON_DELAY)

            # 2. Set speed
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": self._speed_number, "value": speed},
                blocking=True,
            )

            # 3. Send start signal
            await self.hass.services.async_call(
                "switch", "turn_on", {"entity_id": self._start_switch}, blocking=True
            )

            self._target_speed = speed
            self._running = True
            self._notify()

    async def async_ensure_stopped(self) -> None:
        """Idempotently stop the pump and cut power."""
        async with self._lock:
            log.info("ensure_stopped")

            # 1. Stop signal
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._start_switch}, blocking=True
            )
            await asyncio.sleep(STOP_DELAY)

            # 2. Speed to zero
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": self._speed_number, "value": 0},
                blocking=True,
            )

            # 3. Cut mains power
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._power_switch}, blocking=True
            )

            self._target_speed = 0
            self._running = False
            self._notify()

    async def async_set_speed(self, speed: float) -> None:
        """Change speed while running without re-sequencing."""
        async with self._lock:
            await self.hass.services.async_call(
                "number", "set_value",
                {"entity_id": self._speed_number, "value": speed},
                blocking=True,
            )
            self._target_speed = speed
            self._notify()

    async def async_set_program(self, program: str) -> None:
        """Switch to a different program."""
        self._program = program
        self._notify()
        log.info("program changed to %s", program)

    # --- Listener pattern for entities ---

    def add_listener(self, update_callback) -> None:
        self._listeners.append(update_callback)

    def remove_listener(self, update_callback) -> None:
        if update_callback in self._listeners:
            self._listeners.remove(update_callback)

    @callback
    def _notify(self) -> None:
        for listener in self._listeners:
            listener()
