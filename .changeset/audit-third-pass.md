---
"ha-homewizard-instant-release-tools": patch
---

A silent meter now stays unavailable after a reconnect until it sends a new measurement, instead of coming back on the reading it replays. An error reply to a single request no longer drops the connection. Energy totals of 0 kWh are shown as unknown so the energy dashboard does not record a reset. Uptime uses Home Assistant's uptime sensor type. Liter readings from external meters get the liter unit, an external meter whose device was deleted is added again when the P1 meter reports it, and pairing and token errors are translated. Debug logging covers the `homewizard_energy` library. CI runs on Python 3.14 with Home Assistant 2026.8.
