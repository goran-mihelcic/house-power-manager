"""House Power Manager: keeps grid import below the connection limit."""

from __future__ import annotations

import logging
from types import MappingProxyType

from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CONTROL_ENTITY,
    CONF_DEVICE_TYPE,
    CONF_LATENCY,
    CONF_MIN_OFF_TIME,
    CONF_MIN_ON_TIME,
    CONF_NOMINAL_POWER,
    CONF_SUPPLY,
    CONF_SUPPLY_GROUP,
    CONTROL_DOMAINS,
    DEVICE_TYPE_SWITCH,
    DOMAIN,
    SUBENTRY_DEVICE,
    SUBENTRY_SUPPLY,
)
from .coordinator import HpmConfigEntry, HpmCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
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


async def async_migrate_entry(hass: HomeAssistant, entry: HpmConfigEntry) -> bool:
    """Bring entries from older versions up to date."""
    if entry.version > 1:
        return False  # from a newer version: can't be downgraded
    if entry.minor_version < 2:
        _migrate_supply_names(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    if entry.minor_version < 3:
        _migrate_device_types(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=3)
    return True


def _migrate_device_types(hass: HomeAssistant, entry: HpmConfigEntry) -> None:
    """Before 0.5 every device was a switch; its control entity tells what it is."""
    for sub in list(entry.subentries.values()):
        if sub.subentry_type != SUBENTRY_DEVICE:
            continue
        domain = sub.data[CONF_CONTROL_ENTITY].split(".")[0]
        kind = next(
            (kind for kind, domains in CONTROL_DOMAINS.items() if domain in domains),
            DEVICE_TYPE_SWITCH,
        )
        if sub.data.get(CONF_DEVICE_TYPE) != kind:
            hass.config_entries.async_update_subentry(
                entry, sub, data={**sub.data, CONF_DEVICE_TYPE: kind}
            )


def _migrate_supply_names(hass: HomeAssistant, entry: HpmConfigEntry) -> None:
    """0.3 linked devices by a shared supply name; 0.4 has a supply subentry for that.

    The supply gets the largest settings of its devices and the sum of their power, the
    safe side until it is configured with the outdoor unit's real figures.
    """
    groups: dict[str, list[ConfigSubentry]] = {}
    for sub in entry.subentries.values():
        if sub.subentry_type == SUBENTRY_DEVICE and (name := sub.data.get(CONF_SUPPLY_GROUP)):
            groups.setdefault(name.strip().casefold(), []).append(sub)
    for devices in groups.values():
        data = [device.data for device in devices]
        supply = ConfigSubentry(
            subentry_type=SUBENTRY_SUPPLY,
            title=devices[0].data[CONF_SUPPLY_GROUP].strip(),
            unique_id=None,
            data=MappingProxyType(
                {
                    CONF_NOMINAL_POWER: sum(d.get(CONF_NOMINAL_POWER, 0) for d in data),
                    CONF_MIN_ON_TIME: max(d.get(CONF_MIN_ON_TIME, 0) for d in data),
                    CONF_MIN_OFF_TIME: max(d.get(CONF_MIN_OFF_TIME, 0) for d in data),
                    CONF_LATENCY: max(d.get(CONF_LATENCY, 0) for d in data),
                }
            ),
        )
        hass.config_entries.async_add_subentry(entry, supply)
        for device in devices:
            new = {k: v for k, v in device.data.items() if k != CONF_SUPPLY_GROUP}
            hass.config_entries.async_update_subentry(
                entry, device, data={**new, CONF_SUPPLY: supply.subentry_id}
            )
        _LOGGER.info(
            "Shared supply '%s' is now a device of its own, with %s on it",
            supply.title,
            ", ".join(device.title for device in devices),
        )


async def _async_entry_updated(hass: HomeAssistant, entry: HpmConfigEntry) -> None:
    """Settings, or managed devices added, changed or removed."""
    if not entry.runtime_data.apply_entry_update(entry):
        await hass.config_entries.async_reload(entry.entry_id)
