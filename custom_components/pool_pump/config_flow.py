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
    CONF_PROGRAMS,
    DEFAULT_PROGRAMS,
    DEFAULT_THRESHOLDS,
)


class PoolPumpConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pool Pump."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title="Pool Pump",
                data=user_input,
                options={
                    CONF_WINTER_THRESHOLDS: DEFAULT_THRESHOLDS,
                    CONF_PROGRAMS: DEFAULT_PROGRAMS,
                },
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
                    domain="sensor", device_class="temperature", multiple=True,
                ),
            ),
            vol.Optional(CONF_WATER_TEMP): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature"),
            ),
            vol.Optional(CONF_ROOM_TEMP): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature"),
            ),
        })

        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PoolPumpOptionsFlow(config_entry)


class PoolPumpOptionsFlow(config_entries.OptionsFlow):
    """Handle options — automatik settings, programs, thresholds, test."""

    def __init__(self, config_entry):
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "automatik_settings": "Automatik-Einstellungen",
                "add_program": "Programm hinzufügen",
                "remove_program": "Programm entfernen",
                "winter_thresholds": "Frostschutz-Schwellen anzeigen",
                "add_threshold": "Frostschutz-Schwelle hinzufügen",
                "remove_threshold": "Frostschutz-Schwelle entfernen",
                "test": "Testmodus",
            },
        )

    # --- Automatik settings ---

    async def async_step_automatik_settings(self, user_input=None):
        if user_input is not None:
            options = dict(self._entry.options)
            options.update(user_input)
            return self.async_create_entry(title="", data=options)

        opts = self._entry.options
        schema = vol.Schema({
            vol.Required(CONF_NORMAL_WINDOW_START, default=opts.get(CONF_NORMAL_WINDOW_START, "08:00:00")): selector.TimeSelector(),
            vol.Required(CONF_NORMAL_WINDOW_END, default=opts.get(CONF_NORMAL_WINDOW_END, "22:00:00")): selector.TimeSelector(),
            vol.Required(CONF_NORMAL_SPEED, default=opts.get(CONF_NORMAL_SPEED, 30)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
            ),
        })

        return self.async_show_form(step_id="automatik_settings", data_schema=schema)

    # --- User-defined programs ---

    async def async_step_add_program(self, user_input=None):
        if user_input is not None:
            programs = list(self._entry.options.get(CONF_PROGRAMS, DEFAULT_PROGRAMS))
            programs.append({
                "name": user_input["name"],
                "speed": int(user_input["speed"]),
                "duration_min": int(user_input["duration_min"]),
            })
            options = dict(self._entry.options)
            options[CONF_PROGRAMS] = programs
            return self.async_create_entry(title="", data=options)

        schema = vol.Schema({
            vol.Required("name"): str,
            vol.Required("speed", default=50): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required("duration_min", default=5): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
        })

        return self.async_show_form(step_id="add_program", data_schema=schema)

    async def async_step_remove_program(self, user_input=None):
        programs = list(self._entry.options.get(CONF_PROGRAMS, DEFAULT_PROGRAMS))

        if user_input is not None:
            name = user_input["program"]
            programs = [p for p in programs if p["name"] != name]
            options = dict(self._entry.options)
            options[CONF_PROGRAMS] = programs
            return self.async_create_entry(title="", data=options)

        labels = {p["name"]: p["name"] for p in programs}
        if not labels:
            return self.async_abort(reason="no_programs")

        return self.async_show_form(
            step_id="remove_program",
            data_schema=vol.Schema({vol.Required("program"): vol.In(labels)}),
        )

    # --- Winter thresholds ---

    async def async_step_winter_thresholds(self, user_input=None):
        if user_input is not None:
            return await self.async_step_init()

        thresholds = self._entry.options.get(CONF_WINTER_THRESHOLDS, DEFAULT_THRESHOLDS)
        thresholds_sorted = sorted(thresholds, key=lambda t: t["below_temp"], reverse=True)

        if not thresholds_sorted:
            desc = "Keine Schwellen konfiguriert."
        else:
            lines = []
            for t in thresholds_sorted:
                if t["interval_min"] == 0 and t["duration_min"] == 0:
                    lines.append(f"Unter {t['below_temp']}°C → durchgängig bei {t['speed']}%")
                else:
                    lines.append(f"Unter {t['below_temp']}°C → alle {t['interval_min']}min für {t['duration_min']}min bei {t['speed']}%")
            desc = " | ".join(lines)

        return self.async_show_form(
            step_id="winter_thresholds",
            data_schema=vol.Schema({}),
            description_placeholders={"thresholds": desc},
        )

    async def async_step_add_threshold(self, user_input=None):
        if user_input is not None:
            thresholds = list(self._entry.options.get(CONF_WINTER_THRESHOLDS, DEFAULT_THRESHOLDS))
            thresholds.append({
                "below_temp": int(user_input["below_temp"]),
                "interval_min": int(user_input["interval_min"]),
                "duration_min": int(user_input["duration_min"]),
                "speed": int(user_input["speed"]),
            })
            thresholds.sort(key=lambda t: t["below_temp"], reverse=True)
            options = dict(self._entry.options)
            options[CONF_WINTER_THRESHOLDS] = thresholds
            return self.async_create_entry(title="", data=options)

        schema = vol.Schema({
            vol.Required("below_temp", default=0): selector.NumberSelector(
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

        return self.async_show_form(step_id="add_threshold", data_schema=schema)

    async def async_step_remove_threshold(self, user_input=None):
        thresholds = list(self._entry.options.get(CONF_WINTER_THRESHOLDS, DEFAULT_THRESHOLDS))

        if user_input is not None:
            label = user_input["threshold"]
            thresholds = [t for t in thresholds if f"Unter {t['below_temp']}°C" != label]
            options = dict(self._entry.options)
            options[CONF_WINTER_THRESHOLDS] = thresholds
            return self.async_create_entry(title="", data=options)

        labels = {f"Unter {t['below_temp']}°C": f"Unter {t['below_temp']}°C" for t in thresholds}
        if not labels:
            return self.async_abort(reason="no_thresholds")

        return self.async_show_form(
            step_id="remove_threshold",
            data_schema=vol.Schema({vol.Required("threshold"): vol.In(labels)}),
        )

    # --- Test mode ---

    async def async_step_test(self, user_input=None):
        if user_input is not None:
            options = dict(self._entry.options)
            options[CONF_TEST_MODE] = user_input[CONF_TEST_MODE]
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="test",
            data_schema=vol.Schema({
                vol.Required(CONF_TEST_MODE, default=self._entry.options.get(CONF_TEST_MODE, False)): bool,
            }),
        )
