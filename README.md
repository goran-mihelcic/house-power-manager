# House Power Manager

Home Assistant integration that keeps **grid import** below the connection limit by granting, throttling and shedding controllable loads in priority order.

> Status: **0.1.0 – skeleton.** It measures grid import and reports headroom and status. Managed devices, requests and shedding are not implemented yet.

## Installation

HACS → Integrations → ⋮ → Custom repositories → add this repository as an *Integration*, install **House Power Manager**, restart Home Assistant.

Or copy `custom_components/house_power_manager` into your `config/custom_components` folder.

## Configuration

Settings → Devices & services → Add integration → **House Power Manager**. There is one instance.

| Setting | Default | Meaning |
|---|---|---|
| Grid power sensor | – | Power on the main connection (W, kW or MW). With PV this is import minus export, not house load. |
| Sign convention | positive while importing | Pick *negative while importing* if the sensor reads negative when the house draws from the grid. |
| Import limit | 7600 W | Import at which the limiter cuts supply. |
| Margin | 400 W | Import is kept this far below the limit: `target = limit − margin`. |
| Update interval | 5 s | Periodic re-check, in addition to every sensor change. |
| Sensor fault timeout | 60 s | Without a fresh reading for this long the status becomes `sensor_fault`. Until then the last good reading is used. |

All settings can be changed later under **Configure**. Limit and margin can also be changed at runtime with their number entities, without a reload.

## Entities

| Entity | Description |
|---|---|
| `sensor.hpm_grid_import` | Normalised grid power in W: positive importing, negative exporting |
| `sensor.hpm_headroom` | `target − grid_import`; negative when over target |
| `sensor.hpm_managed_load` | Power granted to managed devices (0 until devices exist) |
| `sensor.hpm_status` | `normal` / `limiting` (over target) / `emergency` (over limit) / `sensor_fault` |
| `number.hpm_import_limit`, `number.hpm_margin` | Runtime-adjustable limit and margin |
| `switch.hpm_enabled` | Master switch; restored after restarts |

## Development

Tests start a real Home Assistant core in memory (Linux or WSL 2; Home Assistant does not run on native Windows):

```bash
uv venv --python 3.13 && source .venv/bin/activate
uv pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

The balancing logic lives in `balancer.py`, which has no Home Assistant imports and is unit-tested directly.
