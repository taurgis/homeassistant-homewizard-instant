# Development

This page is for running the integration against a real Home Assistant and emulated P1 and kWh meters. Unit tests stay in `tests/` and do not need Docker.

## What the lab contains

`.devcontainer/docker-compose.yml` starts five containers:

| Service | Address | Role |
| --- | --- | --- |
| `homeassistant` | http://127.0.0.1:8123 | Home Assistant. Default image is `2026.10.4` (current stable, Python 3.14). |
| `meter-3phase` | `172.30.28.10` | API v2 P1 meter, serial `aabbccddeeff`, 3-phase import with gas and water. |
| `meter-1phase` | `172.30.28.11` | API v2 P1 meter, serial `112233445566`, 1-phase import with gas. |
| `meter-kwh3` | `172.30.28.12` | API v2 3-phase kWh meter (SDM630-wifi), serial `5c2faf000001`, replaying a frame captured from a physical meter at night. |
| `ca` | (exits) | Writes a development CA into a shared volume. |

The integration is bind-mounted from `custom_components/homewizard_instant`. Home Assistant config is created in `.devcontainer/ha_config` on first start (gitignored).

The meters speak HTTPS with a certificate shaped like a real P1 meter: common name `appliance/p1dongle/<serial>` (`appliance/energymeter/<serial>` for kWh meters) and no subjectAltName (HomeWizard API documentation, and a P1 dongle certificate reported with an empty subjectAltName list). The integration checks that chain against the HomeWizard CA and then compares the serial from `/api`. It does not pin the TLS hostname. `python-homewizard-energy` only trusts HomeWizard's appliance CA, and that private key is not public, so the lab CA is loaded only when `HOMEWIZARD_DEV_CA` points at a PEM file that contains a real certificate. Loading it logs a warning, because every HomeWizard client in that process then trusts the lab CA. Leave the variable unset on a normal install.

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

Open http://127.0.0.1:8123 and create the owner account. Add the integration with IP `172.30.28.11` (1-phase), `172.30.28.10` (3-phase) or `172.30.28.12` (kWh meter). When it asks for the button, press it from the host and submit within 30 seconds:

```bash
curl -s -X POST http://127.0.0.1:8082/button   # 1-phase meter
curl -s -X POST http://127.0.0.1:8081/button   # 3-phase meter
curl -s -X POST http://127.0.0.1:8083/button   # kWh meter
```

The documented minimum is Home Assistant 2026.8 on Python 3.14. Use that image with:

```bash
HA_IMAGE_TAG=2026.8.0 docker compose up -d
```

Stop the lab with `docker compose down`. Add `-v` to drop the CA volume and the Home Assistant config directory if you want a clean start (`rm -rf ha_config` as well).

## Meter control

Each meter has a plain HTTP control port. From the host that is `8081` for the 3-phase meter, `8082` for the 1-phase meter and `8083` for the kWh meter. Inside the lab network it is port `8080` on the meter IP.

```bash
curl -s http://127.0.0.1:8081/state
curl -s -X POST http://127.0.0.1:8081/profile -H 'Content-Type: application/json' -d '{"name":"export"}'
curl -s -X POST http://127.0.0.1:8081/steady -H 'Content-Type: application/json' -d '{"enabled":false}'
curl -s -X POST http://127.0.0.1:8081/clear
```

P1 profiles are `3phase`, `1phase`, `export` (3-phase feed-in, negative L1 current) and `export_1phase`. kWh profiles are `kwh3` (an SDM630-wifi frame captured at night: 0 W, only reactive standby current, no timestamp, tariff or DSMR fields), `kwh3_solar` (the same meter exporting 2.4 kW) and `kwh1` (an HWE-KWH1 with the documented 1-phase fields). A meter only switches between profiles of its own product type, so start the lab's kWh meter for kWh profiles. `steady` repeats one snapshot so values can be compared exactly. With steady off, power wobbles and import or export energy creeps up.

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

OmniBattery drives a Venus E 3.0 over Modbus TCP. On a Venus E 3.0 that server is part of the Control firmware (socket 0 of its CH395 Ethernet chip, `Task_Modbus_tcp` in the 151 image), not the BMS, so no BMS firmware is involved. The lab can put either of two batteries behind OmniBattery. Both play the same house: grid power is `house load - ac_power` and goes to the 1-phase meter with `{"push": false}` on `/overlay`, so the meter reports it on its own 1 s telegram clock, as a P1 port would. `GET http://127.0.0.1:8090/state` shows the plant; `POST /load` with `{"watts": 900}` changes the house load.

**Venus E 3.0 Modbus model** (`tools/lab/venus_e3_modbus.py`, `172.30.28.21:502`). A plain-Python model of the registers [OmniBattery](https://github.com/ffunes/Omnibattery) 1.5.0 uses for `battery_version: v3`. It copies what the 151 image showed in the emulator: device name `VNSE3-0` (31000), EMS version 151 (30200), `ac_power` (30006) int16 and positive while discharging, force mode (42010) only acting while RS485 control (42000) is 0x55AA, and 44002/44003 storing any value. Discharge is capped at 800 W (the image rejects "max discharge power ... over 800W"). The inverter follows a set-point after 0.6 s and ramps at 1000 W/s (`DEAD_TIME_S`, `RAMP_W_PER_S`). It is a model, so a verdict from it is evidence about this integration and OmniBattery's loop, not about firmware 151.

```bash
export OMNIBATTERY_REPO=/path/to/Omnibattery
docker compose -f docker-compose.yml -f docker-compose.omnibattery.yml up -d
python3 tools/dev/omnibattery_pd.py --minutes 10 --out /tmp/omnibattery-pd.json
```

**Control firmware 151** (`172.30.28.22:502`). [has-marstek-local-api](https://github.com/taurgis/has-marstek-local-api) runs the vendor image `VNSEE3-0_ems_app_0151_0928_094421.bin` (catalog version 151, SHA-256 `a2f566ebdf3f94a5b2b171e071bd65237d0cd0ae68035dab5fc34734cf250148`) in Renode. With `--ethernet` and the emulator's CH395 TCP bridge, the firmware's own Modbus code answers on port 502. `docker-compose.firmware151.yml` builds the emulator image from a pinned commit of that repository (no second clone needed), starts it on the lab network, and runs `venus-e3` in firmware mode (`INVERTER_URL`): it serves no Modbus, reads the inverter output from the emulator's grid meter (`can_peers.py`, house load 0) and only plays the house.

Battery and meter only, for any Modbus client or an OmniBattery you install yourself:

```bash
docker compose -f docker-compose.yml -f docker-compose.firmware151.yml up -d
```

With OmniBattery mounted and the PD run:

```bash
export OMNIBATTERY_REPO=/path/to/Omnibattery
docker compose -f docker-compose.yml -f docker-compose.omnibattery.yml \
    -f docker-compose.firmware151.yml up -d
python3 tools/dev/omnibattery_pd.py --battery-host 172.30.28.22 --out /tmp/omnibattery-pd-151.json
```

Set `MARSTEK_FW_CONTEXT` to a local has-marstek-local-api clone (or another git ref) to build the emulator from there. Behind a TLS-intercepting proxy, set `MARSTEK_BUILD_CA` to the proxy's CA bundle. Compose's bake builder only reads that file inside `.devcontainer/` (`*.crt` there is git-ignored), or pass `--allow`.

The first start writes a fresh EEPROM and reboots once (about a minute). Measured against the emulated 151 image:

- Each Modbus request takes about 300 ms, except the first after a pause; OmniBattery's 150 ms v3 message gap comes on top.
- The image serves one Modbus client at a time.
- A force-discharge write shows up in `sensor.p1_meter_power` about 1 s later: the Modbus write, the emulated inverter's 1 s step, then the next telegram.
- 32104 holds the SoC, as the Local API does. **37005 (OmniBattery's v3 SoC) and 30001 (battery power) read 0**, so OmniBattery sees an empty battery and may refuse to discharge. Whether a physical unit fills them is not known from the emulator.

`docker-compose.consumers.yml` also has `venus-e3-151` at `172.30.28.20` for the Marstek local-API integration; that one has no Ethernet and no Modbus.

Results of `omnibattery_pd.py --minutes 5 --step-seconds 90` with OmniBattery 1.5.0 (commit 39b052b):

| Battery | Steady 450 W | Load steps | Grid sensor | Meter drop |
|---|---|---|---|---|
| Python model | `stable` (battery 430 W, grid about 17 W) | `sluggish`; each step settled to under 50 W of grid in 15-20 s | 1.000 s, stdev 0.8-1.5 ms | back in 1.0 s |
| Firmware 151 | `battery_limited` (37005 reads SoC 0, no discharge) | charging works: -600 W export settled at 620 W charge | 1.000 s, stdev 0.7-1.7 ms | back in 1.0 s |

The `sluggish` verdicts after the steps come from how OmniBattery scores. While the grid error is inside its deadband (40 W), the control cycle returns before it updates the quality metric. The RMS therefore only samples step transients, and it freezes at that value once the loop settles. `metric_age_s` on the sensor shows this, and the verdict falls back to `collecting_data` after 300 s. Hold a phase for more than five minutes to see the settled verdict.

**Tuning against a 1 s HomeWizard meter.** `tools/dev/omnibattery_house.py` replays one 12-minute house slice against several OmniBattery configs on the Python model. The slice has east/west 6 kWp solar with cloud edges, a kettle, a washer, an oven and a fridge. Each config is scored on the grid energy a perfect controller within the battery limits would have avoided. The baseline is deadband 40 W, min charge 150 W, min discharge 100 W, relay cooldown 10 s, target 0 W, and the `balanced` profile:

| Config | Avoidable import + export (Wh) | Cost (EUR cent) |
|---|---|---|
| baseline, three runs | 27.0 / 28.6 / 26.0 | 0.77 / 0.81 / 0.74 |
| min charge and discharge 50 W | 25.8 | 0.73 |
| relay cooldown 5 s | 27.3 | 0.77 |
| Kd 0 / Kd 0.1 | 27.0 / 28.7 | 0.76 / 0.82 |
| deadband 25 W | 30.5 | 0.87 |
| target grid power -30 W | 30.9 | 0.88 |

Run-to-run spread is about 0.07 cent, so only the worse configs stand out: a narrower deadband and a negative target both cost more. Separate load-step tests showed:

- PD min cycle interval 0.8 s matches 1.0 s, and 0 s doubles both the error and the Modbus writes.
- The `aggressive` and `very_aggressive` profiles did not settle steps faster.

Every config loses about what a perfect controller would lose with a 4 s response delay. HomeWizard contributes about 1 ms of that. The meter's 1 s telegrams and the battery's dead time and ramp account for roughly 2 s. The rest is OmniBattery's loop: incremental PD steps, a fixed 2 s grid filter, and a dip after each rise. After a step, the next telegram already shows the battery responding while the polled battery output does not yet, and the discharge ceiling cuts the command back before it climbs again (see below).

**Settings for a stock OmniBattery.** A second round used the minimum charge and discharge power of a real setup (90 W / 80 W). The lab model answers Modbus about as fast as a real Venus E 3.0 on firmware 151: OmniBattery's diagnostics show a 1.36 s poll here and 1.44 s on the real unit, mostly OmniBattery's own 150 ms wait after each v3 request.

| Config | Cost (EUR cent) per run | Modbus writes |
|---|---|---|
| baseline (Kp 0.35, interval 1.0 s) | 0.73 / 0.82 | 354 / 378 |
| Kp 0.5 | 0.84 | 273 |
| Kp 0.7 | 0.71 | 279 |
| interval 0.5 s | 0.74 | 402 |
| Kp 0.5, interval 0.5 s | 0.77 | 345 |
| **Kp 0.7, interval 0.5 s** | **0.64 / 0.76 / 0.65** | 405 / 237 / 261 |

Kp 0.7 with a 0.5 s interval averages 0.68 cent against 0.78 for the baseline over all five baseline runs.

`tools/dev/omnibattery_latency.py` times each link of a 400 W load step. With the baseline, the change reaches `sensor.p1_meter_power` in about 0.1 s. On a rise, the first set-point write then comes 0.1-1.2 s later, and the battery covers 90 % of the step after about 12 s. A drop is covered in about 1.2 s because OmniBattery caps discharge at the uncovered house load right away. Kp 0.7 brings rises to about 7 s. Kp 1.0 reaches 90 % in about 2.4 s but takes 16-22 s to settle. Three behaviours of OmniBattery 1.5.0 set these times:

- The incremental P term is normalised to a 2 s nominal cycle, so Kp is per 2 s whatever the meter cadence.
- The load-step feedforward only fires for jumps above max(5 x deadband, 400 W) and waits for a second sample to confirm.
- The discharge ceiling adds the battery's polled `ac_power` to the grid reading. The meter reports every second but a Marstek poll takes about 1.4 s and runs every 3-4 s, so during a ramp it caps discharge at a figure that is too low and the ramp dips before it climbs again.

`omnibattery_pd.py` (needs `aiohttp`) onboards a fresh Home Assistant as `dev` / `devpass1`, pairs the 1-phase meter, adds OmniBattery with the battery at `--battery-host`, and then holds a steady 450 W house, steps the load (700 W, 250 W, -600 W export so the battery charges, back to 450 W), and drops the meter connection once. It writes every `sensor.omnibattery_pd_control_quality` verdict and the report cadence of `sensor.p1_meter_power` per phase.

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
