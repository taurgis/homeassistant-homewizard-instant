# HomeWizard API v1 Reference

Use this reference for endpoint behavior while maintaining the current v1-based integration path.

## Lifecycle Status

- API v1 is in maintenance mode and planned for future removal.
- Keep v1 changes minimal and migration-friendly.

## Endpoint Quick Reference

| Endpoint | Method | Purpose | Notes |
|---|---|---|---|
| `/api/v1/data` | `GET` | Live measurement payload | Fields can be omitted when unavailable |
| `/api/v1/system` | `GET` | Device/system info | Use for diagnostics and capabilities |
| `/api/v1/system` | `PUT` | Update selected system settings | Only send documented writable keys |
| `/api/v1/identify` | `PUT` | Trigger identify signal | Useful during onboarding/troubleshooting |
| `/api/v1/telegram` | `GET` | Raw DSMR telegram | Request `Accept: text/plain` when needed |

## Measurement Payload Notes

- Common fields in v1 payloads include:
  - `total_power_import_kwh`
  - `total_power_export_kwh`
  - `active_power_w`
  - `active_voltage_v`
  - `active_current_a`
  - `active_tariff`
- External utility meter values are modeled via `external`.
- Legacy gas fields are documented for future removal, so prefer `external` where possible.

## Error Shapes

v1 error responses use numeric `id` plus `description`.

Example API-disabled response shape:

```json
{
  "error": {
    "id": 202,
    "description": "API not enabled"
  }
}
```

## HA Integration Mapping

| Condition | Typical v1 response | Integration behavior |
|---|---|---|
| Local API disabled | `403` with error `id: 202` | Raise `UpdateFailed(..., translation_key="api_disabled")` and keep reauth path |
| Transport/request failure | timeout/network/malformed response | Raise `UpdateFailed(..., translation_key="communication_error")` |

## Official References

- API v1 category: https://api-documentation.homewizard.com/docs/category/api-v1
- v1 measurement: https://api-documentation.homewizard.com/docs/v1/measurement
- v1 system: https://api-documentation.homewizard.com/docs/v1/system
- v1 telegram: https://api-documentation.homewizard.com/docs/v1/telegram
- v1 error handling: https://api-documentation.homewizard.com/docs/v1/error-handling
- v1 changelog/lifecycle notes: https://api-documentation.homewizard.com/docs/changelog/#v1
- v1 external meter migration note: https://api-documentation.homewizard.com/docs/v1/measurement#external-devices
