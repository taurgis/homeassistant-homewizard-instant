---
"ha-homewizard-instant-release-tools": patch
---

Keep the WebSocket stream alive when a meter reports null or out-of-range fields (only those sensors become unavailable), mark sensors unavailable when the meter stops sending measurements, recover from unexpected reader errors, and log each outage once. Add sensors whose data appears after setup, sync firmware updates to the device, revoke the pairing token when the entry is removed, return to the address form after a wrong IP, let reconfigure save a new IP when the token was revoked, and keep external meter IDs out of diagnostics. Require Home Assistant 2026.8, the first release with python-homewizard-energy 10.2.0.
