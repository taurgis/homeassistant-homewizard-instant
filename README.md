# HomeWizard Instant (Home Assistant custom integration)

Custom integration for **HomeWizard P1 meters** that polls the local API every **1 second**.

Requires Home Assistant 2026.8.0 or newer.

The official HomeWizard integration is documented at
<https://www.home-assistant.io/integrations/homewizard/>.
This integration uses the domain `homewizard_instant`, so it can run side by side with the official one.

## Why this exists

The official integration updates more slowly (commonly about every 5 seconds). This integration sets the coordinator interval to **1 second** for near real-time power readings.

## Installation

### HACS

1. In HACS, open **Integrations**.
2. Open the overflow menu and choose **Custom repositories**.
3. Add `https://github.com/taurgis/homeassistant-homewizard-instant` with category **Integration**.
4. Search for **HomeWizard Instant** and download it.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services → Add integration**, search for **HomeWizard Instant**, and follow the steps.

### Manual

1. Copy `custom_components/homewizard_instant` into your Home Assistant config directory:
   `<config>/custom_components/homewizard_instant`
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration**.
4. Search for **HomeWizard Instant** and follow the steps.

## Configuration

Configuration is done in the UI. The only required parameter is the **IP address** of the P1 meter.

The config entry is stored with a domain-prefixed unique ID so it does not collide with the official integration.

- Enable the **local API** in the HomeWizard app for the P1 meter.
- The meter must be reachable on the local network.

## Data updates

One coordinator polls the HomeWizard local API every **1 second** (`api.combined()`). Every entity reads that coordinator data. There is no per-entity polling.

## Supported devices

HomeWizard **P1 meters** only. Other HomeWizard products are rejected during setup and discovery.

## Entities

A sensor is created only when the meter reports that field.

| Entity | Key | Notes |
| --- | --- | --- |
| Power | `active_power_w` | Current power, in watts. Per-phase power is added when the meter reports it. |
| Average demand | `active_power_average_w` | 15-minute average demand, in watts. |
| Peak demand current month | `monthly_power_peak_w` | Reported when the meter provides it. |
| Energy import | `total_power_import_kwh` | Total, plus per-tariff totals when those registers exist. |
| Energy export | `total_power_export_kwh` | Disabled by default while the reported value is 0. |
| Tariff | `active_tariff` | Active tariff as an enum (`1`–`4`). |
| Voltage, current, frequency | phase fields | Disabled by default. |
| Apparent power, reactive power, power factor | phase fields | Disabled by default. Power factor is shown as a percentage. |
| DSMR version, smart meter model, identifier | meter info | Diagnostic. |
| Wi-Fi SSID, strength, RSSI, uptime | system info | Diagnostic. Strength, RSSI, and uptime are disabled by default. |
| Power failures, voltage sags, voltage swells | counters | Diagnostic. |
| Gas, water, warm water, heat, inlet heat | external meters | Separate devices. Created when the entry is set up. |

## Examples

- Put **Average demand** on a dashboard to show short-term usage.
- Add **Energy import** to the Energy dashboard for long-term consumption.

## Troubleshooting

- **API disabled**: Enable the local API in the HomeWizard app, then finish the reauthentication prompt.
- **Device unreachable**: Check the IP address and that the meter is online.
- **Discovery does not find the meter**: Add the integration manually and enter the IP address.

## Known limitations

- Polling every second increases local network traffic and load on the meter.
- Only P1 meters are supported.
- This integration does not register services or actions.
- Gas, water, and heat meters are added when the config entry is set up. Connect a meter later and reload the HomeWizard Instant entry to create it. A meter that disappears stays in the device registry until that reload.

## Removal

1. Remove the integration from **Settings → Devices & services**.
2. If you installed it manually, delete `custom_components/homewizard_instant`.
3. If you installed it with HACS, remove **HomeWizard Instant** in HACS. Remove the custom repository there as well if you no longer want updates, then restart Home Assistant.
