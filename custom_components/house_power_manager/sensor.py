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
from .device import DeviceState, Supply
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
        entities: list[SensorEntity] = [DeviceStateSensor(coordinator, device, DEVICE_STATE)]
        if not device.supply_id:  # a device on a shared supply: its power is the supply's
            entities.append(GrantedPowerSensor(coordinator, device, GRANTED_POWER))
        async_add_entities(entities, config_subentry_id=device.id)
    for supply in coordinator.supplies.values():
        async_add_entities(
            [
                GrantedPowerSensor(coordinator, supply, GRANTED_POWER),
                DeviceStateSensor(coordinator, supply, DEVICE_STATE),
            ],
            config_subentry_id=supply.id,
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
    """Power the device draws: its power sensor, else its declared power while it is on."""

    @property
    def native_value(self) -> float:
        return self.coordinator.device_power(self._device)


class DeviceStateSensor(HpmDeviceEntity, SensorEntity):
    """What the integration is doing with the device, and why."""

    @property
    def native_value(self) -> str:
        return self._device.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        unit = self._device
        changed_at = unit.changed_at
        attrs: dict[str, Any] = {"priority": unit.priority}
        if isinstance(unit, Supply):
            attrs |= {
                "nominal_power": unit.nominal_power,
                "power_sensor": unit.power_sensor,
                "devices": [device.name for device in unit.members],
                "requested": unit.requested,
            }
        elif supply := self.coordinator.supplies.get(unit.supply_id or ""):
            attrs |= {
                "control_entity": unit.control_entity,
                "power_supply": supply.name,
                "requested": unit.requested,
                "managed": unit.managed,
            }
        else:
            attrs |= {
                "nominal_power": unit.nominal_power,
                "control_entity": unit.control_entity,
                "power_sensor": unit.power_sensor,
                "requested": unit.requested,
                "managed": unit.managed,
            }
        return attrs | {
            "granted": unit.granted,
            "reason": unit.reason,
            "last_change": (
                dt_util.utc_from_timestamp(changed_at).isoformat() if changed_at else None
            ),
        }
