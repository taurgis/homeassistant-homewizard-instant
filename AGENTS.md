# Agent Protocol: HomeWizard Instant (Home Assistant Custom Integration)

This repository contains a Home Assistant custom integration for **HomeWizard P1 meters**.

The project’s primary differentiator is **near real-time polling**: it refreshes device data every **1 second**.

## What this integration is (and is not)

- **Domain**: `homewizard_instant` (intentionally different from the official `homewizard` integration).
- **Scope**: Only **P1 meters** are supported.
- **Transport**: HomeWizard local API (API v1 via `python-homewizard-energy`).
- **Pattern**: `local_polling` using a single `DataUpdateCoordinator`.

## Architectural constraints you must respect

### 1) Keep polling centralized
- Do **not** add per-entity polling.
- All entities must read from `HWEnergyDeviceUpdateCoordinator.data`.
- `PARALLEL_UPDATES = 1` is intentional to avoid stressing the device.

### 2) Async-only I/O
- Never use blocking I/O.
- Use the Home Assistant-managed aiohttp session via `async_get_clientsession(hass)`.
- Do not create a new `ClientSession` per request.

### 3) Device resource limits
HomeWizard P1 hardware has limited connection and throughput headroom.
- Avoid concurrent requests.
- Prefer one request per update cycle.
- Be careful when adding new API calls; if you need additional data, try to source it from `api.combined()` first.

### 4) API-disabled handling (403/Disabled)
This integration explicitly supports a “Local API disabled” recovery path.
- `DisabledError` triggers a config entry reload and starts re-auth to guide the user to re-enable the API.
- Do not turn expected connectivity failures into noisy stack traces.

## Code map (where to implement changes)

| Concern | File | Notes |
|---|---|---|
| Setup / teardown | `custom_components/homewizard_instant/__init__.py` | Builds `HomeWizardEnergyV1`, creates coordinator, forwards platforms |
| Config flow | `custom_components/homewizard_instant/config_flow.py` | User input, zeroconf, dhcp, reauth, reconfigure |
| Polling + error handling | `custom_components/homewizard_instant/coordinator.py` | Single source of truth; raises `UpdateFailed` with translation keys |
| Entities | `custom_components/homewizard_instant/sensor.py` | Uses descriptions + `has_fn` to avoid creating invalid entities |
| Base entity & device registry | `custom_components/homewizard_instant/entity.py` | Uses DOMAIN-prefixed identifiers to avoid collisions |
| Diagnostics | `custom_components/homewizard_instant/diagnostics.py` | Redacts IP/serial/token/etc |
| Text/translations | `custom_components/homewizard_instant/strings.json` and `custom_components/homewizard_instant/translations/en.json` | Keep keys stable |

## Adding or changing sensors

Preferred pattern:
1. Extend `SENSORS` in `sensor.py` using `HomeWizardSensorEntityDescription`.
2. Gate entity creation using `has_fn` so devices that don’t provide a field don’t get “unavailable clutter”.
3. Use correct `device_class`, `state_class`, and `native_unit_of_measurement`.
4. Add/adjust translation keys in `translations/en.json` (and keep `strings.json` in sync if needed).

External devices (gas/water/heat meters):
- Use `EXTERNAL_SENSORS` and `HomeWizardExternalSensorEntity`.
- Unit mapping: API may return `m3`; normalize to `UnitOfVolume.CUBIC_METERS`.

## Polling interval

- The update interval is hard-coded in `custom_components/homewizard_instant/const.py` as `UPDATE_INTERVAL = timedelta(seconds=1)`.
- Changes to this value alter the core purpose of this repository; only adjust if explicitly requested.

## Quality and style expectations

- Follow Home Assistant patterns for coordinator-based integrations.
- Keep changes minimal and consistent with existing style.
- Prefer translation-aware errors (`UpdateFailed(..., translation_domain=DOMAIN, translation_key=...)`).

## Local development

- Use the VS Code task "Start Home Assistant" to run a dev instance.
- When troubleshooting, check Home Assistant logs for `homewizard_instant` messages.
