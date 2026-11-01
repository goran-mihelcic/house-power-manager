"""Constants for the House Power Manager integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "house_power_manager"

# ---- Global options (stored in entry.options) ---------------------------
CONF_GRID_SENSOR: Final = "grid_sensor"
CONF_SIGN: Final = "sign_convention"
CONF_IMPORT_LIMIT: Final = "import_limit"  # W
CONF_MARGIN: Final = "margin"  # W kept below the limit: target = limit - margin
CONF_UPDATE_INTERVAL: Final = "update_interval"  # s between periodic re-checks
CONF_FAULT_TIMEOUT: Final = "fault_timeout"  # s without a fresh reading before sensor_fault

SIGN_IMPORT_POSITIVE: Final = "import_positive"
SIGN_IMPORT_NEGATIVE: Final = "import_negative"

DEFAULT_IMPORT_LIMIT: Final = 7600.0
DEFAULT_MARGIN: Final = 400.0
DEFAULT_UPDATE_INTERVAL: Final = 5
DEFAULT_FAULT_TIMEOUT: Final = 60

DEFAULTS: Final = {
    CONF_SIGN: SIGN_IMPORT_POSITIVE,
    CONF_IMPORT_LIMIT: DEFAULT_IMPORT_LIMIT,
    CONF_MARGIN: DEFAULT_MARGIN,
    CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
    CONF_FAULT_TIMEOUT: DEFAULT_FAULT_TIMEOUT,
}

# Options the coordinator takes without a reload (set from the number entities).
LIVE_OPTIONS: Final = frozenset({CONF_IMPORT_LIMIT, CONF_MARGIN})

# Grid sensor updates closer together than this are evaluated once.
DEBOUNCE_SECONDS: Final = 1.0

# ---- Why the grid sensor can't be trusted -------------------------------
FAULT_UNAVAILABLE: Final = "unavailable"
FAULT_NOT_NUMERIC: Final = "not_numeric"
FAULT_UNSUPPORTED_UNIT: Final = "unsupported_unit"
FAULT_STALE: Final = "stale"
