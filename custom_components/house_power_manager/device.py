"""Runtime state of one managed device (a config subentry)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers.device_registry import DeviceInfo

from .balancer import DeviceInput
from .const import (
    CONF_CONTROL_ENTITY,
    CONF_LATENCY,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_NOMINAL_POWER,
    CONF_POWER_SENSOR,
    CONF_PRIORITY,
    DOMAIN,
)

# Kept in the store and restored after a restart
_STORED = ("requested", "granted", "managed", "changed_at", "reason")


class DeviceState(StrEnum):
    IDLE = "idle"  # not requested
    RUNNING = "running"  # granted
    THROTTLED = "throttled"  # adjustable device below its requested power
    SHED = "shed"  # requested, not granted
    OVERRIDE = "override"  # not managed: left alone


@dataclass(slots=True)
class ManagedDevice:
    id: str  # subentry id
    name: str
    priority: int
    control_entity: str
    nominal_power: float
    power_sensor: str | None
    min_on: float
    min_off: float
    latency: float
    requested: bool = False
    granted: bool = False
    managed: bool = True
    changed_at: float | None = None  # epoch s of the last grant or managed change
    reason: str | None = None  # why the grant last changed
    commanded_at: float | None = None  # last command sent to the control entity

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> ManagedDevice:
        data = subentry.data
        return cls(
            id=subentry.subentry_id,
            name=subentry.title,
            priority=int(data[CONF_PRIORITY]),
            control_entity=data[CONF_CONTROL_ENTITY],
            nominal_power=float(data[CONF_NOMINAL_POWER]),
            power_sensor=data.get(CONF_POWER_SENSOR),
            min_on=float(data[CONF_MIN_ON_TIME]),
            min_off=float(data[CONF_MIN_OFF_TIME]),
            latency=float(data[CONF_LATENCY]),
        )

    def stored(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in _STORED}

    def restore(self, stored: dict[str, Any]) -> None:
        for key in _STORED:
            if key in stored:
                setattr(self, key, stored[key])

    def as_input(self, measured_power: float | None) -> DeviceInput:
        return DeviceInput(
            id=self.id,
            priority=self.priority,
            nominal_power=self.nominal_power,
            measured_power=measured_power,
            requested=self.requested,
            granted=self.granted,
            min_on=self.min_on,
            min_off=self.min_off,
            latency=self.latency,
            changed_at=self.changed_at,
            commanded_at=self.commanded_at,
        )

    @property
    def state(self) -> DeviceState:
        if not self.managed:
            return DeviceState.OVERRIDE
        if self.granted:
            return DeviceState.RUNNING
        return DeviceState.SHED if self.requested else DeviceState.IDLE

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self.id)}, name=self.name, model="Managed device")
