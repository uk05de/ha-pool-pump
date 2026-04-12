"""Pool Pump coordinator — program management, scheduler, Shelly driver."""

import asyncio
import logging
import time
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    CONF_POWER_SWITCH,
    CONF_SPEED_NUMBER,
    CONF_START_SWITCH,
    CONF_OUTSIDE_TEMPS,
    CONF_WATER_TEMP,
    CONF_ROOM_TEMP,
    CONF_TEST_MODE,
    CONF_WINTER_OVERRIDE,
    CONF_WINTER_THRESHOLDS,
    CONF_NORMAL_WINDOW_START,
    CONF_NORMAL_WINDOW_END,
    CONF_NORMAL_SPEED,
    CONF_NORMAL_TEMP_DIVISOR,
    CONF_PROGRAMS,
    CONF_BACKWASH_INTERVAL_DAYS,
    CONF_BACKWASH_PROGRAM_NAME,
    DEFAULT_BACKWASH_INTERVAL,
    DEFAULT_PROGRAMS,
    DEFAULT_THRESHOLDS,
    POWER_ON_DELAY,
    STOP_DELAY,
    MIN_RUN_FOR_SAMPLE,
    SCHEDULER_INTERVAL,
    MIN_PAUSE_MINUTES,
    MODE_MANUAL,
    MODE_AUTOMATIK,
    TEMP_MIN_PLAUSIBLE,
    TEMP_MAX_PLAUSIBLE,
)

log = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}_state"
STORAGE_VERSION = 1


class PoolPumpCoordinator:
    """Central brain — program switches, scheduler, Shelly driver."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry

        # Shelly entity IDs
        self._power_switch = entry.data[CONF_POWER_SWITCH]
        self._speed_number = entry.data[CONF_SPEED_NUMBER]
        self._start_switch = entry.data[CONF_START_SWITCH]

        # Sensor entity IDs
        self._outside_temps: list[str] = entry.data.get(CONF_OUTSIDE_TEMPS, [])
        self._water_temp_entity: str | None = entry.data.get(CONF_WATER_TEMP)
        self._room_temp_entity: str | None = entry.data.get(CONF_ROOM_TEMP)

        # Program state
        self._active_program: str | None = None  # None = manual, "automatik", or program name
        self._target_speed: float = 0
        self._running = False
        self._run_start_time: float | None = None
        self._buffered_water_temp: float | None = None

        # Frost state
        self._last_frost_run_end: float | None = None
        self._frost_cycle_start: float | None = None
        self._current_frost_threshold: dict | None = None

        # Automatik sub-mode (normal vs frost — only relevant when automatik is active)
        self._auto_sub_mode: str | None = None  # "normal" or "frost_protection"

        # Backwash reminder
        self._last_backwash_date: str | None = None  # ISO date string
        self._last_notification_date: str | None = None  # prevent daily spam

        # Tasks
        self._scheduler_task: asyncio.Task | None = None
        self._program_task: asyncio.Task | None = None

        # Listeners + persistence
        self._listeners: list = []
        self._lock = asyncio.Lock()
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    # --- Options accessors ---

    @property
    def test_mode(self) -> bool:
        return self.entry.options.get(CONF_TEST_MODE, False)

    @property
    def winter_override(self) -> bool:
        return self.entry.options.get(CONF_WINTER_OVERRIDE, False)

    @property
    def thresholds(self) -> list[dict]:
        return self.entry.options.get(CONF_WINTER_THRESHOLDS, DEFAULT_THRESHOLDS)

    @property
    def programs(self) -> list[dict]:
        return self.entry.options.get(CONF_PROGRAMS, DEFAULT_PROGRAMS)

    @property
    def normal_window_start(self) -> str:
        return self.entry.options.get(CONF_NORMAL_WINDOW_START, "08:00:00")

    @property
    def normal_window_end(self) -> str:
        return self.entry.options.get(CONF_NORMAL_WINDOW_END, "22:00:00")

    @property
    def normal_speed(self) -> int:
        return self.entry.options.get(CONF_NORMAL_SPEED, 30)

    @property
    def temp_divisor(self) -> float:
        return self.entry.options.get(CONF_NORMAL_TEMP_DIVISOR, 2.0)

    # --- State accessors ---

    @property
    def active_program(self) -> str | None:
        """None = manual, 'automatik' = scheduler, or program name."""
        return self._active_program

    @property
    def auto_sub_mode(self) -> str | None:
        """When automatik is active: 'normal' or 'frost_protection'."""
        return self._auto_sub_mode

    @property
    def target_speed(self) -> float:
        return self._target_speed

    @property
    def running(self) -> bool:
        return self._running

    @property
    def buffered_water_temp(self) -> float | None:
        return self._buffered_water_temp

    @property
    def backwash_interval_days(self) -> int:
        return self.entry.options.get(CONF_BACKWASH_INTERVAL_DAYS, DEFAULT_BACKWASH_INTERVAL)

    @property
    def backwash_program_name(self) -> str:
        return self.entry.options.get(CONF_BACKWASH_PROGRAM_NAME, "Backwash")

    @property
    def days_since_backwash(self) -> int | None:
        if not self._last_backwash_date:
            return None
        try:
            last = datetime.fromisoformat(self._last_backwash_date).date()
            return (datetime.now().date() - last).days
        except ValueError:
            return None

    @property
    def backwash_overdue(self) -> bool:
        days = self.days_since_backwash
        if days is None:
            return True  # never done → overdue
        return days >= self.backwash_interval_days

    # --- Temperature reading ---

    def _read_temp(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable"):
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        if value < TEMP_MIN_PLAUSIBLE or value > TEMP_MAX_PLAUSIBLE:
            return None
        return value

    @property
    def outside_temperature(self) -> float | None:
        values = [v for e in self._outside_temps if (v := self._read_temp(e)) is not None]
        return sum(values) / len(values) if values else None

    @property
    def water_temperature(self) -> float | None:
        return self._read_temp(self._water_temp_entity)

    @property
    def room_temperature(self) -> float | None:
        return self._read_temp(self._room_temp_entity)

    # --- Lifecycle ---

    async def async_setup(self) -> None:
        stored = await self._store.async_load()
        if stored:
            self._buffered_water_temp = stored.get("buffered_water_temp")
            self._last_backwash_date = stored.get("last_backwash_date")
            log.info("Restored buffered water temp: %s°C, last backwash: %s",
                     self._buffered_water_temp, self._last_backwash_date)
        self._scheduler_task = self.entry.async_create_background_task(
            self.hass, self._scheduler_loop(), "pool_pump_scheduler"
        )
        log.info("Pool Pump coordinator started (test_mode=%s)", self.test_mode)

    async def async_shutdown(self) -> None:
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
        if self._program_task and not self._program_task.done():
            self._program_task.cancel()
        await self._persist_state()
        log.info("Pool Pump coordinator stopped")

    async def _persist_state(self) -> None:
        await self._store.async_save({
            "buffered_water_temp": self._buffered_water_temp,
            "last_backwash_date": self._last_backwash_date,
        })

    # --- Program switching ---

    async def async_activate_program(self, program_name: str) -> None:
        """Activate a program. Deactivates any other active program first."""
        if self._active_program == program_name:
            return

        # Stop current program
        await self._stop_current_program()

        self._active_program = program_name
        log.info("Program activated: %s", program_name)

        if program_name == MODE_AUTOMATIK:
            # Automatik — scheduler will pick up on next cycle, but evaluate immediately
            self._notify()
            await self._evaluate()
        else:
            # Timed program — find config and start
            prog = self._find_program(program_name)
            if prog:
                self._notify()
                await self.async_ensure_running(prog["speed"])
                if prog["duration_min"] > 0:
                    duration_sec = prog["duration_min"] * 60
                    self._program_task = self.entry.async_create_background_task(
                        self.hass,
                        self._program_timed_stop(duration_sec, program_name),
                        f"pool_pump_{program_name}",
                    )

    async def async_deactivate_program(self, program_name: str) -> None:
        """Deactivate a specific program → manual mode."""
        if self._active_program != program_name:
            return
        await self._stop_current_program()
        self._active_program = None
        self._notify()
        log.info("Program deactivated: %s → manual mode", program_name)

    async def _stop_current_program(self) -> None:
        """Stop whatever is currently running."""
        if self._program_task and not self._program_task.done():
            self._program_task.cancel()
            self._program_task = None

        if self._running:
            await self._sample_water_temp_if_eligible()
            await self.async_ensure_stopped()

        # Reset frost state
        self._frost_cycle_start = None
        self._current_frost_threshold = None
        self._auto_sub_mode = None

    async def _program_timed_stop(self, duration: float, program_name: str) -> None:
        """Auto-stop a timed program after duration."""
        await asyncio.sleep(duration)
        log.info("Program '%s' finished after %.0fs", program_name, duration)
        await self._sample_water_temp_if_eligible()
        await self.async_ensure_stopped()
        # Check if this was the backwash program → reset counter
        if program_name == self.backwash_program_name:
            await self.async_reset_backwash_counter()
        # Deactivate the program switch — back to manual
        self._active_program = None
        self._program_task = None
        self._notify()

    async def async_pump_switch_off(self) -> None:
        """User turned off pump switch → cancel any active program, go manual."""
        await self._stop_current_program()
        self._active_program = None
        self._notify()
        log.info("Pump switch off → manual mode")

    def _find_program(self, name: str) -> dict | None:
        for p in self.programs:
            if p["name"] == name:
                return p
        return None

    def is_program_active(self, name: str) -> bool:
        return self._active_program == name

    # --- Scheduler (only runs when automatik is active) ---

    async def _scheduler_loop(self) -> None:
        await asyncio.sleep(10)
        while True:
            try:
                if self._active_program == MODE_AUTOMATIK:
                    await self._evaluate()
                await self._check_backwash_reminder()
            except Exception:
                log.exception("Scheduler error")
            await asyncio.sleep(SCHEDULER_INTERVAL)

    async def _check_backwash_reminder(self) -> None:
        """Send daily notification if backwash is overdue."""
        if not self.backwash_overdue:
            return
        today = datetime.now().date().isoformat()
        if self._last_notification_date == today:
            return  # already notified today
        self._last_notification_date = today
        days = self.days_since_backwash
        msg = f"Rückspülung fällig! Letzte Rückspülung vor {days} Tagen." if days else "Rückspülung fällig! Noch nie durchgeführt."
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {"title": "Pool Pump — Rückspülung", "message": msg, "notification_id": "pool_pump_backwash"},
            blocking=False,
        )
        log.info("Backwash reminder sent: %s", msg)

    async def _evaluate(self) -> None:
        """Decide what automatik should do right now."""
        if self._active_program != MODE_AUTOMATIK:
            return

        outside = self.outside_temperature
        frost_needed = False

        if self.winter_override:
            frost_needed = True
        elif outside is not None:
            threshold = self._find_threshold(outside)
            if threshold is not None:
                frost_needed = True

        if frost_needed:
            if self._running and self._auto_sub_mode == "normal":
                await self._sample_water_temp_if_eligible()
                await self.async_ensure_stopped()
            self._auto_sub_mode = "frost_protection"
            self._notify()
            await self._handle_frost_mode(outside)
        else:
            if self._running and self._auto_sub_mode == "frost_protection":
                await self.async_ensure_stopped()
                self._frost_cycle_start = None
                self._current_frost_threshold = None
                if self._program_task and not self._program_task.done():
                    self._program_task.cancel()
                    self._program_task = None
            self._auto_sub_mode = "normal"
            self._notify()
            await self._handle_normal_mode()

    # --- Frost protection ---

    def _find_threshold(self, temp: float) -> dict | None:
        match = None
        for t in sorted(self.thresholds, key=lambda x: x["below_temp"], reverse=True):
            if temp < t["below_temp"]:
                match = t
        return match

    async def _handle_frost_mode(self, outside: float | None) -> None:
        if outside is None:
            threshold = self.thresholds[-1] if self.thresholds else None
        else:
            threshold = self._find_threshold(outside)

        if not threshold:
            return

        is_continuous = threshold["interval_min"] == 0 and threshold["duration_min"] == 0
        threshold_changed = (
            self._current_frost_threshold is not None
            and threshold["below_temp"] != self._current_frost_threshold["below_temp"]
        )

        # Already running a frost cycle — react to changes
        if self._running and self._auto_sub_mode == "frost_protection":
            if threshold_changed:
                self._current_frost_threshold = threshold
                log.info("Frost threshold changed: adjusting to below %d°C, speed %d%%",
                         threshold["below_temp"], threshold["speed"])
                await self.async_set_speed(threshold["speed"])

                if is_continuous:
                    if self._program_task and not self._program_task.done():
                        self._program_task.cancel()
                        self._program_task = None
                else:
                    elapsed = time.monotonic() - self._frost_cycle_start if self._frost_cycle_start else 0
                    remaining = max(0, threshold["duration_min"] * 60 - elapsed)
                    if self._program_task and not self._program_task.done():
                        self._program_task.cancel()
                    if remaining > 0:
                        self._program_task = self.entry.async_create_background_task(
                            self.hass, self._frost_timed_stop(remaining), "pool_pump_frost"
                        )
            return

        # Not running — start if needed
        self._current_frost_threshold = threshold

        if is_continuous:
            log.info("Frost: continuous at %d%% (outside=%.1f°C)", threshold["speed"], outside or 0)
            self._frost_cycle_start = time.monotonic()
            await self.async_ensure_running(threshold["speed"])
        else:
            now = time.monotonic()
            interval_sec = threshold["interval_min"] * 60
            if self._last_frost_run_end is None or (now - self._last_frost_run_end) >= interval_sec:
                log.info("Frost: %dmin at %d%% (outside=%.1f°C)",
                         threshold["duration_min"], threshold["speed"], outside or 0)
                self._frost_cycle_start = time.monotonic()
                await self.async_ensure_running(threshold["speed"])
                self._program_task = self.entry.async_create_background_task(
                    self.hass,
                    self._frost_timed_stop(threshold["duration_min"] * 60),
                    "pool_pump_frost",
                )

    async def _frost_timed_stop(self, duration: float) -> None:
        await asyncio.sleep(duration)
        await self._sample_water_temp_if_eligible()
        await self.async_ensure_stopped()
        self._last_frost_run_end = time.monotonic()
        self._frost_cycle_start = None
        self._current_frost_threshold = None
        # Stay in automatik — scheduler will re-evaluate
        self._notify()

    # --- Normal operation ---

    async def _handle_normal_mode(self) -> None:
        now = datetime.now()
        window_start = self._parse_time(self.normal_window_start)
        window_end = self._parse_time(self.normal_window_end)
        if not window_start or not window_end:
            return

        in_window = window_start <= now.time() <= window_end
        water_temp = self._buffered_water_temp
        run_hours = max(1.0, water_temp / self.temp_divisor) if water_temp else 6.0
        should_run = in_window and self._should_run_now(now, window_start, window_end, run_hours)

        if should_run and not self._running:
            await self.async_ensure_running(self.normal_speed)
        elif not should_run and self._running:
            await self._sample_water_temp_if_eligible()
            await self.async_ensure_stopped()

    def _should_run_now(self, now, window_start, window_end, run_hours):
        window_hours = (
            datetime.combine(now.date(), window_end) -
            datetime.combine(now.date(), window_start)
        ).total_seconds() / 3600

        if run_hours >= window_hours:
            return True

        pause_hours = window_hours - run_hours

        for num_blocks in [3, 2, 1]:
            block_hours = run_hours / num_blocks
            num_gaps = num_blocks + 1
            gap_hours = pause_hours / num_gaps

            if gap_hours * 60 < MIN_PAUSE_MINUTES and num_blocks > 1:
                continue

            minutes_into_window = (
                datetime.combine(now.date(), now.time()) -
                datetime.combine(now.date(), window_start)
            ).total_seconds() / 60

            cycle_minutes = (block_hours + gap_hours) * 60
            pos_in_cycle = (minutes_into_window - gap_hours * 60) % cycle_minutes

            return 0 <= pos_in_cycle < block_hours * 60

        return True

    def _parse_time(self, time_str):
        try:
            parts = time_str.split(":")
            return datetime.strptime(":".join(parts[:3]), "%H:%M:%S").time()
        except (ValueError, IndexError):
            try:
                return datetime.strptime(time_str, "%H:%M").time()
            except ValueError:
                log.error("Cannot parse time: %s", time_str)
                return None

    # --- Water temp sampling ---

    async def _sample_water_temp_if_eligible(self) -> None:
        if not self._run_start_time:
            return
        run_duration = time.monotonic() - self._run_start_time
        if run_duration < MIN_RUN_FOR_SAMPLE:
            return
        live_temp = self.water_temperature
        if live_temp is not None:
            old = self._buffered_water_temp
            self._buffered_water_temp = live_temp
            await self._persist_state()
            log.info("Buffered water temp: %s°C → %.1f°C", old, live_temp)

    # --- Shelly driver ---

    async def _call(self, domain, service, data):
        if self.test_mode:
            log.warning("[TEST MODE] would call %s.%s %s", domain, service, data)
            return
        await self.hass.services.async_call(domain, service, data, blocking=True)

    async def _set_speed_entity(self, speed):
        domain = self._speed_number.split(".", 1)[0]
        if domain == "light":
            if speed <= 0:
                await self._call("light", "turn_off", {"entity_id": self._speed_number})
            else:
                await self._call("light", "turn_on",
                                 {"entity_id": self._speed_number, "brightness_pct": speed})
        else:
            await self._call("number", "set_value",
                             {"entity_id": self._speed_number, "value": speed})

    async def async_ensure_running(self, speed):
        async with self._lock:
            log.info("ensure_running at %.0f%%", speed)
            power_state = self.hass.states.get(self._power_switch)
            if not power_state or power_state.state != "on":
                await self._call("switch", "turn_on", {"entity_id": self._power_switch})
                await asyncio.sleep(POWER_ON_DELAY)
            await self._set_speed_entity(speed)
            await self._call("switch", "turn_on", {"entity_id": self._start_switch})
            self._target_speed = speed
            if not self._running:
                self._run_start_time = time.monotonic()
            self._running = True
            self._notify()

    async def async_ensure_stopped(self):
        async with self._lock:
            log.info("ensure_stopped")
            await self._call("switch", "turn_off", {"entity_id": self._start_switch})
            await asyncio.sleep(STOP_DELAY)
            await self._set_speed_entity(0)
            self._target_speed = 0
            self._running = False
            self._run_start_time = None
            self._notify()

    async def async_set_speed(self, speed):
        async with self._lock:
            await self._set_speed_entity(speed)
            self._target_speed = speed
            self._notify()

    # --- Backwash counter ---

    async def async_reset_backwash_counter(self) -> None:
        """Reset the backwash counter to today."""
        self._last_backwash_date = datetime.now().date().isoformat()
        self._last_notification_date = None
        await self._persist_state()
        self._notify()
        log.info("Backwash counter reset to %s", self._last_backwash_date)

    # --- Listener pattern ---

    def add_listener(self, cb):
        self._listeners.append(cb)

    def remove_listener(self, cb):
        if cb in self._listeners:
            self._listeners.remove(cb)

    @callback
    def _notify(self):
        for cb in self._listeners:
            cb()
