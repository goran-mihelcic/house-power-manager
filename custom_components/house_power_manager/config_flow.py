"""UI setup and reconfiguration of the global settings."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector as sel
import voluptuous as vol

from .balancer import POWER_UNITS
from .const import (
    CONF_FAULT_TIMEOUT,
    CONF_GRID_SENSOR,
    CONF_IMPORT_LIMIT,
    CONF_MARGIN,
    CONF_SIGN,
    CONF_UPDATE_INTERVAL,
    DEFAULTS,
    DOMAIN,
    SIGN_IMPORT_NEGATIVE,
    SIGN_IMPORT_POSITIVE,
)

TITLE = "House Power Manager"


def _number(low: float, high: float, step: float, unit: str) -> sel.NumberSelector:
    return sel.NumberSelector(
        sel.NumberSelectorConfig(
            min=low, max=high, step=step, unit_of_measurement=unit, mode=sel.NumberSelectorMode.BOX
        )
    )


SCHEMA = vol.Schema(
    {
        vol.Required(CONF_GRID_SENSOR): sel.EntitySelector(
            sel.EntitySelectorConfig(domain="sensor", device_class="power")
        ),
        vol.Required(CONF_SIGN): sel.SelectSelector(
            sel.SelectSelectorConfig(
                options=[SIGN_IMPORT_POSITIVE, SIGN_IMPORT_NEGATIVE],
                translation_key="sign_convention",
                mode=sel.SelectSelectorMode.LIST,
            )
        ),
        vol.Required(CONF_IMPORT_LIMIT): _number(1000, 50000, 100, "W"),
        vol.Required(CONF_MARGIN): _number(0, 5000, 50, "W"),
        vol.Required(CONF_UPDATE_INTERVAL): _number(1, 60, 1, "s"),
        vol.Required(CONF_FAULT_TIMEOUT): _number(10, 600, 5, "s"),
    }
)


def _validate(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    state = hass.states.get(user_input[CONF_GRID_SENSOR])
    if state is None:
        errors[CONF_GRID_SENSOR] = "sensor_not_found"
    elif state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) not in POWER_UNITS:
        errors[CONF_GRID_SENSOR] = "unsupported_unit"
    if user_input[CONF_MARGIN] >= user_input[CONF_IMPORT_LIMIT]:
        errors[CONF_MARGIN] = "margin_too_large"
    return errors


class HpmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the single House Power Manager entry."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HpmOptionsFlow:
        return HpmOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(title=TITLE, data={}, options=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                SCHEMA, {**DEFAULTS, **(user_input or {})}
            ),
            errors=errors,
        )


class HpmOptionsFlow(OptionsFlow):
    """Change the global settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(data={**self.config_entry.options, **user_input})
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                SCHEMA, {**self.config_entry.options, **(user_input or {})}
            ),
            errors=errors,
        )
