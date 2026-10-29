"""Continue the lab checks from an already paired 1-phase meter."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request

import aiohttp

HA = "http://127.0.0.1:8123"
CLIENT = HA + "/"
ONE = "http://127.0.0.1:8082"
THREE = "http://127.0.0.1:8081"
POWER = "sensor.p1_meter_power"


def call(method: str, path: str, payload: dict | None = None, token: str | None = None, form: bool = False, timeout: int = 60):
    headers: dict[str, str] = {}
    data = None
    if payload is not None:
        if form:
            data = urllib.parse.urlencode(payload).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(HA + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode()
        return json.loads(body) if body else {}


def meter(method: str, base: str, path: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def login() -> str:
    flow = call(
        "POST",
        "/auth/login_flow",
        {"client_id": CLIENT, "handler": ["homeassistant", None], "redirect_uri": CLIENT},
    )
    flow = call(
        "POST",
        f"/auth/login_flow/{flow['flow_id']}",
        {"client_id": CLIENT, "username": "dev", "password": "devpass1"},
    )
    return call(
        "POST",
        "/auth/token",
        {"grant_type": "authorization_code", "code": flow["result"], "client_id": CLIENT},
        form=True,
    )["access_token"]


def state(token: str, entity_id: str) -> dict | None:
    try:
        return call("GET", f"/api/states/{entity_id}", token=token)
    except Exception:
        return None


def wait_until(token: str, entity_id: str, wanted: str, timeout: float) -> dict | None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = state(token, entity_id)
        if last and last["state"] == wanted:
            return last
        time.sleep(0.4)
    return last


async def enable(token: str, entity_ids: list[str]) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"{HA}/api/websocket") as websocket:
            await websocket.receive_json()
            await websocket.send_json({"type": "auth", "access_token": token})
            if (await websocket.receive_json()).get("type") != "auth_ok":
                raise RuntimeError("auth failed")
            for number, entity_id in enumerate(entity_ids, start=1):
                await websocket.send_json(
                    {
                        "id": number,
                        "type": "config/entity_registry/update",
                        "entity_id": entity_id,
                        "disabled_by": None,
                    }
                )
                while True:
                    message = await websocket.receive_json()
                    if message.get("id") == number:
                        if not message.get("success"):
                            raise RuntimeError(str(message))
                        break


def record(name: str, ok: bool, detail: str, results: list[dict]) -> None:
    results.append({"scenario": name, "ok": ok, "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)


def main() -> None:
    token = login()
    results: list[dict] = []
    asyncio.run(
        enable(
            token,
            [
                "sensor.p1_meter_voltage",
                "sensor.p1_meter_current_phase_1",
                "sensor.p1_meter_power_factor",
                "sensor.p1_meter_uptime",
                "sensor.p1_meter_wi_fi_strength",
                "sensor.p1_meter_frequency",
            ],
        )
    )
    time.sleep(2)
    voltage = wait_until(token, "sensor.p1_meter_voltage", "229.6", 20)
    current = state(token, "sensor.p1_meter_current_phase_1")
    factor = state(token, "sensor.p1_meter_power_factor")
    strength = state(token, "sensor.p1_meter_wi_fi_strength")
    frequency = state(token, "sensor.p1_meter_frequency")
    record(
        "voltage current power factor wifi frequency",
        bool(
            voltage
            and voltage["attributes"].get("device_class") == "voltage"
            and current
            and current["state"] == "2.95"
            and current["attributes"].get("device_class") == "current"
            and factor
            and factor["state"] == "99"
            and factor["attributes"].get("device_class") == "power_factor"
            and strength
            and strength["state"] == "80"
            and frequency
            and frequency["state"] == "50.0"
        ),
        f"V={None if not voltage else voltage['state']} A={None if not current else current['state']} pf={None if not factor else factor['state']} wifi={None if not strength else strength['state']} Hz={None if not frequency else frequency['state']}",
        results,
    )
    uptime = state(token, "sensor.p1_meter_uptime")
    time.sleep(4)
    later = state(token, "sensor.p1_meter_uptime")
    record(
        "uptime stable between system pushes",
        bool(uptime and later and uptime["state"] == later["state"] and uptime["attributes"].get("device_class") == "timestamp"),
        str(None if not uptime else uptime["state"]),
        results,
    )
    meter("POST", ONE, "/uptime", {"seconds": 10})
    jumped = None
    deadline = time.time() + 15
    while time.time() < deadline:
        jumped = state(token, "sensor.p1_meter_uptime")
        if uptime and jumped and jumped["state"] != uptime["state"]:
            break
        time.sleep(0.4)
    record(
        "uptime moves after reboot-sized system push",
        bool(uptime and jumped and jumped["state"] != uptime["state"]),
        f"{None if not uptime else uptime['state']} -> {None if not jumped else jumped['state']}",
        results,
    )
    meter("POST", ONE, "/profile", {"name": "export_1phase"})
    exporting = wait_until(token, POWER, "-678.0", 15)
    exporting_current = state(token, "sensor.p1_meter_current_phase_1")
    record(
        "export sign on power and current",
        bool(exporting and exporting_current and exporting_current["state"].startswith("-")),
        f"power={None if not exporting else exporting['state']} current={None if not exporting_current else exporting_current['state']}",
        results,
    )
    meter("POST", ONE, "/profile", {"name": "1phase"})
    wait_until(token, POWER, "678.0", 15)

    import subprocess

    def new_logs() -> str:
        return subprocess.check_output(
            ["docker", "logs", "--since", "20s", "homewizard-instant-homeassistant-1"],
            text=True,
            stderr=subprocess.STDOUT,
        )

    meter("POST", ONE, "/fault", {"name": "wrong_types"})
    time.sleep(2)
    kept = state(token, POWER)
    logged = "wrong type" in new_logs()
    meter("POST", ONE, "/clear")
    wait_until(token, POWER, "678.0", 15)
    record("wrong_types", bool(kept and kept["state"] == "678.0" and logged), f"state={None if not kept else kept['state']} logged={logged}", results)

    meter("POST", ONE, "/fault", {"name": "restart"}, timeout=20)
    restarted = wait_until(token, POWER, "678.0", 30)
    record("server restart", bool(restarted), f"state={None if not restarted else restarted['state']}", results)

    meter("POST", ONE, "/fault", {"name": "tls_change"}, timeout=20)
    down = None
    deadline = time.time() + 30
    while time.time() < deadline:
        down = state(token, POWER)
        if down and down["state"] == "unavailable":
            break
        time.sleep(0.4)
    meter("POST", ONE, "/clear")
    back = wait_until(token, POWER, "678.0", 30)
    record("tls cert change", bool(down and down["state"] == "unavailable" and back), f"down={None if not down else down['state']} back={None if not back else back['state']}", results)

    meter("POST", ONE, "/fault", {"name": "slow", "interval": 8})
    time.sleep(4)
    slow = state(token, POWER)
    meter("POST", ONE, "/clear")
    record("slow pushes stay available", bool(slow and slow["state"] not in {"unavailable", "unknown"}), f"state={None if not slow else slow['state']}", results)

    meter("POST", ONE, "/fault", {"name": "identity_change"})
    deadline = time.time() + 20
    identity = None
    while time.time() < deadline:
        identity = state(token, POWER)
        if identity and identity["state"] == "unavailable":
            break
        time.sleep(0.4)
    logged_identity = "identity changed" in subprocess.check_output(
        ["docker", "logs", "--since", "25s", "homewizard-instant-homeassistant-1"],
        text=True,
        stderr=subprocess.STDOUT,
    )
    meter("POST", ONE, "/clear")
    identity_back = wait_until(token, POWER, "678.0", 30)
    record(
        "identity change",
        bool(identity and identity["state"] == "unavailable" and identity_back and logged_identity),
        f"down={None if not identity else identity['state']} back={None if not identity_back else identity_back['state']} logged={logged_identity}",
        results,
    )

    other = call(
        "POST",
        "/api/config/config_entries/flow",
        {"handler": "homewizard_instant", "show_advanced_options": False},
        token=token,
    )
    asked = call(
        "POST",
        f"/api/config/config_entries/flow/{other['flow_id']}",
        {"ip_address": "172.30.28.10"},
        token=token,
    )
    meter("POST", THREE, "/button")
    created = call("POST", f"/api/config/config_entries/flow/{asked['flow_id']}", {}, token=token)
    phase3 = wait_until(token, "sensor.p1_meter_power_phase_3", "10.0", 20)
    water = state(token, "sensor.water_meter_water_meter")
    sockets = meter("GET", THREE, "/state")["websockets"]
    record(
        "second entry 3-phase and water",
        created.get("type") == "create_entry" and bool(phase3) and bool(water and water["state"] == "123.456") and sockets == 1,
        f"flow={created.get('type')} L3={None if not phase3 else phase3['state']} water={None if not water else water['state']} sockets={sockets}",
        results,
    )
    entries = [
        entry
        for entry in call("GET", "/api/config/config_entries/entry", token=token)
        if entry["domain"] == "homewizard_instant"
    ]
    three_id = next(entry["entry_id"] for entry in entries if entry["entry_id"] != "01M6442DRJ9B1D2X6KJ4EGSY42")
    call("DELETE", f"/api/config/config_entries/entry/{three_id}", token=token)
    time.sleep(2)
    closed = meter("GET", THREE, "/state")["websockets"]
    still = state(token, POWER)
    record(
        "remove entry closes its socket",
        closed == 0 and bool(still and still["state"] == "678.0"),
        f"sockets={closed} power={None if not still else still['state']}",
        results,
    )
    entry_id = "01M6442DRJ9B1D2X6KJ4EGSY42"
    call("POST", f"/api/config/config_entries/entry/{entry_id}/reload", token=token)
    time.sleep(1)
    mid = meter("GET", ONE, "/state")["websockets"]
    reloaded = wait_until(token, POWER, "678.0", 20)
    record("reload entry", bool(reloaded) and mid <= 1, f"state={None if not reloaded else reloaded['state']} sockets={mid}", results)

    with open("/tmp/hw-lab-rest.json", "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)
    failed = [item for item in results if not item["ok"]]
    print(f"passed={len(results)-len(failed)} failed={len(failed)}", flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
