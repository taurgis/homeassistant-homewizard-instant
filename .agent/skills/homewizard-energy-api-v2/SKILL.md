---
name: homewizard-energy-api-v2
description: Guidance for implementing and migrating to HomeWizard local API v2, including auth, versioning, and measurement/system usage
---

# HomeWizard Energy API v2

This skill provides practical implementation guidance for HomeWizard local **API v2** in the context of this repository.

Use this skill when adding v2 support or planning migration from the current v1-based path.

## When to Use

- You need to design or review v2 authentication/token handling
- You are mapping v1 entities to v2 measurement fields
- You are implementing v2 polling or websocket subscription behavior
- You are handling v2-specific errors and API version negotiation

## Core v2 Concepts

- Base path style: `/api/...` (not `/api/v1/...`)
- Version negotiation: set request header `X-Api-Version: 2`
- Authentication: bearer token in `Authorization: Bearer <token>`
- Transport: HTTPS with certificate validation guidance in the official docs

## Auth Workflow (Required for v2)

1. Start user creation: `POST /api/user` with preferred username.
2. Handle waiting state: if button is not pressed yet, API can return `403 user:creation-not-enabled`.
3. Ask user to press the device button and retry during the activation window.
4. Store returned token securely and use it in all subsequent requests.

For Home Assistant UX, this should be modeled as a guided setup or reauth step rather than a single-shot failure.

## Endpoint Quick Reference

- Measurement: `GET /api/measurement`
- System: `GET /api/system`, `PUT /api/system`
- Identify: `PUT /api/system/identify`
- Reboot: `PUT /api/system/reboot`
- Batteries (if supported): `GET /api/batteries`, `PUT /api/batteries`
- Telegram: `GET /api/telegram`
- WebSocket: `wss://<device>/api/ws`

## v1 to v2 Field Mapping (Common Cases)

- `total_power_import_kwh` -> `energy_import_kwh`
- `total_power_export_kwh` -> `energy_export_kwh`
- `active_power_w` -> `power_w`
- `active_current_a` -> `current_a`
- `active_voltage_v` -> `voltage_v`
- `active_tariff` -> `tariff`

Keep defensive parsing because v2 can omit fields that are not available on a specific device or moment.

## Error Handling Guidance

Treat v2 error codes as actionable UX states.

| v2 error | Meaning | Suggested handling |
|---|---|---|
| `user:unauthorized` | Missing/invalid token | Trigger reauth/token refresh path |
| `user:creation-not-enabled` | User has not pressed button | Show explicit instruction and retry path |
| `request:api-version-not-supported` | Requested API major not supported | Fallback or block with clear version guidance |

## Polling and Realtime Strategy

- Keep coordinator-first architecture and avoid per-entity requests.
- If v2 polling is used, keep request count minimal per cycle.
- Consider websocket updates (`/api/ws`) for lower-latency updates without adding HTTP load.
- Preserve the repository's stability constraints when operating at 1s update cadence.

## Discovery and Compatibility Notes

- v1 and v2+ use different mDNS service names in docs (`_hwenergy._tcp` vs `_homewizard._tcp`).
- Device support for v2 varies by product and firmware; verify using the official support matrix.
- Versioning follows semver-style expectations; older API versions can be removed.

## Relationship to Existing Skill

- Current implementation behavior and `python-homewizard-energy` v1 usage: `../homewizard-energy-api-v1/SKILL.md`
- Use both skills together during migration planning and code review.

## Examples

- Token onboarding and authenticated request flows: `references/EXAMPLES.md`
- Websocket auth and subscription message examples: `references/EXAMPLES.md`

## Reference

- Endpoint, headers, and error-code quick reference: `references/API-V2-REFERENCE.md`
- Discovery/versioning and support-matrix pointers: `references/API-V2-REFERENCE.md`

## Official References

- API v2 category: https://api-documentation.homewizard.com/docs/category/api-v2/
- Authorization (token flow): https://api-documentation.homewizard.com/docs/v2/authorization/
- Versioning and `X-Api-Version`: https://api-documentation.homewizard.com/docs/versioning/
- v2 measurement: https://api-documentation.homewizard.com/docs/v2/measurement/
- v2 system: https://api-documentation.homewizard.com/docs/v2/system/
- v2 websocket: https://api-documentation.homewizard.com/docs/v2/websocket/
- Discovery service types: https://api-documentation.homewizard.com/docs/discovery/
- Device/API support matrix: https://api-documentation.homewizard.com/docs/introduction/#devices
