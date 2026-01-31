````skill
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
| Setup / teardown / coordinator wiring | `__init__.py` |
| Config flow (user, dhcp, zeroconf) | `config_flow.py` |
| Central polling (1s interval) | `coordinator.py` |
| Sensors | `sensor.py` (EntityDescription pattern) |
| Base entity & device registry | `entity.py` |
| Diagnostics | `diagnostics.py` |
| Text / translations | `strings.json`, `translations/en.json` |
| Icons | `icons.json` |
| Constants | `const.py` |
| Helpers | `helpers.py` |

## Core Rules

1. **Coordinator-only I/O**
   - Never add per-entity HTTP/API calls.
   - Read everything from `HWEnergyDeviceUpdateCoordinator.data`.
   - All data is fetched via `api.combined()` in a single call per update cycle.
   - Set `PARALLEL_UPDATES = 1` to avoid concurrent requests to the device.

2. **Async-only**
   - Only do async I/O; never block the event loop.
   - Use `async_get_clientsession(hass)` for the shared aiohttp session.

### Coordinator Error Handling
```python
async def _async_update_data(self) -> DeviceResponseEntry:
    try:
        return await self.api.combined()
    except RequestError as ex:
        raise UpdateFailed(
            ex, translation_domain=DOMAIN, translation_key="communication_error"
        ) from ex
    except DisabledError as ex:
        # Trigger reauth flow when API is disabled
        if not self.api_disabled:
            self.api_disabled = True
            if self.data is not None:
                self.hass.config_entries.async_schedule_reload(
                    self.config_entry.entry_id
                )
        raise UpdateFailed(
            ex, translation_domain=DOMAIN, translation_key="api_disabled"
        ) from ex
```

3. **Avoid unavailable clutter**
   - Only create entities when there's a corresponding data key in coordinator output.
   - Use `has_fn` on entity descriptions to gate entity creation.

4. **Use translation-aware errors**
   - Config flow errors should use keys defined in `strings.json`.
   - Coordinator errors use `translation_domain` and `translation_key`.

5. **Stable identifiers**
   - Use serial number-based unique IDs for entities and devices.
   - Use DOMAIN-prefixed identifiers in `entity.py` to avoid conflicts with the official integration.
   - Keep `_attr_has_entity_name = True` and set `device_info` for grouping.

## Adding a new sensor

Steps:
1. Find the value on `coordinator.data` (a `CombinedModels` object from `python-homewizard-energy`).
2. Add a `HomeWizardSensorEntityDescription` to the `SENSORS` tuple in `sensor.py`.
3. Use `has_fn` to conditionally create entities (avoids entities showing unavailable).
4. Keep the `unique_id` stable (unique_id + sensor key).
5. Add translation keys in `translations/en.json` (and keep `strings.json` in sync).
6. Use `suggested_display_precision` for numeric sensors.
7. Only register entities for data keys that exist to avoid permanent `unavailable` noise.

### Entity Patterns (Mandatory for New Integrations)

```python
class HomeWizardSensorEntity(HomeWizardEntity, SensorEntity):
    _attr_has_entity_name = True  # MANDATORY
    
    def __init__(
        self,
        coordinator: HWEnergyDeviceUpdateCoordinator,
        description: HomeWizardSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{description.key}"
```

### EntityDescription Pattern (Used in this repo)

```python
@dataclass(frozen=True, kw_only=True)
class HomeWizardSensorEntityDescription(SensorEntityDescription):
    enabled_fn: Callable[[CombinedModels], bool] = lambda x: True
    has_fn: Callable[[CombinedModels], bool]
    value_fn: Callable[[CombinedModels], StateType | datetime]

SENSORS: Final[tuple[HomeWizardSensorEntityDescription, ...]] = (
    HomeWizardSensorEntityDescription(
        key="active_power_w",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        has_fn=lambda data: data.measurement.power_w is not None,
        value_fn=lambda data: data.measurement.power_w,
    ),
    HomeWizardSensorEntityDescription(
        key="total_power_import_kwh",
        translation_key="total_energy_import_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        has_fn=lambda data: data.measurement.energy_import_kwh is not None,
        value_fn=lambda data: data.measurement.energy_import_kwh or None,
    ),
)
```

### Icon Translations (Preferred over `icon` property)
Create `icons.json`:
```json
{
  "entity": {
    "sensor": {
      "active_power_w": {
        "default": "mdi:flash"
      }
    }
  }
}
```

### Entity Categories
- `EntityCategory.DIAGNOSTIC` - WiFi RSSI, firmware version, meter model, uptime
- `EntityCategory.CONFIG` - Settings the user can change
- Set `entity_registry_enabled_default = False` for rarely-used sensors (e.g., WiFi RSSI)

### State Classes for Energy Sensors
- `SensorStateClass.MEASUREMENT` - Instantaneous values (power, voltage, current)
- `SensorStateClass.TOTAL` - Values that can increase/decrease (net energy)
- `SensorStateClass.TOTAL_INCREASING` - Only increases, resets to 0 (cumulative energy import/export)

## External Devices (Gas/Water/Heat Meters)

The P1 meter can have external devices connected. Use `EXTERNAL_SENSORS` dict and `HomeWizardExternalSensorEntity`:

```python
EXTERNAL_SENSORS = {
    ExternalDevice.DeviceType.GAS_METER: HomeWizardExternalSensorEntityDescription(
        key="gas_meter",
        translation_key="gas_meter",
        suggested_device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_name="Gas meter",
    ),
}
```

**Unit mapping**: API returns `m3`; normalize to `UnitOfVolume.CUBIC_METERS`.

## Common pitfalls

- Adding extra API calls per update loop (device has limited connection headroom at 1 Hz polling).
- Creating entities without `has_fn` leads to many entities showing unavailable.
- Raising raw exceptions instead of translation-aware `UpdateFailed` degrades UX.
- Breaking unique IDs (must remain stable across upgrades and IP changes).
- Using IP addresses in unique IDs instead of serial numbers.
- Not handling `DisabledError` properly (should trigger reauth flow).

## Device Resource Limits

HomeWizard P1 hardware has limited connection and throughput headroom:
- Avoid concurrent requests
- Prefer one request per update cycle
- Be careful when adding new API calls; source data from `api.combined()` first

````
