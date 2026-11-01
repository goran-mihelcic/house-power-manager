"""Whether each managed device is granted power."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HpmConfigEntry
from .entity import HpmDeviceEntity

GRANTED = BinarySensorEntityDescription(key="granted", translation_key="granted")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    for device in coordinator.devices.values():
        async_add_entities(
            [GrantedSensor(coordinator, device, GRANTED)], config_subentry_id=device.id
        )


class GrantedSensor(HpmDeviceEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool:
        return self._device.granted

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"reason": self._device.reason}
