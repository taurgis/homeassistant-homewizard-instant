# HomeWizard API v1 Examples

Use these examples when working with the current `python-homewizard-energy` v1 path.

## Curl: Read Measurement

```bash
curl -sS "http://<DEVICE_IP>/api/v1/data"
```

## Curl: Read System

```bash
curl -sS "http://<DEVICE_IP>/api/v1/system"
```

## Curl: Read Telegram Text

```bash
curl -sS \
  -H "Accept: text/plain" \
  "http://<DEVICE_IP>/api/v1/telegram"
```

## Home Assistant Pattern: Coordinator Update

```python
from homeassistant.helpers.update_coordinator import UpdateFailed
from homewizard_energy import HomeWizardEnergyV1
from homewizard_energy.errors import DisabledError, RequestError

async def fetch_combined(host: str, session):
    api = HomeWizardEnergyV1(host, session=session)
    try:
        return await api.combined()
    except DisabledError as err:
        raise UpdateFailed(
            err,
            translation_domain="homewizard_instant",
            translation_key="api_disabled",
        ) from err
    except RequestError as err:
        raise UpdateFailed(
            err,
            translation_domain="homewizard_instant",
            translation_key="communication_error",
        ) from err
    finally:
        await api.close()
```

## Field Mapping Example: v1 to Internal Sensor Keys

```python
# v1 payload -> sensor values
state = {
    "energy_import_kwh": measurement.total_power_import_kwh,
    "power_w": measurement.active_power_w,
    "current_a": measurement.active_current_a,
    "tariff": measurement.active_tariff,
}
```

## Notes

- Keep one request path per update cycle.
- Keep `has_fn` style guards for optional fields.
- Prefer `external` meter structures over legacy gas-only keys when available.
