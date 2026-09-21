---
"ha-homewizard-instant-release-tools": patch
---

Fix sensors freezing on stale values when the websocket stays connected but the HTTP API stops responding. Websocket freshness is now judged on completed refreshes rather than on incoming frames, so polling resumes, and a failed websocket-triggered refresh is reported to the coordinator instead of being silently discarded.
