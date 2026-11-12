"""Managed devices: requests, grants, control entities, persistence, services and flows."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.house_power_manager.const import (
    CONF_CONTROL_ENTITY,
    CONF_LATENCY,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_NOMINAL_POWER,
    CONF_PRIORITY,
    DOMAIN,
    EVENT_DEVICE_RESTORED,
    EVENT_DEVICE_SHED,
    SUBENTRY_DEVICE,
)

from .conftest import STORAGE_KEY, device, set_grid, setup_booleans, setup_hpm

FLOOR = device("floor", "Floor heating", "input_boolean.floor", 2000, priority=5)
POOL = device("pool", "Pool pump", "input_boolean.pool", 1000, priority=1)


def _state(hass: HomeAssistant, entity_id: str) -> str:
    return hass.states.get(entity_id).state


async def _elapse(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _switch(hass: HomeAssistant, entity_id: str, on: bool) -> None:
    await hass.services.async_call(
        "switch", "turn_on" if on else "turn_off", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, *devices: dict[str, Any], grid: float = 1000):
    await setup_booleans(hass, "floor", "pool")
    set_grid(hass, grid)
    return await setup_hpm(hass, list(devices))


async def test_device_entities(hass: HomeAssistant) -> None:
    await _setup(hass, FLOOR)
    assert _state(hass, "switch.floor_heating_requested") == "off"
    assert _state(hass, "switch.floor_heating_managed") == "on"
    assert _state(hass, "binary_sensor.floor_heating_granted") == "off"
    assert _state(hass, "sensor.floor_heating_granted_power") == "0.0"
    assert _state(hass, "sensor.floor_heating_state") == "idle"
    attrs = hass.states.get("sensor.floor_heating_state").attributes
    assert attrs["priority"] == 5
    assert attrs["control_entity"] == "input_boolean.floor"


async def test_power_without_sensor_is_declared_power_while_on(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, FLOOR)
    await _switch(hass, "switch.floor_heating_requested", True)
    assert float(_state(hass, "sensor.floor_heating_granted_power")) == 2000

    # Left alone and running: still 2000 W, not 0; not part of the managed load
    await _switch(hass, "switch.floor_heating_managed", False)
    await _elapse(hass, freezer, 6)
    assert float(_state(hass, "sensor.floor_heating_granted_power")) == 2000
    assert float(_state(hass, "sensor.hpm_managed_load")) == 0

    await _by_hand(hass, "input_boolean.floor", False)
    await _elapse(hass, freezer, 6)
    assert float(_state(hass, "sensor.floor_heating_granted_power")) == 0


async def test_power_sensor_is_preferred(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.floor_power", "1.5", {"unit_of_measurement": "kW"})
    await _setup(hass, {**FLOOR, "data": {**FLOOR["data"], "power_sensor": "sensor.floor_power"}})
    await _switch(hass, "switch.floor_heating_requested", True)
    assert float(_state(hass, "sensor.floor_heating_granted_power")) == 1500
    assert float(_state(hass, "sensor.hpm_managed_load")) == 1500


async def test_device_already_on_when_added_is_followed(hass: HomeAssistant) -> None:
    await setup_booleans(hass, "floor", on=("floor",))
    set_grid(hass, 3000)
    await setup_hpm(hass, [FLOOR])
    assert _state(hass, "input_boolean.floor") == "on"
    assert _state(hass, "switch.floor_heating_requested") == "on"
    assert _state(hass, "sensor.floor_heating_state") == "running"
    assert float(_state(hass, "sensor.floor_heating_granted_power")) == 2000


async def test_request_is_granted_and_switches_the_device(hass: HomeAssistant) -> None:
    await _setup(hass, FLOOR)
    restored = async_capture_events(hass, EVENT_DEVICE_RESTORED)
    await _switch(hass, "switch.floor_heating_requested", True)

    assert _state(hass, "input_boolean.floor") == "on"
    assert _state(hass, "binary_sensor.floor_heating_granted") == "on"
    assert _state(hass, "sensor.floor_heating_state") == "running"
    assert float(_state(hass, "sensor.floor_heating_granted_power")) == 2000
    assert float(_state(hass, "sensor.hpm_managed_load")) == 2000
    assert [e.data["reason"] for e in restored] == ["headroom"]
    assert restored[0].data["device"] == "Floor heating"


async def test_no_headroom_no_grant(hass: HomeAssistant) -> None:
    await _setup(hass, FLOOR, grid=6000)  # headroom 1200 W < 2000 + 200
    await _switch(hass, "switch.floor_heating_requested", True)
    assert _state(hass, "input_boolean.floor") == "off"
    assert _state(hass, "sensor.floor_heating_state") == "shed"


async def test_over_target_sheds_least_important(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, FLOOR, POOL)
    shed = async_capture_events(hass, EVENT_DEVICE_SHED)
    await _switch(hass, "switch.floor_heating_requested", True)
    await _switch(hass, "switch.pool_pump_requested", True)
    assert _state(hass, "input_boolean.pool") == "off"  # waits for the floor heating to settle
    await _elapse(hass, freezer, 11)
    assert _state(hass, "input_boolean.pool") == "on"

    await _elapse(hass, freezer, 11)
    set_grid(hass, 7500)  # 300 W over target
    await hass.async_block_till_done()
    assert _state(hass, "input_boolean.pool") == "off"
    assert _state(hass, "input_boolean.floor") == "on"
    assert _state(hass, "sensor.pool_pump_state") == "shed"
    assert [(e.data["device"], e.data["reason"]) for e in shed] == [("Pool pump", "over_target")]

    # The pool pump's 1000 W leaves the measurement: under target, nothing else is shed
    set_grid(hass, 6500)
    await _elapse(hass, freezer, 11)
    assert _state(hass, "input_boolean.floor") == "on"
    assert [e.data["device"] for e in shed] == ["Pool pump"]

    # Import drops: the pool pump comes back once there is room for it
    await _elapse(hass, freezer, 2)
    set_grid(hass, 5000)
    await hass.async_block_till_done()
    assert _state(hass, "input_boolean.pool") == "on"


async def test_release_switches_off(hass: HomeAssistant) -> None:
    await _setup(hass, FLOOR)
    await _switch(hass, "switch.floor_heating_requested", True)
    await _switch(hass, "switch.floor_heating_requested", False)
    assert _state(hass, "input_boolean.floor") == "off"
    assert _state(hass, "sensor.floor_heating_state") == "idle"


async def test_min_on_time_delays_release(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    ac = device("ac", "AC", "input_boolean.floor", 1000, **{CONF_MIN_ON_TIME: 60})
    await _setup(hass, ac)
    await _switch(hass, "switch.ac_requested", True)
    await _switch(hass, "switch.ac_requested", False)
    assert _state(hass, "input_boolean.floor") == "on"
    await _elapse(hass, freezer, 61)
    assert _state(hass, "input_boolean.floor") == "off"


async def _by_hand(hass: HomeAssistant, entity_id: str, on: bool) -> None:
    await hass.services.async_call(
        "input_boolean", "turn_on" if on else "turn_off", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()


async def test_switched_on_by_hand_is_followed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, FLOOR)
    await _by_hand(hass, "input_boolean.floor", True)
    await _elapse(hass, freezer, 6)
    assert _state(hass, "input_boolean.floor") == "on"
    assert _state(hass, "switch.floor_heating_requested") == "on"
    assert _state(hass, "sensor.floor_heating_state") == "running"
    assert hass.states.get("sensor.floor_heating_state").attributes["reason"] == "manual"


async def test_switched_off_by_hand_is_followed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, FLOOR)
    await _switch(hass, "switch.floor_heating_requested", True)
    await _elapse(hass, freezer, 11)
    await _by_hand(hass, "input_boolean.floor", False)
    await _elapse(hass, freezer, 6)
    assert _state(hass, "input_boolean.floor") == "off"
    assert _state(hass, "switch.floor_heating_requested") == "off"
    assert _state(hass, "sensor.floor_heating_state") == "idle"


async def test_switched_on_by_hand_is_still_shed_over_target(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, FLOOR, grid=6000)  # not enough room for the floor heating
    shed = async_capture_events(hass, EVENT_DEVICE_SHED)
    await _by_hand(hass, "input_boolean.floor", True)
    assert _state(hass, "input_boolean.floor") == "on"  # followed while its power arrives

    await _elapse(hass, freezer, 11)
    set_grid(hass, 8000)  # its 2000 W is in the measurement: over the limit
    await hass.async_block_till_done()
    assert _state(hass, "input_boolean.floor") == "off"
    assert [e.data["reason"] for e in shed] == ["emergency"]
    # Still requested: switched on again once there is room
    assert _state(hass, "sensor.floor_heating_state") == "shed"
    await _elapse(hass, freezer, 11)
    set_grid(hass, 3000)
    await hass.async_block_till_done()
    assert _state(hass, "input_boolean.floor") == "on"


async def test_late_report_of_own_command_is_not_manual(hass: HomeAssistant) -> None:
    slow = device("ac", "AC", "input_boolean.floor", 1000, **{CONF_LATENCY: 30})
    await _setup(hass, slow)
    await _switch(hass, "switch.ac_requested", True)
    assert _state(hass, "input_boolean.floor") == "on"
    # A cloud device briefly reports its old state before settling
    hass.states.async_set("input_boolean.floor", "off")
    await hass.async_block_till_done()
    assert _state(hass, "switch.ac_requested") == "on"
    assert _state(hass, "binary_sensor.ac_granted") == "on"


async def test_unmanaged_device_is_left_alone(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, FLOOR)
    await _switch(hass, "switch.floor_heating_managed", False)
    assert _state(hass, "sensor.floor_heating_state") == "override"
    await _by_hand(hass, "input_boolean.floor", True)
    await _elapse(hass, freezer, 6)
    assert _state(hass, "input_boolean.floor") == "on"
    assert _state(hass, "switch.floor_heating_requested") == "off"

    # Managed again: its current state is taken over
    await _switch(hass, "switch.floor_heating_managed", True)
    assert _state(hass, "input_boolean.floor") == "on"
    assert _state(hass, "switch.floor_heating_requested") == "on"
    assert _state(hass, "sensor.floor_heating_state") == "running"


async def test_disabled_leaves_devices_alone(hass: HomeAssistant) -> None:
    await _setup(hass, FLOOR)
    await _switch(hass, "switch.hpm_enabled", False)
    await _switch(hass, "switch.floor_heating_requested", True)
    assert _state(hass, "input_boolean.floor") == "off"
    await _switch(hass, "switch.hpm_enabled", True)
    assert _state(hass, "input_boolean.floor") == "on"


async def test_restart_restores_requests_and_grants(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "data": {
            "enabled": True,
            "devices": {
                "floor": {"requested": True, "granted": True, "managed": True},
                "pool": {"requested": True, "granted": False, "managed": True},
            },
        },
    }
    await _setup(hass, FLOOR, POOL, grid=5000)  # room for the floor heating only
    assert _state(hass, "input_boolean.floor") == "on"
    assert _state(hass, "switch.floor_heating_requested") == "on"
    assert _state(hass, "switch.pool_pump_requested") == "on"
    assert _state(hass, "sensor.pool_pump_state") == "shed"


async def test_state_is_saved(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    entry = await _setup(hass, FLOOR)
    await _switch(hass, "switch.floor_heating_requested", True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    stored = hass_storage[STORAGE_KEY]["data"]["devices"]["floor"]
    assert stored["requested"] is True
    assert stored["granted"] is True
    assert stored["reason"] == "headroom"


# ---- Services -----------------------------------------------------------


async def test_request_and_release_services(hass: HomeAssistant) -> None:
    entry = await _setup(hass, FLOOR)
    await hass.services.async_call(
        DOMAIN, "request_power", {"device": "floor heating"}, blocking=True
    )
    await hass.async_block_till_done()
    assert _state(hass, "input_boolean.floor") == "on"

    coordinator = entry.runtime_data
    device_id = coordinator.registry_device_id(coordinator.devices["floor"])
    await hass.services.async_call(DOMAIN, "release_power", {"device": device_id}, blocking=True)
    await hass.async_block_till_done()
    assert _state(hass, "input_boolean.floor") == "off"


async def test_unknown_device(hass: HomeAssistant) -> None:
    await _setup(hass, FLOOR)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "request_power", {"device": "sauna"}, blocking=True)


async def test_set_priority_applies_without_reload(hass: HomeAssistant) -> None:
    entry = await _setup(hass, FLOOR)
    coordinator = entry.runtime_data
    await hass.services.async_call(
        DOMAIN, "set_priority", {"device": "Floor heating", "priority": 80}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.subentries["floor"].data[CONF_PRIORITY] == 80
    assert entry.runtime_data is coordinator
    assert hass.states.get("sensor.floor_heating_state").attributes["priority"] == 80


async def test_rebalance(hass: HomeAssistant) -> None:
    await _setup(hass, FLOOR)
    await hass.services.async_call(DOMAIN, "rebalance", {}, blocking=True)


# ---- Subentry flows -----------------------------------------------------

NEW_DEVICE = {
    CONF_NAME: "Pool pump",
    CONF_PRIORITY: 1,
    CONF_CONTROL_ENTITY: "input_boolean.pool",
    CONF_NOMINAL_POWER: 1000,
    CONF_MIN_ON_TIME: 0,
    CONF_MIN_OFF_TIME: 0,
    CONF_LATENCY: 10,
}


async def test_add_device(hass: HomeAssistant) -> None:
    entry = await _setup(hass, FLOOR)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_DEVICE), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], NEW_DEVICE)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert _state(hass, "sensor.pool_pump_state") == "idle"
    assert _state(hass, "switch.floor_heating_requested") == "off"


async def test_add_device_validation(hass: HomeAssistant) -> None:
    entry = await _setup(hass, FLOOR)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_DEVICE), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**NEW_DEVICE, CONF_NAME: "floor heating", CONF_CONTROL_ENTITY: "input_boolean.floor"},
    )
    assert result["errors"] == {CONF_NAME: "name_in_use", CONF_CONTROL_ENTITY: "control_in_use"}


async def test_reconfigure_device(hass: HomeAssistant) -> None:
    entry = await _setup(hass, FLOOR)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_DEVICE),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": "floor"},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**NEW_DEVICE, CONF_NAME: "Floor heating", CONF_CONTROL_ENTITY: "input_boolean.floor"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries["floor"].data[CONF_NOMINAL_POWER] == 1000
    assert hass.states.get("sensor.floor_heating_state").attributes["nominal_power"] == 1000


# ---- Shared power supply -------------------------------------------------

BEDROOM_AC = device(
    "bedroom_ac",
    "Bedroom AC",
    "input_boolean.bedroom_ac",
    800,
    priority=2,
    supply_group="Outdoor unit",
)
LIVING_AC = device(
    "living_ac",
    "Living AC",
    "input_boolean.living_ac",
    1200,
    priority=2,
    supply_group="outdoor unit ",
)


async def _setup_acs(hass: HomeAssistant, grid: float = 1000, **options: Any):
    await setup_booleans(hass, "floor", "bedroom_ac", "living_ac")
    set_grid(hass, grid)
    return await setup_hpm(hass, [FLOOR, BEDROOM_AC, LIVING_AC], **options)


async def test_shared_supply_is_switched_off_together(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup_acs(hass)
    for name in ("floor_heating", "bedroom_ac", "living_ac"):
        await _switch(hass, f"switch.{name}_requested", True)
        await _elapse(hass, freezer, 11)
    assert _state(hass, "input_boolean.bedroom_ac") == "on"
    assert _state(hass, "input_boolean.living_ac") == "on"
    attrs = hass.states.get("sensor.bedroom_ac_state").attributes
    assert attrs["shares_power_with"] == ["Living AC"]

    set_grid(hass, 7400)  # 200 W over target: shedding one indoor unit would do
    await hass.async_block_till_done()
    assert _state(hass, "input_boolean.bedroom_ac") == "off"
    assert _state(hass, "input_boolean.living_ac") == "off"
    assert _state(hass, "input_boolean.floor") == "on"


async def test_supply_field_offers_names_in_use(hass: HomeAssistant) -> None:
    entry = await _setup_acs(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_DEVICE), context={"source": SOURCE_USER}
    )
    schema = result["data_schema"].schema
    supply = next(key for key in schema if key == "supply_group")
    # "Outdoor unit" and "outdoor unit " are the same supply: offered once
    assert schema[supply].config["options"] == ["Outdoor unit"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**NEW_DEVICE, "supply_group": "  "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    pool = next(s for s in entry.subentries.values() if s.title == "Pool pump")
    assert "supply_group" not in pool.data


# ---- Notifications --------------------------------------------------------

NOTIFY = {
    "notifications": {
        "notify_targets": ["phone"],
        "notify_shed": True,
        "notify_limit": True,
        "warning_margin": 200.0,
    }
}


async def test_notification_when_devices_are_shed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls = async_mock_service(hass, "notify", "phone")
    await _setup_acs(hass, **NOTIFY)
    await _switch(hass, "switch.bedroom_ac_requested", True)
    await _elapse(hass, freezer, 11)
    await _switch(hass, "switch.living_ac_requested", True)
    await _elapse(hass, freezer, 11)

    set_grid(hass, 7300)  # over target, under the warning level (7400 W)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 1
    assert calls[0].data["message"] == (
        "Switched off Bedroom AC, Living AC: grid import 7300 W is over the 7200 W target."
    )


async def test_no_notification_for_release_or_when_turned_off(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "notify", "phone")
    notify = {"notifications": {**NOTIFY["notifications"], "notify_shed": False}}
    await _setup_acs(hass, **notify)
    await _switch(hass, "switch.floor_heating_requested", True)
    await _switch(hass, "switch.floor_heating_requested", False)
    set_grid(hass, 7300)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == []


async def test_limit_warning_once_until_back_under_target(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls = async_mock_service(hass, "notify", "phone")
    await _setup_acs(hass, **NOTIFY)
    for value in (7300, 7450, 7500, 7300):  # warning level 7400 W
        await _elapse(hass, freezer, 2)
        set_grid(hass, value)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert [c.data["message"] for c in calls] == [
        "Grid import 7450 W is close to the 7600 W limit."
    ]

    for value in (7000, 7450):  # back under target, then close again
        await _elapse(hass, freezer, 2)
        set_grid(hass, value)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 2
