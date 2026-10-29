# Agent Protocol: HomeWizard Instant (Home Assistant Custom Integration)

This repository contains a Home Assistant custom integration for **HomeWizard P1 meters**.

The project’s primary differentiator is **real-time push updates**: every measurement the meter sends over the authenticated **API v2 WebSocket** reaches Home Assistant, without a polling interval.

## What this integration is (and is not)

- **Domain**: `homewizard_instant` (intentionally different from the official `homewizard` integration).
- **Scope**: Only **P1 meters** are supported.
- **Transport**: HomeWizard local API v2. One TLS-verified `wss://<host>/api/ws` connection per entry (`websocket.py`); REST through `HomeWizardEnergyV2` only for pairing, identity checks and token revocation.
- **Pattern**: `local_push` using a single `DataUpdateCoordinator` fed by a background reader task.

## Architectural constraints you must respect

### 1) Keep updates centralized
- Do **not** add per-entity polling or REST polling next to the WebSocket.
- All entities must read from `HWEnergyDeviceUpdateCoordinator.data`.
- `PARALLEL_UPDATES = 1` is intentional to avoid stressing the device.

### 2) Async-only I/O
- Never use blocking I/O.
- Use the Home Assistant-managed aiohttp session via `async_get_clientsession(hass)`.
- Do not create a new `ClientSession` per request.

### 3) Device resource limits
HomeWizard P1 hardware has limited connection and throughput headroom.
- Keep one WebSocket per entry; avoid concurrent requests.
- If you need additional data, check the `device`, `measurement` and `system` topics first.

### 4) Token and connection handling
- Entries without a v2 token raise `ConfigEntryAuthFailed`; pairing happens through reauth (physical button press).
- A token rejected at runtime starts reauth.
- Connection loss: entities unavailable, reconnect with 1-60 s backoff. Log once when the connection drops, once when it recovers, and retries at debug.
- Unexpected exceptions in the reader are logged once with a traceback and then reconnect; the reader must not die silently.
- A silent meter (no measurement for `STALE_AFTER`) marks entities unavailable until measurements resume.
- Invalid or null fields drop only that field; do not reject a whole snapshot for one bad value.
- Do not turn expected connectivity failures into noisy stack traces.

### 5) Home Assistant integration contract
- Config/UI-first: no new YAML setup; keep config/reauth/reconfigure in `config_flow.py`.
- Unique IDs must stay stable and **DOMAIN-prefixed** to avoid collisions with the official integration.
- User-facing text lives in `strings.json` and mirrors to `translations/en.json`.
- Entity classes should set `_attr_has_entity_name = True` when appropriate and expose `device_info` for proper grouping.
- Use `EntityDescription` dataclasses for declarative sensor definitions.
- Loading must be async and non-blocking; any sync work belongs in executor jobs.
- Use `entry.async_on_unload()` for cleanup callbacks.

## Discovery and IP changes

- DHCP discovery updates existing entries with new IPs.
- Unknown devices should abort discovery; do not add fallback discovery inside coordinator updates.

## Code map (where to implement changes)

| Concern | File | Notes |
|---|---|---|
| Setup / teardown | `custom_components/homewizard_instant/__init__.py` | Builds `HomeWizardWebSocket`, creates coordinator, forwards platforms, revokes the token on removal |
| Config flow | `custom_components/homewizard_instant/config_flow.py` | User input, zeroconf, dhcp, reauth, reconfigure |
| Push loop + error handling | `custom_components/homewizard_instant/coordinator.py` | Single source of truth; reconnect backoff, stale watchdog, `UpdateFailed` with translation keys |
| WebSocket transport | `custom_components/homewizard_instant/websocket.py` | Handshake, subscriptions, TLS, per-field validation |
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

## Testing and QA expectations (lightweight)
- Prefer pytest + `pytest-homeassistant-custom-component` for new tests.
- Mock the HomeWizard API client; never hit real devices in tests.
- Cover config flow failures: cannot_connect, invalid_auth/invalid_discovery_info, already_configured.
- Mark coordinator failures with `UpdateFailed` to surface entity unavailability.

## Services and actions

- This integration does not register services or actions.
- Keep it that way unless explicitly requested; add idempotent registration if services are introduced.

## Update rate

- There is no polling interval; the meter pushes measurements (1 s for DSMR 5, 10 s for older meters).
- `STALE_AFTER` in `const.py` (60 s) decides when a silent meter makes entities unavailable. Keep it well above the slowest meter interval; only change it if explicitly requested.

## Quality and style expectations

- Follow Home Assistant patterns for coordinator-based integrations.
- Keep changes minimal and consistent with existing style.
- Prefer translation-aware errors (`UpdateFailed(..., translation_domain=DOMAIN, translation_key=...)`).

## Local development

- `docs/development.md` describes the Docker lab: Home Assistant plus emulated API v2 meters with fault injection.
- On macOS shells, use `python3` for Python commands; `python` may resolve to Python 2.7 or be missing.
- Prefer `python3 -m pip ...` over bare `pip`/`pip3` so installs target the same interpreter used for tests.
- When troubleshooting, check Home Assistant logs for `homewizard_instant` messages.

## Verification after changes

- After code modifications, run the relevant linting and tests configured for this repository.
- If no automated checks are configured, note this explicitly and perform a focused manual verification (e.g., start the dev instance and confirm logs).
- Always run tests, linting, and type checks after every modification.