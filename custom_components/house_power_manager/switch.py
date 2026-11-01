"""Master switch for power management; requested and managed switches per device."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import ENTITY_ID_FORMAT, SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HpmConfigEntry, HpmCoordinator
from .entity import HpmDeviceEntity, HpmEntity

ENABLED = SwitchEntityDescription(key="enabled", translation_key="enabled")
REQUESTED = SwitchEntityDescription(key="requested", translation_key="requested")
MANAGED = SwitchEntityDescription(
    key="managed", translation_key="managed", entity_category=EntityCategory.CONFIG
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([HpmEnabledSwitch(coordinator)])
    for device in coordinator.devices.values():
        async_add_entities(
            [
                RequestedSwitch(coordinator, device, REQUESTED),
                ManagedSwitch(coordinator, device, MANAGED),
            ],
            config_subentry_id=device.id,
        )


class HpmEnabledSwitch(HpmEntity, SwitchEntity):
    """When off, the integration measures but leaves devices alone."""

    def __init__(self, coordinator: HpmCoordinator) -> None:
        super().__init__(coordinator, ENABLED, ENTITY_ID_FORMAT)

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.async_set_enabled(False)


class RequestedSwitch(HpmDeviceEntity, SwitchEntity):
    """Asks for power. The device itself is switched only when the request is granted."""

    @property
    def is_on(self) -> bool:
        return self._device.requested

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.async_request(self._device, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.async_request(self._device, False)


class ManagedSwitch(HpmDeviceEntity, SwitchEntity):
    """When off, the device is left alone; its power still shows in grid import."""

    @property
    def is_on(self) -> bool:
        return self._device.managed

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.async_set_managed(self._device, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.async_set_managed(self._device, False)
