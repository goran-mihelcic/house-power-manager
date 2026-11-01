"""Master switch for power management, restored after restarts."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import ENTITY_ID_FORMAT, SwitchEntity, SwitchEntityDescription
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import HpmConfigEntry, HpmCoordinator
from .entity import HpmEntity

ENABLED = SwitchEntityDescription(key="enabled", translation_key="enabled")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HpmEnabledSwitch(entry.runtime_data)])


class HpmEnabledSwitch(HpmEntity, SwitchEntity, RestoreEntity):
    """When off, the integration measures but leaves devices alone."""

    def __init__(self, coordinator: HpmCoordinator) -> None:
        super().__init__(coordinator, ENABLED, ENTITY_ID_FORMAT)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_OFF:
            self.coordinator.async_set_enabled(False)

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.async_set_enabled(False)
