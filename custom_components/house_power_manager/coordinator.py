"""Reads the grid import sensor, balances the managed devices and drives them."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any, TypeGuard

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
    split_entity_id,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .balancer import Action, GridSnapshot, Reason, Status, decide, normalize_import, to_watts
from .const import (
    CONF_FAULT_TIMEOUT,
    CONF_GRID_SENSOR,
    CONF_HYSTERESIS,
    CONF_IMPORT_LIMIT,
    CONF_MARGIN,
    CONF_PRIORITY,
    CONF_SIGN,
    CONF_UPDATE_INTERVAL,
    DEBOUNCE_SECONDS,
    DEFAULT_HYSTERESIS,
    DOMAIN,
    EVENT_DEVICE_RESTORED,
    EVENT_DEVICE_SHED,
    FAULT_NOT_NUMERIC,
    FAULT_STALE,
    FAULT_UNAVAILABLE,
    FAULT_UNSUPPORTED_UNIT,
    LIVE_OPTIONS,
    RETRY_SECONDS,
    SIGN_IMPORT_POSITIVE,
    SUBENTRY_DEVICE,
)
from .device import ManagedDevice

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 1  # s

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


def _known(state: State | None) -> TypeGuard[State]:
    return state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)


def _reload_relevant(entry: ConfigEntry) -> dict[str, tuple[str, dict[str, Any]]]:
    """Device settings that need a reload when changed: all but the priority."""
    return {
        subentry_id: (sub.title, {k: v for k, v in sub.data.items() if k != CONF_PRIORITY})
        for subentry_id, sub in entry.subentries.items()
    }


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
        self._subentries = _reload_relevant(entry)
        self.grid_sensor: str = options[CONF_GRID_SENSOR]
        self.import_positive = options[CONF_SIGN] == SIGN_IMPORT_POSITIVE
        self.fault_timeout = timedelta(seconds=options[CONF_FAULT_TIMEOUT])
        self.limit = float(options[CONF_IMPORT_LIMIT])
        self.margin = float(options[CONF_MARGIN])
        self.hysteresis = float(options.get(CONF_HYSTERESIS, DEFAULT_HYSTERESIS))
        self.enabled = True
        self.devices: dict[str, ManagedDevice] = {
            subentry_id: ManagedDevice.from_subentry(sub)
            for subentry_id, sub in entry.subentries.items()
            if sub.subentry_type == SUBENTRY_DEVICE
        }
        # Time of the last valid reading. Start-up counts as one, so a grid sensor that
        # loads after this integration doesn't raise a fault straight away.
        self.measured_at = dt_util.utcnow()
        self.status: Status | None = None
        self.status_since = self.measured_at
        self._last_import: float | None = None
        self._lock = asyncio.Lock()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="House Power Manager",
            entry_type=DeviceEntryType.SERVICE,
        )

    # ---- Lifecycle -------------------------------------------------------

    async def async_load(self) -> None:
        """Restore requests and grants from before the restart."""
        stored = await self._store.async_load() or {}
        self.enabled = stored.get("enabled", True)
        for device_id, data in stored.get("devices", {}).items():
            if device := self.devices.get(device_id):
                device.restore(data)

    @callback
    def async_start(self) -> None:
        """Re-evaluate whenever the grid sensor changes."""
        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, self.grid_sensor, self._async_grid_changed)
        )

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self._store.async_save(self._stored())

    def _stored(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "devices": {device.id: device.stored() for device in self.devices.values()},
        }

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(self._stored, SAVE_DELAY)

    @callback
    def _async_grid_changed(self, event: Event[EventStateChangedData]) -> None:
        self.config_entry.async_create_task(self.hass, self.async_request_refresh())

    @callback
    def _refresh_now(self) -> None:
        """Re-balance straight away, not debounced: for changes made by the user."""
        self.config_entry.async_create_task(self.hass, self.async_refresh())

    # ---- Cycle -----------------------------------------------------------

    async def _async_update_data(self) -> GridSnapshot:
        async with self._lock:
            snapshot = self._evaluate()
            if self.enabled:
                now = dt_util.utcnow().timestamp()
                # Re-apply existing grants first (e.g. restored after a restart), so the
                # balancer knows that power is on its way
                await self._async_enforce(now)
                if self._balance(snapshot, now):
                    await self._async_enforce(now)
            return snapshot

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

    def _balance(self, grid: GridSnapshot, now: float) -> bool:
        """Apply the balancer's decisions. True when a grant changed."""
        inputs = [
            device.as_input(self.measured_power(device))
            for device in self.devices.values()
            if device.managed
        ]
        actions = decide(grid, inputs, now, self.hysteresis)
        for action in actions:
            self._apply(self.devices[action.device_id], action, grid, now)
        if actions:
            self._save()
        return bool(actions)

    def _apply(self, device: ManagedDevice, action: Action, grid: GridSnapshot, now: float) -> None:
        device.granted = action.grant
        device.changed_at = now
        device.reason = action.reason
        device.commanded_at = None
        if action.grant:
            verb = "Restoring"
        else:
            verb = "Releasing" if action.reason is Reason.RELEASED else "Shedding"
        _LOGGER.info(
            "%s %s (%s): import %s W, headroom %s W",
            verb,
            device.name,
            action.reason,
            "?" if grid.grid_import is None else f"{grid.grid_import:.0f}",
            "?" if grid.headroom is None else f"{grid.headroom:.0f}",
        )
        if action.reason is Reason.RELEASED:
            return
        registry_device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, device.id)}
        )
        self.hass.bus.async_fire(
            EVENT_DEVICE_RESTORED if action.grant else EVENT_DEVICE_SHED,
            {
                "device": device.name,
                "device_id": registry_device.id if registry_device else None,
                "reason": action.reason.value,
                "power": device.nominal_power,
                "grid_import": grid.grid_import,
            },
        )

    async def _async_enforce(self, now: float) -> None:
        """Bring each managed control entity to its grant, retrying slow devices."""
        for device in self.devices.values():
            state = self.hass.states.get(device.control_entity)
            if not device.managed or not _known(state):
                continue
            if (state.state != STATE_OFF) == device.granted:
                continue
            if device.commanded_at is not None:
                if now - device.commanded_at < max(device.latency, RETRY_SECONDS):
                    continue
                _LOGGER.info(
                    "%s is %s but %s: sending the command again",
                    device.control_entity,
                    state.state,
                    "granted" if device.granted else "not granted",
                )
            device.commanded_at = now
            service = SERVICE_TURN_ON if device.granted else SERVICE_TURN_OFF
            try:
                await self.hass.services.async_call(
                    split_entity_id(device.control_entity)[0],
                    service,
                    {ATTR_ENTITY_ID: device.control_entity},
                    blocking=False,
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Cannot %s %s: %s", service, device.control_entity, err)

    # ---- Readings --------------------------------------------------------

    def measured_power(self, device: ManagedDevice) -> float | None:
        """W from the device's power sensor; None without one or while it can't be read."""
        if not device.power_sensor:
            return None
        state = self.hass.states.get(device.power_sensor)
        if not _known(state):
            return None
        try:
            return to_watts(float(state.state), state.attributes.get(ATTR_UNIT_OF_MEASUREMENT))
        except ValueError:
            return None

    def granted_power(self, device: ManagedDevice) -> float:
        if not device.managed or not device.granted:
            return 0.0
        measured = self.measured_power(device)
        return device.nominal_power if measured is None else measured

    @property
    def managed_load(self) -> float:
        return sum(self.granted_power(device) for device in self.devices.values())

    # ---- Changes from entities, services and the UI ----------------------

    @callback
    def apply_entry_update(self, entry: HpmConfigEntry) -> bool:
        """Take limit, margin, hysteresis and priorities live. False when a reload is needed."""
        options = entry.options
        changed = {
            k
            for k in options.keys() | self._options.keys()
            if options.get(k) != self._options.get(k)
        }
        if changed - LIVE_OPTIONS or _reload_relevant(entry) != self._subentries:
            return False
        self._options = dict(options)
        self.limit = float(options[CONF_IMPORT_LIMIT])
        self.margin = float(options[CONF_MARGIN])
        self.hysteresis = float(options.get(CONF_HYSTERESIS, DEFAULT_HYSTERESIS))
        for subentry_id, device in self.devices.items():
            device.priority = int(entry.subentries[subentry_id].data[CONF_PRIORITY])
        self._refresh_now()
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
        self._save()
        self.async_update_listeners()
        if enabled:
            self._refresh_now()

    @callback
    def async_request(self, device: ManagedDevice, requested: bool) -> None:
        if requested != device.requested:
            _LOGGER.info("%s %s", device.name, "requested" if requested else "released")
        device.requested = requested
        self._save()
        self.async_update_listeners()
        self._refresh_now()

    @callback
    def async_set_managed(self, device: ManagedDevice, managed: bool) -> None:
        """When management resumes, a device that is on and requested keeps running."""
        if managed == device.managed:
            return
        _LOGGER.info("%s %s", device.name, "managed" if managed else "overridden: left alone")
        device.managed = managed
        if managed:
            state = self.hass.states.get(device.control_entity)
            device.granted = device.requested and _known(state) and state.state != STATE_OFF
            device.commanded_at = None
        self._save()
        self.async_update_listeners()
        self._refresh_now()

    @callback
    def async_set_priority(self, device: ManagedDevice, priority: int) -> None:
        """Store a new priority; the entry update listener applies it."""
        subentry = self.config_entry.subentries[device.id]
        self.hass.config_entries.async_update_subentry(
            self.config_entry, subentry, data={**subentry.data, CONF_PRIORITY: priority}
        )
