"""Shared bases for House Power Manager entities."""

from __future__ import annotations

from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import HpmCoordinator
from .device import ManagedDevice


class HpmEntity(CoordinatorEntity[HpmCoordinator]):
    """Ties an entity to the House Power Manager device, as `<domain>.hpm_<key>`."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: HpmCoordinator, description: EntityDescription, id_format: str
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self.entity_id = id_format.format(f"hpm_{description.key}")
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"
        self._attr_device_info = coordinator.device_info


class HpmDeviceEntity(CoordinatorEntity[HpmCoordinator]):
    """Ties an entity to one managed device, as `<domain>.<device name>_<key>`."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: HpmCoordinator, device: ManagedDevice, description: EntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._device = device
        self._attr_unique_id = f"{device.id}_{description.key}"
        self._attr_device_info = device.device_info
