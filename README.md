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

## Notes

- Polling every second increases local network traffic and load on the device compared to slower polling intervals.
- Only **P1 meters** are supported by this integration.
