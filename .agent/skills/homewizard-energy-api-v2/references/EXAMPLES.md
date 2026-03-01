# HomeWizard API v2 Examples

Use these examples for v2 onboarding, authenticated requests, and realtime subscriptions.

## Curl: Create Token (Button Confirmation Flow)

```bash
curl -sS -X POST \
  -H "Content-Type: application/json" \
  -H "X-Api-Version: 2" \
  -d '{"name":"home-assistant"}' \
  "https://<DEVICE_IP>/api/user"
```

Expected pattern:
- Before button press: `403 {"error":"user:creation-not-enabled"}`
- After button press window: `200 {"token":"...","name":"local/home-assistant"}`

## Curl: Read Measurement with Token

```bash
curl -sS \
  -H "Authorization: Bearer <TOKEN>" \
  -H "X-Api-Version: 2" \
  "https://<DEVICE_IP>/api/measurement"
```

## Curl: Read System with Token

```bash
curl -sS \
  -H "Authorization: Bearer <TOKEN>" \
  -H "X-Api-Version: 2" \
  "https://<DEVICE_IP>/api/system"
```

## WebSocket Auth + Subscribe Sequence

```json
{"type":"authorization","data":"<TOKEN>"}
{"type":"subscribe","data":"measurement"}
{"type":"subscribe","data":"system"}
```

Typical server sequence:

```json
{"type":"authorization_requested","data":{"api_version":"2.0.0"}}
{"type":"authorized"}
{"type":"measurement","data":{"power_w":1234}}
```

## Home Assistant Pattern: Handle Token Creation State

```python
# Pseudocode for config flow behavior
resp = await create_user(name="home-assistant")
if resp.error == "user:creation-not-enabled":
    return self.async_show_form(
        step_id="press_button",
        errors={"base": "press_device_button"},
    )

store_token(resp.token)
```

## Home Assistant Pattern: Coordinator Error Mapping

```python
if error_code == "user:unauthorized":
    # Reauth path should request a new token
    raise UpdateFailed(
        "Unauthorized",
        translation_domain="homewizard_instant",
        translation_key="communication_error",
    )
```

## Notes

- Keep coordinator-first architecture and avoid per-entity requests.
- If using websocket updates, still keep robust fallback polling/error handling.
- Continue using defensive parsing for optional measurement fields.
