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

The meters speak HTTPS with a certificate shaped like a real P1 meter: common name `appliance/p1dongle/<serial>` and no subjectAltName (HomeWizard API documentation, and a P1 dongle certificate reported with an empty subjectAltName list). The integration checks that chain against the HomeWizard CA and then compares the serial from `/api`. It does not pin the TLS hostname. `python-homewizard-energy` only trusts HomeWizard's appliance CA, and that private key is not public, so the lab CA is loaded only when `HOMEWIZARD_DEV_CA` points at a PEM file that contains a real certificate. Loading it logs a warning, because every HomeWizard client in that process then trusts the lab CA. Leave the variable unset on a normal install.

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

The documented minimum is Home Assistant 2026.8 on Python 3.14. Use that image with:

```bash
HA_IMAGE_TAG=2026.8.0 docker compose up -d
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
| `silent` | Stops pushing measurements, as if the P1 cable were unplugged. A new subscription gets the last reading again, unchanged. |
| `restart` | Restarts the HTTPS server. |
| `tls_change` | Restarts HTTPS with a certificate the lab CA did not sign. |

`POST /clear` removes the flags and restores the trusted certificate. `POST /push` with `{"topic":"system"}` sends a system snapshot immediately. `POST /uptime` with `{"seconds":10}` simulates a reboot. `POST /overlay` merges fields into the next measurement and pushes it at once; add `"push":false` to leave it for the next scheduled telegram, and `{"reset":true}` removes them. Telegrams follow a fixed 1 s schedule, so a slow send does not shift the ones after it. `POST /clock` with `{"frozen":true}` keeps the smart-meter timestamp still so later pushes are identical; `{"frozen":false}` lets it advance again. `GET /state` includes `last_push_unix`, the host time of the last frame that left the socket.

## OmniBattery and a Venus E 3.0

Two different batteries can be attached to this lab. They are not interchangeable.

**Firmware, Venus E 3.0 Control 151.** [has-marstek-local-api](https://github.com/taurgis/has-marstek-local-api) runs the vendor image `VNSEE3-0_ems_app_0151_0928_094421.bin` in Renode (`tools/firmware_emulator`). The catalog entry is version 151, SHA-256 `a2f566ebdf3f94a5b2b171e071bd65237d0cd0ae68035dab5fc34734cf250148`. That process serves the local API on UDP 30000. Its README states that TCP listen sockets, including Modbus TCP on port 502, are not delivered to the firmware. [OmniBattery](https://github.com/ffunes/Omnibattery) 1.5.0 (`custom_components/omnibattery/manifest.json`) drives Marstek only through Modbus TCP (`drivers/marstek.py`). It cannot command this emulator. The Marstek local-API integration from that same repository can. Build the image in the clone (`docker build -f tools/firmware_emulator/Dockerfile -t marstek-fw-emulator:local .`) and start it with this repo's overlay:

```bash
export MARSTEK_REPO=/path/to/has-marstek-local-api
export OMNIBATTERY_REPO=/path/to/Omnibattery
docker compose -f docker-compose.yml -f docker-compose.consumers.yml up -d venus-e3-151 venus-e3-151-ct
```

Add the Marstek integration and point it at `172.30.28.20`. What it reports (device name, firmware version, mode) is the firmware's own local API. It is not an OmniBattery PD verdict, and it is not a claim about a physical battery.

**Venus E 3.0 Modbus model, with OmniBattery.** OmniBattery needs Modbus TCP, which the firmware emulator cannot serve. On a Venus E 3.0 that server is part of the Control firmware (socket 0 of its CH395 Ethernet chip, `Task_Modbus_tcp` in the 151 image), not the BMS, so no BMS firmware is involved; the emulator's `ch395.py` simply does not bridge TCP. `tools/lab/venus_e3_modbus.py` is a plain-Python model of the registers OmniBattery 1.5.0 uses for `battery_version: v3`. From the 151 image it copies the device name `VNSE3-0`, EMS version 151, the 800 W discharge cap (the image rejects "max discharge power ... over 800W") and the rule that 44002/44003 only accept 800 or 2500. Register numbers and signs follow OmniBattery's `registers_v3.py`: `battery_power` (30001) is positive while charging, `ac_power` (30006) positive while discharging. Force mode only acts while RS485 control (42000 = 0x55AA) is on. The inverter follows a set-point after 0.6 s and ramps at 1000 W/s (`DEAD_TIME_S`, `RAMP_W_PER_S`). It is a model, so a verdict from it is evidence about this integration and OmniBattery's loop, not about firmware 151.

The model also plays the house: grid power is `house load - ac_power` and goes to the 1-phase meter with `{"push": false}` on `/overlay`, so the meter reports it on its own 1 s telegram clock, as a P1 port would. `GET http://127.0.0.1:8090/state` shows the plant; `POST /load` with `{"watts": 900}` changes the house load.

```bash
export OMNIBATTERY_REPO=/path/to/Omnibattery
docker compose -f docker-compose.yml -f docker-compose.omnibattery.yml up -d
python3 tools/dev/omnibattery_pd.py --minutes 10 --out /tmp/omnibattery-pd.json
```

`omnibattery_pd.py` (needs `aiohttp`) onboards a fresh Home Assistant as `dev` / `devpass1`, pairs the 1-phase meter, adds OmniBattery with the model at `172.30.28.21:502`, and then holds a steady 450 W house, steps the load (700 W, 250 W, -600 W export so the battery charges, back to 450 W), and drops the meter connection once. It writes every `sensor.omnibattery_pd_control_quality` verdict and the report cadence of `sensor.p1_meter_power` per phase.

Two things to know when measuring freshness yourself. Home Assistant does not deliver `state_reported` through `subscribe_events`, and `/api/states` serves a cached `last_reported` after a report that did not change the state. Read `{{ states.sensor.p1_meter_power.last_reported }}` through `/api/template` instead; that is the attribute OmniBattery reads.

If containers on the lab bridge can reach published host ports but not each other, bridged packets are being filtered. `sysctl net.bridge.bridge-nf-call-iptables=0` clears that on a host where Docker's bridge netfilter hook drops them.

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

Use Python 3.14 for those commands. CI does.

`tools/dev/exercise_ha.py` signs in as `dev` / `devpass1` and walks the pairing and fault checks against a lab that is already up. `tools/dev/exercise_rest.py` continues from an entry that is already paired.
