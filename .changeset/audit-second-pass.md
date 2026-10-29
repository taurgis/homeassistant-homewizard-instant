---
"ha-homewizard-instant-release-tools": patch
---

Refresh Wi-Fi signal and uptime once a minute over the open WebSocket, since the meter does not push those fields. Keep backing off when a meter accepts and then immediately drops connections, instead of retrying every second. Only treat an explicit `user:unauthorized` reply as a rejected token, so an unexpected handshake reply no longer starts reauthentication. Log a disconnect that follows a stale-data outage, and translate the error shown while waiting for a reconnect.

A malformed timestamp, tariff, counter or external meter entry now drops only that field or meter instead of freezing every sensor. Outages log one warning instead of a warning plus an ERROR line, and diagnostics keep the failure reason with the IP address redacted. Pairing no longer aborts when a discovery card for the same meter is pending, tokens are revoked when a flow aborts after pairing, a different meter at the configured address is reported as the wrong device instead of "unreachable", and token revocation on removal is capped at 8 seconds. External meter sensors are named after their reading ("Total gas") instead of repeating the device name, the voltage sag and swell icons now apply, and external meters the P1 meter no longer reports can be deleted.
