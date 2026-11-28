"""UI setup of the global settings, managed devices and shared supplies (subentries)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
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
    CONF_NOTIFICATIONS,
    CONF_NOTIFY_LIMIT,
    CONF_NOTIFY_SHED,
    CONF_NOTIFY_TARGETS,
    CONF_POWER_SENSOR,
    CONF_PRIORITY,
    CONF_SIGN,
    CONF_SUPPLY,
    CONF_UPDATE_INTERVAL,
    CONF_WARNING_MARGIN,
    CONTROL_DOMAINS,
    DEFAULTS,
    DEVICE_DEFAULTS,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_WATER_HEATER,
    DEVICE_TYPES,
    DOMAIN,
    SIGN_IMPORT_NEGATIVE,
    SIGN_IMPORT_POSITIVE,
    SUBENTRY_DEVICE,
    SUBENTRY_SUPPLY,
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
        vol.Required(CONF_NOTIFICATIONS): section(
            vol.Schema(
                {
                    vol.Optional(CONF_NOTIFY_TARGETS): sel.TextSelector(
                        sel.TextSelectorConfig(multiple=True)
                    ),
                    vol.Required(CONF_NOTIFY_SHED): sel.BooleanSelector(),
                    vol.Required(CONF_NOTIFY_LIMIT): sel.BooleanSelector(),
                    vol.Required(CONF_WARNING_MARGIN): _number(0, 5000, 50, "W"),
                }
            ),
            {"collapsed": False},
        ),
    }
)


def _supply_options(entry: ConfigEntry) -> list[sel.SelectOptionDict]:
    return [
        sel.SelectOptionDict(value=sub.subentry_id, label=sub.title)
        for sub in sorted(entry.subentries.values(), key=lambda s: s.title.casefold())
        if sub.subentry_type == SUBENTRY_SUPPLY
    ]


def _device_schema(entry: ConfigEntry, kind: str) -> vol.Schema:
    """Who the device is; the control entity of its type; the power supply field only
    once a supply exists."""
    schema: dict[vol.Marker, Any] = {
        vol.Required(CONF_NAME): sel.TextSelector(),
        vol.Required(CONF_PRIORITY): _number(1, 100, 1),
        vol.Required(CONF_CONTROL_ENTITY): sel.EntitySelector(
            sel.EntitySelectorConfig(domain=CONTROL_DOMAINS[kind])
        ),
    }
    if supplies := _supply_options(entry):
        schema[vol.Optional(CONF_SUPPLY)] = sel.SelectSelector(
            sel.SelectSelectorConfig(options=supplies, mode=sel.SelectSelectorMode.DROPDOWN)
        )
    return vol.Schema(schema)


# Power and timing of a device on its own supply, or of a shared supply
POWER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NOMINAL_POWER): _number(1, 50000, 1, "W"),
        vol.Optional(CONF_POWER_SENSOR): _power_sensor(),
        vol.Required(CONF_MIN_ON_TIME): _number(0, 86400, 1, "s"),
        vol.Required(CONF_MIN_OFF_TIME): _number(0, 86400, 1, "s"),
        vol.Required(CONF_LATENCY): _number(0, 600, 1, "s"),
    }
)

SUPPLY_SCHEMA = vol.Schema({vol.Required(CONF_NAME): sel.TextSelector()}).extend(
    POWER_SCHEMA.schema
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
    notifications = user_input[CONF_NOTIFICATIONS]
    targets = [
        t.strip().removeprefix("notify.") for t in notifications.get(CONF_NOTIFY_TARGETS) or []
    ]
    notifications[CONF_NOTIFY_TARGETS] = [t for t in targets if t]
    for target in notifications[CONF_NOTIFY_TARGETS]:
        # A notify action (notify.mobile_app_x) or a notify entity (e.g. a notify group)
        if not hass.services.has_service("notify", target) and (
            hass.states.get(f"notify.{target}") is None
        ):
            errors[CONF_NOTIFY_TARGETS] = "unknown_notify"
    if notifications[CONF_WARNING_MARGIN] > user_input[CONF_MARGIN]:
        # The warning would go off during normal balancing, below the target
        errors[CONF_WARNING_MARGIN] = "warning_below_target"
    return errors


def _validate_name(entry: ConfigEntry, name: str, subentry_id: str | None) -> str | None:
    """Device and supply names name their entities, so they must differ."""
    if not name.strip():
        return "name_required"
    others = (sub for sub in entry.subentries.values() if sub.subentry_id != subentry_id)
    if any(sub.title.casefold() == name.strip().casefold() for sub in others):
        return "name_in_use"
    return None


def _validate_device(
    entry: ConfigEntry, user_input: dict[str, Any], subentry_id: str | None
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if error := _validate_name(entry, user_input[CONF_NAME], subentry_id):
        errors[CONF_NAME] = error
    others = (sub for sub in entry.subentries.values() if sub.subentry_id != subentry_id)
    if any(sub.data.get(CONF_CONTROL_ENTITY) == user_input[CONF_CONTROL_ENTITY] for sub in others):
        errors[CONF_CONTROL_ENTITY] = "control_in_use"
    return errors


def _validate_power(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    if (sensor := user_input.get(CONF_POWER_SENSOR)) and (error := _power_unit_error(hass, sensor)):
        return {CONF_POWER_SENSOR: error}
    return {}


class HpmConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the single House Power Manager entry."""

    VERSION = 1
    # 2: shared supplies are subentries, not names on devices. 3: devices have a type.
    MINOR_VERSION = 3

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HpmOptionsFlow:
        return HpmOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_DEVICE: DeviceSubentryFlow, SUBENTRY_SUPPLY: SupplySubentryFlow}

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
    """Add or change a managed device. The name is the subentry title.

    Adding starts with the device type, which sets the kind of control entity and the
    stand-in entity. A device on a shared supply is done after the device step; one on
    its own supply goes on to its power and timing settings. The type stays as added.
    """

    def __init__(self) -> None:
        self._kind = DEVICE_TYPE_SWITCH
        self._name = ""
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(step_id="user", menu_options=list(DEVICE_TYPES))

    async def async_step_climate(self, user_input: None = None) -> SubentryFlowResult:
        return await self._async_pick(DEVICE_TYPE_CLIMATE)

    async def async_step_switch(self, user_input: None = None) -> SubentryFlowResult:
        return await self._async_pick(DEVICE_TYPE_SWITCH)

    async def async_step_water_heater(self, user_input: None = None) -> SubentryFlowResult:
        return await self._async_pick(DEVICE_TYPE_WATER_HEATER)

    async def _async_pick(self, kind: str) -> SubentryFlowResult:
        self._kind = kind
        return await self.async_step_device()

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_step_device("device", None, user_input)

    async def async_step_power(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_step_power("power", None, user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        self._kind = subentry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_SWITCH)
        return await self._async_step_device(
            "reconfigure", {CONF_NAME: subentry.title, **subentry.data}, user_input
        )

    async def async_step_reconfigure_power(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_step_power(
            "reconfigure_power", dict(self._get_reconfigure_subentry().data), user_input
        )

    @property
    def _reconfiguring(self) -> bool:
        return self.source == SOURCE_RECONFIGURE

    async def _async_step_device(
        self, step_id: str, current: dict[str, Any] | None, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        subentry_id = self._get_reconfigure_subentry().subentry_id if self._reconfiguring else None
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_device(entry, user_input, subentry_id)
            if not errors:
                self._name = user_input.pop(CONF_NAME).strip()
                self._data = {CONF_DEVICE_TYPE: self._kind, **user_input}
                if user_input.get(CONF_SUPPLY):
                    return self._async_finish()
                if self._reconfiguring:
                    return await self.async_step_reconfigure_power()
                return await self.async_step_power()
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                _device_schema(entry, self._kind),
                {**DEVICE_DEFAULTS, **(current or {}), **(user_input or {})},
            ),
            errors=errors,
        )

    async def _async_step_power(
        self, step_id: str, current: dict[str, Any] | None, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_power(self.hass, user_input)
            if not errors:
                self._data.update(user_input)
                return self._async_finish()
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                POWER_SCHEMA, {**DEVICE_DEFAULTS, **(current or {}), **(user_input or {})}
            ),
            errors=errors,
            description_placeholders={"name": self._name},
        )

    @callback
    def _async_finish(self) -> SubentryFlowResult:
        # The data is replaced, not merged, so cleared fields are removed
        if self._reconfiguring:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                title=self._name,
                data=self._data,
            )
        return self.async_create_entry(title=self._name, data=self._data)


class SupplySubentryFlow(ConfigSubentryFlow):
    """Add or change a shared power supply, e.g. a multi-split outdoor unit."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_step("user", None, user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        return await self._async_step(
            "reconfigure", {CONF_NAME: subentry.title, **subentry.data}, user_input
        )

    async def _async_step(
        self, step_id: str, current: dict[str, Any] | None, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        reconfiguring = self.source == SOURCE_RECONFIGURE
        subentry_id = self._get_reconfigure_subentry().subentry_id if reconfiguring else None
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_power(self.hass, user_input)
            if error := _validate_name(entry, user_input[CONF_NAME], subentry_id):
                errors[CONF_NAME] = error
            if not errors:
                name = user_input.pop(CONF_NAME).strip()
                if reconfiguring:
                    return self.async_update_and_abort(
                        entry, self._get_reconfigure_subentry(), title=name, data=user_input
                    )
                return self.async_create_entry(title=name, data=user_input)
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                SUPPLY_SCHEMA, {**DEVICE_DEFAULTS, **(current or {}), **(user_input or {})}
            ),
            errors=errors,
        )
