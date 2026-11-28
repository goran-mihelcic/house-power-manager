"""Runtime state of managed devices and shared power supplies (config subentries)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers.device_registry import DeviceInfo

from .balancer import DeviceInput, Reason
from .const import (
    CONF_CONTROL_ENTITY,
    CONF_DEVICE_TYPE,
    CONF_LATENCY,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_NOMINAL_POWER,
    CONF_POWER_SENSOR,
    CONF_PRIORITY,
    CONF_SUPPLY,
    DEVICE_DEFAULTS,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_SWITCH,
    DEVICE_TYPE_WATER_HEATER,
    DOMAIN,
)


class DeviceState(StrEnum):
    IDLE = "idle"  # not requested
    RUNNING = "running"  # granted
    THROTTLED = "throttled"  # adjustable device below its requested power
    SHED = "shed"  # requested, not granted
    OVERRIDE = "override"  # not managed: left alone


def _power_settings(data: Any) -> dict[str, Any]:
    """Power and timing settings; a device on a shared supply has none of its own."""
    return {
        "nominal_power": float(data.get(CONF_NOMINAL_POWER, 0)),
        "power_sensor": data.get(CONF_POWER_SENSOR),
        "min_on": float(data.get(CONF_MIN_ON_TIME, DEVICE_DEFAULTS[CONF_MIN_ON_TIME])),
        "min_off": float(data.get(CONF_MIN_OFF_TIME, DEVICE_DEFAULTS[CONF_MIN_OFF_TIME])),
        "latency": float(data.get(CONF_LATENCY, DEVICE_DEFAULTS[CONF_LATENCY])),
    }


@dataclass(slots=True, kw_only=True)
class PowerUnit:
    """What the balancer grants power to: a managed device or a shared supply."""

    MODEL: ClassVar[str]
    STORED: ClassVar[tuple[str, ...]] = ("granted", "changed_at", "reason")

    id: str  # subentry id
    name: str
    nominal_power: float
    power_sensor: str | None
    min_on: float
    min_off: float
    latency: float
    granted: bool = False
    changed_at: float | None = None  # epoch s of the last grant change
    reason: str | None = None  # why the grant last changed
    commanded_at: float | None = None  # last command sent to a control entity

    def stored(self) -> dict[str, Any]:
        """Kept in the store and restored after a restart."""
        return {key: getattr(self, key) for key in self.STORED}

    def restore(self, stored: dict[str, Any]) -> None:
        for key in self.STORED:
            if key in stored:
                setattr(self, key, stored[key])

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self.id)}, name=self.name, model=self.MODEL)


@dataclass(slots=True, kw_only=True)
class ManagedDevice(PowerUnit):
    """A load switched through its control entity."""

    MODEL: ClassVar[str] = "Managed device"
    STORED: ClassVar[tuple[str, ...]] = (
        *PowerUnit.STORED,
        "requested",
        "managed",
        "desired_mode",
        "desired_temp",
    )

    priority: int
    control_entity: str
    kind: str = DEVICE_TYPE_SWITCH  # device type
    supply_id: str | None = None  # draws from this shared supply
    requested: bool = False
    managed: bool = True
    # A climate device or water heater: the mode to run in (HVAC or operation mode) and
    # the setpoint asked for, applied when granted
    desired_mode: str | None = None
    desired_temp: float | None = None

    @property
    def uses_modes(self) -> bool:
        """Switched with a mode rather than turn_on / turn_off."""
        return self.kind in (DEVICE_TYPE_CLIMATE, DEVICE_TYPE_WATER_HEATER)

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> ManagedDevice:
        data = subentry.data
        return cls(
            id=subentry.subentry_id,
            name=subentry.title,
            priority=int(data[CONF_PRIORITY]),
            control_entity=data[CONF_CONTROL_ENTITY],
            kind=data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_SWITCH),
            supply_id=data.get(CONF_SUPPLY),
            **_power_settings(data),
        )

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
            followed=self.reason == Reason.MANUAL,
        )

    @property
    def state(self) -> DeviceState:
        if not self.managed:
            return DeviceState.OVERRIDE
        if self.granted:
            return DeviceState.RUNNING
        return DeviceState.SHED if self.requested else DeviceState.IDLE


@dataclass(slots=True, kw_only=True)
class Supply(PowerUnit):
    """Power shared by several devices, e.g. a multi-split outdoor unit.

    The balancer sees one load: requested while any of its devices is requested, as
    important as the most important of them. Its devices run while it is granted.
    """

    MODEL: ClassVar[str] = "Shared power supply"

    members: list[ManagedDevice] = field(default_factory=list)

    @classmethod
    def from_subentry(cls, subentry: ConfigSubentry) -> Supply:
        return cls(id=subentry.subentry_id, name=subentry.title, **_power_settings(subentry.data))

    @property
    def active(self) -> list[ManagedDevice]:
        """Its managed devices; one left alone doesn't take part."""
        return [device for device in self.members if device.managed]

    @property
    def requested(self) -> bool:
        return any(device.requested for device in self.active)

    @property
    def priority(self) -> int:
        requested = [d.priority for d in self.active if d.requested]
        return max(requested or [d.priority for d in self.active] or [1])

    def as_input(self, measured_power: float | None) -> DeviceInput:
        commands = [d.commanded_at for d in self.active if d.commanded_at is not None]
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
            commanded_at=max(commands, default=None),
            followed=self.reason == Reason.MANUAL,
        )

    @property
    def state(self) -> DeviceState:
        if self.granted:
            return DeviceState.RUNNING
        return DeviceState.SHED if self.requested else DeviceState.IDLE
