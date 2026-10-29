---
"ha-homewizard-instant-release-tools": patch
---

Refresh Wi-Fi signal and uptime once a minute over the open WebSocket, since the meter does not push those fields. Keep backing off when a meter accepts and then immediately drops connections, instead of retrying every second. Only treat an explicit `user:unauthorized` reply as a rejected token, so an unexpected handshake reply no longer starts reauthentication. Log a disconnect that follows a stale-data outage, and translate the error shown while waiting for a reconnect.
