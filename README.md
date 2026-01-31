# HomeWizard Instant (Home Assistant custom integration)

This is a custom integration for **HomeWizard P1 meters** that refreshes data every **1 second**.

Home Assistant’s official HomeWizard integration is documented here:
- https://www.home-assistant.io/integrations/homewizard/

## Why this exists

The official integration typically updates at a slower interval (commonly ~5s). This custom integration sets the coordinator update interval to **1s** to get near real-time power readings.

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

The integration will store the device by a domain-prefixed unique ID to avoid collisions with the official integration.

### Installation parameters

- **Local API must be enabled** in the HomeWizard app for your P1 meter.
- The device must be reachable on your local network.

## Data updates

The integration polls the HomeWizard local API every **1 second** using a single coordinator update call. All entities read from the coordinator data.

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

## Notes

- Polling every second increases local network traffic and load on the device compared to slower polling intervals.
- Only **P1 meters** are supported by this integration.
