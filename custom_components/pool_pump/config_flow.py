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
    CONF_TEMP_SENSORS,
    CONF_TEST_MODE,
)


class PoolPumpConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pool Pump."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Initial step — pick the Shellys and sensors."""
        errors = {}

        if user_input is not None:
            return self.async_create_entry(
                title="Pool Pump",
                data=user_input,
                options={},
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
            vol.Optional(CONF_TEMP_SENSORS, default=[]): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor",
                    device_class="temperature",
                    multiple=True,
                ),
            ),
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PoolPumpOptionsFlow(config_entry)


class PoolPumpOptionsFlow(config_entries.OptionsFlow):
    """Handle options — programs and winter thresholds."""

    def __init__(self, config_entry):
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        """Options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["programs", "winter", "test"],
        )

    async def async_step_test(self, user_input=None):
        """Enable/disable test mode (no real service calls)."""
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

    async def async_step_programs(self, user_input=None):
        """Configure normal/backwash/rinse schedules and speeds."""
        if user_input is not None:
            options = dict(self._entry.options)
            options.update(user_input)
            return self.async_create_entry(title="", data=options)

        opts = self._entry.options
        schema = vol.Schema({
            vol.Required("normal_start", default=opts.get("normal_start", "08:00:00")): selector.TimeSelector(),
            vol.Required("normal_end", default=opts.get("normal_end", "18:00:00")): selector.TimeSelector(),
            vol.Required("normal_speed", default=opts.get("normal_speed", 30)): vol.All(int, vol.Range(min=0, max=100)),
            vol.Required("backwash_duration", default=opts.get("backwash_duration", 3)): vol.All(int, vol.Range(min=1, max=30)),
            vol.Required("backwash_speed", default=opts.get("backwash_speed", 70)): vol.All(int, vol.Range(min=0, max=100)),
            vol.Required("rinse_duration", default=opts.get("rinse_duration", 1)): vol.All(int, vol.Range(min=1, max=30)),
            vol.Required("rinse_speed", default=opts.get("rinse_speed", 50)): vol.All(int, vol.Range(min=0, max=100)),
        })

        return self.async_show_form(step_id="programs", data_schema=schema)

    async def async_step_winter(self, user_input=None):
        """Placeholder — winter thresholds come later."""
        if user_input is not None:
            options = dict(self._entry.options)
            options["winter_enabled"] = user_input["winter_enabled"]
            return self.async_create_entry(title="", data=options)

        schema = vol.Schema({
            vol.Required("winter_enabled", default=self._entry.options.get("winter_enabled", False)): bool,
        })

        return self.async_show_form(step_id="winter", data_schema=schema)
