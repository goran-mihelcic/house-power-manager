"""UI setup of the global settings and of the managed devices (config subentries)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector as sel
import voluptuous as vol

from .balancer import POWER_UNITS
from .const import (
    CONF_CONTROL_ENTITY,
    CONF_DEVICE_TYPE,
    CONF_FAULT_TIMEOUT,
    CONF_GRID_SENSOR,
    CONF_HYSTERESIS,
    CONF_IMPORT_LIMIT,
    CONF_LATENCY,
    CONF_MARGIN,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_NOMINAL_POWER,
    CONF_POWER_SENSOR,
    CONF_PRIORITY,
    CONF_SIGN,
    CONF_UPDATE_INTERVAL,
    CONTROL_DOMAINS,
    DEFAULTS,
    DEVICE_DEFAULTS,
    DEVICE_TYPE_SWITCH,
    DOMAIN,
    SIGN_IMPORT_NEGATIVE,
    SIGN_IMPORT_POSITIVE,
    SUBENTRY_DEVICE,
)

TITLE = "House Power Manager"


def _number(low: float, high: float, step: float, unit: str | None = None) -> sel.NumberSelector:
    config = sel.NumberSelectorConfig(min=low, max=high, step=step, mode=sel.NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return sel.NumberSelector(config)


def _power_sensor() -> sel.EntitySelector:
    return sel.EntitySelector(sel.EntitySelectorConfig(domain="sensor", device_class="power"))


SCHEMA = vol.Schema(
    {
        vol.Required(CONF_GRID_SENSOR): _power_sensor(),
        vol.Required(CONF_SIGN): sel.SelectSelector(
            sel.SelectSelectorConfig(
                options=[SIGN_IMPORT_POSITIVE, SIGN_IMPORT_NEGATIVE],
                translation_key="sign_convention",
                mode=sel.SelectSelectorMode.LIST,
            )
        ),
        vol.Required(CONF_IMPORT_LIMIT): _number(1000, 50000, 100, "W"),
        vol.Required(CONF_MARGIN): _number(0, 5000, 50, "W"),
        vol.Required(CONF_HYSTERESIS): _number(0, 5000, 50, "W"),
        vol.Required(CONF_UPDATE_INTERVAL): _number(1, 60, 1, "s"),
        vol.Required(CONF_FAULT_TIMEOUT): _number(10, 600, 5, "s"),
    }
)

DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): sel.TextSelector(),
        vol.Required(CONF_PRIORITY): _number(1, 100, 1),
        vol.Required(CONF_CONTROL_ENTITY): sel.EntitySelector(
            sel.EntitySelectorConfig(domain=CONTROL_DOMAINS)
        ),
        vol.Required(CONF_NOMINAL_POWER): _number(1, 50000, 1, "W"),
        vol.Optional(CONF_POWER_SENSOR): _power_sensor(),
        vol.Required(CONF_MIN_ON_TIME): _number(0, 86400, 1, "s"),
        vol.Required(CONF_MIN_OFF_TIME): _number(0, 86400, 1, "s"),
        vol.Required(CONF_LATENCY): _number(0, 600, 1, "s"),
    }
)


def _power_unit_error(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is None:
        return "sensor_not_found"
    if state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) not in POWER_UNITS:
        return "unsupported_unit"
    return None


def _validate(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if error := _power_unit_error(hass, user_input[CONF_GRID_SENSOR]):
        errors[CONF_GRID_SENSOR] = error
    if user_input[CONF_MARGIN] >= user_input[CONF_IMPORT_LIMIT]:
        errors[CONF_MARGIN] = "margin_too_large"
    return errors


def _validate_device(
    hass: HomeAssistant, entry: ConfigEntry, user_input: dict[str, Any], subentry_id: str | None
) -> dict[str, str]:
    errors: dict[str, str] = {}
    others = [sub for sub in entry.subentries.values() if sub.subentry_id != subentry_id]
    if not user_input[CONF_NAME].strip():
        errors[CONF_NAME] = "name_required"
    elif any(sub.title.casefold() == user_input[CONF_NAME].strip().casefold() for sub in others):
        errors[CONF_NAME] = "name_in_use"
    if any(sub.data.get(CONF_CONTROL_ENTITY) == user_input[CONF_CONTROL_ENTITY] for sub in others):
        errors[CONF_CONTROL_ENTITY] = "control_in_use"
    if (sensor := user_input.get(CONF_POWER_SENSOR)) and (error := _power_unit_error(hass, sensor)):
        errors[CONF_POWER_SENSOR] = error
    return errors


class HpmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the single House Power Manager entry."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HpmOptionsFlow:
        return HpmOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_DEVICE: DeviceSubentryFlow}

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
                SCHEMA, {**DEFAULTS, **self.config_entry.options, **(user_input or {})}
            ),
            errors=errors,
        )


class DeviceSubentryFlow(ConfigSubentryFlow):
    """Add or change a managed device. The name is the subentry title."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_device(self.hass, self._get_entry(), user_input, None)
            if not errors:
                name = user_input.pop(CONF_NAME).strip()
                return self.async_create_entry(
                    title=name, data={CONF_DEVICE_TYPE: DEVICE_TYPE_SWITCH, **user_input}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                DEVICE_SCHEMA, {**DEVICE_DEFAULTS, **(user_input or {})}
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_device(self.hass, entry, user_input, subentry.subentry_id)
            if not errors:
                name = user_input.pop(CONF_NAME).strip()
                # Replace rather than merge, so a cleared power sensor is removed
                data = {CONF_DEVICE_TYPE: subentry.data[CONF_DEVICE_TYPE], **user_input}
                return self.async_update_and_abort(entry, subentry, title=name, data=data)
        current = {CONF_NAME: subentry.title, **subentry.data}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                DEVICE_SCHEMA, {**current, **(user_input or {})}
            ),
            errors=errors,
        )
