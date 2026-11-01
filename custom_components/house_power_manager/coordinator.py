"""Reads the grid import sensor and holds the balancing state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .balancer import GridSnapshot, Status, normalize_import
from .const import (
    CONF_FAULT_TIMEOUT,
    CONF_GRID_SENSOR,
    CONF_IMPORT_LIMIT,
    CONF_MARGIN,
    CONF_SIGN,
    CONF_UPDATE_INTERVAL,
    DEBOUNCE_SECONDS,
    DOMAIN,
    FAULT_NOT_NUMERIC,
    FAULT_STALE,
    FAULT_UNAVAILABLE,
    FAULT_UNSUPPORTED_UNIT,
    LIVE_OPTIONS,
    SIGN_IMPORT_POSITIVE,
)

_LOGGER = logging.getLogger(__name__)

type HpmConfigEntry = ConfigEntry[HpmCoordinator]


def _read(state: State | None, import_positive: bool) -> tuple[float | None, str | None]:
    """The grid import in W, or why the sensor state can't be used."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None, FAULT_UNAVAILABLE
    try:
        value = float(state.state)
    except ValueError:
        return None, FAULT_NOT_NUMERIC
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    try:
        return normalize_import(value, unit, import_positive), None
    except ValueError:
        return None, FAULT_UNSUPPORTED_UNIT


class HpmCoordinator(DataUpdateCoordinator[GridSnapshot]):
    """Evaluates grid import on every sensor update (debounced) and on a periodic tick."""

    config_entry: HpmConfigEntry

    def __init__(self, hass: HomeAssistant, entry: HpmConfigEntry) -> None:
        options = entry.options
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=options[CONF_UPDATE_INTERVAL]),
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=DEBOUNCE_SECONDS, immediate=True
            ),
        )
        self._options = dict(options)
        self.grid_sensor: str = options[CONF_GRID_SENSOR]
        self.import_positive = options[CONF_SIGN] == SIGN_IMPORT_POSITIVE
        self.fault_timeout = timedelta(seconds=options[CONF_FAULT_TIMEOUT])
        self.limit = float(options[CONF_IMPORT_LIMIT])
        self.margin = float(options[CONF_MARGIN])
        self.enabled = True
        # Time of the last valid reading. Start-up counts as one, so a grid sensor that
        # loads after this integration doesn't raise a fault straight away.
        self.measured_at = dt_util.utcnow()
        self.status: Status | None = None
        self.status_since = self.measured_at
        self._last_import: float | None = None
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="House Power Manager",
            entry_type=DeviceEntryType.SERVICE,
        )

    @callback
    def async_start(self) -> None:
        """Re-evaluate whenever the grid sensor changes."""
        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, self.grid_sensor, self._async_grid_changed)
        )

    @callback
    def _async_grid_changed(self, event: Event[EventStateChangedData]) -> None:
        self.config_entry.async_create_task(self.hass, self.async_request_refresh())

    async def _async_update_data(self) -> GridSnapshot:
        return self._evaluate()

    def _evaluate(self) -> GridSnapshot:
        now = dt_util.utcnow()
        state = self.hass.states.get(self.grid_sensor)
        grid_import, fault = _read(state, self.import_positive)
        if state is not None and grid_import is not None:
            self._last_import, self.measured_at = grid_import, state.last_reported
            fault = FAULT_STALE  # only counts once the reading is too old
        # A bad or old reading is bridged with the last good one until the timeout.
        if now - self.measured_at <= self.fault_timeout:
            fault = None
        snapshot = GridSnapshot(
            grid_import=None if fault else self._last_import,
            limit=self.limit,
            margin=self.margin,
            fault=fault,
        )
        self._track_status(snapshot)
        return snapshot

    def _track_status(self, snapshot: GridSnapshot) -> None:
        status = snapshot.status
        if status == self.status:
            return
        level = (
            logging.WARNING if status in (Status.SENSOR_FAULT, Status.EMERGENCY) else logging.INFO
        )
        _LOGGER.log(
            level,
            "Status %s -> %s (import %s W, target %.0f W, limit %.0f W%s)",
            self.status,
            status,
            "?" if snapshot.grid_import is None else f"{snapshot.grid_import:.0f}",
            snapshot.target,
            snapshot.limit,
            f", {self.grid_sensor} {snapshot.fault}" if snapshot.fault else "",
        )
        self.status = status
        self.status_since = dt_util.utcnow()

    @callback
    def apply_options(self, options: Mapping[str, Any]) -> bool:
        """Take changed limit and margin live. False when another change needs a reload."""
        changed = {
            k
            for k in options.keys() | self._options.keys()
            if options.get(k) != self._options.get(k)
        }
        if changed - LIVE_OPTIONS:
            return False
        self._options = dict(options)
        self.limit = float(options[CONF_IMPORT_LIMIT])
        self.margin = float(options[CONF_MARGIN])
        self.async_set_updated_data(self._evaluate())
        return True

    @callback
    def async_set_option(self, key: str, value: float) -> None:
        """Store a runtime-adjustable setting; the entry update listener applies it."""
        options = {**self.config_entry.options, key: value}
        if options[CONF_MARGIN] >= options[CONF_IMPORT_LIMIT]:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="margin_too_large"
            )
        self.hass.config_entries.async_update_entry(self.config_entry, options=options)

    @callback
    def async_set_enabled(self, enabled: bool) -> None:
        if enabled != self.enabled:
            _LOGGER.info("Power management %s", "enabled" if enabled else "disabled")
        self.enabled = enabled
        self.async_update_listeners()
