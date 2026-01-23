---
name: homeassistant-integration-patterns
description: Project-specific patterns for HomeWizard Instant (config flow, coordinator, entities, translations)
---

# Home Assistant Integration Patterns (HomeWizard Instant)

This skill helps you make correct, repo-consistent changes to this Home Assistant custom integration.

## When to Use

- Adding/changing sensors
- Changing discovery/config flow behavior
- Updating coordinator error handling
- Working on translations or diagnostics

## Quick Map

| Task | File(s) |
|---|---|
| Setup / session / coordinator wiring | `custom_components/homewizard_instant/__init__.py` |
| Config flow (user, zeroconf, dhcp, reauth, reconfigure) | `custom_components/homewizard_instant/config_flow.py` |
| Central polling | `custom_components/homewizard_instant/coordinator.py` |
| Entities (sensor descriptions, external devices) | `custom_components/homewizard_instant/sensor.py` |
| Device registry identifiers (avoid collisions with official integration) | `custom_components/homewizard_instant/entity.py` |
| Diagnostics redaction | `custom_components/homewizard_instant/diagnostics.py` |
| Text / translations | `custom_components/homewizard_instant/strings.json`, `custom_components/homewizard_instant/translations/en.json` |

## Core Rules

1. **Coordinator-only I/O**
   - Never add per-entity HTTP calls.
   - Read everything from `HWEnergyDeviceUpdateCoordinator.data`.

2. **Async-only**
   - Use Home Assistant’s shared aiohttp session (`async_get_clientsession(hass)`).

3. **Avoid unavailable clutter**
   - Only create entities if the device actually provides data.
   - Use `has_fn` in `HomeWizardSensorEntityDescription` to gate creation.

4. **Use translation-aware errors**
   - In the coordinator, raise `UpdateFailed(..., translation_domain=DOMAIN, translation_key=...)`.

## Adding a new sensor

Steps:
1. Find the value on `coordinator.data` (a `homewizard_energy.models.CombinedModels`).
2. Add a new `HomeWizardSensorEntityDescription` in `SENSORS`:
   - `key`: stable identifier
   - `translation_key`: preferred over hardcoded names
   - `has_fn`: ensure the field exists / is not `None`
   - `value_fn`: return the exact value (or timestamp)
3. If user-facing, add translation in `translations/en.json` under `entity.sensor.<translation_key>.name`.

## External meters (gas/water/heat)

- External devices are surfaced via `measurement.external_devices`.
- Supported types are mapped in `EXTERNAL_SENSORS`.
- Normalize `m3` to `UnitOfVolume.CUBIC_METERS`.

## Common pitfalls

- Polling more than once per cycle (stresses device).
- Creating a new HTTP session (leaks resources and breaks HA patterns).
- Creating entities for missing fields (causes “unavailable spam”).
- Breaking unique IDs (must remain stable across upgrades).
