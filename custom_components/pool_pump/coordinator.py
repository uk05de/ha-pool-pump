"""Pool Pump coordinator — owns state, scheduler, drives the Shellys."""

import asyncio
import logging
import time
from datetime import datetime, timedelta

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
    CONF_AUTOMATION_ENABLED,
    CONF_TEST_MODE,
    CONF_WINTER_OVERRIDE,
    CONF_WINTER_THRESHOLDS,
    CONF_NORMAL_WINDOW_START,
    CONF_NORMAL_WINDOW_END,
    CONF_NORMAL_SPEED,
    CONF_NORMAL_TEMP_DIVISOR,
    CONF_BACKWASH_DURATION,
    CONF_BACKWASH_SPEED,
    CONF_RINSE_DURATION,
    CONF_RINSE_SPEED,
    DEFAULT_THRESHOLDS,
    POWER_ON_DELAY,
    STOP_DELAY,
    MIN_RUN_FOR_SAMPLE,
    SCHEDULER_INTERVAL,
    MIN_PAUSE_MINUTES,
    MODE_OFF,
    MODE_NORMAL,
    MODE_FROST,
    MODE_BACKWASH,
    MODE_RINSE,
    MODE_MANUAL,
    TEMP_MIN_PLAUSIBLE,
    TEMP_MAX_PLAUSIBLE,
)

log = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}_state"
STORAGE_VERSION = 1


class PoolPumpCoordinator:
    """Central brain — scheduler, state, Shelly driver."""

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

        # Runtime state
        self._mode = MODE_OFF
        self._target_speed: float = 0
        self._running = False
        self._run_start_time: float | None = None  # monotonic
        self._last_frost_run_end: float | None = None  # monotonic
        self._frost_cycle_start: float | None = None  # monotonic, when current frost cycle started
        self._current_frost_threshold: dict | None = None  # active threshold during a frost cycle
        self._buffered_water_temp: float | None = None

        # Scheduler
        self._scheduler_task: asyncio.Task | None = None
        self._program_task: asyncio.Task | None = None  # for timed programs (backwash, rinse)

        # Listeners
        self._listeners: list = []
        self._lock = asyncio.Lock()
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    # --- Options accessors ---

    @property
    def automation_enabled(self) -> bool:
        return self.entry.options.get(CONF_AUTOMATION_ENABLED, True)

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
    def mode(self) -> str:
        return self._mode

    @property
    def target_speed(self) -> float:
        return self._target_speed

    @property
    def running(self) -> bool:
        return self._running

    @property
    def buffered_water_temp(self) -> float | None:
        return self._buffered_water_temp

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
        """Average of all plausible outside sensors."""
        values = [v for e in self._outside_temps if (v := self._read_temp(e)) is not None]
        return sum(values) / len(values) if values else None

    @property
    def water_temperature(self) -> float | None:
        """Live reading from Dosieranlage (only reliable when pump is running)."""
        return self._read_temp(self._water_temp_entity)

    @property
    def room_temperature(self) -> float | None:
        return self._read_temp(self._room_temp_entity)

    # --- Lifecycle ---

    async def async_setup(self) -> None:
        """Called during integration setup."""
        # Restore persisted state
        stored = await self._store.async_load()
        if stored:
            self._buffered_water_temp = stored.get("buffered_water_temp")
            log.info("Restored buffered water temp: %s°C", self._buffered_water_temp)

        # Start scheduler as background task (must not block setup)
        self._scheduler_task = self.entry.async_create_background_task(
            self.hass, self._scheduler_loop(), "pool_pump_scheduler"
        )
        log.info("Pool Pump coordinator started (test_mode=%s)", self.test_mode)

    async def async_shutdown(self) -> None:
        """Called during integration unload."""
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
        if self._program_task and not self._program_task.done():
            self._program_task.cancel()
        await self._persist_state()
        log.info("Pool Pump coordinator stopped")

    async def _persist_state(self) -> None:
        await self._store.async_save({
            "buffered_water_temp": self._buffered_water_temp,
        })

    # --- Scheduler ---

    async def _scheduler_loop(self) -> None:
        """Main loop — evaluates conditions every SCHEDULER_INTERVAL seconds."""
        await asyncio.sleep(10)  # let HA settle on startup
        while True:
            try:
                await self._evaluate()
            except Exception:
                log.exception("Scheduler error")
            await asyncio.sleep(SCHEDULER_INTERVAL)

    async def _evaluate(self) -> None:
        """Decide what the pump should be doing right now."""
        # Don't interfere with manual mode or active programs (backwash/rinse)
        if self._mode in (MODE_MANUAL, MODE_BACKWASH, MODE_RINSE):
            return

        # Automation disabled — do nothing
        if not self.automation_enabled:
            if self._running and self._mode in (MODE_NORMAL, MODE_FROST):
                log.info("Automation disabled, stopping pump")
                await self.async_ensure_stopped()
                self._mode = MODE_OFF
                self._notify()
            return

        outside = self.outside_temperature
        frost_needed = False

        # Check frost protection
        if self.winter_override:
            frost_needed = True
        elif outside is not None:
            threshold = self._find_threshold(outside)
            if threshold is not None:
                frost_needed = True

        if frost_needed:
            # If switching from normal to frost, stop normal operation first
            if self._running and self._mode == MODE_NORMAL:
                await self._sample_water_temp_if_eligible()
                await self.async_ensure_stopped()
            await self._handle_frost_mode(outside)
        else:
            # If switching from frost to normal, stop frost first
            if self._running and self._mode == MODE_FROST:
                await self.async_ensure_stopped()
                self._frost_cycle_start = None
                self._current_frost_threshold = None
                if self._program_task and not self._program_task.done():
                    self._program_task.cancel()
                    self._program_task = None
            await self._handle_normal_mode()

    def _find_threshold(self, temp: float) -> dict | None:
        """Find the most aggressive matching frost threshold.

        Thresholds are sorted descending by below_temp. We walk through
        and return the last one where temp < below_temp (= most aggressive).
        """
        match = None
        for t in sorted(self.thresholds, key=lambda x: x["below_temp"], reverse=True):
            if temp < t["below_temp"]:
                match = t
        return match

    # --- Frost protection ---

    async def _handle_frost_mode(self, outside: float | None) -> None:
        """Run frost protection — reactive to threshold changes mid-run."""
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

        # --- Already running a frost cycle ---
        if self._running and self._mode == MODE_FROST:
            if threshold_changed:
                old = self._current_frost_threshold
                self._current_frost_threshold = threshold
                log.info("Frost threshold changed mid-run: below %d°C → below %d°C",
                         old["below_temp"], threshold["below_temp"])

                # Adjust speed immediately
                if self._target_speed != threshold["speed"]:
                    log.info("Adjusting speed: %d%% → %d%%", self._target_speed, threshold["speed"])
                    await self.async_set_speed(threshold["speed"])

                if is_continuous:
                    # Cancel timed stop — switch to continuous
                    if self._program_task and not self._program_task.done():
                        self._program_task.cancel()
                        self._program_task = None
                    log.info("Switched to continuous frost protection at %d%%", threshold["speed"])
                else:
                    # Adjust remaining duration
                    elapsed = time.monotonic() - self._frost_cycle_start if self._frost_cycle_start else 0
                    new_duration_sec = threshold["duration_min"] * 60
                    remaining = max(0, new_duration_sec - elapsed)

                    if remaining > 0 and self._program_task and not self._program_task.done():
                        # Cancel old timer, start new one with remaining time
                        self._program_task.cancel()
                        log.info("Adjusted frost cycle: %.0fs remaining at %d%%", remaining, threshold["speed"])
                        self._program_task = self.entry.async_create_background_task(
                            self.hass, self._frost_timed_stop(remaining), "pool_pump_frost_cycle"
                        )
            return

        # --- Not running yet ---
        self._current_frost_threshold = threshold

        if is_continuous:
            log.info("Frost protection: continuous at %d%% (outside=%.1f°C)",
                     threshold["speed"], outside or 0)
            self._mode = MODE_FROST
            self._frost_cycle_start = time.monotonic()
            self._notify()
            await self.async_ensure_running(threshold["speed"])
        else:
            # Interval mode — check if it's time
            now = time.monotonic()
            interval_sec = threshold["interval_min"] * 60
            if self._last_frost_run_end is None or (now - self._last_frost_run_end) >= interval_sec:
                duration_sec = threshold["duration_min"] * 60
                log.info("Frost protection: %dmin at %d%% (outside=%.1f°C, interval=%dmin)",
                         threshold["duration_min"], threshold["speed"],
                         outside or 0, threshold["interval_min"])
                self._mode = MODE_FROST
                self._frost_cycle_start = time.monotonic()
                self._notify()
                await self.async_ensure_running(threshold["speed"])
                self._program_task = self.entry.async_create_background_task(
                    self.hass, self._frost_timed_stop(duration_sec), "pool_pump_frost_cycle"
                )

    async def _frost_timed_stop(self, duration: float) -> None:
        """Stop frost cycle after duration seconds. Mode stays on frost_protection."""
        await asyncio.sleep(duration)
        await self._sample_water_temp_if_eligible()
        await self.async_ensure_stopped()
        self._last_frost_run_end = time.monotonic()
        self._frost_cycle_start = None
        self._current_frost_threshold = None
        # Mode stays MODE_FROST — scheduler will re-evaluate and either
        # keep frost mode (still cold) or switch to normal/off
        self._notify()

    # --- Normal operation ---

    async def _handle_normal_mode(self) -> None:
        """Run normal filtering based on water temperature and time window."""
        now = datetime.now()
        window_start = self._parse_time(self.normal_window_start)
        window_end = self._parse_time(self.normal_window_end)

        if not window_start or not window_end:
            return

        in_window = window_start <= now.time() <= window_end

        # Calculate required runtime
        water_temp = self._buffered_water_temp
        if water_temp is None:
            # No data yet — use conservative default (6 hours)
            run_hours = 6.0
        else:
            run_hours = max(1.0, water_temp / self.temp_divisor)

        # Check if pump should be running now
        should_run = in_window and self._should_run_now(now, window_start, window_end, run_hours)

        # Set mode to normal while in the time window (even during pauses)
        if in_window:
            if self._mode != MODE_NORMAL:
                self._mode = MODE_NORMAL
                self._notify()
        else:
            if self._mode == MODE_NORMAL:
                self._mode = MODE_OFF
                self._notify()

        if should_run and not self._running:
            await self.async_ensure_running(self.normal_speed)
        elif not should_run and self._running and self._mode == MODE_NORMAL:
            await self._sample_water_temp_if_eligible()
            await self.async_ensure_stopped()

    def _should_run_now(self, now: datetime, window_start, window_end, run_hours: float) -> bool:
        """Determine if pump should run at this moment — distributes runtime across window."""
        window_hours = (
            datetime.combine(now.date(), window_end) -
            datetime.combine(now.date(), window_start)
        ).total_seconds() / 3600

        if run_hours >= window_hours:
            # Need to run longer than window — run continuously
            return True

        pause_hours = window_hours - run_hours

        # Try 3 blocks, then 2, then 1
        for num_blocks in [3, 2, 1]:
            block_hours = run_hours / num_blocks
            num_gaps = num_blocks + 1  # gaps before, between, and after blocks
            gap_hours = pause_hours / num_gaps

            if gap_hours * 60 < MIN_PAUSE_MINUTES and num_blocks > 1:
                continue

            # Calculate if we're in a run block
            minutes_into_window = (
                datetime.combine(now.date(), now.time()) -
                datetime.combine(now.date(), window_start)
            ).total_seconds() / 60

            cycle_minutes = (block_hours + gap_hours) * 60
            pos_in_cycle = (minutes_into_window - gap_hours * 60) % cycle_minutes

            if 0 <= pos_in_cycle < block_hours * 60:
                return True

            return False

        return True  # fallback: run

    def _parse_time(self, time_str: str):
        """Parse HH:MM:SS or HH:MM string to time object."""
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
        """Sample water temp if pump has been running long enough."""
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
            log.info("Buffered water temp updated: %s°C → %.1f°C (after %.0fs run)",
                     old, live_temp, run_duration)

    # --- Shelly service calls ---

    async def _call(self, domain: str, service: str, data: dict) -> None:
        if self.test_mode:
            log.warning("[TEST MODE] would call %s.%s %s", domain, service, data)
            return
        await self.hass.services.async_call(domain, service, data, blocking=True)

    async def _async_set_speed_entity(self, speed: float) -> None:
        domain = self._speed_number.split(".", 1)[0]
        if domain == "light":
            if speed <= 0:
                await self._call("light", "turn_off", {"entity_id": self._speed_number})
            else:
                await self._call(
                    "light", "turn_on",
                    {"entity_id": self._speed_number, "brightness_pct": speed},
                )
        else:
            await self._call(
                "number", "set_value",
                {"entity_id": self._speed_number, "value": speed},
            )

    async def async_ensure_running(self, speed: float) -> None:
        """Bring the pump up at a given speed."""
        async with self._lock:
            log.info("ensure_running at %.0f%% (test_mode=%s)", speed, self.test_mode)

            power_state = self.hass.states.get(self._power_switch)
            if not power_state or power_state.state != "on":
                await self._call("switch", "turn_on", {"entity_id": self._power_switch})
                await asyncio.sleep(POWER_ON_DELAY)

            await self._async_set_speed_entity(speed)
            await self._call("switch", "turn_on", {"entity_id": self._start_switch})

            self._target_speed = speed
            if not self._running:
                self._run_start_time = time.monotonic()
            self._running = True
            self._notify()

    async def async_ensure_stopped(self) -> None:
        """Stop the pump — keeps mains power on (VFD-friendly)."""
        async with self._lock:
            log.info("ensure_stopped (test_mode=%s)", self.test_mode)

            await self._call("switch", "turn_off", {"entity_id": self._start_switch})
            await asyncio.sleep(STOP_DELAY)
            await self._async_set_speed_entity(0)

            self._target_speed = 0
            self._running = False
            self._run_start_time = None
            self._notify()

    async def async_set_speed(self, speed: float) -> None:
        """Change speed while running."""
        async with self._lock:
            await self._async_set_speed_entity(speed)
            self._target_speed = speed
            self._notify()

    # --- Manual program triggers ---

    async def async_start_backwash(self) -> None:
        """Run backwash for configured duration."""
        duration = self.entry.options.get(CONF_BACKWASH_DURATION, 3) * 60
        speed = self.entry.options.get(CONF_BACKWASH_SPEED, 70)
        log.info("Starting backwash: %ds at %d%%", duration, speed)
        if self._program_task and not self._program_task.done():
            self._program_task.cancel()
        self._mode = MODE_BACKWASH
        self._notify()
        await self.async_ensure_running(speed)
        self._program_task = self.entry.async_create_background_task(
            self.hass, self._timed_stop(duration), "pool_pump_backwash"
        )

    async def async_start_rinse(self) -> None:
        """Run rinse for configured duration."""
        duration = self.entry.options.get(CONF_RINSE_DURATION, 1) * 60
        speed = self.entry.options.get(CONF_RINSE_SPEED, 50)
        log.info("Starting rinse: %ds at %d%%", duration, speed)
        if self._program_task and not self._program_task.done():
            self._program_task.cancel()
        self._mode = MODE_RINSE
        self._notify()
        await self.async_ensure_running(speed)
        self._program_task = self.entry.async_create_background_task(
            self.hass, self._timed_stop(duration), "pool_pump_rinse"
        )

    async def async_set_mode(self, mode: str) -> None:
        """Manually set the operating mode."""
        self._mode = mode
        self._notify()
        log.info("Mode set to %s", mode)

    async def async_evaluate_now(self) -> None:
        """Force immediate scheduler evaluation (e.g. after leaving manual mode)."""
        log.info("Immediate evaluation triggered")
        await self._evaluate()

    # --- Listener pattern ---

    def add_listener(self, update_callback) -> None:
        self._listeners.append(update_callback)

    def remove_listener(self, update_callback) -> None:
        if update_callback in self._listeners:
            self._listeners.remove(update_callback)

    @callback
    def _notify(self) -> None:
        for listener in self._listeners:
            listener()
