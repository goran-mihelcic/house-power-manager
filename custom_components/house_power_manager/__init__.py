"""House Power Manager: keeps grid import below the connection limit."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import HpmConfigEntry, HpmCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HpmConfigEntry) -> bool:
    """Restore the devices' state and start balancing."""
    coordinator = HpmCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HpmConfigEntry) -> bool:
    """Stop balancing and remove the entities."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_entry_updated(hass: HomeAssistant, entry: HpmConfigEntry) -> None:
    """Settings, or managed devices added, changed or removed."""
    if not entry.runtime_data.apply_entry_update(entry):
        await hass.config_entries.async_reload(entry.entry_id)
