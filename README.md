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

### Notifications

In the **Notifications** section of the same form:

| Setting | Default | Meaning |
|---|---|---|
| Notify targets | – | A notify action such as `mobile_app_my_phone`, or a notify entity such as `notify.family`. Several are allowed. |
| Device switched off because of overload | on | One message per balancing step listing the devices switched off because import went over the target or the limit. |
| Import close to the limit | on | Sent when import comes within the warning margin of the limit, and again only after import was back under the target. |
| Warning margin | 200 W | With the default limit and margin: warns at 7400 W, between the 7200 W target and the 7600 W limit. At most the margin. |

Devices switched off to make room for a more important device (`preempted`) or because their request ended are not notified. Every message is also written to the log as a warning.

## Managed devices

Settings → Devices & services → **House Power Manager** → **Add managed device**. Each device can be changed (⋮ → Reconfigure) or deleted on its own.

First pick the device type. It decides which entity controls the device and what stands in for it in automations:

| Type | Control entity | Switched with | Stand-in for automations |
|---|---|---|---|
| Climate | `climate` (thermostat, air conditioner, heat pump) | its HVAC mode | `climate.<name>_power_managed` |
| Switch | `switch`, `input_boolean`, `fan`, `light` | turn on / off | `switch.<name>_requested` |
| Water heater | `water_heater` (e.g. a heat pump's hot water) | its operation mode | `water_heater.<name>_power_managed` |

The type stays as added; to change it, delete the device and add it again.

| Setting | Meaning |
|---|---|
| Name | Also names the device's entities, e.g. *Floor heating* → `switch.floor_heating_requested` |
| Priority | 1–100. **1 is shed first.** A waiting device can take power from running devices with a lower number. |
| Control entity | The entity of the chosen type, turned on when power is granted and off when shed |
| Power supply | Optional, once a shared power supply exists (see below). A device on a shared supply has no power settings of its own: the next step is skipped. |

A device on its own supply then gets a second step:

| Setting | Meaning |
|---|---|
| Nominal power | W drawn when running; decides whether there is room to switch it on |
| Power sensor | Optional. Real consumption, used instead of nominal power when deciding what to shed |
| Minimum on / off time | Protects compressors and avoids churn. Minimum on time is ignored when import exceeds the hard limit. |
| Latency | Seconds until a command shows in the grid measurement. Use 30–60 s for cloud devices (LG ThinQ). |

### Shared power supplies

Several devices can draw from one supply, such as the indoor units of a multi-split air conditioner on one outdoor unit. Switching off one indoor unit doesn't stop the outdoor unit, and its power meter covers all of them.

1. **Add shared power supply** (next to **Add managed device**): a name (e.g. *Outdoor unit*), its nominal power (the outdoor unit's rated input, all indoor units running), an optional power sensor on the outdoor unit, minimum on / off time and latency.
2. Add each indoor unit as a managed device with its priority and control entity, and choose the supply as its **Power supply**.

The balancer then treats the supply as one load:

- It is requested while any of its devices is requested, and as important as the most important of them.
- Switching it on needs headroom for the supply's nominal power + hysteresis. More devices joining a running supply are switched on straight away.
- When it is shed, all its devices are switched off. When there is room again, the devices still requested come back on.
- The last device released keeps running until the supply has run its minimum on time.
- A device switched on by hand also runs its supply.

Each indoor unit keeps its own **Requested** and **Managed** switches. Power, granted and state for the outdoor unit are on the supply's device.

### Thermostats, water heaters and Room Climate: the power-managed stand-ins

A **Climate** device gets a stand-in `climate.<name>_power_managed`, a **Water heater** device gets `water_heater.<name>_power_managed`. Give the stand-in to Room Climate, or any other automation, **instead of the device itself**. The water heater stand-in works like the climate one below, with operation modes in place of HVAC modes.

- **Setting a mode is a request.** Setting off releases it. The thermostat is switched on in that mode, with the setpoint asked for, as soon as power is granted.
- **It shows what was asked for, not what was granted.** While the device is shed, the stand-in still shows *heat* and the requested setpoint, so Room Climate sees nothing to correct and doesn't switch the device back on behind the balancer. The `power_state` attribute shows `running` or `shed`.
- **Everything else is the thermostat's own:** current temperature, HVAC action, modes, temperature limits and fan modes. Setpoint and fan mode go straight to a running thermostat.
- **Changes on the thermostat itself are followed:** a mode or setpoint set on the device or in its app shows up on the stand-in.
- **Pass-through:** with power management switched off, or the device's **Managed** switch off, commands go straight to the thermostat.

Climate devices are switched with their HVAC mode (`set_hvac_mode`) and water heaters with their operation mode (`set_operation_mode`), so devices without the optional turn on / turn off features work too.

For a **Switch** device, the `switch.<name>_requested` switch is the stand-in: it shows the request, not the grant.

> **Switching a device by hand is followed, not fought.** If a managed device is switched on or off outside the integration (wall switch, app, another automation), that becomes its new request and grant. A device that is already on when you add it keeps running. A device switched on by hand still counts in balancing: when the measurement shows import over target it can be shed like any other, and it comes back on once there is room. Turn the device's **Managed** switch off to keep the integration from ever switching it.

### How balancing works

- **Over target:** granted devices are shed, least important first (equal priority: largest first), until import is back under target. Devices whose power sensor shows they draw nothing are skipped.
- **Under target:** one requested device is restored per cycle, most important first, when headroom ≥ its nominal power + hysteresis. Nothing is restored while an earlier change hasn't shown in the measurement yet (latency).
- **Priority:** if the most important waiting device doesn't fit, less important running devices are shed to make room for it.
- **Shared power supply:** one load for the balancer; see above.
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
| `sensor.<name>_granted_power` | Power the device draws: its power sensor, or its nominal power while it is on (0 when off). Not for a device on a shared supply. |
| `sensor.<name>_state` | `idle` / `running` / `shed` / `override`; attributes priority, reason, last change, `power_supply` |
| `switch.<name>_managed` | Off: the device is left alone (its power still counts in grid import) |
| `climate.<name>_power_managed` / `water_heater.<name>_power_managed` | Climate / Water heater devices: the stand-in to drive instead of the device (see above) |

Each shared power supply has `binary_sensor.<name>_granted`, `sensor.<name>_granted_power` (its power sensor, or its nominal power while any of its devices is on) and `sensor.<name>_state` (attribute `devices`).

## Services and events

| Service | Fields |
|---|---|
| `house_power_manager.request_power` | `device` (device ID or name), `power` (adjustable devices, later) |
| `house_power_manager.release_power` | `device` |
| `house_power_manager.set_priority` | `device`, `priority` (1–100) |
| `house_power_manager.rebalance` | – |

Events `house_power_manager_device_shed` and `house_power_manager_device_restored` carry `device`, `device_id`, `devices` (for a shared supply: its devices), `reason` (`over_target`, `emergency`, `preempted`, `headroom`), `power` and `grid_import`.

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
