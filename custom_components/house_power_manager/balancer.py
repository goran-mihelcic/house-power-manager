"""Balancing decisions, free of Home Assistant so they can be unit tested.

So far this covers the measurement: normalising the grid sensor and classifying
import against the limit. Shedding and restoring devices builds on it.
"""

from __future__ import annotations

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
