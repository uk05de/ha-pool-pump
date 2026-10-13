"""Pool Pump coordinator — program management, scheduler, Shelly driver."""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    CONF_POWER_SWITCH,
    CONF_SPEED_NUMBER,
    CONF_START_SWITCH,
    CONF_OUTSIDE_TEMPS,
    CONF_WATER_TEMP,
    CONF_ROOM_TEMP,
    CONF_SIMPLE_MODE,
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
    CONF_FRESHWATER_SWITCH,
    CONF_FRESHWATER_DURATION,
    DEFAULT_FRESHWATER_DURATION,
    CONF_HEAT_PUMP_POWER_ENTITY,
    CONF_HEAT_PUMP_PROGRAMS,
    CONF_HEAT_PUMP_SHUTDOWN_DELAY,
    DEFAULT_HEAT_PUMP_SHUTDOWN_DELAY,
    CONF_DOSING_SWITCHES,
    CONF_DOSING_PROGRAMS,
    CONF_DOSING_SHUTDOWN_DELAY,
    DEFAULT_DOSING_SHUTDOWN_DELAY,
    CONF_NOTIFY_SERVICE,
    CONF_ROOM_FROST_TEMP,
    DEFAULT_ROOM_FROST_TEMP,
    CONF_WATER_WARN_TEMP,
    DEFAULT_WATER_WARN_TEMP,
    WATCHDOG_GRACE_UNPROTECTED,
    WATCHDOG_GRACE_HARDWARE,
    WATCHDOG_GRACE_SENSOR,
    DEFAULT_PROGRAMS,
    DEFAULT_THRESHOLDS,
    POWER_ON_DELAY,
    STOP_DELAY,
    MIN_RUN_FOR_SAMPLE,
    SCHEDULER_INTERVAL,
    MIN_PAUSE_MINUTES,
    FROST_HYSTERESIS_C,
    MODE_MANUAL,
    MODE_AUTOMATIK,
    TEMP_MIN_PLAUSIBLE,
    TEMP_MAX_PLAUSIBLE,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownstreamDevice:
    """A device whose power follows the pool pump's run state.

    Examples: a heat pump that needs cool-down flow, or a dosing pump set
    whose chemistry must flush out of the cell before flow stops. Each
    device declares its own allowlist of programs in which it should run,
    its own user-enable flag ("mit-schalten" switch), and its own
    shutdown delay; the pool pump's nachlauf waits max(delay) across all
    devices that need takedown.
    """

    name: str                # short identifier for logs ("heat_pump", "dosing")
    entities: tuple[str, ...]   # one or more switch entity IDs
    allowlist: tuple[str, ...]  # active_program must be in here
    enabled: bool            # user mit-schalten flag
    shutdown_delay: int      # seconds of nachlauf for this device

    def co_controlled_for(self, active_program: str | None) -> bool:
        return (
            bool(self.entities)
            and self.enabled
            and active_program in self.allowlist
        )

STORAGE_KEY = f"{DOMAIN}_state"
STORAGE_VERSION = 1


def _parse_dt(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


class PoolPumpCoordinator:
    """Central brain — program switches, scheduler, Shelly driver."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry

        # Shelly entity IDs (power_switch / speed_number absent in simple mode)
        self._power_switch = entry.data.get(CONF_POWER_SWITCH)
        self._speed_number = entry.data.get(CONF_SPEED_NUMBER)
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

        # Technikraum below room_frost_temp (with hysteresis) → frost
        # protection runs continuously. Updated every scheduler tick.
        self._room_cold = False

        # Frost watchdog: condition key → first seen (monotonic), and the
        # conditions that have outlasted their grace period and were sent.
        self._watch_since: dict[str, float] = {}
        self._watch_alerts: dict[str, str] = {}

        # run_hours is only recalculated while the pump is running. This
        # prevents a water-temp sample (taken right at block end) from
        # shifting the block schedule and causing a phantom restart.
        self._cached_run_hours: float | None = None

        # Freshwater
        self._freshwater_switch: str | None = entry.data.get(CONF_FRESHWATER_SWITCH)
        self._freshwater_running = False
        self._freshwater_task: asyncio.Task | None = None

        # Backwash reminder
        self._last_backwash_date: str | None = None
        self._last_notification_date: str | None = None

        # Downstream-device user gates ("mit-schalten" switches). Persisted
        # in the coordinator store rather than entry.options to avoid
        # triggering a full reload (which would kill any running program)
        # on toggle.
        self._heat_pump_enabled: bool = False
        self._dosing_enabled: bool = False

        # Tasks
        self._scheduler_task: asyncio.Task | None = None
        self._program_task: asyncio.Task | None = None
        self._nachlauf_task: asyncio.Task | None = None
        # Wall-clock end time of the currently running timed program. Used
        # by the "Nächster Wechsel" sensor. Frost cycles compute their end
        # separately via `_frost_cycle_start` + threshold duration.
        self._program_end_at: datetime | None = None
        # Wall-clock end time of the heat-pump shutdown nachlauf, if active.
        self._nachlauf_end_at: datetime | None = None

        # Listeners + persistence
        self._listeners: list = []
        self._lock = asyncio.Lock()
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)

        # Restart handling: the stored state is kept until the hardware has
        # been reconciled (after HA has started); the scheduler waits for it.
        self._restored: dict = {}
        self._setup_program: str | None = None
        self._ready = asyncio.Event()

    # --- Options accessors ---

    @property
    def test_mode(self) -> bool:
        return self.entry.options.get(CONF_TEST_MODE, False)

    @property
    def simple_mode(self) -> bool:
        """No power switch, no speed control — just dumb on/off via start_switch."""
        return self.entry.data.get(CONF_SIMPLE_MODE, False)

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

    @property
    def notify_service(self) -> str | None:
        return self.entry.options.get(CONF_NOTIFY_SERVICE) or None

    @property
    def room_frost_temp(self) -> float:
        return self.entry.options.get(CONF_ROOM_FROST_TEMP, DEFAULT_ROOM_FROST_TEMP)

    @property
    def water_warn_temp(self) -> float:
        return self.entry.options.get(CONF_WATER_WARN_TEMP, DEFAULT_WATER_WARN_TEMP)

    # --- Heat pump accessors ---

    @property
    def heat_pump_power_entity(self) -> str | None:
        return self.entry.options.get(CONF_HEAT_PUMP_POWER_ENTITY) or None

    @property
    def heat_pump_programs(self) -> list[str]:
        return self.entry.options.get(CONF_HEAT_PUMP_PROGRAMS, []) or []

    @property
    def heat_pump_shutdown_delay(self) -> int:
        return self.entry.options.get(
            CONF_HEAT_PUMP_SHUTDOWN_DELAY, DEFAULT_HEAT_PUMP_SHUTDOWN_DELAY
        )

    @property
    def heat_pump_configured(self) -> bool:
        return bool(self.heat_pump_power_entity)

    @property
    def heat_pump_enabled(self) -> bool:
        """User wants the pool pump to co-control the heat pump."""
        return self._heat_pump_enabled

    # --- Dosing accessors ---

    @property
    def dosing_switches(self) -> list[str]:
        return self.entry.options.get(CONF_DOSING_SWITCHES, []) or []

    @property
    def dosing_programs(self) -> list[str]:
        return self.entry.options.get(CONF_DOSING_PROGRAMS, []) or []

    @property
    def dosing_shutdown_delay(self) -> int:
        return self.entry.options.get(
            CONF_DOSING_SHUTDOWN_DELAY, DEFAULT_DOSING_SHUTDOWN_DELAY
        )

    @property
    def dosing_configured(self) -> bool:
        return bool(self.dosing_switches)

    @property
    def dosing_enabled(self) -> bool:
        """User wants the pool pump to co-control the dosing pumps."""
        return self._dosing_enabled

    # --- Nachlauf state ---

    @property
    def in_nachlauf(self) -> bool:
        return self._nachlauf_task is not None and not self._nachlauf_task.done()

    @property
    def nachlauf_end_at(self) -> datetime | None:
        return self._nachlauf_end_at

    # --- Downstream device collection ---

    def _downstream_devices(self) -> list[DownstreamDevice]:
        """All configured downstream devices, in stop-order (HP first, then dosing).

        Order matters for the takedown loop: we send turn_off in this order,
        but the actual wait is max() across them so total time = slowest
        device's delay.
        """
        devs: list[DownstreamDevice] = []
        if self.heat_pump_power_entity:
            devs.append(DownstreamDevice(
                name="heat_pump",
                entities=(self.heat_pump_power_entity,),
                allowlist=tuple(self.heat_pump_programs),
                enabled=self._heat_pump_enabled,
                shutdown_delay=self.heat_pump_shutdown_delay,
            ))
        if self.dosing_switches:
            devs.append(DownstreamDevice(
                name="dosing",
                entities=tuple(self.dosing_switches),
                allowlist=tuple(self.dosing_programs),
                enabled=self._dosing_enabled,
                shutdown_delay=self.dosing_shutdown_delay,
            ))
        return devs

    def _device_any_entity_on(self, dev: DownstreamDevice) -> bool:
        for ent in dev.entities:
            state = self.hass.states.get(ent)
            if state and state.state == "on":
                return True
        return False

    async def _device_turn_on(self, dev: DownstreamDevice) -> None:
        log.info("downstream %s: ON (program=%s)", dev.name, self._active_program)
        for ent in dev.entities:
            await self._call("switch", "turn_on", {"entity_id": ent})

    async def _device_turn_off(self, dev: DownstreamDevice) -> None:
        log.info("downstream %s: OFF", dev.name)
        for ent in dev.entities:
            await self._call("switch", "turn_off", {"entity_id": ent})

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
    def next_transition_at(self) -> datetime | None:
        """Wall-clock time of the next pump state change.

        - Nachlauf running: nachlauf end time (highest priority)
        - Timed program running: program end time
        - Automatik / frost interval: end of current run, or next run start
        - Automatik / normal: end of current block, or next block start
        - Otherwise (manual, frost continuous, outside window with no schedule): None
        """
        # Nachlauf — about to actually stop the pool pump
        if self._nachlauf_end_at:
            return self._nachlauf_end_at

        # Timed program (custom user-program) → simple stored end time
        if self._program_end_at and self._active_program not in (None, MODE_AUTOMATIK):
            return self._program_end_at

        if self._active_program != MODE_AUTOMATIK:
            return None

        # Frost protection
        if self._auto_sub_mode == "frost_protection" and self._current_frost_threshold:
            t = self._current_frost_threshold
            if t["interval_min"] == 0 and t["duration_min"] == 0:
                return None  # continuous — no transition planned
            now_mono = time.monotonic()
            now_wall = dt_util.now()
            if self._running and self._frost_cycle_start is not None:
                seconds_left = (self._frost_cycle_start + t["duration_min"] * 60) - now_mono
                return now_wall + timedelta(seconds=max(0, seconds_left))
            if self._last_frost_run_end is not None:
                seconds_left = (self._last_frost_run_end + t["interval_min"] * 60) - now_mono
                return now_wall + timedelta(seconds=max(0, seconds_left))
            return None

        # Normal automatik mode
        if self._auto_sub_mode == "normal":
            return self._normal_next_transition(dt_util.now())

        return None

    def _normal_next_transition(self, now: datetime) -> datetime | None:
        """Compute next block-boundary transition for normal mode."""
        window_start = self._parse_time(self.normal_window_start)
        window_end = self._parse_time(self.normal_window_end)
        if not window_start or not window_end:
            return None

        today_start = now.replace(hour=window_start.hour, minute=window_start.minute,
                                  second=window_start.second, microsecond=0)
        today_end = now.replace(hour=window_end.hour, minute=window_end.minute,
                                second=window_end.second, microsecond=0)

        # Outside window — next transition is the next window start
        if now < today_start:
            return today_start
        if now > today_end:
            return today_start + timedelta(days=1)

        water_temp = self._buffered_water_temp
        run_hours_fresh = max(1.0, water_temp / self.temp_divisor) if water_temp else 6.0
        run_hours = self._cached_run_hours or run_hours_fresh

        window_hours = (today_end - today_start).total_seconds() / 3600
        if run_hours >= window_hours:
            return today_end  # continuous in window → ends with window

        pause_hours = window_hours - run_hours
        for num_blocks in [3, 2, 1]:
            block_hours = run_hours / num_blocks
            num_gaps = num_blocks + 1
            gap_hours = pause_hours / num_gaps
            if gap_hours * 60 < MIN_PAUSE_MINUTES and num_blocks > 1:
                continue
            cycle_min = (block_hours + gap_hours) * 60
            block_min = block_hours * 60
            gap_min = gap_hours * 60
            minutes_into_window = (now - today_start).total_seconds() / 60
            pos_in_cycle = (minutes_into_window - gap_min) % cycle_min
            if 0 <= pos_in_cycle < block_min:
                return now + timedelta(minutes=block_min - pos_in_cycle)
            return now + timedelta(minutes=cycle_min - pos_in_cycle)
        return today_end

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
            return (dt_util.now().date() - last).days
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
        stored = await self._store.async_load() or {}
        self._restored = stored
        restored_program: str | None = stored.get("active_program")
        if stored:
            self._buffered_water_temp = stored.get("buffered_water_temp")
            self._last_backwash_date = stored.get("last_backwash_date")
            self._heat_pump_enabled = bool(stored.get("heat_pump_enabled", False))
            self._dosing_enabled = bool(stored.get("dosing_enabled", False))
            log.info(
                "Restored buffered water temp: %s°C, last backwash: %s, "
                "active program: %s, running: %s, heat pump enabled: %s, "
                "dosing enabled: %s",
                self._buffered_water_temp, self._last_backwash_date,
                restored_program, stored.get("running"),
                self._heat_pump_enabled, self._dosing_enabled,
            )

        # Automatik is restored right away; timed programs, manual runs and
        # an interrupted nachlauf need the real Shelly state first — see
        # `_async_reconcile_hardware`.
        if restored_program == MODE_AUTOMATIK:
            self._active_program = MODE_AUTOMATIK
            log.info("Re-activating automatik from persisted state")
        self._setup_program = self._active_program

        self._scheduler_task = self.entry.async_create_background_task(
            self.hass, self._scheduler_loop(), "pool_pump_scheduler"
        )
        # Shelly states are only meaningful once HA has started (runs
        # immediately on an entry reload).
        self.entry.async_on_unload(
            async_at_started(self.hass, self._async_reconcile_hardware)
        )

        log.info("Pool Pump coordinator started (test_mode=%s)", self.test_mode)

    # --- Restart reconciliation ---

    def _hardware_running(self) -> bool:
        start = self.hass.states.get(self._start_switch)
        if not start or start.state != "on":
            return False
        if self.simple_mode:
            return True
        power = self.hass.states.get(self._power_switch)
        return bool(power and power.state == "on")

    def _hardware_speed(self) -> float:
        if self.simple_mode:
            return 100
        state = self.hass.states.get(self._speed_number)
        try:
            if self._speed_number.startswith("light."):
                if state.state != "on":
                    return 0
                return round(state.attributes.get("brightness", 0) / 255 * 100)
            return float(state.state)
        except (AttributeError, TypeError, ValueError):
            return self.normal_speed

    async def _async_reconcile_hardware(self, _hass: HomeAssistant | None = None) -> None:
        """Align coordinator state with the real Shelly state after (re)start.

        Shelly relays keep their state across HA restarts and entry reloads,
        so the pump may still be running although a fresh coordinator
        believes it is stopped. Adopt the real state first, then finish
        whatever was in flight before (nachlauf, timed program, manual run).
        """
        try:
            await self._reconcile()
        except Exception:
            log.exception("Hardware reconcile failed")
        finally:
            self._restored = {}
            self._ready.set()
            self._notify()

    async def _reconcile(self) -> None:
        stored = self._restored
        if self._hardware_running():
            async with self._lock:
                self._running = True
                # Water temp sampling restarts its MIN_RUN_FOR_SAMPLE window.
                self._run_start_time = time.monotonic()
                self._target_speed = self._hardware_speed()
            log.info("Pump found running at %.0f%% — adopting", self._target_speed)

        if self._active_program != self._setup_program:
            return  # user already switched programs — don't override that

        now = dt_util.now()
        program = stored.get("active_program")
        nachlauf_end = _parse_dt(stored.get("nachlauf_end_at"))

        if nachlauf_end is not None:
            if self._running:
                remaining = max(0.0, (nachlauf_end - now).total_seconds())
                log.info("Resuming interrupted nachlauf (%.0fs left)", remaining)
                self._nachlauf_end_at = now + timedelta(seconds=remaining)
                self._nachlauf_task = self.entry.async_create_background_task(
                    self.hass, self._nachlauf_then_stop(remaining),
                    "pool_pump_nachlauf",
                )
        elif program not in (None, MODE_AUTOMATIK):
            prog = self._find_program(program)
            if prog:
                await self._restore_timed_program(
                    prog, _parse_dt(stored.get("program_end_at")), now
                )
        elif program is None and stored.get("running") and not self._running:
            speed = stored.get("target_speed") or self.normal_speed
            log.info("Manual run was interrupted — restarting at %.0f%%", speed)
            await self.async_ensure_running(speed)

        await self._persist_state()

    async def _restore_timed_program(
        self, prog: dict, end_at: datetime | None, now: datetime
    ) -> None:
        name = prog["name"]
        continuous = prog["duration_min"] == 0

        if not self._running and not continuous:
            # Pump went down together with HA (power cut?). Don't restart a
            # timed program on our own — Backwash etc. need the 6-way valve
            # set by hand with the pump stopped.
            log.info("Program '%s' was interrupted with pump off — not resuming", name)
            return

        self._active_program = name
        if continuous:
            log.info("Resuming continuous program '%s'", name)
            if not self._running:
                await self.async_ensure_running(prog["speed"])
            return

        remaining = max(0.0, (end_at - now).total_seconds()) if end_at else 0.0
        log.info("Resuming program '%s' (%.0fs left)", name, remaining)
        self._program_end_at = now + timedelta(seconds=remaining)
        self._program_task = self.entry.async_create_background_task(
            self.hass, self._program_timed_stop(remaining, name),
            f"pool_pump_{name}",
        )

    async def async_shutdown(self) -> None:
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
        if self._program_task and not self._program_task.done():
            self._program_task.cancel()
        if self._nachlauf_task and not self._nachlauf_task.done():
            self._nachlauf_task.cancel()
        if self._freshwater_task and not self._freshwater_task.done():
            self._freshwater_task.cancel()
        await self._persist_state()
        log.info("Pool Pump coordinator stopped")

    async def _persist_state(self) -> None:
        await self._store.async_save({
            "buffered_water_temp": self._buffered_water_temp,
            "last_backwash_date": self._last_backwash_date,
            "active_program": self._active_program,
            "heat_pump_enabled": self._heat_pump_enabled,
            "dosing_enabled": self._dosing_enabled,
            # Run state — lets `_reconcile` finish what a restart interrupted
            "running": self._running,
            "target_speed": self._target_speed,
            "program_end_at": (
                self._program_end_at.isoformat() if self._program_end_at else None
            ),
            "nachlauf_end_at": (
                self._nachlauf_end_at.isoformat() if self._nachlauf_end_at else None
            ),
        })

    # --- Program switching ---

    async def async_activate_program(self, program_name: str) -> None:
        """Activate a program. Smooth transition — does NOT stop the pump.

        Switching between programs (or from manual to a program) just adjusts
        speed via `ensure_running`. The pump is only stopped when the user
        explicitly deactivates a program → manual.
        """
        if self._active_program == program_name:
            return

        # Cancel any active program/frost timer + reset frost state, but
        # leave the pump running — the new program will adjust the speed.
        self._cancel_program_state()

        self._active_program = program_name
        await self._persist_state()
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
                    self._program_end_at = dt_util.now() + timedelta(seconds=duration_sec)
                    self._program_task = self.entry.async_create_background_task(
                        self.hass,
                        self._program_timed_stop(duration_sec, program_name),
                        f"pool_pump_{program_name}",
                    )
                    await self._persist_state()
                    self._notify()

    async def async_deactivate_program(self, program_name: str) -> None:
        """Deactivate a specific program → manual mode."""
        if self._active_program != program_name:
            return
        await self._stop_current_program()
        self._active_program = None
        await self._persist_state()
        self._notify()
        log.info("Program deactivated: %s → manual mode", program_name)

    def _cancel_program_state(self) -> None:
        """Cancel program task + reset frost/auto state. Does NOT touch the pump."""
        if self._program_task and not self._program_task.done():
            self._program_task.cancel()
        self._program_task = None
        self._program_end_at = None
        self._frost_cycle_start = None
        self._current_frost_threshold = None
        self._auto_sub_mode = None

    async def _stop_current_program(self) -> None:
        """Stop whatever is currently running. Used when going to manual.

        Sampling water temp is the stop sequence's responsibility — it
        happens inside `ensure_stopped` (immediate path) or `_nachlauf_then_stop`
        (graceful path), right before the actual pool stop. Sampling here
        too would update `_buffered_water_temp` while `_running` is still
        True during nachlauf, shifting `_cached_run_hours` and triggering
        a phantom restart after nachlauf ends.
        """
        self._cancel_program_state()
        if self._running:
            await self.async_ensure_stopped()

    async def _program_timed_stop(self, duration: float, program_name: str) -> None:
        """Auto-stop a timed program after duration.

        Always falls back to manual mode. Auto-resume to automatik is not
        offered because all current custom programs (Backwash etc.) require
        a manual 6-way-valve change with the pump stopped first.
        """
        await asyncio.sleep(duration)
        log.info("Program '%s' finished after %.0fs", program_name, duration)
        await self.async_ensure_stopped()
        if program_name == self.backwash_program_name:
            await self.async_reset_backwash_counter()
        self._active_program = None
        self._program_task = None
        self._program_end_at = None
        await self._persist_state()
        self._notify()

    async def async_pump_switch_off(self) -> None:
        """User turned off pump switch → cancel any active program, go manual."""
        await self._stop_current_program()
        self._active_program = None
        await self._persist_state()
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
        await self._ready.wait()
        while True:
            try:
                self._update_room_cold()
                if self._active_program == MODE_AUTOMATIK:
                    await self._evaluate()
                await self._check_backwash_reminder()
                await self._check_frost_watchdog()
            except Exception:
                log.exception("Scheduler error")
            await asyncio.sleep(SCHEDULER_INTERVAL)

    async def _check_backwash_reminder(self) -> None:
        """Send daily notification if backwash is overdue."""
        # Manual mode (e.g. winter shutdown, pool drained) → stay silent.
        if self._active_program != MODE_AUTOMATIK:
            return
        if not self.backwash_overdue:
            return
        today = dt_util.now().date().isoformat()
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

    # --- Frost watchdog ---

    def _update_room_cold(self) -> None:
        room = self.room_temperature
        if room is None:
            self._room_cold = False
            return
        limit = self.room_frost_temp
        if self._room_cold:
            limit += FROST_HYSTERESIS_C
        if (room < limit) != self._room_cold:
            self._room_cold = room < limit
            log.info("Technikraum %.1f°C → room frost %s", room,
                     "active" if self._room_cold else "cleared")

    def _frost_relevant(self) -> bool:
        """Is it cold enough that the pool needs frost protection?

        An unavailable outside sensor counts as relevant — we can't rule
        frost out, so the watchdog should keep watching.
        """
        if self.winter_override or self._room_cold:
            return True
        if not self.thresholds:
            return False
        if self._outside_temps:
            outside = self.outside_temperature
            if outside is None:
                return True
            mildest = max(t["below_temp"] for t in self.thresholds)
            return outside < mildest
        return False

    def _watchdog_conditions(self) -> dict[str, tuple[float, str, str]]:
        """Currently true problems → (grace seconds, title, message)."""
        conds: dict[str, tuple[float, str, str]] = {}
        frost = self._frost_relevant()

        if frost and self._active_program != MODE_AUTOMATIK and not self._running:
            conds["unprotected"] = (
                WATCHDOG_GRACE_UNPROTECTED, "Frostschutz inaktiv",
                "Es ist frostig, aber die Automatik ist aus und die Pumpe steht. "
                "Programm Automatik wieder einschalten!",
            )

        if frost:
            hw = [e for e in (self._power_switch, self._speed_number, self._start_switch) if e]
            unavailable = [
                e for e in hw
                if (st := self.hass.states.get(e)) is None
                or st.state in ("unavailable", "unknown")
            ]
            if unavailable:
                conds["hardware"] = (
                    WATCHDOG_GRACE_HARDWARE, "Shelly nicht erreichbar",
                    "Pumpensteuerung nicht erreichbar: " + ", ".join(unavailable)
                    + ". Frostschutz kann die Pumpe nicht schalten.",
                )
            elif self._running and not self.test_mode and not self._hardware_running():
                conds["mismatch"] = (
                    WATCHDOG_GRACE_HARDWARE, "Pumpe steht unerwartet",
                    "Die Pumpe sollte laufen, laut Shelly ist sie aber aus "
                    "(von außen abgeschaltet?).",
                )

        if self._outside_temps and self.outside_temperature is None:
            conds["outside_sensor"] = (
                WATCHDOG_GRACE_SENSOR, "Außentemperatur fehlt",
                "Kein Außentemperatur-Sensor verfügbar. Frostschutz läuft nur mit "
                "erzwungenem Wintermodus — dann auf der strengsten Stufe.",
            )

        if self._room_cold:
            room = self.room_temperature
            conds["room_cold"] = (
                0, "Technikraum kalt",
                f"Technikraum nur {room:.1f} °C — Pumpe läuft im Dauerbetrieb, "
                "solange Automatik aktiv ist.",
            )

        # The live water temp is only meaningful with water flowing past the
        # probe; otherwise use the buffered value sampled at the last stop.
        water = self._buffered_water_temp
        if (
            self._running and self._run_start_time is not None
            and time.monotonic() - self._run_start_time >= MIN_RUN_FOR_SAMPLE
            and self.water_temperature is not None
        ):
            water = self.water_temperature
        water_limit = self.water_warn_temp
        if "water_cold" in self._watch_alerts:
            water_limit += FROST_HYSTERESIS_C  # don't flap on noisy readings
        if water is not None and water < water_limit:
            conds["water_cold"] = (
                0, "Poolwasser sehr kalt",
                f"Wassertemperatur {water:.1f} °C — Eisbildung im Pool möglich.",
            )
        return conds

    async def _check_frost_watchdog(self) -> None:
        conds = self._watchdog_conditions()
        now = time.monotonic()
        changed = False

        for key in list(self._watch_since):
            if key in conds:
                continue
            del self._watch_since[key]
            title = self._watch_alerts.pop(key, None)
            if title is not None:
                changed = True
                await self._send_alert(key, f"Erledigt: {title}",
                                       "Problem besteht nicht mehr.", resolved=True)

        for key, (grace, title, message) in conds.items():
            since = self._watch_since.setdefault(key, now)
            if key not in self._watch_alerts and now - since >= grace:
                self._watch_alerts[key] = title
                changed = True
                await self._send_alert(key, title, message)

        if changed:
            self._notify()

    async def _send_alert(
        self, key: str, title: str, message: str, *, resolved: bool = False
    ) -> None:
        """Push to the configured notify service + HA notification.

        Not routed through `_call`: alerts must also work in test mode.
        """
        log.warning("Frost watchdog: %s — %s", title, message)
        notification_id = f"pool_pump_frost_{key}"
        try:
            if resolved:
                await self.hass.services.async_call(
                    "persistent_notification", "dismiss",
                    {"notification_id": notification_id}, blocking=False,
                )
            else:
                await self.hass.services.async_call(
                    "persistent_notification", "create",
                    {"title": f"Pool — {title}", "message": message,
                     "notification_id": notification_id},
                    blocking=False,
                )
            await self._push(f"Pool — {title}", message, urgent=not resolved)
        except Exception:
            log.exception("Sending frost alert failed")

    async def _push(self, title: str, message: str, *, urgent: bool) -> None:
        service = self.notify_service
        if not service:
            return
        name = service.removeprefix("notify.")
        data: dict = {"title": title, "message": message}
        if urgent and name.startswith("mobile_app_"):
            # Break through focus modes (iOS) / deliver immediately (Android)
            data["data"] = {
                "push": {"interruption-level": "time-sensitive"},
                "ttl": 0, "priority": "high",
            }
        await self.hass.services.async_call("notify", name, data, blocking=False)

    async def async_send_test_alert(self) -> None:
        await self._push(
            "Pool — Test", "Frost-Wächter Benachrichtigung funktioniert.", urgent=True
        )

    @property
    def frost_alerts(self) -> list[str]:
        """Titles of the watchdog alerts currently raised."""
        return list(self._watch_alerts.values())

    @property
    def room_cold(self) -> bool:
        return self._room_cold

    async def _evaluate(self) -> None:
        """Decide what automatik should do right now."""
        if self._active_program != MODE_AUTOMATIK:
            return

        outside = self.outside_temperature
        frost_needed = False

        if self.winter_override or self._room_cold:
            frost_needed = True
        elif outside is not None:
            # Hysteresis on the frost↔normal boundary: when we're already in
            # frost mode, raise the bar for leaving by FROST_HYSTERESIS_C.
            # This stops normal↔frost flapping when outside temp hovers near
            # a threshold, which would otherwise toggle the pump (and the WP)
            # every scheduler tick.
            if self._auto_sub_mode == "frost_protection":
                probe = outside - FROST_HYSTERESIS_C
            else:
                probe = outside
            threshold = self._find_threshold(probe)
            if threshold is not None:
                frost_needed = True

        if frost_needed:
            if self._running and self._auto_sub_mode == "normal":
                await self.async_ensure_stopped()
            self._auto_sub_mode = "frost_protection"
            self._notify()
            await self._handle_frost_mode(outside)
        else:
            if self._running and self._auto_sub_mode == "frost_protection":
                await self.async_ensure_stopped()
                if self._program_task and not self._program_task.done():
                    self._program_task.cancel()
                    self._program_task = None
            self._frost_cycle_start = None
            self._current_frost_threshold = None
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

    def _frost_threshold_for(self, outside: float | None) -> dict | None:
        """Threshold to apply while frost protection is active.

        - No outside temp (sensor down): strictest threshold — fail safe.
        - Above every threshold (winter override, or inside the hysteresis
          band on the way out): mildest threshold as base circulation, so
          the pump never sits idle while frost protection is active.
        - Moving to a milder threshold also needs FROST_HYSTERESIS_C, so
          speed and cycle don't flap while the temp hovers on a boundary.
        """
        if not self.thresholds:
            return None
        if outside is None:
            threshold = min(self.thresholds, key=lambda t: t["below_temp"])
        else:
            threshold = self._find_threshold(outside) or max(
                self.thresholds, key=lambda t: t["below_temp"]
            )
            current = self._current_frost_threshold
            if (
                current is not None
                and current["below_temp"] < threshold["below_temp"]
                and outside - FROST_HYSTERESIS_C < current["below_temp"]
            ):
                threshold = next(
                    (t for t in self.thresholds if t["below_temp"] == current["below_temp"]),
                    threshold,
                )
        # Cold Technikraum: keep the threshold's speed, but never pause —
        # the pool water (warmer than the room) keeps pump and filter ice-free.
        if self._room_cold and (threshold["interval_min"] or threshold["duration_min"]):
            threshold = {**threshold, "interval_min": 0, "duration_min": 0}
        return threshold

    async def _handle_frost_mode(self, outside: float | None) -> None:
        threshold = self._frost_threshold_for(outside)
        if not threshold:
            return

        is_continuous = threshold["interval_min"] == 0 and threshold["duration_min"] == 0
        threshold_changed = (
            self._current_frost_threshold is not None
            and threshold != self._current_frost_threshold
        )

        # Already running a frost cycle — react to changes
        if self._running and self._frost_cycle_start is not None:
            if threshold_changed:
                self._current_frost_threshold = threshold
                log.info("Frost threshold changed: adjusting to below %d°C, speed %d%%",
                         threshold["below_temp"], threshold["speed"])
                await self.async_set_speed(threshold["speed"])

                # Cycle restarts on threshold change — continuous has no duration,
                # so its elapsed time must not count against an interval-mode timer.
                if self._program_task and not self._program_task.done():
                    self._program_task.cancel()
                    self._program_task = None
                self._frost_cycle_start = time.monotonic()

                if not is_continuous:
                    self._program_task = self.entry.async_create_background_task(
                        self.hass,
                        self._frost_timed_stop(threshold["duration_min"] * 60),
                        "pool_pump_frost",
                    )
            return

        # A stop is in progress (end of a normal block / frost cycle) — let
        # it finish, the next tick starts fresh.
        if self.in_nachlauf:
            return

        # No cycle running — start one if due. The pump may already be on
        # without a cycle (adopted after a restart): then the cycle just
        # takes it over, instead of leaving it running without a timer.
        self._current_frost_threshold = threshold

        if is_continuous:
            log.info("Frost: continuous at %d%% (outside=%s°C)", threshold["speed"], outside)
            self._frost_cycle_start = time.monotonic()
            await self.async_ensure_running(threshold["speed"])
        else:
            now = time.monotonic()
            interval_sec = threshold["interval_min"] * 60
            if self._last_frost_run_end is None or (now - self._last_frost_run_end) >= interval_sec:
                log.info("Frost: %dmin at %d%% (outside=%s°C)",
                         threshold["duration_min"], threshold["speed"], outside)
                self._frost_cycle_start = time.monotonic()
                await self.async_ensure_running(threshold["speed"])
                self._program_task = self.entry.async_create_background_task(
                    self.hass,
                    self._frost_timed_stop(threshold["duration_min"] * 60),
                    "pool_pump_frost",
                )
            elif self._running:
                # Running outside of any cycle while the interval isn't due
                await self.async_ensure_stopped()

    async def _frost_timed_stop(self, duration: float) -> None:
        await asyncio.sleep(duration)
        await self.async_ensure_stopped()
        self._last_frost_run_end = time.monotonic()
        self._frost_cycle_start = None
        self._current_frost_threshold = None
        # Stay in automatik — scheduler will re-evaluate
        self._notify()

    # --- Normal operation ---

    async def _handle_normal_mode(self) -> None:
        now = dt_util.now()
        window_start = self._parse_time(self.normal_window_start)
        window_end = self._parse_time(self.normal_window_end)
        if not window_start or not window_end:
            return

        in_window = window_start <= now.time() <= window_end
        water_temp = self._buffered_water_temp
        run_hours_fresh = max(1.0, water_temp / self.temp_divisor) if water_temp else 6.0

        # Freeze the schedule while the pump is stopped OR in nachlauf.
        # A water-temp sample taken right before stop (see _nachlauf_then_stop)
        # would otherwise shift run_hours during the 120 s nachlauf while
        # _running is still True, extending the block and triggering a
        # phantom restart immediately after the pool actually stops.
        if self._running and not self.in_nachlauf:
            self._cached_run_hours = run_hours_fresh
        run_hours = self._cached_run_hours or run_hours_fresh

        should_run = in_window and self._should_run_now(now, window_start, window_end, run_hours)

        # in_nachlauf means we're mid-stop-sequence (pool still flowing, but a
        # task is scheduled to actually stop it). If the schedule now wants us
        # running again (e.g. user toggled automatik off → on during nachlauf),
        # call ensure_running to cancel the pending stop and re-engage the
        # downstream devices. ensure_stopped is idempotent against in_nachlauf.
        if should_run and (not self._running or self.in_nachlauf):
            self._cached_run_hours = run_hours_fresh
            await self.async_ensure_running(self.normal_speed)
        elif not should_run and self._running:
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

    async def _start_pool_pump_hardware(self, speed):
        """Shelly start sequence. Caller must hold `_lock`."""
        if self.simple_mode:
            log.info("pool pump start [simple]")
            await self._call("switch", "turn_on", {"entity_id": self._start_switch})
            self._target_speed = 100
        else:
            log.info("pool pump start at %.0f%%", speed)
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
        await self._persist_state()
        self._notify()

    async def _stop_pool_pump_hardware(self):
        """Shelly stop sequence. Caller must hold `_lock`."""
        if self.simple_mode:
            log.info("pool pump stop [simple]")
            await self._call("switch", "turn_off", {"entity_id": self._start_switch})
        else:
            log.info("pool pump stop")
            await self._call("switch", "turn_off", {"entity_id": self._start_switch})
            await asyncio.sleep(STOP_DELAY)
            await self._set_speed_entity(0)
        self._target_speed = 0
        self._running = False
        self._run_start_time = None
        await self._persist_state()
        self._notify()

    # --- Pool pump high-level run/stop (with downstream gating) ---

    async def async_ensure_running(self, speed):
        async with self._lock:
            # If a nachlauf was scheduled, cancel it — we're starting again
            if self.in_nachlauf:
                self._nachlauf_task.cancel()
                self._nachlauf_task = None
                self._nachlauf_end_at = None

            # Start order: pool pump first, then all downstream devices
            await self._start_pool_pump_hardware(speed)

            # HA's view of each downstream state can be stale right after a
            # turn_off (mid-transition), so for co-controlled devices we
            # always send turn_on — the underlying integrations are idempotent.
            for dev in self._downstream_devices():
                if dev.co_controlled_for(self._active_program):
                    await self._device_turn_on(dev)
                elif self._device_any_entity_on(dev):
                    # Program switched to non-allowlist while device was on —
                    # kill it. No nachlauf needed because the pool keeps running.
                    await self._device_turn_off(dev)

    async def async_ensure_stopped(self, *, immediate: bool = False):
        """Stop the pool pump.

        - immediate=False (default): turn off every co-controlled downstream
          device that's currently on, then schedule the pool pump stop after
          max(device.shutdown_delay). Returns once nachlauf is scheduled —
          actual stop happens async.
        - immediate=True: skip nachlauf, cancel any in-flight nachlauf, hard
          stop. Used by Notaus. Also force-off every configured downstream
          device that happens to be on, regardless of co-control.
        """
        async with self._lock:
            # Notaus during a running nachlauf: cancel and proceed to hard stop
            if self.in_nachlauf and immediate:
                self._nachlauf_task.cancel()
                self._nachlauf_task = None
                self._nachlauf_end_at = None
                self._notify()
            elif self.in_nachlauf:
                # Already stopping gracefully — idempotent
                return

            devices = self._downstream_devices()

            if not self._running:
                # Pool already off. An immediate stop (Notaus) must still kill
                # any downstream device that's somehow on.
                if immediate:
                    for dev in devices:
                        if self._device_any_entity_on(dev):
                            await self._device_turn_off(dev)
                return

            # Collect which devices need takedown and the resulting max delay.
            max_delay = 0
            took_down_co_controlled = False
            for dev in devices:
                is_on = self._device_any_entity_on(dev)
                if not is_on:
                    continue
                co_ctrl = dev.co_controlled_for(self._active_program)
                if co_ctrl or immediate:
                    await self._device_turn_off(dev)
                    if co_ctrl and not immediate:
                        took_down_co_controlled = True
                        if dev.shutdown_delay > max_delay:
                            max_delay = dev.shutdown_delay

            # Nachlauf only when at least one co-controlled device was taken
            # down gracefully and its delay is > 0.
            if took_down_co_controlled and not immediate and max_delay > 0:
                self._nachlauf_end_at = dt_util.now() + timedelta(seconds=max_delay)
                await self._persist_state()
                self._notify()
                self._nachlauf_task = self.entry.async_create_background_task(
                    self.hass, self._nachlauf_then_stop(max_delay),
                    "pool_pump_nachlauf",
                )
                return

            # Immediate / no downstream to wait for — stop the pool pump now
            # (still in the lock).
            await self._sample_water_temp_if_eligible()
            await self._stop_pool_pump_hardware()

    async def _nachlauf_then_stop(self, delay: float) -> None:
        """Wait nachlauf duration, then actually stop the pool pump."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return  # Notaus / restart cancelled us — don't stop the pump
        async with self._lock:
            self._nachlauf_end_at = None
            self._nachlauf_task = None
            self._notify()
            if self._running:
                await self._sample_water_temp_if_eligible()
                await self._stop_pool_pump_hardware()

    async def async_set_speed(self, speed):
        if self.simple_mode:
            return  # no speed control in simple mode
        async with self._lock:
            await self._set_speed_entity(speed)
            self._target_speed = speed
            await self._persist_state()
            self._notify()

    # --- Downstream user toggle ("mit-schalten" switches) ---

    async def _apply_downstream_toggle(self, dev_name: str) -> None:
        """Reconcile a downstream device's state with its freshly toggled enable.

        Called after `_heat_pump_enabled` / `_dosing_enabled` was flipped:
        - If pool is not running or we're in nachlauf, just keep the
          preference; it takes effect at the next program start.
        - If pool is running:
          - new enabled=True AND program in allowlist → turn device on
          - new enabled=False AND device entities currently on → turn off
            (pool keeps flowing, so no flush/E3 concerns)
        """
        if self.in_nachlauf or not self._running:
            return
        dev = next(
            (d for d in self._downstream_devices() if d.name == dev_name), None
        )
        if dev is None:
            return  # not configured

        async with self._lock:
            if dev.co_controlled_for(self._active_program):
                await self._device_turn_on(dev)
            elif not dev.enabled and self._device_any_entity_on(dev):
                await self._device_turn_off(dev)

    async def async_set_heat_pump_enabled(self, enabled: bool) -> None:
        """Toggle the user-side WP-mit-schalten switch."""
        if enabled == self._heat_pump_enabled:
            return
        self._heat_pump_enabled = enabled
        await self._persist_state()
        self._notify()
        await self._apply_downstream_toggle("heat_pump")

    async def async_set_dosing_enabled(self, enabled: bool) -> None:
        """Toggle the user-side Dosier-mit-schalten switch."""
        if enabled == self._dosing_enabled:
            return
        self._dosing_enabled = enabled
        await self._persist_state()
        self._notify()
        await self._apply_downstream_toggle("dosing")

    # --- Notaus (emergency stop) ---

    async def async_emergency_stop(self) -> None:
        """Hard stop: cancel program/nachlauf, force pool + HP off in parallel.

        Accepts an E3 error on the HP — this is for emergencies, not for
        graceful operation. Use the regular OFF switches for nachlauf.
        """
        log.warning("EMERGENCY STOP triggered")
        self._cancel_program_state()
        if self._nachlauf_task and not self._nachlauf_task.done():
            self._nachlauf_task.cancel()
        self._nachlauf_task = None
        self._nachlauf_end_at = None
        self._active_program = None
        await self._persist_state()
        self._notify()
        await self.async_ensure_stopped(immediate=True)

    # --- Freshwater control ---

    @property
    def freshwater_available(self) -> bool:
        return self._freshwater_switch is not None

    @property
    def freshwater_running(self) -> bool:
        return self._freshwater_running

    @property
    def freshwater_duration(self) -> int:
        return self.entry.options.get(CONF_FRESHWATER_DURATION, DEFAULT_FRESHWATER_DURATION)

    async def async_start_freshwater(self) -> None:
        """Open freshwater valve for configured duration."""
        if not self._freshwater_switch:
            log.warning("No freshwater switch configured")
            return
        duration = self.freshwater_duration * 60
        log.info("Freshwater: opening valve for %dmin", self.freshwater_duration)
        await self._call("switch", "turn_on", {"entity_id": self._freshwater_switch})
        self._freshwater_running = True
        self._notify()
        if self._freshwater_task and not self._freshwater_task.done():
            self._freshwater_task.cancel()
        self._freshwater_task = self.entry.async_create_background_task(
            self.hass, self._freshwater_timed_stop(duration), "pool_pump_freshwater"
        )

    async def async_stop_freshwater(self) -> None:
        """Close freshwater valve."""
        if not self._freshwater_switch:
            return
        if self._freshwater_task and not self._freshwater_task.done():
            self._freshwater_task.cancel()
            self._freshwater_task = None
        log.info("Freshwater: closing valve")
        await self._call("switch", "turn_off", {"entity_id": self._freshwater_switch})
        self._freshwater_running = False
        self._notify()

    async def _freshwater_timed_stop(self, duration: float) -> None:
        """Auto-close valve after duration."""
        await asyncio.sleep(duration)
        log.info("Freshwater: timer expired, closing valve")
        await self._call("switch", "turn_off", {"entity_id": self._freshwater_switch})
        self._freshwater_running = False
        self._freshwater_task = None
        self._notify()

    # --- Backwash counter ---

    async def async_reset_backwash_counter(self) -> None:
        """Reset the backwash counter to today."""
        self._last_backwash_date = dt_util.now().date().isoformat()
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
