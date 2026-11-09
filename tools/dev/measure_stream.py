"""Measure emulator-push to Home Assistant state latency.

The P1 emulator and Home Assistant lab must already be running. This script
does not talk to a physical meter. Times are host wall clock, which the
emulator container shares. Results go to /tmp/hw-stream-measure.json.
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

HA = "http://127.0.0.1:8123"
CLIENT = "http://127.0.0.1:8123/"
METER = "http://127.0.0.1:8082"
POWER = "sensor.p1_meter_power"


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
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
        raise RuntimeError(f"{method} {url} -> {err.code} {detail[:300]}") from err


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


def meter_state() -> dict[str, Any]:
    return http_json("GET", f"{METER}/state")


def ha_state(token: str, entity_id: str = POWER) -> dict[str, Any] | None:
    try:
        return http_json("GET", f"{HA}/api/states/{entity_id}", token=token)
    except RuntimeError:
        return None


def parse_stamp(value: str | None) -> float | None:
    if not value:
        return None
    # Home Assistant returns UTC with offset, for example 2026-11-08T22:36:33.123456+00:00
    from datetime import datetime

    return datetime.fromisoformat(value).timestamp()


def wait_power(token: str, predicate, timeout: float) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = ha_state(token)
        if last is not None and predicate(last):
            return last
        time.sleep(0.05)
    return last


def pair(token: str) -> None:
    entries = http_json("GET", f"{HA}/api/config/config_entries/entry", token=token)
    if any(item.get("domain") == "homewizard_instant" for item in entries):
        return
    started = http_json(
        "POST",
        f"{HA}/api/config/config_entries/flow",
        {"handler": "homewizard_instant", "show_advanced_options": False},
        token=token,
    )
    asked = http_json(
        "POST",
        f"{HA}/api/config/config_entries/flow/{started['flow_id']}",
        {"ip_address": "172.30.28.11"},
        token=token,
        timeout=60,
    )
    if asked.get("step_id") != "authorize":
        raise RuntimeError(f"unexpected flow {asked}")
    http_json("POST", f"{METER}/button")
    result = http_json(
        "POST",
        f"{HA}/api/config/config_entries/flow/{asked['flow_id']}",
        {},
        token=token,
    )
    if result.get("type") != "create_entry":
        http_json("POST", f"{METER}/button")
        result = http_json(
            "POST",
            f"{HA}/api/config/config_entries/flow/{asked['flow_id']}",
            {},
            token=token,
        )
    if result.get("type") != "create_entry":
        raise RuntimeError(f"pairing failed: {result}")


def step_latency(token: str, power: float) -> dict[str, Any]:
    """Time from the emulator sending a new power to the HA state change."""
    before = meter_state()
    before_push = before.get("last_push_unix") or 0
    http_json("POST", f"{METER}/overlay", {"power_w": power})
    sent = None
    deadline = time.time() + 5
    while time.time() < deadline:
        state = meter_state()
        push = state.get("last_push_unix") or 0
        if push > before_push and state.get("last_push_power_w") == power:
            sent = push
            break
        time.sleep(0.01)
    if sent is None:
        return {"power_w": power, "error": "emulator did not send"}

    def matches(item: dict[str, Any]) -> bool:
        try:
            return abs(float(item["state"]) - power) < 0.05
        except (TypeError, ValueError):
            return False

    updated = wait_power(token, matches, 5)
    if updated is None:
        return {"power_w": power, "sent": sent, "error": "state did not change"}
    reported = parse_stamp(updated.get("last_reported"))
    changed = parse_stamp(updated.get("last_changed"))
    return {
        "power_w": power,
        "state": updated["state"],
        "send_to_last_reported_ms": None
        if reported is None
        else round((reported - sent) * 1000, 1),
        "send_to_last_changed_ms": None
        if changed is None
        else round((changed - sent) * 1000, 1),
        "unit": updated["attributes"].get("unit_of_measurement"),
        "device_class": updated["attributes"].get("device_class"),
    }


def sample_freshness(token: str, seconds: float) -> dict[str, Any]:
    """How often last_reported moves while the numeric state stays put."""
    samples: list[dict[str, Any]] = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        item = ha_state(token)
        meter = meter_state()
        if item is not None:
            samples.append(
                {
                    "t": time.time(),
                    "state": item["state"],
                    "last_reported": item.get("last_reported"),
                    "last_changed": item.get("last_changed"),
                    "last_updated": item.get("last_updated"),
                    "push": meter.get("last_push_unix"),
                    "push_power": meter.get("last_push_power_w"),
                }
            )
        time.sleep(0.2)
    reports = []
    previous = None
    for sample in samples:
        stamp = sample["last_reported"]
        if stamp and stamp != previous:
            reports.append(parse_stamp(stamp))
            previous = stamp
    gaps = [
        round((reports[index] - reports[index - 1]) * 1000, 1)
        for index in range(1, len(reports))
        if reports[index] is not None and reports[index - 1] is not None
    ]
    states = {sample["state"] for sample in samples}
    return {
        "samples": len(samples),
        "distinct_states": sorted(states),
        "report_count": len(reports),
        "report_gap_ms_median": statistics.median(gaps) if gaps else None,
        "report_gap_ms_max": max(gaps) if gaps else None,
        "available": all(
            sample["state"] not in {"unavailable", "unknown"} for sample in samples
        ),
    }


def main() -> None:
    token = login()
    pair(token)
    http_json("POST", f"{METER}/clear")
    http_json("POST", f"{METER}/overlay", {"reset": True})
    http_json("POST", f"{METER}/clock", {"frozen": False})
    http_json("POST", f"{METER}/steady", {"enabled": True})
    ready = wait_power(
        token, lambda item: item["state"] not in {"unavailable", "unknown"}, 40
    )
    if ready is None:
        raise SystemExit("power entity did not become available")

    steps = [
        step_latency(token, power) for power in (100, 0, -250, 400, 400.0, -1, 1500)
    ]
    # 400 repeated on purpose: the second write is an equal value with a new send.
    steady = sample_freshness(token, 8)
    http_json(
        "POST", f"{METER}/clock", {"frozen": True, "timestamp": "2026-11-08T12:00:00"}
    )
    http_json("POST", f"{METER}/overlay", {"power_w": 222})
    frozen_ready = wait_power(
        token,
        lambda item: (
            item["state"] not in {"unavailable", "unknown"}
            and abs(float(item["state"]) - 222) < 0.05
        ),
        5,
    )
    frozen = sample_freshness(token, 8)
    http_json("POST", f"{METER}/clock", {"frozen": False})

    http_json("POST", f"{METER}/fault", {"name": "drop"})
    dropped_at = time.time()
    dropped = wait_power(token, lambda item: item["state"] == "unavailable", 15)
    http_json("POST", f"{METER}/clear")
    restored = wait_power(
        token, lambda item: item["state"] not in {"unavailable", "unknown"}, 20
    )
    drop = {
        "unavailable_after_s": None
        if dropped is None
        else round(time.time() - dropped_at, 2),
        "restored": restored is not None and restored["state"] != "unavailable",
        "restored_state": None if restored is None else restored["state"],
    }

    http_json("POST", f"{METER}/fault", {"name": "malformed_json"})
    time.sleep(3)
    during_junk = ha_state(token)
    http_json("POST", f"{METER}/clear")
    after_junk = wait_power(
        token, lambda item: item["state"] not in {"unavailable", "unknown"}, 15
    )

    http_json("POST", f"{METER}/fault", {"name": "silent"})
    silent_started = time.time()
    still = None
    gone = None
    while time.time() - silent_started < 75:
        item = ha_state(token)
        age = time.time() - silent_started
        if item and item["state"] != "unavailable" and age > 15 and still is None:
            still = {
                "age_s": round(age, 1),
                "state": item["state"],
                "last_reported": item.get("last_reported"),
            }
        if item and item["state"] == "unavailable" and gone is None:
            gone = {"age_s": round(age, 1), "last_reported": item.get("last_reported")}
            break
        time.sleep(1)
    http_json("POST", f"{METER}/clear")
    silent_back = wait_power(
        token, lambda item: item["state"] not in {"unavailable", "unknown"}, 20
    )

    entries = [
        item
        for item in http_json(
            "GET", f"{HA}/api/config/config_entries/entry", token=token
        )
        if item.get("domain") == "homewizard_instant"
    ]
    sockets_before = meter_state().get("websockets")
    if entries:
        http_json(
            "POST",
            f"{HA}/api/config/config_entries/entry/{entries[0]['entry_id']}/reload",
            token=token,
        )
    time.sleep(2)
    sockets_during = meter_state().get("websockets")
    reloaded = wait_power(
        token, lambda item: item["state"] not in {"unavailable", "unknown"}, 30
    )

    numeric = [
        item["send_to_last_changed_ms"]
        for item in steps
        if isinstance(item.get("send_to_last_changed_ms"), (int, float))
        and item["send_to_last_changed_ms"] >= 0
    ]
    report = {
        "power_entity": POWER,
        "steps": steps,
        "step_latency_ms_median": statistics.median(numeric) if numeric else None,
        "step_latency_ms_max": max(numeric) if numeric else None,
        "steady_advancing_timestamp": steady,
        "frozen_timestamp_same_power": {
            "reached": frozen_ready is not None,
            **frozen,
        },
        "drop": drop,
        "malformed": {
            "state_during": None if during_junk is None else during_junk["state"],
            "state_after": None if after_junk is None else after_junk["state"],
        },
        "silent": {
            "still_live_after_15s": still,
            "unavailable": gone,
            "restored": silent_back is not None,
        },
        "reload": {
            "sockets_before": sockets_before,
            "sockets_during": sockets_during,
            "restored": reloaded is not None,
            "state": None if reloaded is None else reloaded["state"],
        },
    }
    print(json.dumps(report, indent=2))
    with open("/tmp/hw-stream-measure.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)


if __name__ == "__main__":
    main()
