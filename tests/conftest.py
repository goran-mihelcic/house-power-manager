from typing import Any

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.house_power_manager.const import CONF_GRID_SENSOR, DEFAULTS, DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"

GRID = "sensor.grid_power"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def set_grid(hass: HomeAssistant, value: Any, unit: str | None = "W") -> None:
    """Report a grid power reading."""
    attrs = {"unit_of_measurement": unit, "device_class": "power"} if unit else {}
    hass.states.async_set(GRID, str(value), attrs, force_update=True)


async def setup_hpm(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="House Power Manager",
        options={**DEFAULTS, CONF_GRID_SENSOR: GRID, **options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
