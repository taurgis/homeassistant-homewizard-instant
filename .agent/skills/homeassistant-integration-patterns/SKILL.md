---
name: homeassistant-integration-patterns
description: Project-specific patterns for the HomeWizard Instant integration (config flow, coordinator, entities, translations)
---

# Home Assistant Integration Patterns (HomeWizard Instant)

This skill helps you make correct, repo-consistent changes to this Home Assistant custom integration.

## When to Use

- Adding/changing sensors
- Changing discovery/config flow behavior
- Updating coordinator error handling
- Working on translations or diagnostics
- Ensuring changes meet Home Assistant integration quality expectations

## Quick Map

| Task | File(s) |
|---|---|
| Setup / teardown / coordinator wiring | `custom_components/homewizard_instant/__init__.py` |
| Config flow (user, zeroconf, dhcp, reauth, reconfigure) | `custom_components/homewizard_instant/config_flow.py` |
| WebSocket reader, reconnect backoff, stale watchdog | `custom_components/homewizard_instant/coordinator.py` |
| WebSocket transport and frame validation | `custom_components/homewizard_instant/websocket.py` |
| Sensors + external meters | `custom_components/homewizard_instant/sensor.py` |
| Base entity / device registry identifiers | `custom_components/homewizard_instant/entity.py` |
| Diagnostics redaction | `custom_components/homewizard_instant/diagnostics.py` |
| Constants (including `STALE_AFTER`) | `custom_components/homewizard_instant/const.py` |
| Text / translations | `custom_components/homewizard_instant/strings.json`, `custom_components/homewizard_instant/translations/en.json` |

## Core Rules

1. **Coordinator-only I/O**
   - Never add per-entity API calls.
   - Read everything from `HWEnergyDeviceUpdateCoordinator.data`.
   - One WebSocket per entry; updates are pushed (`update_interval` stays `None`).
   - Keep `PARALLEL_UPDATES = 1` in `sensor.py`.

2. **Async-only**
   - Only do async I/O; never block the event loop.
   - Use `async_get_clientsession(hass)` for both `HomeWizardEnergyV2` and the WebSocket.
   - Build SSL contexts in the executor (`create_ssl_context`).

### Coordinator Error Handling
```python
except UnauthorizedError:
    # setup: raise ConfigEntryAuthFailed; reader: async_start_reauth
except CONNECTION_ERRORS as err:
    raise UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="communication_error",
        translation_placeholders={"error": str(err)},
    ) from err
```
Log a lost connection once (warning), retries at debug, and recovery once (info).

3. **Avoid unavailable clutter**
   - Create entities only when `has_fn(data)` is true; the platform listener adds sensors whose data appears later.
   - For external devices, only create entities when `measurement.external_devices` has a supported type.

4. **Token recovery**
   - Legacy entries without a token raise `ConfigEntryAuthFailed`, which starts pairing through reauth.
   - A token rejected at runtime starts reauth; the entry keeps its unique ID and entity IDs.

5. **Stable identifiers**
   - Config entry unique ID: `f"{DOMAIN}_{product_type}_{serial}"`.
   - Sensor unique IDs: `f"{coordinator.config_entry.unique_id}_{description.key}"`.
   - Device registry identifiers must remain `DOMAIN`-prefixed to avoid collisions.
   - Keep `_attr_has_entity_name = True` and set `device_info` for grouping.

## Adding a new sensor

Steps:
1. Find the value on `coordinator.data` (`CombinedModels` from `python-homewizard-energy`).
2. Add a `HomeWizardSensorEntityDescription` to `SENSORS` in `sensor.py`.
3. Use `has_fn` to conditionally create entities (avoids permanent unavailable state).
4. Keep the unique ID stable (`config_entry.unique_id` + sensor key).
5. Add translation keys in `translations/en.json` (and keep `strings.json` in sync).
6. Use `suggested_display_precision` for numeric sensors.
7. For external devices, use `EXTERNAL_SENSORS` and keep `m3` to `UnitOfVolume.CUBIC_METERS` normalization.

### Entity Patterns (Mandatory for New Integrations)

```python
class HomeWizardSensorEntity(HomeWizardEntity, SensorEntity):
    _attr_has_entity_name = True  # MANDATORY

    def __init__(self, coordinator, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.unique_id}_{description.key}"
        )
```

### EntityDescription Pattern (Recommended)

```python
@dataclass(kw_only=True)
class HomeWizardSensorEntityDescription(SensorEntityDescription):
    enabled_fn: Callable[[CombinedModels], bool] = lambda _: True
    has_fn: Callable[[CombinedModels], bool]
    value_fn: Callable[[CombinedModels], StateType | datetime]

SENSORS: tuple[HomeWizardSensorEntityDescription, ...] = (
    HomeWizardSensorEntityDescription(
        key="active_power_w",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        has_fn=lambda data: data.measurement.power_w is not None,
        value_fn=lambda data: data.measurement.power_w,
    ),
)
```

### Icon Translations (Preferred over `icon` property)
Create `icons.json`:
```json
{
  "entity": {
    "sensor": {
      "battery_soc": {
        "default": "mdi:battery",
        "state": {
          "100": "mdi:battery",
          "50": "mdi:battery-50"
        }
      }
    }
  }
}
```

### Entity Categories
- `EntityCategory.DIAGNOSTIC` - WiFi RSSI, WiFi strength, meter metadata
- `EntityCategory.CONFIG` - Settings the user can change
- Set `entity_registry_enabled_default = False` for rarely-used sensors

### State Classes for Energy Sensors
- `SensorStateClass.MEASUREMENT` - Instantaneous values (power, temperature)
- `SensorStateClass.TOTAL` - Values that can increase/decrease (net energy)
- `SensorStateClass.TOTAL_INCREASING` - Only increases, resets to 0 (lifetime energy)
- Use `SensorDeviceClass.ENERGY_STORAGE` for battery capacity (stored Wh)

## Common pitfalls

- Adding per-entity network requests instead of using coordinator data.
- Adding REST polling next to the WebSocket, or lowering `STALE_AFTER` below a 10 s meter interval.
- Increasing request parallelism above `PARALLEL_UPDATES = 1`.
- Breaking unique IDs or dropping `DOMAIN`-prefixed device identifiers.
- Editing only `translations/en.json` without keeping `strings.json` aligned.

## Discovery and IP Changes

- DHCP and zeroconf should update existing entries when the same device appears on a new IP.
- Do not add fallback discovery logic inside coordinator update loops.
- Keep config flow dedup keyed on the integration unique ID, not host/IP.
