"""Constants for Pool Pump integration."""

DOMAIN = "pool_pump"

# Config entry keys — hub setup
CONF_POWER_SWITCH = "power_switch"      # only used in normal mode
CONF_SPEED_NUMBER = "speed_number"      # only used in normal mode
CONF_START_SWITCH = "start_switch"      # in simple mode this is the only pump switch
CONF_OUTSIDE_TEMPS = "outside_temps"
CONF_WATER_TEMP = "water_temp"
CONF_ROOM_TEMP = "room_temp"
CONF_SIMPLE_MODE = "simple_mode"        # True → no power switch, no speed control

# Temperature plausibility filter
TEMP_MIN_PLAUSIBLE = -30.0
TEMP_MAX_PLAUSIBLE = 60.0

# Timing
POWER_ON_DELAY = 5.0       # seconds: wait after mains power on
STOP_DELAY = 1.0            # seconds: wait after stop signal
MIN_RUN_FOR_SAMPLE = 300.0  # seconds: pump must run this long before we trust water temp
SCHEDULER_INTERVAL = 60.0   # seconds: how often the scheduler re-evaluates
MIN_PAUSE_MINUTES = 30      # minutes: below this, don't bother pausing — run continuously
FROST_HYSTERESIS_C = 1.0    # °C: temperature must rise this much above any threshold
                            # before we leave frost protection — prevents flapping
                            # when outside temp hovers near a boundary
WATER_SAMPLE_INTERVAL = 1800.0  # seconds: refresh the buffered water temp during long runs
HARDWARE_WAIT_TIMEOUT = 120.0   # seconds: wait for Shelly entities after HA start
HARDWARE_SETTLE_DELAY = 10.0    # seconds: after a Shelly state change, before comparing
WINTER_OFF_TIMEOUT = 30         # seconds: wait for a downstream switch to report off

# Options keys — normal operation
CONF_NORMAL_WINDOW_START = "normal_window_start"
CONF_NORMAL_WINDOW_END = "normal_window_end"
CONF_NORMAL_SPEED = "normal_speed"

# Options keys — user-defined programs
CONF_PROGRAMS = "programs"
# Each: {name, speed, duration_min}
#   duration_min=0      → runs until manually stopped
#   (after a timed stop the integration falls back to manual mode)

# Options keys — backwash reminder
CONF_BACKWASH_INTERVAL_DAYS = "backwash_interval_days"
CONF_BACKWASH_PROGRAM_NAME = "backwash_program_name"
DEFAULT_BACKWASH_INTERVAL = 14

# Config entry keys — fresh water
CONF_FRESHWATER_SWITCH = "freshwater_switch"
CONF_FRESHWATER_DURATION = "freshwater_duration"
DEFAULT_FRESHWATER_DURATION = 30  # minutes

# Options keys — Winterbetrieb
CONF_WINTER_SPEED = "winter_speed"  # normal-mode speed while Winterbetrieb is on
DEFAULT_WINTER_SPEED = 20

# Options keys — frost protection
CONF_WINTER_THRESHOLDS = "winter_thresholds"
# Each: {below_temp, interval_min, duration_min, speed}
# interval_min=0 + duration_min=0 → continuous

# Options keys — downstream devices that follow the pool pump's run state.
# Each device has its own user-controlled "mit-schalten" switch persisted
# in the coordinator store (NOT in entry.options) so toggling it doesn't
# reload the entry and kill any running program. When stopping the pool
# pump, all configured downstream devices are turned off first; the pool
# keeps flowing for max(device.shutdown_delay) seconds before the actual
# pool stop, so heat exchangers cool down and dosing chemistry flushes out.

# Heat pump
CONF_HEAT_PUMP_POWER_ENTITY = "heat_pump_power_entity"
CONF_HEAT_PUMP_PROGRAMS = "heat_pump_programs"        # list of program names allowed to heat
CONF_HEAT_PUMP_SHUTDOWN_DELAY = "heat_pump_shutdown_delay"
DEFAULT_HEAT_PUMP_SHUTDOWN_DELAY = 120  # seconds — HP run-out before pool pump stops

# Dosing system (one or more switches that enable individual dosing pumps,
# e.g. Bayrol pH and Cl automatik-steuerung). Always disabled together with
# the pool pump so leftover chemistry can flush out of the dosing cell
# before flow stops.
CONF_DOSING_SWITCHES = "dosing_switches"              # list of switch entity IDs
CONF_DOSING_PROGRAMS = "dosing_programs"              # list of program names allowed to dose
CONF_DOSING_SHUTDOWN_DELAY = "dosing_shutdown_delay"
DEFAULT_DOSING_SHUTDOWN_DELAY = 30  # seconds — flush time, shorter than HP thermo-runout

# Options keys — frost watchdog. Equipment (pump, filter) sits in a room,
# the pipes to the pool run outside: a cold room forces continuous
# circulation, and problems that leave the system unprotected are pushed
# to the user's phone.
CONF_NOTIFY_SERVICE = "notify_service"    # e.g. "notify.mobile_app_pixel"; None = HA notification only
CONF_ROOM_FROST_TEMP = "room_frost_temp"  # °C: below this the pump runs continuously
DEFAULT_ROOM_FROST_TEMP = 3.0
CONF_WATER_WARN_TEMP = "water_warn_temp"  # °C: warn when pool water gets this cold
DEFAULT_WATER_WARN_TEMP = 2.0
WATCHDOG_GRACE_UNPROTECTED = 1800.0  # s: automatik off + pump stopped (room for backwash handling)
WATCHDOG_GRACE_HARDWARE = 300.0      # s: Shelly unavailable / state mismatch (reconnects)
WATCHDOG_GRACE_SENSOR = 900.0        # s: outside sensor unavailable

# Options keys — test/debug
CONF_TEST_MODE = "test_mode"

# Mode identifiers
MODE_MANUAL = "manual"
MODE_AUTOMATIK = "automatik"

# Default programs
DEFAULT_PROGRAMS = [
    {"name": "Backwash", "speed": 70, "duration_min": 3},
    {"name": "Nachspülen", "speed": 50, "duration_min": 1},
    {"name": "Reinigen", "speed": 40, "duration_min": 5},
]

# Default frost thresholds
DEFAULT_THRESHOLDS = [
    {"below_temp": 4, "interval_min": 120, "duration_min": 15, "speed": 20},
    {"below_temp": 2, "interval_min": 60, "duration_min": 20, "speed": 25},
    {"below_temp": 0, "interval_min": 45, "duration_min": 20, "speed": 30},
    {"below_temp": -3, "interval_min": 30, "duration_min": 25, "speed": 35},
    {"below_temp": -7, "interval_min": 0, "duration_min": 0, "speed": 40},
    {"below_temp": -12, "interval_min": 0, "duration_min": 0, "speed": 50},
]
