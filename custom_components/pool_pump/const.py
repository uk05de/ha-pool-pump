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

# Options keys — normal operation
CONF_NORMAL_WINDOW_START = "normal_window_start"
CONF_NORMAL_WINDOW_END = "normal_window_end"
CONF_NORMAL_SPEED = "normal_speed"
CONF_NORMAL_TEMP_DIVISOR = "normal_temp_divisor"

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

# Options keys — frost protection
CONF_WINTER_OVERRIDE = "winter_override"
CONF_WINTER_THRESHOLDS = "winter_thresholds"
# Each: {below_temp, interval_min, duration_min, speed}
# interval_min=0 + duration_min=0 → continuous

# Options keys — heat pump (pool pump gates the heat pump's power switch).
# Whether the user wants heating is a separate switch entity stored in the
# coordinator state, so toggling it does not reload the entry (which would
# kill any running program).
CONF_HEAT_PUMP_POWER_ENTITY = "heat_pump_power_entity"
CONF_HEAT_PUMP_PROGRAMS = "heat_pump_programs"        # list of program names allowed to heat
CONF_HEAT_PUMP_SHUTDOWN_DELAY = "heat_pump_shutdown_delay"
DEFAULT_HEAT_PUMP_SHUTDOWN_DELAY = 120  # seconds — HP run-out before pool pump stops

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
