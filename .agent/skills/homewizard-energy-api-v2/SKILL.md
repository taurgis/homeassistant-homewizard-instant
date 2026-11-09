---
name: homewizard-energy-api-v2
description: How this integration talks to HomeWizard P1 meters over the authenticated API v2 WebSocket and REST endpoints, and how to handle their errors
---

# HomeWizard Energy API v2 (WebSocket + python-homewizard-energy)

The integration uses two transports. Both verify the certificate chain against HomeWizard's appliance CA. Neither pins the TLS hostname (`appliance/p1dongle/<serial>`). Real meters have been seen with that name only in the common name and with no subjectAltName, and some certificates do not verify as that hostname at all. Identity is the serial from the device payload, compared case-insensitively with the config-entry unique id. Passing `identifier` to `HomeWizardEnergyV2` turns a certificate mismatch into a failed handshake; do not pass it. The official Home Assistant integration constructs `HomeWizardEnergyV2(ip, token=token)`.

- **REST via `homewizard_energy.HomeWizardEnergyV2`**: pairing, identity checks and token revocation, all in `config_flow.py`.
- **WebSocket via `websocket.HomeWizardWebSocket`**: one `wss://<host>/api/ws` connection that authenticates with the token and subscribes to `device`, `measurement` and `system`. The library's models (`Device`, `Measurement`, `System`) parse each frame. The socket context has `check_hostname` off and `CERT_REQUIRED` on. A `device` frame whose serial does not match the entry, ignoring case, closes the stream.

## When to Use

- You change how device data is received or validated
- You debug pairing, token, TLS or reconnect behaviour
- You add data points (check whether an existing topic already carries them first)

## Key Calls

| Call | Where | Purpose |
|---|---|---|
| `HomeWizardEnergyV2.get_token(name)` | `async_request_token` | Pairing after the physical button press. `DisabledError` means the button was not pressed. |
| `HomeWizardEnergyV2.device()` | `async_try_connect` | Identity check with a token (needs a token; there is no unauthenticated call). |
| `HomeWizardEnergyV2.delete_token()` | `async_revoke_token` | Best-effort cleanup when the entry is removed. |
| `HomeWizardWebSocket.connect()` / `receive()` | coordinator | Initial snapshot, then push updates. |

## Error Handling Contract

- `UnauthorizedError`: token rejected. Setup raises `ConfigEntryAuthFailed`; the running reader starts reauth.
- `RequestError`, `ClientError`, `TimeoutError`, parse errors: mark entities unavailable with the `communication_error` translation key and reconnect with 1-60 s backoff.
- Any other exception in the reader: logged once with a traceback, then handled like a connection error. The reader must never die silently.
- Invalid or null **fields** are dropped individually (`_sanitize`); only unparseable **frames** count toward the 30-frame invalid limit.
- No measurement for `STALE_AFTER` (60 s) while the socket is up: entities become unavailable (`stale_data`) until measurements resume. The check runs every 5 s.
- A measurement frame on the open socket refreshes freshness even when the payload is unchanged, and the coordinator asks entities to write state so `last_reported` moves. Do not set `force_update`. The snapshot replayed during `connect()` does not refresh freshness when it equals the previous measurement.

## Device Limits

- One WebSocket per config entry. Do not add REST polling next to it.
- Keep `PARALLEL_UPDATES = 1`.

## When NOT to Use

- Don't call REST endpoints from the coordinator loop; the WebSocket already carries all sensor data.
