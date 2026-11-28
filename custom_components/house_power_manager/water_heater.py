"""Power-managed stand-ins for managed water heaters.

Like the climate stand-in: automations drive this instead of the water heater itself.
It shows the operation mode and setpoint asked for, so a water heater switched off for
lack of power doesn't look like it was changed by hand. Current temperature, modes and
limits are the water heater's own.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_OPERATION_LIST,
    ATTR_TARGET_TEMP_STEP,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import (
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import DEVICE_TYPE_WATER_HEATER
from .coordinator import HpmConfigEntry, HpmCoordinator, on_mode, to_float
from .device import ManagedDevice
from .entity import HpmDeviceEntity

POWER_MANAGED = EntityDescription(key="water_heater", translation_key="power_managed")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    for device in coordinator.devices.values():
        if device.kind == DEVICE_TYPE_WATER_HEATER:
            async_add_entities(
                [PowerManagedWaterHeater(coordinator, device, POWER_MANAGED)],
                config_subentry_id=device.id,
            )


class PowerManagedWaterHeater(HpmDeviceEntity, WaterHeaterEntity):
    """The managed water heater, as asked for rather than as granted."""

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
        return super().available and source is not None and source.state != STATE_UNAVAILABLE

    @property
    def supported_features(self) -> WaterHeaterEntityFeature:
        features = WaterHeaterEntityFeature.ON_OFF
        source = WaterHeaterEntityFeature(self._attr(ATTR_SUPPORTED_FEATURES) or 0)
        if source & WaterHeaterEntityFeature.TARGET_TEMPERATURE:
            features |= WaterHeaterEntityFeature.TARGET_TEMPERATURE
        if self.operation_list:
            features |= WaterHeaterEntityFeature.OPERATION_MODE
        return features

    @property
    def operation_list(self) -> list[str] | None:
        return self._attr(ATTR_OPERATION_LIST)

    @property
    def current_operation(self) -> str | None:
        """The mode asked for: off while not requested, even when shed."""
        source = self._source
        if source is None:
            return None
        if not self._device.requested:
            return STATE_OFF
        mode = on_mode(self._device, source)
        if mode is None:  # only on / off
            return STATE_ON
        if not self._device.desired_mode and source.state != STATE_OFF:
            return source.state
        return mode

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
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source": self._device.control_entity, "power_state": self._device.state}

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        await self.coordinator.async_mode_command(self._device, mode=operation_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        await self.coordinator.async_mode_command(
            self._device, temperature=kwargs.get(ATTR_TEMPERATURE)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        source = self._source
        mode = on_mode(self._device, source) if source else None
        if mode is None:
            await self.coordinator.async_mode_command(self._device, on=True)
        else:
            await self.coordinator.async_mode_command(self._device, mode=mode)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if STATE_OFF in (self.operation_list or []):
            await self.coordinator.async_mode_command(self._device, mode=STATE_OFF)
        else:
            await self.coordinator.async_mode_command(self._device, on=False)
