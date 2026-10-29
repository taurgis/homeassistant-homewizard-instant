# Development

This page is for running the integration against a real Home Assistant and an emulated P1 meter. Unit tests stay in `tests/` and do not need Docker.

## What the lab contains

`.devcontainer/docker-compose.yml` starts four containers:

| Service | Address | Role |
| --- | --- | --- |
| `homeassistant` | http://127.0.0.1:8123 | Home Assistant. Default image is `2026.10.4` (current stable, Python 3.14). |
| `meter-3phase` | `172.30.28.10` | API v2 P1 meter, serial `aabbccddeeff`, 3-phase import with gas and water. |
| `meter-1phase` | `172.30.28.11` | API v2 P1 meter, serial `112233445566`, 1-phase import with gas. |
| `ca` | (exits) | Writes a development CA into a shared volume. |

The integration is bind-mounted from `custom_components/homewizard_instant`. Home Assistant config is created in `.devcontainer/ha_config` on first start (gitignored).

The meters speak HTTPS with a certificate shaped like a real P1 meter: common name and DNS SAN `appliance/p1dongle/<serial>`. `python-homewizard-energy` only trusts HomeWizard's appliance CA, and that private key is not public, so the lab CA is loaded only when `HOMEWIZARD_DEV_CA` points at a PEM file that contains a real certificate. Loading it logs a warning, because every HomeWizard client in that process then trusts the lab CA. Leave the variable unset on a normal install.

The WebSocket matches the v2 handshake. The meter sends `authorization_requested`, and the client must answer with `{"type":"authorization","data":"<token>"}` within 40 seconds. Measurement and system frames are pushed only after a `subscribe` for that topic. A client that has not subscribed stays quiet. Like real firmware, only measurements stream; a client reads fresh system fields (`wifi_rssi_db`, `uptime_s`) by sending `{"type":"system"}`.

## Run it

Docker Compose v2 is required.

```bash
cd .devcontainer
docker compose up -d --build
```

Home Assistant takes a minute the first time while it installs `python-homewizard-energy`. Follow it with:

```bash
docker compose logs -f homeassistant
```

Open http://127.0.0.1:8123 and create the owner account. Add the integration with IP `172.30.28.11` (1-phase) or `172.30.28.10` (3-phase). When it asks for the button, press it from the host and submit within 30 seconds:

```bash
curl -s -X POST http://127.0.0.1:8082/button   # 1-phase meter
curl -s -X POST http://127.0.0.1:8081/button   # 3-phase meter
```

The documented minimum is Home Assistant 2025.10 on Python 3.13. Use that image with:

```bash
HA_IMAGE_TAG=2025.10.4 docker compose up -d
```

Stop the lab with `docker compose down`. Add `-v` to drop the CA volume and the Home Assistant config directory if you want a clean start (`rm -rf ha_config` as well).

## Meter control

Each meter has a plain HTTP control port. From the host that is `8081` for the 3-phase meter and `8082` for the 1-phase meter. Inside the lab network it is port `8080` on the meter IP.

```bash
curl -s http://127.0.0.1:8081/state
curl -s -X POST http://127.0.0.1:8081/profile -H 'Content-Type: application/json' -d '{"name":"export"}'
curl -s -X POST http://127.0.0.1:8081/steady -H 'Content-Type: application/json' -d '{"enabled":false}'
curl -s -X POST http://127.0.0.1:8081/clear
```

Profiles are `3phase`, `1phase`, `export` (3-phase feed-in, negative L1 current) and `export_1phase`. `steady` repeats one snapshot so values can be compared exactly. With steady off, power wobbles and import or export energy creeps up.

Faults, posted as `{"name":"<fault>"}`:

| Fault | What the meter does |
| --- | --- |
| `drop` | Closes WebSockets. |
| `hang` | Stops reading and writing until cleared. HTTP calls wait too. |
| `refuse_auth` | Closes current sockets with `user:unauthorized` and rejects the token until cleared. Pairing still accepts a button press. |
| `revoke_token` | Deletes tokens and sends `user:unauthorized`. |
| `malformed_json` | Pushes a truncated JSON frame. |
| `unknown_topic` | Pushes a `batteries` topic this integration does not subscribe to. |
| `missing_fields` | Pushes a device object without a serial. |
| `null_fields` | Pushes a measurement with explicit nulls. |
| `wrong_types` | Pushes `power_w` as a string. |
| `out_of_range` | Pushes impossible power, voltage, frequency and tariff. |
| `identity_change` | Reports serial `ffffffffffff`. |
| `slow` | Pushes about every 12 seconds (`interval` overrides). |
| `restart` | Restarts the HTTPS server. |
| `tls_change` | Restarts HTTPS with a certificate the lab CA did not sign. |

`POST /clear` removes the flags and restores the trusted certificate. `POST /push` with `{"topic":"system"}` sends a system snapshot immediately. `POST /uptime` with `{"seconds":10}` simulates a reboot. `POST /overlay` merges fields into the next measurement; `{"reset":true}` removes them.

The control API has no authentication. Compose publishes it on `127.0.0.1` only. A meter started on the host binds to `127.0.0.1` unless `BIND_HOST` is set. Compose sets `BIND_HOST=0.0.0.0` so Home Assistant on the lab network can reach the meters; the host ports stay on localhost.

Run the meter on the host, without Docker, from the repo root. Port 443 needs root. The control port can be anything.

```bash
python3 -m pip install 'aiohttp>=3.10,<4'
PYTHONPATH=tools python3 -m mock_device --serial aabbccddeeff --https-port 443 --control-port 8080 --ca-dir /tmp/hw-ca
```

Set `HOMEWIZARD_DEV_CA=/tmp/hw-ca/ca.pem` in the Home Assistant environment if you are not using the compose file.

## Checks

```bash
python3 -m ruff check custom_components/homewizard_instant/
python3 -m mypy --strict custom_components/homewizard_instant/
pytest tests/ -q --cov=custom_components/homewizard_instant --cov-fail-under=95
```

Use Python 3.13 for those commands. CI does.

`tools/dev/exercise_ha.py` signs in as `dev` / `devpass1` and walks the pairing and fault checks against a lab that is already up. `tools/dev/exercise_rest.py` continues from an entry that is already paired.
