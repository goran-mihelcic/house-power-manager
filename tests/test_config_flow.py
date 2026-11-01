"""Config and options flow."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.house_power_manager.const import (
    CONF_FAULT_TIMEOUT,
    CONF_GRID_SENSOR,
    CONF_IMPORT_LIMIT,
    CONF_MARGIN,
    CONF_SIGN,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    SIGN_IMPORT_NEGATIVE,
    SIGN_IMPORT_POSITIVE,
)

from .conftest import GRID, set_grid, setup_hpm

USER_INPUT = {
    CONF_GRID_SENSOR: GRID,
    CONF_SIGN: SIGN_IMPORT_POSITIVE,
    CONF_IMPORT_LIMIT: 7600.0,
    CONF_MARGIN: 400.0,
    CONF_UPDATE_INTERVAL: 5.0,
    CONF_FAULT_TIMEOUT: 60.0,
}


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return result["flow_id"]


async def test_create_entry(hass: HomeAssistant) -> None:
    set_grid(hass, 1.2, "kW")
    result = await hass.config_entries.flow.async_configure(await _start(hass), USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == USER_INPUT
    assert result["data"] == {}
    await hass.async_block_till_done()
    assert hass.states.get("sensor.hpm_grid_import").state == "1200.0"


async def test_missing_sensor(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_configure(await _start(hass), USER_INPUT)
    assert result["errors"] == {CONF_GRID_SENSOR: "sensor_not_found"}


async def test_unsupported_unit_and_margin(hass: HomeAssistant) -> None:
    set_grid(hass, 1200, "kWh")
    result = await hass.config_entries.flow.async_configure(
        await _start(hass), {**USER_INPUT, CONF_IMPORT_LIMIT: 2000.0, CONF_MARGIN: 2000.0}
    )
    assert result["errors"] == {
        CONF_GRID_SENSOR: "unsupported_unit",
        CONF_MARGIN: "margin_too_large",
    }


async def test_single_instance(hass: HomeAssistant) -> None:
    set_grid(hass, 1000)
    await setup_hpm(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_reloads_with_new_sign(hass: HomeAssistant) -> None:
    set_grid(hass, 1000)
    entry = await setup_hpm(hass)
    assert hass.states.get("sensor.hpm_grid_import").state == "1000.0"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_SIGN: SIGN_IMPORT_NEGATIVE}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_SIGN] == SIGN_IMPORT_NEGATIVE
    assert hass.states.get("sensor.hpm_grid_import").state == "-1000.0"
