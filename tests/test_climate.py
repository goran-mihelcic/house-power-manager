"""The power-managed climate entity that stands in for a managed thermostat."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    setup_test_component_platform,
)

from .conftest import device, set_grid, setup_hpm

THERMOSTAT = "climate.floor_thermostat"
PROXY = "climate.floor_heating_power_managed"
FLOOR = device("floor", "Floor heating", THERMOSTAT, 2000, priority=5)


class Thermostat(ClimateEntity):
    """A floor heating thermostat without the optional turn on / turn off features."""

    _attr_name = "Floor thermostat"
    _attr_unique_id = "floor_thermostat"
    _attr_hvac_mode = HVACMode.OFF
    _attr_fan_mode = "auto"
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_current_temperature = 20.0
    _attr_target_temperature = 21.0
    _attr_min_temp = 5
    _attr_max_temp = 35

    def __init__(self) -> None:
        self._attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
        self._attr_fan_modes = ["auto", "low"]

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        self._attr_target_temperature = kwargs["temperature"]
        self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        self._attr_fan_mode = fan_mode
        self.async_write_ha_state()


@pytest.fixture
async def thermostat(hass: HomeAssistant) -> Thermostat:
    entity = Thermostat()
    setup_test_component_platform(hass, "climate", [entity])
    assert await async_setup_component(hass, "climate", {"climate": {"platform": "test"}})
    await hass.async_block_till_done()
    return entity


async def _setup(hass: HomeAssistant, grid: float = 1000):
    set_grid(hass, grid)
    return await setup_hpm(hass, [FLOOR])


async def _call(hass: HomeAssistant, service: str, entity_id: str = PROXY, **data: Any) -> None:
    await hass.services.async_call(
        "climate", service, {"entity_id": entity_id, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def _elapse(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _mode(hass: HomeAssistant, entity_id: str) -> str:
    return hass.states.get(entity_id).state


async def test_mirrors_the_thermostat(hass: HomeAssistant, thermostat: Thermostat) -> None:
    await _setup(hass)
    state = hass.states.get(PROXY)
    assert state.state == "off"
    assert state.attributes["current_temperature"] == 20
    assert state.attributes["temperature"] == 21
    assert state.attributes["hvac_modes"] == ["off", "heat", "cool"]
    assert state.attributes["fan_modes"] == ["auto", "low"]
    assert state.attributes["min_temp"] == 5
    assert state.attributes["source"] == THERMOSTAT
    features = ClimateEntityFeature(state.attributes["supported_features"])
    assert features & ClimateEntityFeature.TARGET_TEMPERATURE
    assert features & ClimateEntityFeature.TURN_ON


async def test_mode_is_a_request(hass: HomeAssistant, thermostat: Thermostat) -> None:
    await _setup(hass)
    await _call(hass, "set_hvac_mode", hvac_mode="heat")
    assert _mode(hass, THERMOSTAT) == "heat"  # switched with set_hvac_mode
    assert _mode(hass, PROXY) == "heat"
    assert hass.states.get("switch.floor_heating_requested").state == "on"

    await _call(hass, "set_hvac_mode", hvac_mode="off")
    assert _mode(hass, THERMOSTAT) == "off"
    assert _mode(hass, PROXY) == "off"
    assert hass.states.get("switch.floor_heating_requested").state == "off"


async def test_shed_device_still_shows_the_mode_asked_for(
    hass: HomeAssistant, thermostat: Thermostat, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, grid=6000)  # headroom 1200 W < 2000 + 200 W
    await _call(hass, "set_temperature", temperature=23)
    await _call(hass, "set_hvac_mode", hvac_mode="heat")
    assert _mode(hass, THERMOSTAT) == "off"
    assert thermostat.target_temperature == 21  # not touched while off
    proxy = hass.states.get(PROXY)
    assert proxy.state == "heat"
    assert proxy.attributes["temperature"] == 23
    assert proxy.attributes["power_state"] == "shed"

    # Room again: switched on in the mode, with the setpoint, asked for
    await _elapse(hass, freezer, 2)
    set_grid(hass, 3000)
    await hass.async_block_till_done()
    assert _mode(hass, THERMOSTAT) == "heat"
    assert thermostat.target_temperature == 23
    assert hass.states.get(PROXY).attributes["power_state"] == "running"


async def test_shedding_a_running_device_doesnt_change_the_stand_in(
    hass: HomeAssistant, thermostat: Thermostat, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass)
    await _call(hass, "set_hvac_mode", hvac_mode="heat")
    await _elapse(hass, freezer, 11)
    changed = hass.states.get(PROXY).last_changed

    set_grid(hass, 7700)
    await hass.async_block_till_done()
    assert _mode(hass, THERMOSTAT) == "off"
    proxy = hass.states.get(PROXY)
    assert proxy.state == "heat"
    assert proxy.last_changed == changed  # Room Climate sees nothing to correct


async def test_setpoint_and_fan_go_to_a_running_thermostat(
    hass: HomeAssistant, thermostat: Thermostat
) -> None:
    await _setup(hass)
    await _call(hass, "set_hvac_mode", hvac_mode="heat")
    await _call(hass, "set_temperature", temperature=22.5)
    await _call(hass, "set_fan_mode", fan_mode="low")
    assert thermostat.target_temperature == 22.5
    assert thermostat.fan_mode == "low"
    assert hass.states.get(PROXY).attributes["fan_mode"] == "low"


async def test_changes_on_the_thermostat_itself_are_followed(
    hass: HomeAssistant, thermostat: Thermostat, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass)
    await _call(hass, "set_hvac_mode", hvac_mode="heat")
    await _elapse(hass, freezer, 11)

    await _call(hass, "set_hvac_mode", THERMOSTAT, hvac_mode="cool")
    await _call(hass, "set_temperature", THERMOSTAT, temperature=24)
    await _elapse(hass, freezer, 6)
    proxy = hass.states.get(PROXY)
    assert proxy.state == "cool"
    assert proxy.attributes["temperature"] == 24
    assert _mode(hass, THERMOSTAT) == "cool"  # not put back


async def test_requested_switch_uses_the_mode_too(
    hass: HomeAssistant, thermostat: Thermostat
) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.floor_heating_requested"}, blocking=True
    )
    await hass.async_block_till_done()
    assert _mode(hass, THERMOSTAT) == "heat"
    assert _mode(hass, PROXY) == "heat"


async def test_straight_through_while_power_management_is_off(
    hass: HomeAssistant, thermostat: Thermostat
) -> None:
    await _setup(hass, grid=7000)  # no headroom
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.hpm_enabled"}, blocking=True
    )
    await _call(hass, "set_hvac_mode", hvac_mode="heat")
    assert _mode(hass, THERMOSTAT) == "heat"
    await _call(hass, "set_temperature", temperature=19)
    assert thermostat.target_temperature == 19
    await _call(hass, "turn_off")
    assert _mode(hass, THERMOSTAT) == "off"
