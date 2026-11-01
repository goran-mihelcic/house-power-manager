"""House Power Manager: keeps grid import below the connection limit."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import HpmConfigEntry, HpmCoordinator

PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: HpmConfigEntry) -> bool:
    """Start measuring grid import."""
    coordinator = HpmCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HpmConfigEntry) -> bool:
    """Stop measuring and remove the entities."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: HpmConfigEntry) -> None:
    if not entry.runtime_data.apply_options(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)
