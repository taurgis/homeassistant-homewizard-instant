# HomeWizard Instant (Home Assistant custom integration)

This is a custom integration for **HomeWizard P1 meters** that refreshes data every **1 second**.

Home Assistant’s official HomeWizard integration is documented here:
- https://www.home-assistant.io/integrations/homewizard/

## Why this exists

This custom integration keeps a **1 second** coordinator interval to deliver near real-time power readings.

Because this integration uses a different domain (`homewizard_instant`), it can run **side-by-side** with the official integration.

## Installation

### Manual

1. Copy the folder `custom_components/homewizard_instant` into your Home Assistant config folder:
   - `<config>/custom_components/homewizard_instant`
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration**.
4. Search for **HomeWizard Instant** and follow the steps.

### HACS

If you add this repository to HACS as a custom repository (category: **Integration**), HACS will install it under `custom_components/homewizard_instant`.

## Configuration

This integration is configured via the UI. The only required parameter is:

- **IP address**: the local IP address of your HomeWizard P1 meter.

Depending on device firmware/API mode, setup can require pressing the physical button on the HomeWizard device so Home Assistant can obtain a local API token.

The integration will store the device by a domain-prefixed unique ID to avoid collisions with the official integration.

### Installation parameters

- **Local API must be enabled** in the HomeWizard app for your P1 meter.
- The device must be reachable on your local network.

## Data updates

All entities read from one coordinator data source.

- Baseline behavior: poll the HomeWizard local API every **1 second**.
- On v2-capable devices with token auth, websocket events trigger immediate refreshes.
- While websocket updates stay healthy, regular poll fetches are skipped to reduce duplicate requests.
- If websocket activity becomes stale or disconnects, polling continues as fallback.

## Development: Dummy P1 Meter Simulator

For local development in this repository's devcontainer, Home Assistant and a
HomeWizard-like P1 simulator run as separate Docker services via
`.devcontainer/docker-compose.yml`.

Both services are attached to an internal-only Docker network
(`homewizard_instant_isolated`) so they cannot access your main network.

The simulator exposes API v1 + API v2 endpoints and generates realistic measurements:

- 1 Hz updates
- Household load profile with morning/evening peaks and random appliance spikes
- Solar production with daylight curve, clouds, and strong summer vs winter behavior
- Net import/export power with cumulative energy counters and tariff switching
- v2 token flow without physical button press (`POST /api/user` auto-authorizes)
- v2 websocket stream on `wss://dummy-p1:15510/api/ws`

### Start it

Rebuild/reopen the devcontainer. Compose starts both services:

- `homeassistant-dev`
- `dummy-p1`

The `dummy-p1` service is built from `.devcontainer/dummy-p1.Dockerfile`
(`python:3.13-slim` + `aiohttp` + `openssl`) so it stays independent from the
Home Assistant image.

You can still run the simulator manually (outside Compose) if needed:

```bash
python3 .devcontainer/dummy_p1_meter.py --host 0.0.0.0 --port 15510
```

Then configure this integration with host:

- `dummy-p1:15510`

### Useful simulator endpoints

V2 (`https://dummy-p1:15510`):

- `POST /api/user` (returns token without button press in default dev mode)
- `GET /api` (returns `401` without bearer token, as expected for v2 detection)
- `GET /api/measurement`
- `GET/PUT /api/system`
- `GET /api/ws` (websocket handshake + `subscribe` events)

V1 compatibility:

- `GET /api/v1/data`
- `GET /api/v1/system`
- `GET /api/v1/telegram`

Dev-only helpers:

- `GET /sim/state`
- `PUT /sim/api_enabled` with JSON body `{"enabled": false}` to simulate API-disabled responses (`403`).
- `PUT /sim/v2_auto_authorize` with JSON body `{"enabled": false}` to simulate the v2 "button not pressed" path (`403 user:creation-not-enabled`).

Note:
The devcontainer sets `HOMEWIZARD_INSTANT_ALLOW_INSECURE_V2=1` for `dummy-p1` so
the integration can use the simulator's self-signed TLS certificate in development.

## Supported devices

- HomeWizard **P1 meters** only.

## Supported functions

- Near real-time power and energy sensors (import/export totals and tariffs).
- Voltage, current, frequency, and power factor sensors (when provided by the device).
- Device diagnostics (firmware, DSMR version, Wi-Fi details, uptime).
- External meters connected to the P1 meter (gas, water, heat), when reported by the API.

## Examples

- Use the **Average demand** sensor in dashboards to visualize short-term power usage.
- Add the **Energy import** sensor to the Energy dashboard for long-term consumption tracking.

## Troubleshooting

- **API disabled**: Enable the local API in the HomeWizard app and reauthenticate.
- **Device unreachable**: Confirm the IP address and ensure the device is online.
- **Discovery not found**: Add the integration manually and provide the IP address.

## Known limitations

- Polling every second increases local network traffic and device load.
- Only P1 meters are supported; other HomeWizard devices are not supported.
- This integration does not register services or actions.

## Removal

1. Remove the integration from **Settings → Devices & services**.
2. If installed manually, delete `custom_components/homewizard_instant`.
3. If installed via HACS, remove the repository from HACS and restart Home Assistant.

