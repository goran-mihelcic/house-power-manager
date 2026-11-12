"""Constants for the House Power Manager integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "house_power_manager"

# ---- Global options (stored in entry.options) ---------------------------
CONF_GRID_SENSOR: Final = "grid_sensor"
CONF_SIGN: Final = "sign_convention"
CONF_IMPORT_LIMIT: Final = "import_limit"  # W
CONF_MARGIN: Final = "margin"  # W kept below the limit: target = limit - margin
CONF_HYSTERESIS: Final = "hysteresis"  # W of headroom beyond a device's power before restoring
CONF_UPDATE_INTERVAL: Final = "update_interval"  # s between periodic re-checks
CONF_FAULT_TIMEOUT: Final = "fault_timeout"  # s without a fresh reading before sensor_fault

SIGN_IMPORT_POSITIVE: Final = "import_positive"
SIGN_IMPORT_NEGATIVE: Final = "import_negative"

DEFAULT_IMPORT_LIMIT: Final = 7600.0
DEFAULT_MARGIN: Final = 400.0
DEFAULT_HYSTERESIS: Final = 200.0
DEFAULT_UPDATE_INTERVAL: Final = 5
DEFAULT_FAULT_TIMEOUT: Final = 60

# ---- Notifications (a section of the global options) --------------------
CONF_NOTIFICATIONS: Final = "notifications"
CONF_NOTIFY_TARGETS: Final = "notify_targets"  # notify actions or notify entities
CONF_NOTIFY_SHED: Final = "notify_shed"  # a device switched off because of overload
CONF_NOTIFY_LIMIT: Final = "notify_limit"  # import close to the limit
CONF_WARNING_MARGIN: Final = "warning_margin"  # W below the limit that counts as close

NOTIFICATION_DEFAULTS: Final = {
    CONF_NOTIFY_TARGETS: [],
    CONF_NOTIFY_SHED: True,
    CONF_NOTIFY_LIMIT: True,
    CONF_WARNING_MARGIN: 200.0,
}

DEFAULTS: Final = {
    CONF_SIGN: SIGN_IMPORT_POSITIVE,
    CONF_IMPORT_LIMIT: DEFAULT_IMPORT_LIMIT,
    CONF_MARGIN: DEFAULT_MARGIN,
    CONF_HYSTERESIS: DEFAULT_HYSTERESIS,
    CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
    CONF_FAULT_TIMEOUT: DEFAULT_FAULT_TIMEOUT,
    CONF_NOTIFICATIONS: NOTIFICATION_DEFAULTS,
}

# Options the coordinator takes without a reload.
LIVE_OPTIONS: Final = frozenset(
    {CONF_IMPORT_LIMIT, CONF_MARGIN, CONF_HYSTERESIS, CONF_NOTIFICATIONS}
)

# Grid sensor updates closer together than this are evaluated once.
DEBOUNCE_SECONDS: Final = 1.0

# A command that didn't take effect is sent again after this long (at least the latency).
RETRY_SECONDS: Final = 10.0

# ---- Why the grid sensor can't be trusted -------------------------------
FAULT_UNAVAILABLE: Final = "unavailable"
FAULT_NOT_NUMERIC: Final = "not_numeric"
FAULT_UNSUPPORTED_UNIT: Final = "unsupported_unit"
FAULT_STALE: Final = "stale"

# ---- Managed devices (config subentries) --------------------------------
SUBENTRY_DEVICE: Final = "device"
CONF_DEVICE_TYPE: Final = "type"
DEVICE_TYPE_SWITCH: Final = "switch"  # fixed power, on or off
CONF_PRIORITY: Final = "priority"  # 1 = shed first
CONF_CONTROL_ENTITY: Final = "control_entity"
CONF_NOMINAL_POWER: Final = "nominal_power"  # W
CONF_POWER_SENSOR: Final = "power_sensor"
CONF_MIN_ON_TIME: Final = "min_on_time"  # s
CONF_MIN_OFF_TIME: Final = "min_off_time"  # s
CONF_LATENCY: Final = "latency"  # s before a command shows in the grid measurement
# Devices with the same supply name share one power supply, e.g. the indoor units of a
# multi-split outdoor unit; they are shed together
CONF_SUPPLY_GROUP: Final = "supply_group"

DEVICE_DEFAULTS: Final = {
    CONF_PRIORITY: 50,
    CONF_MIN_ON_TIME: 0,
    CONF_MIN_OFF_TIME: 0,
    CONF_LATENCY: 10,
}

# Entity domains a switch-type device can be driven through with turn_on / turn_off
CONTROL_DOMAINS: Final = ["switch", "input_boolean", "climate", "water_heater", "fan", "light"]

# ---- Events -------------------------------------------------------------
EVENT_DEVICE_SHED: Final = f"{DOMAIN}_device_shed"
EVENT_DEVICE_RESTORED: Final = f"{DOMAIN}_device_restored"
