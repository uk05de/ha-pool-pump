"""Constants for Pool Pump integration."""

DOMAIN = "pool_pump"

# Config entry keys — hub setup
CONF_POWER_SWITCH = "power_switch"
CONF_SPEED_NUMBER = "speed_number"
CONF_START_SWITCH = "start_switch"
CONF_TEMP_SENSORS = "temp_sensors"

# Timing (seconds)
POWER_ON_DELAY = 5.0  # wait after mains power on before setting speed
STOP_DELAY = 1.0      # wait after stop signal before changing speed

# Options keys — programs
CONF_PROGRAMS = "programs"
CONF_NORMAL_START = "normal_start"
CONF_NORMAL_END = "normal_end"
CONF_NORMAL_SPEED = "normal_speed"
CONF_BACKWASH_DURATION = "backwash_duration"
CONF_BACKWASH_SPEED = "backwash_speed"
CONF_RINSE_DURATION = "rinse_duration"
CONF_RINSE_SPEED = "rinse_speed"

# Options keys — winter mode
CONF_WINTER_ENABLED = "winter_enabled"
CONF_WINTER_THRESHOLDS = "winter_thresholds"

# Options keys — test/debug
CONF_TEST_MODE = "test_mode"
# Each threshold: {temp_from, temp_to, interval_hours, duration_minutes, speed}

# Programs
PROGRAM_OFF = "off"
PROGRAM_NORMAL = "normal"
PROGRAM_BACKWASH = "backwash"
PROGRAM_RINSE = "rinse"
PROGRAM_WINTER = "winter"
PROGRAM_MANUAL = "manual"

ALL_PROGRAMS = [
    PROGRAM_OFF,
    PROGRAM_NORMAL,
    PROGRAM_BACKWASH,
    PROGRAM_RINSE,
    PROGRAM_WINTER,
    PROGRAM_MANUAL,
]
