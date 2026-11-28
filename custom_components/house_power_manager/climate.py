"""Power-managed stand-ins for managed devices controlled through a climate entity.

Room Climate and other automations drive the stand-in instead of the thermostat itself.
It shows the mode and setpoint asked for, so a device switched off for lack of power
doesn't look like it was changed by hand and isn't switched on again behind the
balancer's back. Everything else is the thermostat's own: its current temperature,
HVAC action, modes, limits and fan modes.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES, ATTR_TEMPERATURE, STATE_OFF
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DEVICE_TYPE_CLIMATE
from .coordinator import HpmConfigEntry, HpmCoordinator, on_mode, to_float
from .device import ManagedDevice
from .entity import HpmDeviceEntity

POWER_MANAGED = EntityDescription(key="climate", translation_key="power_managed")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    for device in coordinator.devices.values():
        if device.kind == DEVICE_TYPE_CLIMATE:
            async_add_entities(
                [PowerManagedClimate(coordinator, device, POWER_MANAGED)],
                config_subentry_id=device.id,
            )


class PowerManagedClimate(HpmDeviceEntity, ClimateEntity):
    """The managed thermostat, as asked for rather than as granted."""

    _device: ManagedDevice

    def __init__(
        self, coordinator: HpmCoordinator, device: ManagedDevice, description: EntityDescription
    ) -> None:
        super().__init__(coordinator, device, description)
        self._attr_temperature_unit = coordinator.hass.config.units.temperature_unit

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._device.control_entity, self._async_source_changed
            )
        )

    @callback
    def _async_source_changed(self, event: Event[EventStateChangedData]) -> None:
        self.async_write_ha_state()

    @property
    def _source(self) -> State | None:
        return self.hass.states.get(self._device.control_entity)

    def _attr(self, name: str) -> Any:
        source = self._source
        return source.attributes.get(name) if source else None

    @property
    def available(self) -> bool:
        source = self._source
        return super().available and source is not None and source.state != "unavailable"

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        source = ClimateEntityFeature(self._attr(ATTR_SUPPORTED_FEATURES) or 0)
        if source & ClimateEntityFeature.TARGET_TEMPERATURE:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self.fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        return features

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = [HVACMode(m) for m in self._attr(ATTR_HVAC_MODES) or [] if m in HVACMode]
        return modes if HVACMode.OFF in modes else [HVACMode.OFF, *modes]

    @property
    def hvac_mode(self) -> HVACMode:
        """The mode asked for: off while not requested, even when shed."""
        source = self._source
        if not self._device.requested or source is None:
            return HVACMode.OFF
        if source.state not in (STATE_OFF, "unknown") and not self._device.desired_mode:
            return HVACMode(source.state)
        mode = on_mode(self._device, source)
        return HVACMode(mode) if mode else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        action = self._attr(ATTR_HVAC_ACTION)
        return HVACAction(action) if action in HVACAction else None

    @property
    def current_temperature(self) -> float | None:
        return to_float(self._attr(ATTR_CURRENT_TEMPERATURE))

    @property
    def target_temperature(self) -> float | None:
        if self._device.desired_temp is not None:
            return self._device.desired_temp
        return to_float(self._attr(ATTR_TEMPERATURE))

    @property
    def target_temperature_step(self) -> float | None:
        return to_float(self._attr(ATTR_TARGET_TEMP_STEP))

    @property
    def min_temp(self) -> float:
        value = to_float(self._attr(ATTR_MIN_TEMP))
        return value if value is not None else super().min_temp

    @property
    def max_temp(self) -> float:
        value = to_float(self._attr(ATTR_MAX_TEMP))
        return value if value is not None else super().max_temp

    @property
    def fan_modes(self) -> list[str] | None:
        return self._attr(ATTR_FAN_MODES)

    @property
    def fan_mode(self) -> str | None:
        return self._attr(ATTR_FAN_MODE)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source": self._device.control_entity, "power_state": self._device.state}

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.coordinator.async_mode_command(self._device, mode=hvac_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        await self.coordinator.async_mode_command(
            self._device,
            temperature=kwargs.get(ATTR_TEMPERATURE),
            mode=kwargs.get(ATTR_HVAC_MODE),
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator.async_mode_command(self._device, fan_mode=fan_mode)

    async def async_turn_on(self) -> None:
        source = self._source
        mode = on_mode(self._device, source) if source else None
        await self.coordinator.async_mode_command(self._device, mode=mode or HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_mode_command(self._device, mode=HVACMode.OFF)
