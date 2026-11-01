"""Loading the integration, grid measurement and the global entities."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.house_power_manager.const import (
    CONF_IMPORT_LIMIT,
    CONF_MARGIN,
    CONF_SIGN,
    SIGN_IMPORT_NEGATIVE,
)

from .conftest import set_grid, setup_hpm


def _state(hass: HomeAssistant, entity_id: str) -> str:
    return hass.states.get(entity_id).state


def _value(hass: HomeAssistant, entity_id: str) -> float:
    return float(_state(hass, entity_id))


async def _elapse(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _report(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, value: Any, unit: str | None = "W"
) -> None:
    """A new reading once the debounce window has passed."""
    await _elapse(hass, freezer, 2)
    set_grid(hass, value, unit)
    await hass.async_block_till_done()


async def test_load_and_unload(hass: HomeAssistant) -> None:
    set_grid(hass, 1500)
    entry = await setup_hpm(hass)
    assert entry.state is ConfigEntryState.LOADED
    for entity_id in (
        "sensor.hpm_grid_import",
        "sensor.hpm_headroom",
        "sensor.hpm_managed_load",
        "sensor.hpm_status",
        "number.hpm_import_limit",
        "number.hpm_margin",
        "switch.hpm_enabled",
    ):
        assert hass.states.get(entity_id) is not None, entity_id
    assert _value(hass, "sensor.hpm_grid_import") == 1500
    assert _value(hass, "sensor.hpm_headroom") == 5700
    assert _value(hass, "sensor.hpm_managed_load") == 0
    assert _state(hass, "sensor.hpm_status") == "normal"
    assert _state(hass, "switch.hpm_enabled") == "on"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_kw_and_negative_import(hass: HomeAssistant) -> None:
    set_grid(hass, -2.5, "kW")
    await setup_hpm(hass, **{CONF_SIGN: SIGN_IMPORT_NEGATIVE})
    assert _value(hass, "sensor.hpm_grid_import") == 2500
    assert _value(hass, "sensor.hpm_headroom") == 4700


async def test_status_follows_import(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    set_grid(hass, 1000)
    await setup_hpm(hass)
    await _report(hass, freezer, 7300)
    assert _state(hass, "sensor.hpm_status") == "limiting"
    await _report(hass, freezer, 8000)
    assert _state(hass, "sensor.hpm_status") == "emergency"
    assert _value(hass, "sensor.hpm_headroom") == -800
    # PV export gives headroom beyond the limit
    await _report(hass, freezer, -3000)
    assert _state(hass, "sensor.hpm_status") == "normal"
    assert _value(hass, "sensor.hpm_headroom") == 10200


async def test_unavailable_bridged_until_timeout(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    set_grid(hass, 2000)
    await setup_hpm(hass)
    await _report(hass, freezer, "unavailable", unit=None)
    # Within the timeout the last good reading still counts
    assert _value(hass, "sensor.hpm_grid_import") == 2000
    assert _state(hass, "sensor.hpm_status") == "normal"

    await _elapse(hass, freezer, 61)
    assert _state(hass, "sensor.hpm_status") == "sensor_fault"
    assert hass.states.get("sensor.hpm_status").attributes["reason"] == "unavailable"
    assert _state(hass, "sensor.hpm_grid_import") == "unknown"
    assert _state(hass, "sensor.hpm_headroom") == "unknown"

    await _report(hass, freezer, 2100)
    assert _state(hass, "sensor.hpm_status") == "normal"
    assert _value(hass, "sensor.hpm_grid_import") == 2100


async def test_stale_reading_is_a_fault(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    set_grid(hass, 2000)
    await setup_hpm(hass)
    await _elapse(hass, freezer, 61)
    assert _state(hass, "sensor.hpm_status") == "sensor_fault"
    assert hass.states.get("sensor.hpm_status").attributes["reason"] == "stale"


async def test_energy_unit_is_a_fault(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    set_grid(hass, 2000)
    await setup_hpm(hass)
    await _report(hass, freezer, 2000, unit="kWh")
    await _elapse(hass, freezer, 61)
    assert hass.states.get("sensor.hpm_status").attributes["reason"] == "unsupported_unit"


async def test_limit_number_applies_live(hass: HomeAssistant) -> None:
    set_grid(hass, 7000)
    entry = await setup_hpm(hass)
    assert _state(hass, "sensor.hpm_status") == "normal"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.hpm_import_limit", "value": 7000},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert entry.options[CONF_IMPORT_LIMIT] == 7000
    assert _value(hass, "number.hpm_import_limit") == 7000
    assert _state(hass, "sensor.hpm_status") == "limiting"
    assert _value(hass, "sensor.hpm_headroom") == -400
    assert entry.state is ConfigEntryState.LOADED


async def test_margin_must_stay_below_limit(hass: HomeAssistant) -> None:
    set_grid(hass, 1000)
    entry = await setup_hpm(hass)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.hpm_import_limit", "value": 5000},
        blocking=True,
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.hpm_margin", "value": 5000},
            blocking=True,
        )
    assert entry.options[CONF_IMPORT_LIMIT] == 5000
    assert entry.options[CONF_MARGIN] == 400


async def test_enabled_switch(hass: HomeAssistant) -> None:
    set_grid(hass, 1000)
    entry = await setup_hpm(hass)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.hpm_enabled"}, blocking=True
    )
    assert _state(hass, "switch.hpm_enabled") == "off"
    assert entry.runtime_data.enabled is False
    assert hass.states.get("sensor.hpm_status").attributes["enabled"] is False


async def test_enabled_switch_restored_off(hass: HomeAssistant) -> None:
    mock_restore_cache(hass, [State("switch.hpm_enabled", "off")])
    set_grid(hass, 1000)
    entry = await setup_hpm(hass)
    assert _state(hass, "switch.hpm_enabled") == "off"
    assert entry.runtime_data.enabled is False
