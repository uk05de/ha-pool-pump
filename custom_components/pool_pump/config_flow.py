"""Config flow for Pool Pump."""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_POWER_SWITCH,
    CONF_SPEED_NUMBER,
    CONF_START_SWITCH,
    CONF_OUTSIDE_TEMPS,
    CONF_WATER_TEMP,
    CONF_ROOM_TEMP,
    CONF_TEST_MODE,
    CONF_WINTER_THRESHOLDS,
    CONF_NORMAL_WINDOW_START,
    CONF_NORMAL_WINDOW_END,
    CONF_NORMAL_SPEED,
    CONF_BACKWASH_DURATION,
    CONF_BACKWASH_SPEED,
    CONF_RINSE_DURATION,
    CONF_RINSE_SPEED,
    DEFAULT_THRESHOLDS,
)


class PoolPumpConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pool Pump."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Initial step — pick the Shellys and sensors."""
        if user_input is not None:
            return self.async_create_entry(
                title="Pool Pump",
                data=user_input,
                options={CONF_WINTER_THRESHOLDS: DEFAULT_THRESHOLDS},
            )

        schema = vol.Schema({
            vol.Required(CONF_POWER_SWITCH): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch"),
            ),
            vol.Required(CONF_SPEED_NUMBER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["number", "light"]),
            ),
            vol.Required(CONF_START_SWITCH): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch"),
            ),
            vol.Optional(CONF_OUTSIDE_TEMPS, default=[]): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor",
                    device_class="temperature",
                    multiple=True,
                ),
            ),
            vol.Optional(CONF_WATER_TEMP): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor",
                    device_class="temperature",
                ),
            ),
            vol.Optional(CONF_ROOM_TEMP): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor",
                    device_class="temperature",
                ),
            ),
        })

        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PoolPumpOptionsFlow(config_entry)


class PoolPumpOptionsFlow(config_entries.OptionsFlow):
    """Handle options — programs, thresholds, test mode."""

    def __init__(self, config_entry):
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        """Options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "programs": "Programme (Normal, Rückspülen, Nachspülen)",
                "winter_thresholds": "Frostschutz-Schwellen anzeigen",
                "add_threshold": "Frostschutz-Schwelle hinzufügen",
                "remove_threshold": "Frostschutz-Schwelle entfernen",
                "test": "Testmodus",
            },
        )

    # --- Normal / Backwash / Rinse ---

    async def async_step_programs(self, user_input=None):
        """Configure normal/backwash/rinse."""
        if user_input is not None:
            options = dict(self._entry.options)
            options.update(user_input)
            return self.async_create_entry(title="", data=options)

        opts = self._entry.options
        speed_selector = lambda default: selector.NumberSelector(
            selector.NumberSelectorConfig(min=5, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
        )
        duration_selector = lambda: selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=30, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
        )

        schema = vol.Schema({
            vol.Required(CONF_NORMAL_WINDOW_START, default=opts.get(CONF_NORMAL_WINDOW_START, "08:00:00")): selector.TimeSelector(),
            vol.Required(CONF_NORMAL_WINDOW_END, default=opts.get(CONF_NORMAL_WINDOW_END, "22:00:00")): selector.TimeSelector(),
            vol.Required(CONF_NORMAL_SPEED, default=opts.get(CONF_NORMAL_SPEED, 30)): speed_selector(30),
            vol.Required(CONF_BACKWASH_DURATION, default=opts.get(CONF_BACKWASH_DURATION, 3)): duration_selector(),
            vol.Required(CONF_BACKWASH_SPEED, default=opts.get(CONF_BACKWASH_SPEED, 70)): speed_selector(70),
            vol.Required(CONF_RINSE_DURATION, default=opts.get(CONF_RINSE_DURATION, 1)): duration_selector(),
            vol.Required(CONF_RINSE_SPEED, default=opts.get(CONF_RINSE_SPEED, 50)): speed_selector(50),
        })

        return self.async_show_form(
            step_id="programs",
            data_schema=schema,
            data_description={
                CONF_NORMAL_WINDOW_START: "Startzeit des täglichen Filterbetriebs",
                CONF_NORMAL_WINDOW_END: "Endzeit des täglichen Filterbetriebs",
                CONF_NORMAL_SPEED: "Pumpengeschwindigkeit im Normalbetrieb",
                CONF_BACKWASH_DURATION: "Dauer der Rückspülung",
                CONF_BACKWASH_SPEED: "Pumpengeschwindigkeit bei Rückspülung",
                CONF_RINSE_DURATION: "Dauer des Nachspülens",
                CONF_RINSE_SPEED: "Pumpengeschwindigkeit beim Nachspülen",
            },
        )

    # --- Winter Thresholds ---

    async def async_step_winter_thresholds(self, user_input=None):
        """Show current thresholds as info, allow add/remove from menu."""
        thresholds = self._entry.options.get(CONF_WINTER_THRESHOLDS, DEFAULT_THRESHOLDS)
        desc = "\n".join(
            f"• {t['temp_from']}°C bis {t['temp_to']}°C: "
            + (f"alle {t['interval_min']}min für {t['duration_min']}min bei {t['speed']}%"
               if t['interval_min'] > 0
               else f"durchgängig bei {t['speed']}%")
            for t in thresholds
        )
        return self.async_show_menu(
            step_id="winter_thresholds",
            menu_options={
                "add_threshold": "Schwelle hinzufügen",
                "remove_threshold": "Schwelle entfernen",
            },
            description_placeholders={"thresholds": desc, "count": str(len(thresholds))},
        )

    async def async_step_add_threshold(self, user_input=None):
        """Add a new frost threshold."""
        if user_input is not None:
            thresholds = list(self._entry.options.get(CONF_WINTER_THRESHOLDS, DEFAULT_THRESHOLDS))
            thresholds.append({
                "temp_from": user_input["temp_from"],
                "temp_to": user_input["temp_to"],
                "interval_min": user_input["interval_min"],
                "duration_min": user_input["duration_min"],
                "speed": user_input["speed"],
            })
            # Sort by temp_from descending
            thresholds.sort(key=lambda t: t["temp_from"], reverse=True)
            options = dict(self._entry.options)
            options[CONF_WINTER_THRESHOLDS] = thresholds
            return self.async_create_entry(title="", data=options)

        schema = vol.Schema({
            vol.Required("temp_from", default=0): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-30, max=10, step=1, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required("temp_to", default=-5): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-30, max=10, step=1, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required("interval_min", default=60): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=480, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required("duration_min", default=20): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required("speed", default=30): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
            ),
        })

        return self.async_show_form(
            step_id="add_threshold",
            data_schema=schema,
            data_description={
                "temp_from": "Obere Temperaturgrenze der Schwelle",
                "temp_to": "Untere Temperaturgrenze der Schwelle",
                "interval_min": "Alle X Minuten laufen lassen (0 = durchgängig)",
                "duration_min": "Laufzeit pro Intervall (0 = durchgängig)",
                "speed": "Pumpengeschwindigkeit in dieser Schwelle",
            },
        )

    async def async_step_remove_threshold(self, user_input=None):
        """Remove a frost threshold."""
        thresholds = list(self._entry.options.get(CONF_WINTER_THRESHOLDS, DEFAULT_THRESHOLDS))

        if user_input is not None:
            label = user_input["threshold"]
            thresholds = [t for t in thresholds
                          if f"{t['temp_from']}°C → {t['temp_to']}°C" != label]
            options = dict(self._entry.options)
            options[CONF_WINTER_THRESHOLDS] = thresholds
            return self.async_create_entry(title="", data=options)

        labels = {
            f"{t['temp_from']}°C → {t['temp_to']}°C": f"{t['temp_from']}°C → {t['temp_to']}°C"
            for t in thresholds
        }

        if not labels:
            return self.async_abort(reason="no_thresholds")

        schema = vol.Schema({
            vol.Required("threshold"): vol.In(labels),
        })

        return self.async_show_form(step_id="remove_threshold", data_schema=schema)

    # --- Test mode ---

    async def async_step_test(self, user_input=None):
        if user_input is not None:
            options = dict(self._entry.options)
            options[CONF_TEST_MODE] = user_input[CONF_TEST_MODE]
            return self.async_create_entry(title="", data=options)

        schema = vol.Schema({
            vol.Required(
                CONF_TEST_MODE,
                default=self._entry.options.get(CONF_TEST_MODE, False),
            ): bool,
        })

        return self.async_show_form(step_id="test", data_schema=schema)
