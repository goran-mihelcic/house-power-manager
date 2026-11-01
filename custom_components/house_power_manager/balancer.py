"""Balancing decisions, free of Home Assistant so they can be unit tested.

The coordinator measures grid import, describes each managed device as a
`DeviceInput` and calls `decide`, which returns the grant changes to make.

- Over target: shed granted devices in ascending priority (1 first) until the
  deficit is covered. Minimum on-times are respected unless import is above the
  hard limit itself.
- Under target: restore one requested device per cycle, most important first,
  if headroom covers its nominal power plus hysteresis and its minimum off-time
  has passed. If the most important waiting device doesn't fit, less important
  running devices are shed to make room for it (preemption).
- Changes made within a device's latency are not in the measurement yet. Their
  power is accounted for when shedding, and nothing is restored until they
  have settled.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

_WATTS_PER_UNIT: dict[str, float] = {"W": 1.0, "kW": 1_000.0, "MW": 1_000_000.0}
POWER_UNITS = frozenset(_WATTS_PER_UNIT)


class Status(StrEnum):
    """What the integration is doing about grid import."""

    NORMAL = "normal"  # import at or below target
    LIMITING = "limiting"  # import above target
    EMERGENCY = "emergency"  # import above the hard limit
    SENSOR_FAULT = "sensor_fault"  # no trustworthy measurement


def to_watts(value: float, unit: str | None) -> float:
    """Convert a power reading to W. A missing or unknown unit is refused, not guessed."""
    factor = _WATTS_PER_UNIT.get(unit) if unit else None
    if factor is None:
        raise ValueError(f"unsupported power unit: {unit!r}")
    return value * factor


def normalize_import(value: float, unit: str | None, import_positive: bool) -> float:
    """Grid power in W: positive while importing, negative while exporting."""
    watts = to_watts(value, unit)
    return watts if import_positive else -watts


@dataclass(frozen=True, slots=True)
class GridSnapshot:
    """One evaluation of grid import against the limit."""

    grid_import: float | None  # W, negative while exporting; None until known
    limit: float
    margin: float
    fault: str | None = None  # why there is no trustworthy measurement

    @property
    def target(self) -> float:
        return self.limit - self.margin

    @property
    def headroom(self) -> float | None:
        """W that may still be drawn before reaching target; negative when over it."""
        if self.grid_import is None:
            return None
        return self.target - self.grid_import

    @property
    def status(self) -> Status | None:
        """None while waiting for the first reading after start-up."""
        if self.fault:
            return Status.SENSOR_FAULT
        if self.grid_import is None:
            return None
        if self.grid_import > self.limit:
            return Status.EMERGENCY
        if self.grid_import > self.target:
            return Status.LIMITING
        return Status.NORMAL


class Reason(StrEnum):
    """Why a grant changed."""

    HEADROOM = "headroom"  # restored: enough headroom
    RELEASED = "released"  # the request ended
    OVER_TARGET = "over_target"
    EMERGENCY = "emergency"  # import above the hard limit
    PREEMPTED = "preempted"  # made room for a more important device


@dataclass(frozen=True, slots=True)
class DeviceInput:
    """A managed device as the balancer sees it. Times are seconds (epoch)."""

    id: str
    priority: int  # 1 = shed first
    nominal_power: float  # W when running
    measured_power: float | None  # W from its power sensor, None without one
    requested: bool
    granted: bool
    min_on: float = 0
    min_off: float = 0
    latency: float = 0  # s before a grant change shows in the measurement
    changed_at: float | None = None  # last grant change
    commanded_at: float | None = None  # last command to the device, e.g. re-applying a grant

    def settling(self, now: float) -> bool:
        """A recent change or command may not show in the measurement yet."""
        times = [t for t in (self.changed_at, self.commanded_at) if t is not None]
        return bool(times) and now - max(times) < self.latency

    def on_long_enough(self, now: float) -> bool:
        return self.changed_at is None or now - self.changed_at >= self.min_on

    def off_long_enough(self, now: float) -> bool:
        return self.changed_at is None or now - self.changed_at >= self.min_off

    def load(self, now: float) -> float:
        """W that revoking the grant would free."""
        if self.settling(now) or self.measured_power is None:
            return self.nominal_power
        return self.measured_power

    def unseen(self, now: float) -> float:
        """W a recent grant change will still add to (negative: remove from) the measurement."""
        if not self.settling(now):
            return 0.0
        measured = self.measured_power
        if self.granted:
            return self.nominal_power if measured is None else max(self.nominal_power - measured, 0)
        return -(self.nominal_power if measured is None else measured)


@dataclass(frozen=True, slots=True)
class Action:
    device_id: str
    grant: bool
    reason: Reason


def _shed_order(devices: Iterable[DeviceInput], now: float) -> list[DeviceInput]:
    """Least important first; among equals the largest load, so fewer devices go off."""
    return sorted(devices, key=lambda d: (d.priority, -d.load(now), d.id))


def decide(
    grid: GridSnapshot, devices: Sequence[DeviceInput], now: float, hysteresis: float = 0
) -> list[Action]:
    """The grant changes to make now."""
    actions: list[Action] = []
    revoked: set[str] = set()

    def revoke(device: DeviceInput, reason: Reason) -> None:
        actions.append(Action(device.id, False, reason))
        revoked.add(device.id)

    for device in devices:
        if device.granted and not device.requested and device.on_long_enough(now):
            revoke(device, Reason.RELEASED)

    if grid.headroom is None:  # no trustworthy measurement: hold
        return actions

    # Headroom once changes not yet in the measurement have taken effect
    headroom = grid.headroom - sum(d.unseen(now) for d in devices if d.id not in revoked)
    headroom += sum(d.load(now) for d in devices if d.id in revoked)
    running = [d for d in devices if d.granted and d.id not in revoked and d.load(now) > 0]

    if headroom < 0:
        emergency = grid.status is Status.EMERGENCY
        for device in _shed_order(running, now):
            if headroom >= 0:
                break
            if emergency or device.on_long_enough(now):
                revoke(device, Reason.EMERGENCY if emergency else Reason.OVER_TARGET)
                headroom += device.load(now)
        return actions

    if actions or any(d.settling(now) for d in devices):
        return actions  # one step per cycle, on a settled measurement

    waiting = sorted(
        (d for d in devices if d.requested and not d.granted and d.off_long_enough(now)),
        key=lambda d: (-d.priority, d.id),
    )
    for device in waiting:
        need = device.nominal_power + hysteresis
        if headroom >= need:
            return [Action(device.id, True, Reason.HEADROOM)]
        lower = [d for d in running if d.priority < device.priority and d.on_long_enough(now)]
        if headroom + sum(d.load(now) for d in lower) >= need:
            for victim in _shed_order(lower, now):
                if headroom >= need:
                    break
                revoke(victim, Reason.PREEMPTED)
                headroom += victim.load(now)
            return actions
    return actions
