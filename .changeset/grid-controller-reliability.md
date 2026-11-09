---
"ha-homewizard-instant-release-tools": patch
---

Report measurement sensors only on a new measurement frame, so the system read every minute no longer refreshes `last_reported` on power sensors without new data. Close a half-open WebSocket within about 22 seconds instead of 45, and reconnect when the meter sends nothing at all for 90 seconds.
