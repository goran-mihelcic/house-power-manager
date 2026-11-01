"""Import limit and margin, adjustable at runtime and stored in the entry options."""

from __future__ import annotations

from homeassistant.components.number import (
    ENTITY_ID_FORMAT,
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_IMPORT_LIMIT, CONF_MARGIN
from .coordinator import HpmConfigEntry, HpmCoordinator
from .entity import HpmEntity


def _watts(key: str, low: float, high: float, step: float) -> NumberEntityDescription:
    return NumberEntityDescription(
        key=key,
        translation_key=key,
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=low,
        native_max_value=high,
        native_step=step,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )


NUMBERS: tuple[NumberEntityDescription, ...] = (
    _watts(CONF_IMPORT_LIMIT, 1000, 50000, 100),
    _watts(CONF_MARGIN, 0, 5000, 50),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(HpmNumber(entry.runtime_data, description) for description in NUMBERS)


class HpmNumber(HpmEntity, NumberEntity):
    """A global setting; the key is its entry option."""

    def __init__(self, coordinator: HpmCoordinator, description: NumberEntityDescription) -> None:
        super().__init__(coordinator, description, ENTITY_ID_FORMAT)

    @property
    def native_value(self) -> float:
        return self.coordinator.config_entry.options[self.entity_description.key]

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.async_set_option(self.entity_description.key, value)
