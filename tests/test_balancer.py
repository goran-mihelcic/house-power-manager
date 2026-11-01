"""Measurement logic, without Home Assistant."""

import pytest

from custom_components.house_power_manager.balancer import (
    GridSnapshot,
    Status,
    normalize_import,
    to_watts,
)


@pytest.mark.parametrize(
    ("value", "unit", "watts"), [(1500, "W", 1500), (1.5, "kW", 1500), (0.002, "MW", 2000)]
)
def test_to_watts(value: float, unit: str, watts: float) -> None:
    assert to_watts(value, unit) == pytest.approx(watts)


@pytest.mark.parametrize("unit", [None, "", "VA", "kWh", "w"])
def test_unknown_unit_is_refused(unit: str | None) -> None:
    with pytest.raises(ValueError):
        to_watts(100, unit)


def test_sign_convention() -> None:
    assert normalize_import(2.0, "kW", import_positive=True) == 2000
    assert normalize_import(2.0, "kW", import_positive=False) == -2000
    # Exporting 1 kW on a sensor that reports import as negative reads +1000
    assert normalize_import(1000, "W", import_positive=False) == -1000


def test_headroom_counts_export() -> None:
    snap = GridSnapshot(grid_import=-2000, limit=7600, margin=400)
    assert snap.target == 7200
    assert snap.headroom == 9200
    assert snap.status is Status.NORMAL


@pytest.mark.parametrize(
    ("grid_import", "status"),
    [
        (7200, Status.NORMAL),  # exactly at target
        (7201, Status.LIMITING),
        (7600, Status.LIMITING),  # exactly at the limit
        (7601, Status.EMERGENCY),
    ],
)
def test_status_thresholds(grid_import: float, status: Status) -> None:
    assert GridSnapshot(grid_import=grid_import, limit=7600, margin=400).status is status


def test_fault_wins_and_hides_headroom() -> None:
    snap = GridSnapshot(grid_import=None, limit=7600, margin=400, fault="stale")
    assert snap.status is Status.SENSOR_FAULT
    assert snap.headroom is None


def test_no_reading_yet() -> None:
    snap = GridSnapshot(grid_import=None, limit=7600, margin=400)
    assert snap.status is None
    assert snap.headroom is None
