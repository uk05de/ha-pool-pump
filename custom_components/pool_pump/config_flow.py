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
    CONF_BACKWASH_INTERVAL_DAYS,
    CONF_BACKWASH_PROGRAM_NAME,
    DEFAULT_BACKWASH_INTERVAL,
    CONF_FRESHWATER_SWITCH,
    CONF_FRESHWATER_DURATION,
    DEFAULT_FRESHWATER_DURATION,
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
            vol.Optional(CONF_FRESHWATER_SWITCH): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch"),
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
                "show_programs": "Programme anzeigen",
                "add_program": "Programm hinzufügen",
                "edit_program": "Programm bearbeiten",
                "remove_program": "Programm entfernen",
                "winter_thresholds": "Frostschutz-Schwellen anzeigen",
                "add_threshold": "Frostschutz-Schwelle hinzufügen",
                "edit_threshold": "Frostschutz-Schwelle bearbeiten",
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
            vol.Required(CONF_BACKWASH_INTERVAL_DAYS, default=opts.get(CONF_BACKWASH_INTERVAL_DAYS, DEFAULT_BACKWASH_INTERVAL)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=60, step=1, unit_of_measurement="Tage", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_BACKWASH_PROGRAM_NAME, default=opts.get(CONF_BACKWASH_PROGRAM_NAME, "Backwash")): str,
            vol.Required(CONF_FRESHWATER_DURATION, default=opts.get(CONF_FRESHWATER_DURATION, DEFAULT_FRESHWATER_DURATION)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=240, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
        })

        return self.async_show_form(step_id="automatik_settings", data_schema=schema)

    # --- User-defined programs ---

    async def async_step_show_programs(self, user_input=None):
        """Show all configured programs — OK to go back."""
        if user_input is not None:
            return await self.async_step_init()

        programs = self._entry.options.get(CONF_PROGRAMS, DEFAULT_PROGRAMS)
        if not programs:
            desc = "Keine Programme konfiguriert."
        else:
            lines = ["Programm | Drehzahl | Dauer", "--- | --- | ---"]
            for p in programs:
                dur = f"{p['duration_min']} min" if p["duration_min"] > 0 else "manuell"
                lines.append(f"{p['name']} | {p['speed']}% | {dur}")
            desc = "\n".join(lines)

        return self.async_show_form(
            step_id="show_programs",
            data_schema=vol.Schema({}),
            description_placeholders={"programs": desc},
        )

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

    async def async_step_edit_program(self, user_input=None):
        """Step 1: select which program to edit."""
        programs = list(self._entry.options.get(CONF_PROGRAMS, DEFAULT_PROGRAMS))

        if user_input is not None:
            self._edit_program_name = user_input["program"]
            return await self.async_step_edit_program_values()

        labels = {p["name"]: p["name"] for p in programs}
        if not labels:
            return self.async_abort(reason="no_programs")

        return self.async_show_form(
            step_id="edit_program",
            data_schema=vol.Schema({vol.Required("program"): vol.In(labels)}),
        )

    async def async_step_edit_program_values(self, user_input=None):
        """Step 2: edit the selected program's values."""
        programs = list(self._entry.options.get(CONF_PROGRAMS, DEFAULT_PROGRAMS))
        prog = next((p for p in programs if p["name"] == self._edit_program_name), None)

        if not prog:
            return self.async_abort(reason="program_not_found")

        if user_input is not None:
            for p in programs:
                if p["name"] == self._edit_program_name:
                    p["speed"] = int(user_input["speed"])
                    p["duration_min"] = int(user_input["duration_min"])
                    break
            options = dict(self._entry.options)
            options[CONF_PROGRAMS] = programs
            return self.async_create_entry(title="", data=options)

        schema = vol.Schema({
            vol.Required("speed", default=prog["speed"]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required("duration_min", default=prog["duration_min"]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
        })

        return self.async_show_form(step_id="edit_program_values", data_schema=schema)

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
            lines = ["Temperatur | Intervall | Dauer | Drehzahl", "--- | --- | --- | ---"]
            for t in thresholds_sorted:
                if t["interval_min"] == 0 and t["duration_min"] == 0:
                    lines.append(f"Unter {t['below_temp']}°C | durchgängig | durchgängig | {t['speed']}%")
                else:
                    lines.append(f"Unter {t['below_temp']}°C | {t['interval_min']} min | {t['duration_min']} min | {t['speed']}%")
            desc = "\n".join(lines)

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
                selector.NumberSelectorConfig(min=-30, max=40, step=1, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX)
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

    async def async_step_edit_threshold(self, user_input=None):
        """Step 1: select which threshold to edit."""
        thresholds = list(self._entry.options.get(CONF_WINTER_THRESHOLDS, DEFAULT_THRESHOLDS))

        if user_input is not None:
            self._edit_threshold_temp = int(float(user_input["threshold"].split("°")[0].replace("Unter ", "")))
            return await self.async_step_edit_threshold_values()

        labels = {f"Unter {t['below_temp']}°C": f"Unter {t['below_temp']}°C" for t in thresholds}
        if not labels:
            return self.async_abort(reason="no_thresholds")

        return self.async_show_form(
            step_id="edit_threshold",
            data_schema=vol.Schema({vol.Required("threshold"): vol.In(labels)}),
        )

    async def async_step_edit_threshold_values(self, user_input=None):
        """Step 2: edit the selected threshold's values."""
        thresholds = list(self._entry.options.get(CONF_WINTER_THRESHOLDS, DEFAULT_THRESHOLDS))
        threshold = next((t for t in thresholds if t["below_temp"] == self._edit_threshold_temp), None)

        if not threshold:
            return self.async_abort(reason="threshold_not_found")

        if user_input is not None:
            for t in thresholds:
                if t["below_temp"] == self._edit_threshold_temp:
                    t["below_temp"] = int(user_input["below_temp"])
                    t["interval_min"] = int(user_input["interval_min"])
                    t["duration_min"] = int(user_input["duration_min"])
                    t["speed"] = int(user_input["speed"])
                    break
            thresholds.sort(key=lambda t: t["below_temp"], reverse=True)
            options = dict(self._entry.options)
            options[CONF_WINTER_THRESHOLDS] = thresholds
            return self.async_create_entry(title="", data=options)

        schema = vol.Schema({
            vol.Required("below_temp", default=threshold["below_temp"]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-30, max=40, step=1, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required("interval_min", default=threshold["interval_min"]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=480, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required("duration_min", default=threshold["duration_min"]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=120, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required("speed", default=threshold["speed"]): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=100, step=5, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
            ),
        })

        return self.async_show_form(step_id="edit_threshold_values", data_schema=schema)

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
