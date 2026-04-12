"""Constants for Pool Pump integration."""

DOMAIN = "pool_pump"

# Config entry keys — hub setup
CONF_POWER_SWITCH = "power_switch"
CONF_SPEED_NUMBER = "speed_number"
CONF_START_SWITCH = "start_switch"
CONF_OUTSIDE_TEMPS = "outside_temps"
CONF_WATER_TEMP = "water_temp"
CONF_ROOM_TEMP = "room_temp"

# Temperature plausibility filter
TEMP_MIN_PLAUSIBLE = -30.0
TEMP_MAX_PLAUSIBLE = 60.0

# Timing
POWER_ON_DELAY = 5.0       # seconds: wait after mains power on
STOP_DELAY = 1.0            # seconds: wait after stop signal
MIN_RUN_FOR_SAMPLE = 300.0  # seconds: pump must run this long before we trust water temp
SCHEDULER_INTERVAL = 60.0   # seconds: how often the scheduler re-evaluates
MIN_PAUSE_MINUTES = 30      # minutes: below this, don't bother pausing — run continuously

# Options keys — normal operation
CONF_NORMAL_WINDOW_START = "normal_window_start"
CONF_NORMAL_WINDOW_END = "normal_window_end"
CONF_NORMAL_SPEED = "normal_speed"
CONF_NORMAL_TEMP_DIVISOR = "normal_temp_divisor"  # runtime = water_temp / divisor

# Options keys — backwash / rinse
CONF_BACKWASH_DURATION = "backwash_duration"
CONF_BACKWASH_SPEED = "backwash_speed"
CONF_RINSE_DURATION = "rinse_duration"
CONF_RINSE_SPEED = "rinse_speed"

# Options keys — winter / frost protection
CONF_WINTER_OVERRIDE = "winter_override"
CONF_WINTER_THRESHOLDS = "winter_thresholds"
# Each threshold: {below_temp, interval_min, duration_min, speed}
# interval_min=0 + duration_min=0 means continuous operation

# Options keys — automation
CONF_AUTOMATION_ENABLED = "automation_enabled"

# Options keys — test/debug
CONF_TEST_MODE = "test_mode"

# Operational modes (automatic, not user-selected)
MODE_OFF = "off"
MODE_NORMAL = "normal"
MODE_FROST = "frost_protection"
MODE_BACKWASH = "backwash"
MODE_RINSE = "rinse"
MODE_MANUAL = "manual"

# Default winter thresholds (sorted descending by below_temp)
DEFAULT_THRESHOLDS = [
    {"below_temp": 4, "interval_min": 120, "duration_min": 15, "speed": 20},
    {"below_temp": 2, "interval_min": 60, "duration_min": 20, "speed": 25},
    {"below_temp": 0, "interval_min": 45, "duration_min": 20, "speed": 30},
    {"below_temp": -3, "interval_min": 30, "duration_min": 25, "speed": 35},
    {"below_temp": -7, "interval_min": 0, "duration_min": 0, "speed": 40},
    {"below_temp": -12, "interval_min": 0, "duration_min": 0, "speed": 50},
]
