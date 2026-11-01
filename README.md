# House Power Manager

Home Assistant integration that keeps **grid import** below the connection limit by granting, throttling and shedding controllable loads in priority order.

Other automations and integrations **request** power for a device; House Power Manager is the only thing that actually switches it, and only when there is room.

> Status: switched (on/off) devices are supported. Adjustable devices such as an EV charger (amps), the sensor-fault policy and repairs are still to come. Single-phase connections only.

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
| Restore hysteresis | 200 W | A device is restored only when headroom exceeds its power by this much. |
| Update interval | 5 s | Periodic re-check, in addition to every sensor change. |
| Sensor fault timeout | 60 s | Without a fresh reading for this long the status becomes `sensor_fault`. Until then the last good reading is used. |

All settings can be changed later under **Configure**. Limit and margin can also be changed at runtime with their number entities, without a reload.

## Managed devices

Settings → Devices & services → **House Power Manager** → **Add managed device**. Each device can be changed (⋮ → Reconfigure) or deleted on its own.

| Setting | Meaning |
|---|---|
| Name | Also names the device's entities, e.g. *Floor heating* → `switch.floor_heating_requested` |
| Priority | 1–100. **1 is shed first.** A waiting device can take power from running devices with a lower number. |
| Control entity | The switch (or climate, water heater, fan, light) turned on when power is granted and off when shed |
| Nominal power | W drawn when running; decides whether there is room to switch it on |
| Power sensor | Optional. Real consumption, used instead of nominal power when deciding what to shed |
| Minimum on / off time | Protects compressors and avoids churn. Minimum on time is ignored when import exceeds the hard limit. |
| Latency | Seconds until a command shows in the grid measurement. Use 30–60 s for cloud devices (LG ThinQ). |

> **Switching a device by hand is followed, not fought.** If a managed device is switched on or off outside the integration (wall switch, app, another automation), that becomes its new request and grant. A device that is already on when you add it keeps running. A device switched on by hand still counts in balancing: when the measurement shows import over target it can be shed like any other, and it comes back on once there is room. Turn the device's **Managed** switch off to keep the integration from ever switching it.

### How balancing works

- **Over target:** granted devices are shed, least important first (equal priority: largest first), until import is back under target. Devices whose power sensor shows they draw nothing are skipped.
- **Under target:** one requested device is restored per cycle, most important first, when headroom ≥ its nominal power + hysteresis. Nothing is restored while an earlier change hasn't shown in the measurement yet (latency).
- **Priority:** if the most important waiting device doesn't fit, less important running devices are shed to make room for it.
- **Sensor fault:** grants are held, nothing new is switched on; ended requests are still switched off.
- **Manual changes:** followed. The power of a device switched on by hand is not anticipated: it is only shed once the measurement shows import over target.
- **Restart:** requests and grants are restored and re-applied straight away; the balancer then sheds if needed.
- **Disabled** (`switch.hpm_enabled` off): devices are left alone.

## Entities

House Power Manager device:

| Entity | Description |
|---|---|
| `sensor.hpm_grid_import` | Normalised grid power in W: positive importing, negative exporting |
| `sensor.hpm_headroom` | `target − grid_import`; negative when over target |
| `sensor.hpm_managed_load` | Power drawn by managed devices |
| `sensor.hpm_status` | `normal` / `limiting` (over target) / `emergency` (over limit) / `sensor_fault` |
| `number.hpm_import_limit`, `number.hpm_margin` | Runtime-adjustable limit and margin |
| `switch.hpm_enabled` | Master switch |

Each managed device (`<name>` from its name):

| Entity | Description |
|---|---|
| `switch.<name>_requested` | Ask for power. Turn this on instead of the device itself. |
| `binary_sensor.<name>_granted` | Power granted; attribute `reason` |
| `sensor.<name>_granted_power` | Power the device draws: its power sensor, or its nominal power while it is on (0 when off) |
| `sensor.<name>_state` | `idle` / `running` / `shed` / `override`; attributes priority, reason, last change |
| `switch.<name>_managed` | Off: the device is left alone (its power still counts in grid import) |

## Services and events

| Service | Fields |
|---|---|
| `house_power_manager.request_power` | `device` (device ID or name), `power` (adjustable devices, later) |
| `house_power_manager.release_power` | `device` |
| `house_power_manager.set_priority` | `device`, `priority` (1–100) |
| `house_power_manager.rebalance` | – |

Events `house_power_manager_device_shed` and `house_power_manager_device_restored` carry `device`, `device_id`, `reason` (`over_target`, `emergency`, `preempted`, `headroom`), `power` and `grid_import`.

### Example: request floor heating from a schedule

```yaml
automation:
  - alias: Bathroom floor heating requests power
    triggers:
      - trigger: state
        entity_id: schedule.bathroom_floor
    actions:
      - action: >-
          house_power_manager.{{ 'request_power' if trigger.to_state.state == 'on' else 'release_power' }}
        data:
          device: Floor heating
```

### Example: notify when something is shed

```yaml
automation:
  - alias: Notify on load shedding
    triggers:
      - trigger: event
        event_type: house_power_manager_device_shed
    actions:
      - action: notify.notify
        data:
          message: "{{ trigger.event.data.device }} switched off ({{ trigger.event.data.reason }})"
```

## Development

Tests start a real Home Assistant core in memory (Linux or WSL 2; Home Assistant does not run on native Windows):

```bash
uv venv --python 3.13 && source .venv/bin/activate
uv pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

The balancing logic lives in `balancer.py`, which has no Home Assistant imports and is unit-tested directly.
