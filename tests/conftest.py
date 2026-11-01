from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.house_power_manager.const import (
    CONF_CONTROL_ENTITY,
    CONF_DEVICE_TYPE,
    CONF_GRID_SENSOR,
    CONF_NOMINAL_POWER,
    DEFAULTS,
    DEVICE_DEFAULTS,
    DEVICE_TYPE_SWITCH,
    DOMAIN,
    SUBENTRY_DEVICE,
)

pytest_plugins = "pytest_homeassistant_custom_component"

GRID = "sensor.grid_power"
ENTRY_ID = "hpm_entry"
STORAGE_KEY = f"{DOMAIN}.{ENTRY_ID}"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def set_grid(hass: HomeAssistant, value: Any, unit: str | None = "W") -> None:
    """Report a grid power reading."""
    attrs = {"unit_of_measurement": unit, "device_class": "power"} if unit else {}
    hass.states.async_set(GRID, str(value), attrs, force_update=True)


def device(subentry_id: str, name: str, control: str, power: float, **data: Any) -> dict[str, Any]:
    """Subentry data for a switched managed device."""
    return {
        "subentry_id": subentry_id,
        "subentry_type": SUBENTRY_DEVICE,
        "title": name,
        "unique_id": None,
        "data": {
            CONF_DEVICE_TYPE: DEVICE_TYPE_SWITCH,
            **DEVICE_DEFAULTS,
            CONF_CONTROL_ENTITY: control,
            CONF_NOMINAL_POWER: power,
            **data,
        },
    }


async def setup_booleans(hass: HomeAssistant, *names: str, on: tuple[str, ...] = ()) -> None:
    """input_boolean control entities that really switch."""
    config = {name: {"initial": name in on} for name in names}
    assert await async_setup_component(hass, "input_boolean", {"input_boolean": config})


async def setup_hpm(
    hass: HomeAssistant, devices: list[dict[str, Any]] | None = None, **options: Any
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        title="House Power Manager",
        options={**DEFAULTS, CONF_GRID_SENSOR: GRID, **options},
        subentries_data=devices or [],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
