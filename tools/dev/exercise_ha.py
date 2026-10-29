"""Drive the lab Home Assistant through the API v2 scenarios.

The meters must already be running (see docs/development.md). Results are
printed as a table and written to /tmp/hw-lab-results.json.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

HA = "http://127.0.0.1:8123"
CLIENT = "http://127.0.0.1:8123/"
METERS = {
    "1phase": {"control": "http://127.0.0.1:8082", "ip": "172.30.28.11", "serial": "112233445566"},
    "3phase": {"control": "http://127.0.0.1:8081", "ip": "172.30.28.10", "serial": "aabbccddeeff"},
}


@dataclass
class Result:
    scenario: str
    ok: bool
    detail: str


@dataclass
class Client:
    token: str
    results: list[Result] = field(default_factory=list)

    def call(self, method: str, path: str, payload: dict | None = None, timeout: int = 90) -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        request = urllib.request.Request(HA + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as err:
            detail = err.read().decode()
            raise RuntimeError(f"{method} {path} -> {err.code} {detail[:400]}") from err

    def record(self, scenario: str, ok: bool, detail: str) -> None:
        self.results.append(Result(scenario, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {scenario}: {detail}")


def enable_entities(token: str, entity_ids: list[str]) -> None:
    import asyncio

    import aiohttp

    async def _enable() -> None:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"{HA}/api/websocket") as websocket:
                await websocket.receive_json()
                await websocket.send_json({"type": "auth", "access_token": token})
                auth = await websocket.receive_json()
                if auth.get("type") != "auth_ok":
                    raise RuntimeError(auth)
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
                                raise RuntimeError(f"{entity_id}: {message}")
                            break

    asyncio.run(_enable())


def http_json(method: str, url: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode()
        return json.loads(body) if body else {}


def login() -> str:
    flow = http_json(
        "POST",
        f"{HA}/auth/login_flow",
        {
            "client_id": CLIENT,
            "handler": ["homeassistant", None],
            "redirect_uri": CLIENT,
        },
    )
    flow = http_json(
        "POST",
        f"{HA}/auth/login_flow/{flow['flow_id']}",
        {"client_id": CLIENT, "username": "dev", "password": "devpass1"},
    )
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": flow["result"],
            "client_id": CLIENT,
        }
    ).encode()
    request = urllib.request.Request(
        f"{HA}/auth/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())["access_token"]


def wait_api() -> None:
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{HA}/api/onboarding", timeout=5).read()
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("Home Assistant did not answer")


def logs() -> str:
    return subprocess.check_output(
        ["docker", "logs", "homewizard-instant-homeassistant-1"],
        text=True,
        stderr=subprocess.STDOUT,
    )


def state_of(client: Client, entity_id: str) -> dict | None:
    try:
        return client.call("GET", f"/api/states/{entity_id}")
    except RuntimeError:
        return None


def wait_state(client: Client, entity_id: str, predicate, timeout: float) -> dict | None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = state_of(client, entity_id)
        if last is not None and predicate(last):
            return last
        time.sleep(0.5)
    return last


def entries(client: Client) -> list[dict]:
    return [
        entry
        for entry in client.call("GET", "/api/config/config_entries/entry")
        if entry["domain"] == "homewizard_instant"
    ]


def flows(client: Client) -> list[dict]:
    """List in-progress flows.

    Home Assistant 2026 removed GET /api/config/config_entries/flow.
    The websocket command is the supported path on 2025.10 and 2026.10.
    """
    import asyncio

    import aiohttp

    async def _progress() -> list[dict]:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"{HA}/api/websocket") as websocket:
                await websocket.receive_json()
                await websocket.send_json({"type": "auth", "access_token": client.token})
                auth = await websocket.receive_json()
                if auth.get("type") != "auth_ok":
                    raise RuntimeError(auth)
                await websocket.send_json({"id": 1, "type": "config_entries/flow/progress"})
                message = await websocket.receive_json()
                if not message.get("success"):
                    raise RuntimeError(message)
                result = message.get("result") or []
                return result if isinstance(result, list) else []

    return asyncio.run(_progress())


def finish_pairing(client: Client, flow_id: str, meter: str) -> dict:
    http_json("POST", f"{METERS[meter]['control']}/button")
    result = client.call("POST", f"/api/config/config_entries/flow/{flow_id}", {})
    if result.get("type") == "form" and (result.get("errors") or {}).get("base"):
        http_json("POST", f"{METERS[meter]['control']}/button")
        result = client.call("POST", f"/api/config/config_entries/flow/{flow_id}", {})
    return result


def configure(client: Client, meter: str) -> dict:
    started = client.call(
        "POST",
        "/api/config/config_entries/flow",
        {"handler": "homewizard_instant", "show_advanced_options": False},
    )
    asked = client.call(
        "POST",
        f"/api/config/config_entries/flow/{started['flow_id']}",
        {"ip_address": METERS[meter]["ip"]},
    )
    if asked.get("step_id") != "authorize":
        return asked
    return finish_pairing(client, asked["flow_id"], meter)


def main() -> None:
    wait_api()
    client = Client(login())
    before = logs()
    power = "sensor.p1_meter_power"

    # The 1-phase meter is paired by the manual check. Re-pair only if missing.
    if not any(entry["title"] == "P1 Meter" for entry in entries(client)):
        created = configure(client, "1phase")
        client.record("fresh config flow", created.get("type") == "create_entry", created.get("type", ""))
    else:
        client.record("fresh config flow", True, "entry already loaded from the pairing check")

    loaded = wait_state(client, power, lambda item: item["state"] not in {"unavailable", "unknown"}, 30)
    client.record(
        "realtime power",
        loaded is not None and loaded["state"] == "678.0",
        f"state={None if loaded is None else loaded['state']} unit={None if loaded is None else loaded['attributes'].get('unit_of_measurement')} class={None if loaded is None else loaded['attributes'].get('device_class')}",
    )
    energy = state_of(client, "sensor.p1_meter_energy_import")
    gas = state_of(client, "sensor.gas_meter_gas_meter")
    client.record(
        "import and gas values",
        energy is not None
        and energy["state"] == "10830.511"
        and energy["attributes"].get("unit_of_measurement") == "kWh"
        and energy["attributes"].get("device_class") == "energy"
        and gas is not None
        and gas["state"] == "111.5"
        and gas["attributes"].get("unit_of_measurement") == "m³"
        and gas["attributes"].get("device_class") == "gas",
        f"import={None if energy is None else energy['state']} gas={None if gas is None else gas['state']}",
    )
    phase2 = state_of(client, "sensor.p1_meter_power_phase_2")
    client.record(
        "1-phase omits phase 2",
        phase2 is None,
        "phase 2 entity absent" if phase2 is None else phase2["state"],
    )
    sockets = http_json("GET", f"{METERS['1phase']['control']}/state")["websockets"]
    client.record("socket open", sockets == 1, f"websockets={sockets}")

    enable_entities(
        client.token,
        [
            "sensor.p1_meter_voltage",
            "sensor.p1_meter_current_phase_1",
            "sensor.p1_meter_power_factor",
            "sensor.p1_meter_uptime",
            "sensor.p1_meter_wi_fi_strength",
            "sensor.p1_meter_frequency",
        ],
    )
    time.sleep(2)
    voltage = wait_state(client, "sensor.p1_meter_voltage", lambda item: item["state"] not in {"unavailable", "unknown"}, 20)
    current = state_of(client, "sensor.p1_meter_current_phase_1")
    factor = state_of(client, "sensor.p1_meter_power_factor")
    strength = state_of(client, "sensor.p1_meter_wi_fi_strength")
    frequency = state_of(client, "sensor.p1_meter_frequency")
    client.record(
        "voltage current power factor wifi frequency",
        voltage is not None
        and voltage["state"] == "229.6"
        and voltage["attributes"].get("device_class") == "voltage"
        and current is not None
        and current["state"] == "2.95"
        and current["attributes"].get("device_class") == "current"
        and factor is not None
        and factor["state"] == "99"
        and factor["attributes"].get("device_class") == "power_factor"
        and strength is not None
        and strength["state"] == "80"
        and frequency is not None
        and frequency["state"] == "50.0",
        f"V={None if voltage is None else voltage['state']} A={None if current is None else current['state']} pf={None if factor is None else factor['state']} wifi={None if strength is None else strength['state']} Hz={None if frequency is None else frequency['state']}",
    )
    uptime = state_of(client, "sensor.p1_meter_uptime")
    time.sleep(4)
    uptime_later = state_of(client, "sensor.p1_meter_uptime")
    client.record(
        "uptime stable between system pushes",
        uptime is not None
        and uptime_later is not None
        and uptime["state"] == uptime_later["state"]
        and uptime["attributes"].get("device_class") == "timestamp",
        f"{None if uptime is None else uptime['state']}",
    )
    http_json("POST", f"{METERS['1phase']['control']}/uptime", {"seconds": 10})
    jumped = wait_state(
        client,
        "sensor.p1_meter_uptime",
        lambda item: uptime is not None and item["state"] != uptime["state"],
        15,
    )
    client.record(
        "uptime moves after a reboot-sized system push",
        jumped is not None,
        f"from {None if uptime is None else uptime['state']} to {None if jumped is None else jumped['state']}",
    )

    http_json("POST", f"{METERS['1phase']['control']}/profile", {"name": "export_1phase"})
    exporting = wait_state(client, power, lambda item: item["state"].startswith("-"), 15)
    exporting_current = state_of(client, "sensor.p1_meter_current_phase_1")
    client.record(
        "export sign on power and current",
        exporting is not None
        and exporting["state"] == "-678.0"
        and exporting_current is not None
        and exporting_current["state"].startswith("-"),
        f"power={None if exporting is None else exporting['state']} current={None if exporting_current is None else exporting_current['state']}",
    )
    http_json("POST", f"{METERS['1phase']['control']}/profile", {"name": "1phase"})
    wait_state(client, power, lambda item: item["state"] == "678.0", 15)

    http_json("POST", f"{METERS['1phase']['control']}/steady", {"enabled": False})
    time.sleep(3)
    moving = state_of(client, power)
    client.record(
        "live power changes",
        moving is not None and moving["state"] not in {"678.0", "unavailable", "unknown"},
        f"state={None if moving is None else moving['state']}",
    )
    http_json("POST", f"{METERS['1phase']['control']}/steady", {"enabled": True})
    http_json("POST", f"{METERS['1phase']['control']}/profile", {"name": "1phase"})
    wait_state(client, power, lambda item: item["state"] == "678.0", 15)

    def unchanged(name: str, marker: str) -> None:
        http_json("POST", f"{METERS['1phase']['control']}/fault", {"name": name})
        time.sleep(3)
        current_state = state_of(client, power)
        fresh = logs()
        http_json("POST", f"{METERS['1phase']['control']}/clear")
        wait_state(client, power, lambda item: item["state"] == "678.0", 20)
        added = fresh[len(before):]
        ok = (
            current_state is not None
            and current_state["state"] == "678.0"
            and "Traceback" not in added
            and marker in added
        )
        client.record(name, ok, f"state={None if current_state is None else current_state['state']} log_has_{marker}={marker in added}")

    unchanged("malformed_json", "Ignoring malformed")
    unchanged("unknown_topic", "Ignoring HomeWizard topic")
    unchanged("null_fields", "null fields")
    unchanged("wrong_types", "wrong type")
    unchanged("out_of_range", "out-of-range")
    unchanged("missing_fields", "Ignoring invalid")

    # Drop and let the backoff reconnect by itself.
    http_json("POST", f"{METERS['1phase']['control']}/fault", {"name": "drop"})
    dropped = wait_state(client, power, lambda item: item["state"] == "unavailable", 15)
    restored = wait_state(client, power, lambda item: item["state"] == "678.0", 20)
    client.record(
        "drop reconnects with backoff",
        dropped is not None and restored is not None and "Traceback" not in logs()[len(before):],
        f"unavailable={dropped is not None} restored={restored is not None}",
    )

    http_json("POST", f"{METERS['1phase']['control']}/fault", {"name": "hang"})
    hung = wait_state(client, power, lambda item: item["state"] == "unavailable", 75)
    http_json("POST", f"{METERS['1phase']['control']}/clear")
    hung_back = wait_state(client, power, lambda item: item["state"] == "678.0", 30)
    client.record(
        "hang timeout then recover",
        hung is not None and hung_back is not None,
        f"unavailable={hung is not None} restored={hung_back is not None}",
    )

    http_json(
        "POST",
        f"{METERS['1phase']['control']}/fault",
        {"name": "restart"},
        timeout=20,
    )
    time.sleep(2)
    restarted = wait_state(client, power, lambda item: item["state"] == "678.0", 30)
    client.record("server restart", restarted is not None, f"state={None if restarted is None else restarted['state']}")

    http_json("POST", f"{METERS['1phase']['control']}/fault", {"name": "tls_change"})
    tls_down = wait_state(client, power, lambda item: item["state"] == "unavailable", 30)
    tls_log = logs()[len(before):]
    http_json("POST", f"{METERS['1phase']['control']}/clear")
    tls_back = wait_state(client, power, lambda item: item["state"] == "678.0", 30)
    client.record(
        "tls cert change",
        tls_down is not None and tls_back is not None and "Traceback" not in tls_log,
        f"unavailable={tls_down is not None} restored={tls_back is not None}",
    )

    http_json("POST", f"{METERS['1phase']['control']}/fault", {"name": "slow", "interval": 8})
    time.sleep(5)
    slow_state = state_of(client, power)
    http_json("POST", f"{METERS['1phase']['control']}/clear")
    client.record(
        "slow pushes stay available",
        slow_state is not None and slow_state["state"] not in {"unavailable", "unknown"},
        f"state={None if slow_state is None else slow_state['state']}",
    )

    http_json("POST", f"{METERS['1phase']['control']}/fault", {"name": "identity_change"})
    identity = wait_state(client, power, lambda item: item["state"] == "unavailable", 20)
    identity_log = logs()[len(before):]
    http_json("POST", f"{METERS['1phase']['control']}/clear")
    identity_back = wait_state(client, power, lambda item: item["state"] == "678.0", 30)
    client.record(
        "identity change",
        identity is not None and identity_back is not None and "identity changed" in identity_log,
        f"unavailable={identity is not None} restored={identity_back is not None} logged={'identity changed' in identity_log}",
    )

    def reauth(name: str) -> None:
        http_json("POST", f"{METERS['1phase']['control']}/fault", {"name": name})
        deadline = time.time() + 20
        progress: list[dict] = []
        while time.time() < deadline:
            progress = [
                item
                for item in flows(client)
                if item.get("handler") == "homewizard_instant" or item.get("context", {}).get("source") == "reauth"
            ]
            if progress:
                break
            time.sleep(0.5)
        down = state_of(client, power)
        http_json("POST", f"{METERS['1phase']['control']}/clear")
        recovered = False
        if progress:
            result = finish_pairing(client, progress[0]["flow_id"], "1phase")
            recovered = result.get("type") in {"abort", "create_entry"} or result.get("reason") == "reauth_successful"
        back = wait_state(client, power, lambda item: item["state"] == "678.0", 30)
        client.record(
            name,
            bool(progress) and down is not None and down["state"] == "unavailable" and back is not None,
            f"reauth={bool(progress)} flow_finished={recovered} restored={back is not None}",
        )

    reauth("refuse_auth")
    reauth("revoke_token")

    entry_list = entries(client)
    entry_id = entry_list[0]["entry_id"]
    client.call("POST", f"/api/config/config_entries/entry/{entry_id}/reload")
    time.sleep(1)
    sockets_mid = http_json("GET", f"{METERS['1phase']['control']}/state")["websockets"]
    reloaded = wait_state(client, power, lambda item: item["state"] == "678.0", 30)
    client.record(
        "reload entry",
        reloaded is not None and sockets_mid <= 1,
        f"state={None if reloaded is None else reloaded['state']} sockets_during={sockets_mid}",
    )

    # Second meter, then remove it and confirm the socket closes.
    other = configure(client, "3phase")
    phase3 = wait_state(
        client,
        "sensor.p1_meter_power_phase_3",
        lambda item: item["state"] not in {"unavailable", "unknown"},
        20,
    )
    water = state_of(client, "sensor.water_meter_water_meter")
    both = http_json("GET", f"{METERS['3phase']['control']}/state")["websockets"]
    client.record(
        "second entry 3-phase and water",
        other.get("type") == "create_entry"
        and phase3 is not None
        and phase3["state"] == "10.0"
        and water is not None
        and water["state"] == "123.456"
        and both == 1,
        f"flow={other.get('type')} L3={None if phase3 is None else phase3['state']} water={None if water is None else water['state']} sockets={both}",
    )
    three_entries = [entry for entry in entries(client)]
    three_id = next(entry["entry_id"] for entry in three_entries if entry["entry_id"] != entry_id)
    client.call("DELETE", f"/api/config/config_entries/entry/{three_id}")
    time.sleep(2)
    closed = http_json("GET", f"{METERS['3phase']['control']}/state")["websockets"]
    still = state_of(client, power)
    client.record(
        "remove entry closes its socket",
        closed == 0 and still is not None and still["state"] == "678.0",
        f"3phase_sockets={closed} 1phase_power={None if still is None else still['state']}",
    )

    added = logs()[len(before):]
    homewizard_lines = [
        line
        for line in added.splitlines()
        if "homewizard" in line.lower() and ("ERROR" in line or "WARNING" in line)
    ]
    tracebacks = [line for line in added.splitlines() if "Traceback" in line]
    client.record(
        "no traceback loop",
        not tracebacks,
        f"tracebacks={len(tracebacks)} homewizard_warnings={len(homewizard_lines)}",
    )
    print("--- homewizard warnings/errors ---")
    for line in homewizard_lines[-30:]:
        print(line)
    payload = {
        "passed": sum(item.ok for item in client.results),
        "failed": sum(not item.ok for item in client.results),
        "results": [item.__dict__ for item in client.results],
    }
    with open("/tmp/hw-lab-results.json", "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"passed={payload['passed']} failed={payload['failed']}")
    if payload["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
