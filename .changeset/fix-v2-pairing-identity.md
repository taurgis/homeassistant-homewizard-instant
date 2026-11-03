---
"ha-homewizard-instant-release-tools": patch
---

Fix pairing and v1 migration on real P1 meters by checking the device serial instead of the TLS hostname, and log the underlying error when pairing fails.
