---
name: homewizard-energy-api-v1
description: How this integration talks to HomeWizard P1 meters via python-homewizard-energy (API v1) and how to handle errors
---

# HomeWizard Energy API v1 (via python-homewizard-energy)

This repository currently uses `python-homewizard-energy` with the HomeWizard local **API v1**. This skill documents the current contract and safe handling patterns.

Official docs now classify v1 as maintenance-only and signal eventual removal. Use this skill for current behavior, and see `../homewizard-energy-api-v2/SKILL.md` for migration planning.

## When to Use

- You are changing how coordinator data is fetched from `HomeWizardEnergyV1`
- You are debugging setup failures, API-disabled flow, or polling failures
- You are adding sensors and need to understand which fields are optional in v1 payloads

## Repository Contract

- Client class: `homewizard_energy.HomeWizardEnergyV1`
- Current calls in this integration:
  - `await api.device()` for config flow validation
  - `await api.combined()` for coordinator polling data
  - `await api.close()` for cleanup
- Polling model stays centralized in the coordinator with `PARALLEL_UPDATES = 1`

## Relevant API v1 Endpoints

- Measurement: `GET /api/v1/data`
- System: `GET /api/v1/system`
- Telegram: `GET /api/v1/telegram`
- Optional system controls: `PUT /api/v1/system`, `PUT /api/v1/identify`

## Data Model Notes That Affect Entities

- Measurement properties can be omitted when not available.
- External utility meter data is present in `external` and should be preferred over legacy gas fields when both are available.
- Keep `has_fn` guards for sensor creation to avoid persistent unavailable entities.

## Error Handling Mapping

Map library errors to Home Assistant user-facing behavior.

| Source | Meaning | Integration behavior |
|---|---|---|
| `DisabledError` | Local API disabled (v1 commonly returns 403 for this state) | Raise `UpdateFailed(..., translation_key="api_disabled")`; keep reauth/recovery path active |
| `RequestError` | Connectivity/transport/request failure | Raise `UpdateFailed(..., translation_key="communication_error")` |

In config flow validation, convert these conditions into user-facing form errors instead of raw stack traces.

## Performance and Safety Rules

- Keep one request per update interval whenever possible.
- Do not add per-entity HTTP calls.
- Keep all I/O async and use Home Assistant's shared `aiohttp` session.

## Migration Readiness (v1 to v2)

If you touch sensor mapping, keep this v1-v2 naming map in mind:

- `total_power_import_kwh` -> `energy_import_kwh`
- `active_power_w` -> `power_w`
- `active_current_a` -> `current_a`
- `active_tariff` -> `tariff`

For implementation guidance on v2 auth, headers, and websocket options, use `../homewizard-energy-api-v2/SKILL.md`.

## Examples

- Request and parsing examples: `references/EXAMPLES.md`
- Common Home Assistant integration patterns using `python-homewizard-energy`: `references/EXAMPLES.md`

## Reference

- Endpoint and payload quick reference: `references/API-V1-REFERENCE.md`
- Migration context and lifecycle notes for v1: `references/API-V1-REFERENCE.md`

## When NOT to Use

- Do not bypass `python-homewizard-energy` with ad-hoc direct `aiohttp` calls unless the library cannot expose a required endpoint.
- Do not use this skill as the source of truth for v2 token/auth behavior.

## Official References

- API v1 category: https://api-documentation.homewizard.com/docs/category/api-v1
- v1 measurement: https://api-documentation.homewizard.com/docs/v1/measurement
- v1 telegram: https://api-documentation.homewizard.com/docs/v1/telegram
- v1 error handling: https://api-documentation.homewizard.com/docs/v1/error-handling
- v1 changelog/deprecation signal: https://api-documentation.homewizard.com/docs/changelog/#v1
