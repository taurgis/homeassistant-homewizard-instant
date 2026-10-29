# HomeWizard Instant (Home Assistant custom integration)

This is a custom integration for **HomeWizard P1 meters** that receives real-time measurement updates over the **HomeWizard API v2 WebSocket**.

Home Assistant’s official HomeWizard integration is documented here:
- https://www.home-assistant.io/integrations/homewizard/

## Why this exists

This integration subscribes to local measurement updates as the P1 meter produces them, without a scheduled polling interval.

Because this integration uses a different domain (`homewizard_instant`), it can run **side-by-side** with the official integration. Both register the meter's MAC address, so Home Assistant shows one device holding the entities of both integrations.

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

After entering the IP address, press the device button when prompted and select Submit within 30 seconds. If the pairing window expires, submit to reopen it, then press the button and submit again.

The integration stores a v2 token under a separate local device user and keeps domain-prefixed unique IDs to avoid conflicts with the official integration.

### Installation parameters

- **Home Assistant 2026.8 or newer** (the first release that ships `python-homewizard-energy` 10.2.0).
- P1 meter firmware supporting **API v2 and `/api/ws`**. The API v1 local-API toggle is not used.
- The device must be reachable on your local network.

## Data updates

One TLS-verified `wss://<host>/api/ws` connection authenticates and subscribes to `device`, `measurement` and `system`. Setup waits for all three initial snapshots. All entities read from the coordinator data.

Disconnects mark entities unavailable and trigger reconnect attempts with backoff from 1 to 60 seconds; the delay only starts over at 1 second after a connection has stayed up for a minute. If the socket stays up but the meter sends no measurement for 60 seconds (for example when the P1 cable is unplugged from the smart meter), sensors become unavailable until a new measurement arrives; reconnecting does not bring them back on the reading the meter replays. A field the meter reports as null, malformed or out of range makes only that sensor unavailable; the rest keep updating. Sensors for fields that first appear after setup are added automatically. Revoked tokens start reauthentication. Unloading cancels the reader and closes the socket.

### Upgrading from API v1

Existing entries prompt for one-time v2 pairing after updating and restarting. Complete that prompt without deleting the entry: sensor IDs remain unchanged, preserving history and dashboard references. Devices without WebSocket support require a firmware update; there is no polling fallback.

## Supported devices

- HomeWizard **P1 meters** only.

## Supported functions

- Near real-time power and energy sensors (import/export totals and tariffs).
- Voltage, current, frequency, and power factor sensors (when provided by the device).
- Device diagnostics (firmware, DSMR version, Wi-Fi details, uptime).
- External meters connected to the P1 meter (gas, water, heat), when reported by the API. A meter the P1 meter no longer reports can be deleted from its device page.

## Examples

- Use the **Average demand** sensor in dashboards to visualize short-term power usage.
- Add the **Energy import** sensor to the Energy dashboard for long-term consumption tracking.

## Troubleshooting

- **Pairing fails**: Reopen the pairing window, press the device button, then submit within 30 seconds.
- **Token revoked**: Complete the reauthentication prompt.
- **Device unreachable**: Confirm the IP address and ensure the device is online.
- **Discovery not found**: Add the integration manually and provide the IP address.

## Known limitations

- Measurement frequency is determined by the physical smart meter. WebSockets cannot make it report faster.
- HomeWizard only pushes some system settings in real time, so the integration reads Wi-Fi signal and uptime once a minute over the open WebSocket.
- A meter that is already silent when Home Assistant starts shows its last reading for up to 60 seconds before its sensors become unavailable.
- An energy total of exactly 0 kWh is shown as unknown, like the official HomeWizard integration does, because Home Assistant would otherwise record it as a meter reset.
- Only P1 meters are supported; other HomeWizard devices are not supported.
- This integration does not register services or actions.

## Removal

1. Remove the integration from **Settings → Devices & services**. Home Assistant also deletes its local user from the P1 meter when the meter is reachable; otherwise remove it in the HomeWizard Energy app.
2. If installed manually, delete `custom_components/homewizard_instant`.
3. If installed via HACS, remove the repository from HACS and restart Home Assistant.

## Notes

- Protocol reference: [HomeWizard API v2 WebSocket documentation](https://api-documentation.homewizard.com/docs/v2/websocket/).
- Only **P1 meters** are supported by this integration.
