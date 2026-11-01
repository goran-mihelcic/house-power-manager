"""Measurement logic, without Home Assistant."""

import pytest

from custom_components.house_power_manager.balancer import (
    Action,
    DeviceInput,
    GridSnapshot,
    Reason,
    Status,
    decide,
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


# ---- Decisions ----------------------------------------------------------
# limit 7600 W, margin 400 W: target 7200 W

NOW = 10_000.0


def grid(grid_import: float | None, fault: str | None = None) -> GridSnapshot:
    return GridSnapshot(grid_import=grid_import, limit=7600, margin=400, fault=fault)


def dev(id: str, priority: int, power: float, **kwargs) -> DeviceInput:
    kwargs.setdefault("requested", True)
    kwargs.setdefault("granted", False)
    kwargs.setdefault("measured_power", None)
    return DeviceInput(id=id, priority=priority, nominal_power=power, **kwargs)


def on(id: str, priority: int, power: float, **kwargs) -> DeviceInput:
    return dev(id, priority, power, granted=True, **kwargs)


def changes(actions: list[Action]) -> list[tuple[str, bool, Reason]]:
    return [(a.device_id, a.grant, a.reason) for a in actions]


def test_balanced_does_nothing() -> None:
    assert decide(grid(5000), [on("a", 1, 2000), on("b", 5, 1000)], NOW, 200) == []


def test_over_target_sheds_least_important_first() -> None:
    devices = [on("pool", 5, 1000), on("floor", 1, 2000), on("ac", 3, 1500)]
    assert changes(decide(grid(7400), devices, NOW)) == [("floor", False, Reason.OVER_TARGET)]


def test_sheds_only_as_much_as_needed() -> None:
    devices = [on("a", 1, 1000), on("b", 2, 1000), on("c", 3, 3000)]
    # 2500 W over target: a and b only free 2000 W, so c goes too
    assert [a.device_id for a in decide(grid(9700), devices, NOW)] == ["a", "b", "c"]
    # 1500 W over: a and b
    assert [a.device_id for a in decide(grid(8700), devices, NOW)] == ["a", "b"]


def test_equal_priority_sheds_largest_first() -> None:
    devices = [on("small", 1, 500), on("big", 1, 2000)]
    assert [a.device_id for a in decide(grid(7400), devices, NOW)] == ["big"]


def test_min_on_time_protects_until_emergency() -> None:
    compressor = on("ac", 1, 1500, min_on=300, changed_at=NOW - 60)
    devices = [compressor, on("floor", 2, 2000)]
    assert changes(decide(grid(7400), devices, NOW)) == [("floor", False, Reason.OVER_TARGET)]
    # Above the hard limit min_on_time no longer counts
    assert changes(decide(grid(7700), devices, NOW)) == [("ac", False, Reason.EMERGENCY)]


def test_measured_power_preferred_over_nominal() -> None:
    # The floor heating's thermostat is satisfied: shedding it would free nothing
    devices = [on("floor", 1, 2000, measured_power=0), on("pool", 2, 1000, measured_power=900)]
    assert [a.device_id for a in decide(grid(7400), devices, NOW)] == ["pool"]


def test_pending_shed_is_not_shed_again() -> None:
    # Shed 2 s ago, the measurement doesn't show it yet
    devices = [dev("floor", 1, 2000, changed_at=NOW - 2, latency=10), on("pool", 2, 1000)]
    assert decide(grid(7400), devices, NOW) == []


def test_pending_restore_counts_as_load() -> None:
    # Granted 2 s ago and not drawing yet: its 2000 W will push import over target
    devices = [on("ev", 1, 2000, changed_at=NOW - 2, latency=10, measured_power=0)]
    assert changes(decide(grid(6000), devices, NOW)) == [("ev", False, Reason.OVER_TARGET)]


def test_restore_needs_power_plus_hysteresis() -> None:
    # Headroom 3200 W
    assert changes(decide(grid(4000), [dev("a", 1, 3000)], NOW, 200)) == [
        ("a", True, Reason.HEADROOM)
    ]
    assert decide(grid(4000), [dev("a", 1, 3100)], NOW, 200) == []


def test_restores_one_device_per_cycle_most_important_first() -> None:
    devices = [dev("low", 1, 500), dev("high", 9, 500), dev("mid", 5, 500)]
    assert changes(decide(grid(1000), devices, NOW)) == [("high", True, Reason.HEADROOM)]


def test_no_restore_while_a_change_settles() -> None:
    devices = [on("a", 5, 500, changed_at=NOW - 2, latency=10), dev("b", 1, 500)]
    assert decide(grid(1000), devices, NOW) == []
    assert changes(decide(grid(1500), devices, NOW + 10)) == [("b", True, Reason.HEADROOM)]


def test_min_off_time() -> None:
    device = dev("ac", 1, 1000, min_off=300, changed_at=NOW - 60)
    assert decide(grid(1000), [device], NOW) == []
    assert changes(decide(grid(1000), [device], NOW + 240)) == [("ac", True, Reason.HEADROOM)]


def test_never_grants_without_request() -> None:
    assert decide(grid(0), [dev("a", 1, 500, requested=False)], NOW) == []


def test_release() -> None:
    assert changes(decide(grid(1000), [on("a", 1, 500, requested=False)], NOW)) == [
        ("a", False, Reason.RELEASED)
    ]


def test_release_waits_for_min_on_time() -> None:
    device = on("ac", 1, 500, requested=False, min_on=300, changed_at=NOW - 60)
    assert decide(grid(1000), [device], NOW) == []


def test_release_frees_room_without_shedding_others() -> None:
    devices = [on("done", 9, 2000, requested=False), on("floor", 1, 1000)]
    assert changes(decide(grid(7400), devices, NOW)) == [("done", False, Reason.RELEASED)]


def test_sensor_fault_holds_grants_but_honours_releases() -> None:
    devices = [on("a", 1, 500), on("b", 1, 500, requested=False), dev("c", 1, 500)]
    actions = decide(grid(None, fault="stale"), devices, NOW)
    assert changes(actions) == [("b", False, Reason.RELEASED)]


def test_pv_export_gives_headroom_beyond_the_limit() -> None:
    # Exporting 3 kW: 10.2 kW of headroom
    assert changes(decide(grid(-3000), [dev("ev", 1, 9000)], NOW, 200)) == [
        ("ev", True, Reason.HEADROOM)
    ]


def test_preempts_less_important_device() -> None:
    # Headroom 1200 W; floor heating (2500 W) outranks the running pool pump
    devices = [on("pool", 1, 2000), dev("floor", 9, 2500)]
    assert changes(decide(grid(6000), devices, NOW, 200)) == [("pool", False, Reason.PREEMPTED)]
    # Next cycle, once the pool pump's 2000 W is gone from the measurement
    devices = [dev("pool", 1, 2000), dev("floor", 9, 2500)]
    assert changes(decide(grid(4000), devices, NOW + 10, 200)) == [("floor", True, Reason.HEADROOM)]


def test_no_preemption_of_equal_or_more_important_devices() -> None:
    devices = [on("pool", 9, 2000), on("ac", 5, 2000), dev("floor", 5, 2500)]
    assert decide(grid(6000), devices, NOW, 200) == []


def test_no_preemption_when_it_would_not_be_enough() -> None:
    devices = [on("pool", 1, 500), dev("floor", 9, 5000)]
    assert decide(grid(6000), devices, NOW, 200) == []


def test_smaller_device_runs_while_a_bigger_one_cannot() -> None:
    devices = [dev("ev", 9, 7000), dev("pool", 3, 800)]
    assert changes(decide(grid(6000), devices, NOW, 200)) == [("pool", True, Reason.HEADROOM)]


def test_command_in_flight_counts_as_load() -> None:
    # Grant restored after a restart and just re-sent: its power isn't measured yet
    floor = on("floor", 5, 2000, changed_at=NOW - 3600, commanded_at=NOW, latency=10)
    devices = [floor, dev("pool", 1, 1000)]
    assert decide(grid(5000), devices, NOW, 200) == []


def test_device_switched_by_hand_is_not_shed_in_advance() -> None:
    # Switched on by hand 2 s ago, not measured yet: wait for the measurement
    floor = on("floor", 1, 2000, changed_at=NOW - 2, latency=10, followed=True)
    assert decide(grid(6000), [floor], NOW) == []
    # ...but other devices are not restored meanwhile
    assert decide(grid(1000), [floor, dev("pool", 9, 500)], NOW) == []
    # Once it shows and import is over target, it is balanced like any other device
    assert changes(decide(grid(8000), [floor], NOW + 10)) == [("floor", False, Reason.EMERGENCY)]
