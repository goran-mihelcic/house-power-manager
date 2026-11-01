# House Power Manager — Claude Code brief

Home Assistant custom integration (`house_power_manager`) that keeps total **grid import** below the connection limit by granting, throttling and shedding controllable loads in priority order.

Repo: `goran-mihelcic/house-power-manager` (private). Integration lives in `custom_components/house_power_manager/`.

---

## 1. Problem

- The house has a grid connection with a hard import limit (currently **7.6 kW**). Above it, the limiter cuts supply.
- The limit applies to **grid import**, not house load. A SolaX X1-Hybrid-G4 (PV only, no battery, 7.28 kW rated) reduces import when producing, so the house can draw more than 7.6 kW on sunny days.
- Several loads have known or adjustable power. The integration must decide which may run, and at what power, so that measured import stays under a target below the limit.
- Other integrations and automations (Room Climate, EV charging, pool, etc.) will **request** power through this integration instead of switching devices directly. This integration is the single authority that actually turns devices on, off, up or down.

## 2. Site context

| Item | Detail |
|---|---|
| Import measurement | SolaX grid power sensor; CT clamp on the main connection before the main breaker. Sign convention must be configurable (import positive vs negative). |
| Inverter | SolaX X1-Hybrid-G4, single phase, no battery. Export limit 3.6 kW (not this integration's concern). |
| EV charger | Planned Teltonika TeltoCharge 22 kW (1P/3P switchable), car BYD Seal 6 DM-i. Control via a `number` entity (amps) and/or switch; integration method TBD (OCPP / Modbus / native). Minimum 6 A. |
| Heat pump | LG Therma V monobloc HM091M.U43 via LG ThinQ **cloud** (slow, unreliable commands). |
| Other loads | Bathroom electric floor heating (switch), LG ACs (ThinQ cloud), pool pump, possibly others later. |
| HA host | Mini PC, YAML config in a separate GitOps repo (`goran-mihelcic/home-assistant-config`). Sister project: `room_climate` custom integration. |

## 3. Functional requirements

1. **Config entry (global):** grid import sensor, sign convention, import limit (W), target margin (W), update interval, optional per-phase handling (see open questions).
2. **Managed devices** (added via options flow / config subentries, one per device):
   - `name`, `priority` (1 = shed first, i.e. lowest importance)
   - `type`: `switch` (fixed power) or `adjustable` (continuous / stepped)
   - `control_entity`: switch / number / climate entity the integration drives
   - `nominal_power` (fixed) or `min_power`, `max_power`, `step` plus a mapping to the control unit (e.g. W ↔ amps × 230 V × phases)
   - optional `power_sensor` giving the device's real consumption (preferred over nominal when available)
   - `min_on_time`, `min_off_time` (protect compressors, avoid EV session churn)
   - `latency` hint (cloud devices respond slowly; they should be shed with extra margin and not counted as "freed" until confirmed)
3. **Request model.** Each device has a *requested* state and a *granted* state.
   - Requests come from services or from a per-device "requested" switch / number.
   - Last request is persisted and restored on restart (`RestoreEntity` or `Store`).
   - Granted state is what is applied to `control_entity`. Requests never touch the device directly.
4. **Balancing loop** (coordinator, event-driven on import sensor change plus periodic tick):
   - `headroom = target − measured_import`, where `target = limit − margin`.
   - **Over target:** reduce in ascending priority. Adjustable devices step down to `min_power` first, then off; fixed devices turn off. Act fast (seconds) and shed only as much as needed, accounting for power already being shed but not yet reflected in the measurement.
   - **Under target with spare headroom:** restore in descending priority. Restore only if headroom ≥ device's expected power + hysteresis, respect `min_off_time`, and wait for the measurement to settle after each restore (one step per cycle).
   - **Emergency:** if import exceeds the hard limit itself, shed immediately regardless of `min_on_time`.
   - Never grant a device that has no active request.
5. **Fail-safe:** if the import sensor is `unavailable`/`unknown`/stale for longer than N seconds, move to a conservative state (configurable: hold current grants, or shed everything above a set priority). Raise a repair issue or persistent notification.
6. **Manual override:** per-device "managed" switch. When off, the integration ignores that device but still counts its measured power.

## 4. Entities to expose

Global device:
- `sensor.hpm_grid_import` (normalized W), `sensor.hpm_headroom`, `sensor.hpm_managed_load`
- `sensor.hpm_status` (enum: normal / limiting / emergency / sensor_fault)
- `number.hpm_import_limit`, `number.hpm_margin` (runtime adjustable)
- `switch.hpm_enabled`

Per managed device:
- `switch.<dev>_requested` (or `number.<dev>_requested_power` for adjustable)
- `binary_sensor.<dev>_granted`, `sensor.<dev>_granted_power`
- `sensor.<dev>_state` (enum: idle / running / throttled / shed / override)
- `switch.<dev>_managed`

All entities should carry useful attributes (priority, last change, reason for last action).

## 5. Services (`services.yaml`)

- `house_power_manager.request_power` — `device`, optional `power` (W) for adjustable devices
- `house_power_manager.release_power` — `device`
- `house_power_manager.set_priority` — `device`, `priority`
- `house_power_manager.rebalance` — force an immediate cycle

Fire events `house_power_manager_device_shed` / `_device_restored` with reason, for automations and logbook.

## 6. Technical conventions

- Target the current HA release; Python 3.13; fully async; type hints throughout.
- `config_flow: true`, no YAML configuration. Use config subentries for devices if supported by the target HA version, otherwise the options flow.
- `DataUpdateCoordinator` (or a custom coordinator) holding all balancing state. Keep the decision logic in a **pure, HA-independent module** (`balancer.py`) taking inputs (measurement, device list, timestamps) and returning actions. This makes it unit-testable.
- Use `async_track_state_change_event` on the import sensor; debounce rapid updates.
- Logging via `_LOGGER`; every shed/restore logged at INFO with the reason.
- `strings.json` + `translations/en.json` for all flows and entities.
- `hacs.json` present; repo should pass `hassfest` and HACS validation.
- Tests: `pytest` + `pytest-homeassistant-custom-component`. Unit-test `balancer.py` heavily (over-limit shedding order, EV throttle before off, hysteresis, min on/off times, sensor fault, PV-covered loads).
- GitHub Actions: hassfest, HACS validation, pytest, ruff.

## 7. Milestones

1. Skeleton: manifest, config flow for global settings, import sensor normalization, global sensors. Loads in HA.
2. `balancer.py` with full unit tests (no HA dependency).
3. Device management (fixed-power switches only), request/grant entities, services, persistence.
4. Adjustable devices (EV amps mapping, step-down before off).
5. Fail-safe, repairs, events, diagnostics (`diagnostics.py`).
6. CI, README with example automations (e.g. Room Climate requesting floor heating; EV charging requesting power).

## 8. Open questions (ask before assuming)

- Is the 7.6 kW limit single-phase or per-phase on a three-phase connection? If three-phase, the balancer needs per-phase import and per-phase device assignment.
- How fast does the limiter trip (instant vs time-delayed overload)? This sets how aggressive shedding must be.
- TeltoCharge control method in HA (OCPP, Modbus, or other), and whether 1P/3P switching should be managed by this integration.
- Should the heat pump be controlled directly (on/off via ThinQ) or by lowering its setpoint / switching mode, given cloud latency?
- Desired behaviour on HA restart: re-apply last grants immediately, or start conservative and ramp up?
