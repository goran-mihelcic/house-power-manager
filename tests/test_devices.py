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
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.house_power_manager.const import (
    CONF_CONTROL_ENTITY,
    CONF_GRID_SENSOR,
    CONF_LATENCY,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_NOMINAL_POWER,
    CONF_PRIORITY,
    CONF_SUPPLY,
    DEFAULTS,
    DOMAIN,
    EVENT_DEVICE_RESTORED,
    EVENT_DEVICE_SHED,
    SUBENTRY_DEVICE,
    SUBENTRY_SUPPLY,
)

from .conftest import (
    ENTRY_ID,
    GRID,
    STORAGE_KEY,
    device,
    member,
    set_grid,
    setup_booleans,
    setup_hpm,
    supply,
)

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
}
NEW_POWER = {
    CONF_NOMINAL_POWER: 1000,
    CONF_MIN_ON_TIME: 0,
    CONF_MIN_OFF_TIME: 0,
    CONF_LATENCY: 10,
}


async def _start_flow(hass: HomeAssistant, entry, subentry_type: str = SUBENTRY_DEVICE) -> dict:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, subentry_type), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return result


async def test_add_device(hass: HomeAssistant) -> None:
    entry = await _setup(hass, FLOOR)
    result = await _start_flow(hass, entry)
    assert CONF_SUPPLY not in result["data_schema"].schema  # no supply exists yet
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], NEW_DEVICE)
    assert result["step_id"] == "power"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], NEW_POWER)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert _state(hass, "sensor.pool_pump_state") == "idle"
    assert _state(hass, "switch.floor_heating_requested") == "off"


async def test_add_device_validation(hass: HomeAssistant) -> None:
    entry = await _setup(hass, FLOOR)
    result = await _start_flow(hass, entry)
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
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**NEW_DEVICE, CONF_NAME: "Floor heating", CONF_CONTROL_ENTITY: "input_boolean.floor"},
    )
    assert result["step_id"] == "reconfigure_power"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], NEW_POWER)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries["floor"].data[CONF_NOMINAL_POWER] == 1000
    assert hass.states.get("sensor.floor_heating_state").attributes["nominal_power"] == 1000


async def test_add_supply_then_a_device_on_it(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.outdoor_power", "0", {"unit_of_measurement": "W"})
    entry = await _setup(hass, FLOOR)
    result = await _start_flow(hass, entry, SUBENTRY_SUPPLY)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Outdoor unit", **NEW_POWER, "power_sensor": "sensor.outdoor_power"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    outdoor = next(s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_SUPPLY)
    assert _state(hass, "sensor.outdoor_unit_state") == "idle"

    # The device form now offers the supply; with it chosen there is no power step
    result = await _start_flow(hass, entry)
    supply_field = result["data_schema"].schema[CONF_SUPPLY]
    assert supply_field.config["options"] == [
        {"value": outdoor.subentry_id, "label": "Outdoor unit"}
    ]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**NEW_DEVICE, CONF_SUPPLY: outdoor.subentry_id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    pool = next(s for s in entry.subentries.values() if s.title == "Pool pump")
    assert pool.data[CONF_SUPPLY] == outdoor.subentry_id
    assert CONF_NOMINAL_POWER not in pool.data
    attrs = hass.states.get("sensor.pool_pump_state").attributes
    assert attrs["power_supply"] == "Outdoor unit"
    assert hass.states.get("sensor.pool_pump_granted_power") is None


async def test_supply_validation(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.outdoor_energy", "0", {"unit_of_measurement": "kWh"})
    entry = await _setup(hass, FLOOR)
    result = await _start_flow(hass, entry, SUBENTRY_SUPPLY)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NAME: "Floor Heating", **NEW_POWER, "power_sensor": "sensor.outdoor_energy"},
    )
    assert result["errors"] == {CONF_NAME: "name_in_use", "power_sensor": "unsupported_unit"}


# ---- Shared power supply -------------------------------------------------

OUTDOOR = supply("outdoor", "Outdoor unit", 2500)
BEDROOM_AC = member("bedroom_ac", "Bedroom AC", "input_boolean.bedroom_ac", "outdoor", 2)
LIVING_AC = member("living_ac", "Living AC", "input_boolean.living_ac", "outdoor", 3)


async def _setup_acs(
    hass: HomeAssistant, grid: float = 1000, outdoor: dict | None = None, **options: Any
):
    await setup_booleans(hass, "floor", "bedroom_ac", "living_ac")
    set_grid(hass, grid)
    devices = [FLOOR, outdoor or OUTDOOR, BEDROOM_AC, LIVING_AC]
    return await setup_hpm(hass, devices, **options)


async def _run_both_acs(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await _switch(hass, "switch.bedroom_ac_requested", True)
    await _switch(hass, "switch.living_ac_requested", True)
    await _elapse(hass, freezer, 11)
    assert _state(hass, "input_boolean.bedroom_ac") == "on"
    assert _state(hass, "input_boolean.living_ac") == "on"


async def test_supply_entities(hass: HomeAssistant) -> None:
    await _setup_acs(hass)
    assert _state(hass, "binary_sensor.outdoor_unit_granted") == "off"
    assert _state(hass, "sensor.outdoor_unit_state") == "idle"
    assert float(_state(hass, "sensor.outdoor_unit_granted_power")) == 0
    attrs = hass.states.get("sensor.outdoor_unit_state").attributes
    assert attrs["devices"] == ["Bedroom AC", "Living AC"]
    assert attrs["nominal_power"] == 2500
    # Its devices have no power of their own and no requested switch on the supply
    assert hass.states.get("sensor.bedroom_ac_granted_power") is None
    assert hass.states.get("switch.outdoor_unit_requested") is None


async def test_first_request_needs_room_for_the_whole_supply(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup_acs(hass, grid=5000)  # headroom 2200 W < 2500 + 200 W
    await _switch(hass, "switch.bedroom_ac_requested", True)
    assert _state(hass, "input_boolean.bedroom_ac") == "off"
    assert _state(hass, "sensor.bedroom_ac_state") == "shed"
    assert _state(hass, "sensor.outdoor_unit_state") == "shed"

    await _elapse(hass, freezer, 2)
    set_grid(hass, 4000)
    await hass.async_block_till_done()
    assert _state(hass, "input_boolean.bedroom_ac") == "on"
    assert _state(hass, "sensor.outdoor_unit_state") == "running"
    assert float(_state(hass, "sensor.outdoor_unit_granted_power")) == 2500


async def test_more_devices_join_a_running_supply(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup_acs(hass)
    await _switch(hass, "switch.bedroom_ac_requested", True)
    await _elapse(hass, freezer, 11)
    set_grid(hass, 6900)  # little headroom left, but the outdoor unit already runs
    await hass.async_block_till_done()
    await _switch(hass, "switch.living_ac_requested", True)
    assert _state(hass, "input_boolean.living_ac") == "on"


async def test_shed_switches_off_all_and_restores_those_still_requested(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup_acs(hass)
    shed = async_capture_events(hass, EVENT_DEVICE_SHED)
    await _run_both_acs(hass, freezer)

    set_grid(hass, 7400)
    await hass.async_block_till_done()
    assert _state(hass, "input_boolean.bedroom_ac") == "off"
    assert _state(hass, "input_boolean.living_ac") == "off"
    assert _state(hass, "sensor.outdoor_unit_state") == "shed"
    assert _state(hass, "sensor.bedroom_ac_state") == "shed"
    assert shed[0].data["device"] == "Outdoor unit"
    assert shed[0].data["devices"] == ["Bedroom AC", "Living AC"]

    # The living room no longer wants it while the supply is off
    await _switch(hass, "switch.living_ac_requested", False)
    await _elapse(hass, freezer, 11)
    set_grid(hass, 3000)
    await hass.async_block_till_done()
    assert _state(hass, "input_boolean.bedroom_ac") == "on"
    assert _state(hass, "input_boolean.living_ac") == "off"


async def test_supply_is_as_important_as_its_most_important_request(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    # Floor heating priority 5; living AC 3, bedroom AC 2
    await _setup_acs(hass)
    await _switch(hass, "switch.floor_heating_requested", True)
    await _elapse(hass, freezer, 11)
    await _run_both_acs(hass, freezer)
    set_grid(hass, 7400)
    await hass.async_block_till_done()
    assert _state(hass, "input_boolean.floor") == "on"
    assert _state(hass, "input_boolean.living_ac") == "off"


async def test_last_device_released_waits_for_the_supply_min_on_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    outdoor = supply("outdoor", "Outdoor unit", 2500, min_on_time=120)
    await _setup_acs(hass, outdoor=outdoor)
    await _run_both_acs(hass, freezer)

    await _switch(hass, "switch.living_ac_requested", False)
    assert _state(hass, "input_boolean.living_ac") == "off"  # the other still runs it
    await _switch(hass, "switch.bedroom_ac_requested", False)
    assert _state(hass, "input_boolean.bedroom_ac") == "on"  # protects the compressor
    await _elapse(hass, freezer, 120)
    assert _state(hass, "input_boolean.bedroom_ac") == "off"
    assert _state(hass, "sensor.outdoor_unit_state") == "idle"


async def test_supply_power_meter(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.states.async_set("sensor.outdoor_power", "1.8", {"unit_of_measurement": "kW"})
    await _setup_acs(
        hass, outdoor=supply("outdoor", "Outdoor unit", 2500, power_sensor="sensor.outdoor_power")
    )
    await _run_both_acs(hass, freezer)
    assert float(_state(hass, "sensor.outdoor_unit_granted_power")) == 1800
    assert float(_state(hass, "sensor.hpm_managed_load")) == 1800


async def test_device_switched_on_by_hand_runs_its_supply(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    await _setup_acs(hass)
    await _by_hand(hass, "input_boolean.bedroom_ac", True)
    await _elapse(hass, freezer, 6)
    assert _state(hass, "input_boolean.bedroom_ac") == "on"
    assert _state(hass, "sensor.outdoor_unit_state") == "running"
    assert hass.states.get("sensor.outdoor_unit_state").attributes["reason"] == "manual"


async def test_restart_restores_the_supply(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "data": {
            "enabled": True,
            "devices": {
                "bedroom_ac": {"requested": True, "granted": True, "managed": True},
                "living_ac": {"requested": False, "granted": False, "managed": True},
            },
            "supplies": {"outdoor": {"granted": True, "reason": "headroom"}},
        },
    }
    await _setup_acs(hass)
    assert _state(hass, "input_boolean.bedroom_ac") == "on"
    assert _state(hass, "input_boolean.living_ac") == "off"
    assert _state(hass, "sensor.outdoor_unit_state") == "running"


async def test_supply_names_from_0_3_are_migrated(hass: HomeAssistant) -> None:
    await setup_booleans(hass, "bedroom_ac", "living_ac")
    set_grid(hass, 1000)
    old = [
        device(
            "bedroom_ac",
            "Bedroom AC",
            "input_boolean.bedroom_ac",
            800,
            priority=2,
            supply_group="Outdoor unit",
            min_on_time=60,
        ),
        device(
            "living_ac",
            "Living AC",
            "input_boolean.living_ac",
            1200,
            priority=3,
            supply_group="outdoor unit ",
        ),
        FLOOR,
    ]
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        options={**DEFAULTS, CONF_GRID_SENSOR: GRID},
        subentries_data=old,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 2
    supplies = [s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_SUPPLY]
    assert [s.title for s in supplies] == ["Outdoor unit"]
    assert supplies[0].data[CONF_NOMINAL_POWER] == 2000
    assert supplies[0].data[CONF_MIN_ON_TIME] == 60
    for device_id in ("bedroom_ac", "living_ac"):
        data = entry.subentries[device_id].data
        assert data[CONF_SUPPLY] == supplies[0].subentry_id
        assert "supply_group" not in data
    assert CONF_SUPPLY not in entry.subentries["floor"].data
    assert _state(hass, "sensor.outdoor_unit_state") == "idle"


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
    await _run_both_acs(hass, freezer)

    set_grid(hass, 7300)  # over target, under the warning level (7400 W)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 1
    assert calls[0].data["message"] == (
        "Switched off Outdoor unit (Bedroom AC, Living AC): "
        "grid import 7300 W is over the 7200 W target."
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
