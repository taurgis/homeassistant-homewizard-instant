---
"ha-homewizard-instant-release-tools": major
---

Replace API v1 polling with authenticated API v2 WebSocket push updates. Existing entries require one-time button pairing and P1 firmware supporting WebSockets; sensor IDs are preserved. Add verified TLS, reconnect backoff, token recovery, and protocol/lifecycle tests.
