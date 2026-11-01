"""Grid import, headroom, managed load and status sensors."""

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

from .balancer import Status
from .coordinator import HpmConfigEntry, HpmCoordinator
from .entity import HpmEntity


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
    # Sum of granted device power; there are no managed devices yet.
    _power("managed_load", value_fn=lambda _: 0.0),
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
    async_add_entities(HpmSensor(entry.runtime_data, description) for description in SENSORS)


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
