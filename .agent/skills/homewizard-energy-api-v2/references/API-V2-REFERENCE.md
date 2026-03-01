# HomeWizard API v2 Reference

Use this reference when implementing or reviewing API v2 support.

## Required Request Headers

| Header | Value | Why |
|---|---|---|
| `Authorization` | `Bearer <token>` | Required after token onboarding |
| `X-Api-Version` | `2` | Locks requests to API major version 2 |

## Endpoint Quick Reference

| Endpoint | Method | Purpose | Notes |
|---|---|---|---|
| `/api/user` | `POST` | Create user/token | Requires physical button confirmation flow |
| `/api/measurement` | `GET` | Live measurements | Fields can be omitted when unavailable |
| `/api/system` | `GET` | Device/system info | Includes platform and capability fields |
| `/api/system` | `PUT` | Update settings | Send only documented writable settings |
| `/api/system/identify` | `PUT` | Trigger identify behavior | Useful during setup |
| `/api/system/reboot` | `PUT` | Reboot device | Operational/admin action |
| `/api/telegram` | `GET` | Raw telegram | May return `503 telegram:no-telegram-received` |
| `/api/ws` | WebSocket | Realtime push | Auth handshake then subscribe/unsubscribe |

## Error Code Quick Reference

| Error code | Typical cause | Suggested integration behavior |
|---|---|---|
| `user:unauthorized` | Invalid/missing token | Trigger reauth or token renewal flow |
| `user:creation-not-enabled` | Button not pressed yet | Show instruction and retry path |
| `request:api-version-not-supported` | Wrong `X-Api-Version` major | Fallback or block with explicit guidance |
| `telegram:no-telegram-received` | No telegram received yet | Surface temporary unavailable state |

## Discovery and Compatibility

- v2+ discovery service: `_homewizard._tcp`
- v1 discovery service: `_hwenergy._tcp`
- Verify per-device v2 support in the official support matrix.

## Official References

- API v2 category: https://api-documentation.homewizard.com/docs/category/api-v2/
- Authorization: https://api-documentation.homewizard.com/docs/v2/authorization/
- Versioning: https://api-documentation.homewizard.com/docs/versioning/
- v2 measurement: https://api-documentation.homewizard.com/docs/v2/measurement/
- v2 system: https://api-documentation.homewizard.com/docs/v2/system/
- v2 telegram: https://api-documentation.homewizard.com/docs/v2/telegram/
- v2 websocket: https://api-documentation.homewizard.com/docs/v2/websocket/
- Discovery: https://api-documentation.homewizard.com/docs/discovery/
- Device support matrix: https://api-documentation.homewizard.com/docs/introduction#devices
