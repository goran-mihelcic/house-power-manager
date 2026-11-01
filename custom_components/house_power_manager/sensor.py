"""Grid import, headroom, managed load and status; granted power and state per device."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .balancer import Status
from .coordinator import HpmConfigEntry, HpmCoordinator
from .device import DeviceState, ManagedDevice
from .entity import HpmDeviceEntity, HpmEntity


@dataclass(frozen=True, kw_only=True)
class HpmSensorDescription(SensorEntityDescription):
    value_fn: Callable[[HpmCoordinator], StateType]
    attrs_fn: Callable[[HpmCoordinator], dict[str, Any]] = lambda _: {}


def _measurement_attrs(ctl: HpmCoordinator) -> dict[str, Any]:
    return {
        "source": ctl.grid_sensor,
        "sign_convention": "import_positive" if ctl.import_positive else "import_negative",
        "measured_at": ctl.measured_at.isoformat(),
    }


def _headroom_attrs(ctl: HpmCoordinator) -> dict[str, Any]:
    return {"limit": ctl.limit, "margin": ctl.margin, "target": ctl.data.target}


def _status_attrs(ctl: HpmCoordinator) -> dict[str, Any]:
    return {
        "reason": ctl.data.fault,
        "since": ctl.status_since.isoformat(),
        "enabled": ctl.enabled,
    }


def _power(key: str, **kwargs: Any) -> HpmSensorDescription:
    return HpmSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        **kwargs,
    )


SENSORS: tuple[HpmSensorDescription, ...] = (
    _power(
        "grid_import",
        value_fn=lambda ctl: ctl.data.grid_import,
        attrs_fn=_measurement_attrs,
    ),
    _power(
        "headroom",
        value_fn=lambda ctl: ctl.data.headroom,
        attrs_fn=_headroom_attrs,
    ),
    _power("managed_load", value_fn=lambda ctl: ctl.managed_load),
    HpmSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[status.value for status in Status],
        value_fn=lambda ctl: ctl.data.status,
        attrs_fn=_status_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(HpmSensor(coordinator, description) for description in SENSORS)
    for device in coordinator.devices.values():
        async_add_entities(
            [
                GrantedPowerSensor(coordinator, device, GRANTED_POWER),
                DeviceStateSensor(coordinator, device, DEVICE_STATE),
            ],
            config_subentry_id=device.id,
        )


class HpmSensor(HpmEntity, SensorEntity):
    entity_description: HpmSensorDescription

    def __init__(self, coordinator: HpmCoordinator, description: HpmSensorDescription) -> None:
        super().__init__(coordinator, description, ENTITY_ID_FORMAT)

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self.coordinator)


GRANTED_POWER = SensorEntityDescription(
    key="granted_power",
    translation_key="granted_power",
    device_class=SensorDeviceClass.POWER,
    native_unit_of_measurement=UnitOfPower.WATT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
)
DEVICE_STATE = SensorEntityDescription(
    key="state",
    translation_key="device_state",
    device_class=SensorDeviceClass.ENUM,
    options=[state.value for state in DeviceState],
)


class GrantedPowerSensor(HpmDeviceEntity, SensorEntity):
    """Measured power while granted (nominal without a power sensor), else 0."""

    @property
    def native_value(self) -> float:
        return self.coordinator.granted_power(self._device)


class DeviceStateSensor(HpmDeviceEntity, SensorEntity):
    """What the integration is doing with the device, and why."""

    @property
    def native_value(self) -> str:
        return self._device.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device: ManagedDevice = self._device
        changed_at = device.changed_at
        return {
            "priority": device.priority,
            "nominal_power": device.nominal_power,
            "control_entity": device.control_entity,
            "power_sensor": device.power_sensor,
            "requested": device.requested,
            "granted": device.granted,
            "managed": device.managed,
            "reason": device.reason,
            "last_change": (
                dt_util.utc_from_timestamp(changed_at).isoformat() if changed_at else None
            ),
        }
