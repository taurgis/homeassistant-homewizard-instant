"""Configure OmniBattery and the Marstek local-API integration in the lab.

The Home Assistant lab, the Venus E 3.0 Modbus model and, when it has booted,
the Renode Venus E 3.0 Control 151 emulator must already be running. This does
not contact a physical battery or the user's Home Assistant.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HA = "http://127.0.0.1:8123"
CLIENT = "http://127.0.0.1:8123/"
POWER = "sensor.p1_meter_power"
QUALITY = "sensor.omnibattery_pd_control_quality"
FIRMWARE = ("172.30.28.20", 30000)


def call(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    form: bool = False,
    timeout: float = 120,
) -> dict[str, Any]:
    if form:
        data = urllib.parse.urlencode(payload or {}).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    elif payload is None:
        data = None
        headers = {}
    else:
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode()
        raise RuntimeError(f"{method} {url} -> {err.code} {detail[:500]}") from err


def login() -> str:
    flow = call(
        "POST",
        f"{HA}/auth/login_flow",
        {
            "client_id": CLIENT,
            "handler": ["homeassistant", None],
            "redirect_uri": CLIENT,
        },
    )
    flow = call(
        "POST",
        f"{HA}/auth/login_flow/{flow['flow_id']}",
        {"client_id": CLIENT, "username": "dev", "password": "devpass1"},
    )
    token = call(
        "POST",
        f"{HA}/auth/token",
        {
            "grant_type": "authorization_code",
            "code": flow["result"],
            "client_id": CLIENT,
        },
        form=True,
    )
    return token["access_token"]


def state(token: str, entity_id: str) -> dict[str, Any] | None:
    try:
        return call("GET", f"{HA}/api/states/{entity_id}", token=token, timeout=15)
    except RuntimeError:
        return None


def entries(token: str, domain: str) -> list[dict[str, Any]]:
    found = call("GET", f"{HA}/api/config/config_entries/entry", token=token)
    return [item for item in found if item.get("domain") == domain]


def advance(token: str, flow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return call(
        "POST",
        f"{HA}/api/config/config_entries/flow/{flow_id}",
        payload,
        token=token,
        timeout=180,
    )


def setup_omnibattery(token: str) -> dict[str, Any]:
    if entries(token, "omnibattery"):
        return {"type": "already_configured"}
    started = call(
        "POST",
        f"{HA}/api/config/config_entries/flow",
        {"handler": "omnibattery", "show_advanced_options": False},
        token=token,
    )
    flow_id = started["flow_id"]
    steps = {
        "user": {
            "consumption_sensor": POWER,
            "meter_inverted": False,
            "max_contracted_power": 7000,
            "three_phase_enabled": False,
        },
        "batteries": {"num_batteries": 1},
        "battery_brand": {"brand": "marstek"},
        "battery_connection": {
            "name": "Venus E3",
            "host": "172.30.28.21",
            "port": 502,
            "slave_id": 1,
            "battery_version": "v3",
            "rs485_gateway": False,
        },
        "battery_limits": {
            "max_charge_power": 2500,
            "max_discharge_power": 800,
            "max_soc": 100,
            "min_soc": 12,
            "charge_hysteresis_percent": 2,
            "backup_offgrid_threshold": 50,
            "full_charge_voltage_taper_enabled": False,
            "track_mac": False,
        },
        "time_slots": {"configure_time_slots": False},
        "excluded_devices": {"configure_excluded_devices": False},
        "predictive_charging": {"configure_predictive_charging": False},
    }
    result = started
    for _ in range(12):
        if result.get("type") in {"create_entry", "abort"}:
            return result
        step = result.get("step_id")
        if step not in steps:
            return result
        result = advance(token, flow_id, steps[step])
    return result


def probe_firmware() -> dict[str, Any] | None:
    request = json.dumps(
        {"id": 1, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}}
    ).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    try:
        sock.sendto(request, FIRMWARE)
        data, _addr = sock.recvfrom(4096)
    except TimeoutError:
        return None
    finally:
        sock.close()
    try:
        return json.loads(data)
    except ValueError:
        return {"raw": data[:200].decode("utf-8", "replace")}


def modbus_closed() -> bool:
    sock = socket.socket()
    sock.settimeout(2)
    try:
        sock.connect(("172.30.28.20", 502))
    except OSError:
        return True
    else:
        return False
    finally:
        sock.close()


def setup_marstek(token: str) -> dict[str, Any]:
    if entries(token, "marstek"):
        return {"type": "already_configured"}
    result = call(
        "POST",
        f"{HA}/api/config/config_entries/flow",
        {"handler": "marstek", "show_advanced_options": False},
        token=token,
        timeout=180,
    )
    for _ in range(6):
        if result.get("type") in {"create_entry", "abort"}:
            return result
        step = result.get("step_id")
        flow_id = result.get("flow_id")
        if not flow_id:
            return result
        if step == "manual":
            result = advance(token, flow_id, {"host": "172.30.28.20", "port": 30000})
        elif step in {"user", "user_venus_e2"}:
            result = advance(token, flow_id, {"device": "__manual__"})
        else:
            return result
    return result


def sample(token: str, seconds: float) -> list[dict[str, Any]]:
    rows = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        power = state(token, POWER)
        quality = state(token, QUALITY)
        rows.append(
            {
                "t": round(time.time(), 1),
                "power": None if power is None else power.get("state"),
                "power_reported": None if power is None else power.get("last_reported"),
                "quality": None if quality is None else quality.get("state"),
                "quality_attrs": None if quality is None else quality.get("attributes"),
            }
        )
        time.sleep(5)
    return rows


def main() -> None:
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{HA}/api/", timeout=5).read()
            break
        except (OSError, urllib.error.URLError):
            time.sleep(3)
    token = login()
    ready = time.time() + 60
    while time.time() < ready and (state(token, POWER) or {}).get("state") in {
        None,
        "unavailable",
        "unknown",
    }:
        time.sleep(2)
    omni = setup_omnibattery(token)
    print(
        "omnibattery",
        json.dumps({k: omni.get(k) for k in ("type", "step_id", "reason", "errors")}),
    )
    firmware = None
    deadline = time.time() + 180
    while time.time() < deadline and firmware is None:
        firmware = probe_firmware()
        if firmware is None:
            time.sleep(5)
    print("firmware_get_device", json.dumps(firmware)[:800] if firmware else None)
    print("firmware_modbus_502_closed", modbus_closed())
    marstek: dict[str, Any] | None = None
    if firmware is not None:
        try:
            marstek = setup_marstek(token)
        except RuntimeError as err:
            marstek = {"error": str(err)}
        print("marstek_flow", json.dumps(marstek)[:800])
    print("sampling steady 90s")
    steady = sample(token, 90)
    print("steady_quality", [row["quality"] for row in steady])
    print("steady_power", [row["power"] for row in steady])
    report = {
        "omnibattery_version": "1.5.0",
        "omnibattery_source": "https://github.com/ffunes/Omnibattery 39b052bdac2856aeab02db1a7d20124142284d71",
        "grid_sensor": POWER,
        "sign": "positive import, negative export, meter_inverted false",
        "venus_e3_model": "172.30.28.21:502 tools/lab/venus_e3_modbus.py, Python model, not firmware",
        "omnibattery_flow": {
            k: omni.get(k) for k in ("type", "step_id", "reason", "errors")
        },
        "firmware_get_device": firmware,
        "firmware_modbus_502_closed": modbus_closed(),
        "marstek_flow": marstek,
        "steady": steady,
    }
    with open("/tmp/hw-consumer-observe.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print("wrote /tmp/hw-consumer-observe.json")


if __name__ == "__main__":
    main()
