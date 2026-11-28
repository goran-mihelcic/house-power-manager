"""The power-managed water heater that stands in for a managed water heater."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.water_heater import WaterHeaterEntity, WaterHeaterEntityFeature
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    setup_test_component_platform,
)

from .conftest import device, set_grid, setup_hpm

HEATER = "water_heater.hot_water"
PROXY = "water_heater.hot_water_tank_power_managed"
TANK = device("tank", "Hot water tank", HEATER, 3000, priority=4)


class HotWater(WaterHeaterEntity):
    """A heat pump's hot water: operation modes, no optional turn on / turn off."""

    _attr_name = "Hot water"
    _attr_unique_id = "hot_water"
    _attr_current_operation = "off"
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE | WaterHeaterEntityFeature.OPERATION_MODE
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_current_temperature = 42.0
    _attr_target_temperature = 48.0
    _attr_min_temp = 30
    _attr_max_temp = 65

    def __init__(self) -> None:
        self._attr_operation_list = ["off", "heat_pump", "eco"]

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        self._attr_current_operation = operation_mode
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        self._attr_target_temperature = kwargs["temperature"]
        self.async_write_ha_state()


@pytest.fixture
async def heater(hass: HomeAssistant) -> HotWater:
    entity = HotWater()
    setup_test_component_platform(hass, "water_heater", [entity])
    assert await async_setup_component(hass, "water_heater", {"water_heater": {"platform": "test"}})
    await hass.async_block_till_done()
    return entity


async def _setup(hass: HomeAssistant, grid: float = 1000):
    set_grid(hass, grid)
    return await setup_hpm(hass, [TANK])


async def _call(hass: HomeAssistant, service: str, entity_id: str = PROXY, **data: Any) -> None:
    await hass.services.async_call(
        "water_heater", service, {"entity_id": entity_id, **data}, blocking=True
    )
    await hass.async_block_till_done()


async def _elapse(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> str:
    return hass.states.get(entity_id).state


async def test_mirrors_the_water_heater(hass: HomeAssistant, heater: HotWater) -> None:
    await _setup(hass)
    state = hass.states.get(PROXY)
    assert state.state == "off"
    assert state.attributes["current_temperature"] == 42
    assert state.attributes["temperature"] == 48
    assert state.attributes["operation_list"] == ["off", "heat_pump", "eco"]
    assert state.attributes["source"] == HEATER
    assert hass.states.get("climate.hot_water_tank_power_managed") is None


async def test_operation_mode_is_a_request(hass: HomeAssistant, heater: HotWater) -> None:
    await _setup(hass)
    await _call(hass, "set_operation_mode", operation_mode="eco")
    assert _state(hass, HEATER) == "eco"
    assert _state(hass, PROXY) == "eco"
    assert _state(hass, "switch.hot_water_tank_requested") == "on"

    await _call(hass, "turn_off")
    assert _state(hass, HEATER) == "off"
    assert _state(hass, PROXY) == "off"


async def test_shed_water_heater_still_shows_the_mode_asked_for(
    hass: HomeAssistant, heater: HotWater, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, grid=5000)  # headroom 2200 W < 3000 + 200 W
    await _call(hass, "set_temperature", temperature=55)
    await _call(hass, "set_operation_mode", operation_mode="heat_pump")
    assert _state(hass, HEATER) == "off"
    proxy = hass.states.get(PROXY)
    assert proxy.state == "heat_pump"
    assert proxy.attributes["temperature"] == 55
    assert proxy.attributes["power_state"] == "shed"

    await _elapse(hass, freezer, 2)
    set_grid(hass, 2000)
    await hass.async_block_till_done()
    assert _state(hass, HEATER) == "heat_pump"
    assert heater.target_temperature == 55


async def test_turn_on_uses_a_heating_mode(hass: HomeAssistant, heater: HotWater) -> None:
    await _setup(hass)
    await _call(hass, "turn_on")
    assert _state(hass, HEATER) == "heat_pump"


async def test_changes_on_the_water_heater_are_followed(
    hass: HomeAssistant, heater: HotWater, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass)
    await _call(hass, "set_operation_mode", operation_mode="heat_pump")
    await _elapse(hass, freezer, 11)
    await _call(hass, "set_operation_mode", HEATER, operation_mode="eco")
    await _elapse(hass, freezer, 6)
    assert _state(hass, PROXY) == "eco"
    assert _state(hass, HEATER) == "eco"
