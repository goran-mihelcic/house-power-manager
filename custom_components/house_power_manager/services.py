"""Services for requesting power and steering the balancer."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
import voluptuous as vol

from .const import DOMAIN
from .coordinator import HpmCoordinator
from .device import ManagedDevice

SERVICE_REQUEST_POWER = "request_power"
SERVICE_RELEASE_POWER = "release_power"
SERVICE_SET_PRIORITY = "set_priority"
SERVICE_REBALANCE = "rebalance"

ATTR_DEVICE = "device"  # device id, or the managed device's name
ATTR_POWER = "power"
ATTR_PRIORITY = "priority"

DEVICE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE): cv.string})
REQUEST_SCHEMA = DEVICE_SCHEMA.extend(
    # Used by adjustable devices; a switched device runs at its nominal power.
    {vol.Optional(ATTR_POWER): vol.All(vol.Coerce(float), vol.Range(min=0))}
)
PRIORITY_SCHEMA = DEVICE_SCHEMA.extend(
    {vol.Required(ATTR_PRIORITY): vol.All(vol.Coerce(int), vol.Range(min=1, max=100))}
)


def _coordinator(hass: HomeAssistant) -> HpmCoordinator:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return entries[0].runtime_data


def _device(hass: HomeAssistant, call: ServiceCall) -> tuple[HpmCoordinator, ManagedDevice]:
    coordinator = _coordinator(hass)
    target = call.data[ATTR_DEVICE]
    if registry_device := dr.async_get(hass).async_get(target):
        for domain, identifier in registry_device.identifiers:
            if domain == DOMAIN and identifier in coordinator.devices:
                return coordinator, coordinator.devices[identifier]
    for device in coordinator.devices.values():
        if device.name.casefold() == target.casefold():
            return coordinator, device
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_device",
        translation_placeholders={"device": target},
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def request_power(call: ServiceCall) -> None:
        coordinator, device = _device(hass, call)
        coordinator.async_request(device, True)

    async def release_power(call: ServiceCall) -> None:
        coordinator, device = _device(hass, call)
        coordinator.async_request(device, False)

    async def set_priority(call: ServiceCall) -> None:
        coordinator, device = _device(hass, call)
        coordinator.async_set_priority(device, call.data[ATTR_PRIORITY])

    async def rebalance(call: ServiceCall) -> None:
        await _coordinator(hass).async_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REQUEST_POWER, request_power, REQUEST_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RELEASE_POWER, release_power, DEVICE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_PRIORITY, set_priority, PRIORITY_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_REBALANCE, rebalance)
