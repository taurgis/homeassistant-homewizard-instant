"""Run OmniBattery against the lab P1 meter and report its PD verdict.

Start the lab first:

    export OMNIBATTERY_REPO=/path/to/Omnibattery
    cd .devcontainer
    docker compose -f docker-compose.yml -f docker-compose.omnibattery.yml up -d

Then, with aiohttp installed:

    python3 tools/dev/omnibattery_pd.py --minutes 10 --out /tmp/omnibattery-pd.json

The script onboards a fresh Home Assistant as dev / devpass1, pairs
homewizard_instant with the 1-phase meter, adds OmniBattery with the Venus E 3.0
Modbus model (or, with ``--battery-host 172.30.28.22``, Control firmware 151 in
the Renode emulator; see docs/development.md), and then runs three phases: a steady house load, load steps
(including export, so the battery charges), and one dropped meter connection.
It records every verdict of ``sensor.omnibattery_pd_control_quality`` and the
interval between reports of ``sensor.p1_meter_power``. Nothing here talks to a
physical meter or battery.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

import aiohttp

HA = "http://127.0.0.1:8123"
CLIENT = f"{HA}/"
METER = "http://127.0.0.1:8082"
VENUS = "http://127.0.0.1:8090"
POWER = "sensor.p1_meter_power"
QUALITY = "sensor.omnibattery_pd_control_quality"
USER = {"name": "Dev", "username": "dev", "password": "devpass1", "language": "en"}

OMNIBATTERY_STEPS: dict[str, dict[str, Any]] = {
    # Offered after an earlier entry was removed; start clean so --battery-host
    # applies.
    "restore_backup": {"restore": False},
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


class Lab:
    """Home Assistant REST and WebSocket helpers."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.token = ""

    async def call(
        self, method: str, url: str, payload: Any = None, **kwargs: Any
    ) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        async with self.session.request(
            method, url, json=payload, headers=headers, **kwargs
        ) as response:
            text = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"{method} {url} -> {response.status} {text[:300]}")
            return json.loads(text) if text else {}

    async def wait_ready(self) -> None:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                async with self.session.get(f"{HA}/api/") as response:
                    if response.status in (200, 401):
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(3)
        raise RuntimeError("Home Assistant did not answer")

    async def _needs_owner(self) -> bool:
        """True on a fresh instance; the onboarding API is gone once done."""
        async with self.session.get(f"{HA}/api/onboarding") as response:
            if response.status == 404:
                return False
            steps = await response.json()
        return not next(step["done"] for step in steps if step["step"] == "user")

    async def login(self) -> None:
        fresh = await self._needs_owner()
        if fresh:
            created = await self.call(
                "POST", f"{HA}/api/onboarding/users", {**USER, "client_id": CLIENT}
            )
            code = created["auth_code"]
        else:
            flow = await self.call(
                "POST",
                f"{HA}/auth/login_flow",
                {
                    "client_id": CLIENT,
                    "handler": ["homeassistant", None],
                    "redirect_uri": CLIENT,
                },
            )
            flow = await self.call(
                "POST",
                f"{HA}/auth/login_flow/{flow['flow_id']}",
                {
                    "client_id": CLIENT,
                    "username": USER["username"],
                    "password": USER["password"],
                },
            )
            code = flow["result"]
        async with self.session.post(
            f"{HA}/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": CLIENT,
            },
        ) as response:
            self.token = (await response.json())["access_token"]
        if not fresh:
            return
        for step in ("core_config", "analytics", "integration"):
            payload = {"client_id": CLIENT, "redirect_uri": CLIENT}
            if step == "analytics":
                payload = {}
            try:
                await self.call("POST", f"{HA}/api/onboarding/{step}", payload)
            except RuntimeError:
                pass  # already done

    async def entries(self, domain: str) -> list[dict[str, Any]]:
        found = await self.call("GET", f"{HA}/api/config/config_entries/entry")
        return [entry for entry in found if entry["domain"] == domain]

    async def state(self, entity_id: str) -> dict[str, Any] | None:
        try:
            return await self.call("GET", f"{HA}/api/states/{entity_id}")
        except RuntimeError:
            return None

    async def flow(self, handler: str) -> dict[str, Any]:
        return await self.call(
            "POST",
            f"{HA}/api/config/config_entries/flow",
            {"handler": handler, "show_advanced_options": False},
        )

    async def advance(self, flow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.call(
            "POST",
            f"{HA}/api/config/config_entries/flow/{flow_id}",
            payload,
            timeout=aiohttp.ClientTimeout(total=180),
        )

    async def pair_meter(self) -> str:
        if await self.entries("homewizard_instant"):
            return "already_configured"
        started = await self.flow("homewizard_instant")
        asked = await self.advance(started["flow_id"], {"ip_address": "172.30.28.11"})
        for _ in range(2):
            await self.call("POST", f"{METER}/button")
            result = await self.advance(asked["flow_id"], {})
            if result.get("type") == "create_entry":
                return "create_entry"
        raise RuntimeError(f"pairing failed: {result}")

    async def add_omnibattery(self) -> str:
        if await self.entries("omnibattery"):
            return "already_configured"
        result = await self.flow("omnibattery")
        for _ in range(15):
            if result.get("type") in {"create_entry", "abort"}:
                break
            step = result.get("step_id")
            if step not in OMNIBATTERY_STEPS:
                raise RuntimeError(f"unknown OmniBattery step {step}: {result}")
            result = await self.advance(result["flow_id"], OMNIBATTERY_STEPS[step])
        if result.get("type") != "create_entry":
            raise RuntimeError(f"OmniBattery flow ended with {result}")
        return "create_entry"

    async def wait_state(self, entity_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = await self.state(entity_id)
            if current and current["state"] not in {"unknown", "unavailable"}:
                return current
            await asyncio.sleep(2)
        raise RuntimeError(f"{entity_id} did not become available")


class Recorder:
    """Every report of the grid sensor and every change of the PD verdict.

    Home Assistant does not deliver ``state_reported`` through
    ``subscribe_events``, and ``/api/states`` keeps a cached ``last_reported``
    after an unchanged report, so reports are read from the live attribute
    through the template API, ten times a second.
    """

    def __init__(self) -> None:
        self.reports: list[tuple[float, str]] = []
        self.verdicts: list[dict[str, Any]] = []
        self.phase = "setup"

    async def run(self, lab: Lab) -> None:
        await asyncio.gather(self._reports(lab), self._verdicts(lab))

    async def _reports(self, lab: Lab) -> None:
        template = (
            f"{{{{ states.{POWER}.last_reported.timestamp() }}}} "
            f"{{{{ states('{POWER}') }}}}"
        )
        last = None
        while True:
            reply = await lab.session.post(
                f"{HA}/api/template",
                json={"template": template},
                headers={"Authorization": f"Bearer {lab.token}"},
            )
            stamp, state = (await reply.text()).split(" ", 1)
            if stamp != last:
                last = stamp
                self.reports.append((float(stamp), state))
            await asyncio.sleep(0.1)

    async def _verdicts(self, lab: Lab) -> None:
        async with lab.session.ws_connect(f"{HA}/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": lab.token})
            await ws.receive_json()
            await ws.send_json(
                {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
            )
            async for message in ws:
                payload = json.loads(message.data)
                if payload.get("type") != "event":
                    continue
                data = payload["event"]["data"]
                new = data.get("new_state")
                if data.get("entity_id") == QUALITY and new is not None:
                    self.verdicts.append(
                        {
                            "t": round(time.time(), 1),
                            "phase": self.phase,
                            "state": new["state"],
                            "rms_error_w": new["attributes"].get("rms_error_w"),
                            "oscillation_per_min": new["attributes"].get(
                                "oscillation_per_min"
                            ),
                        }
                    )


def cadence(
    reports: list[tuple[float, str]], start: float, end: float
) -> dict[str, Any]:
    stamps = [t for t, state in reports if start <= t <= end and state != "unavailable"]
    gaps = [b - a for a, b in itertools.pairwise(stamps)]
    if len(gaps) < 2:
        return {"reports": len(stamps)}
    gaps_sorted = sorted(gaps)
    return {
        "reports": len(stamps),
        "mean_s": round(statistics.fmean(gaps), 4),
        "stdev_ms": round(statistics.stdev(gaps) * 1000, 2),
        "p99_s": round(gaps_sorted[int(0.99 * (len(gaps_sorted) - 1))], 4),
        "max_s": round(max(gaps), 3),
        "under_1s": sum(gap < 1.0 for gap in gaps),
    }


async def poll_quality(
    lab: Lab, recorder: Recorder, seconds: float
) -> list[dict[str, Any]]:
    """The quality sensor polls its controller; sample it like a dashboard would."""
    rows = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = await lab.state(QUALITY)
        venus = await lab.call("GET", f"{VENUS}/state")
        if current:
            rows.append(
                {
                    "t": round(time.time(), 1),
                    "phase": recorder.phase,
                    "state": current["state"],
                    "rms_error_w": current["attributes"].get("rms_error_w"),
                    "oscillation_per_min": current["attributes"].get(
                        "oscillation_per_min"
                    ),
                    "grid_w": venus["grid_w"],
                    "battery_ac_w": venus["ac_power_w"],
                    "house_w": venus["house_w"],
                }
            )
        await asyncio.sleep(5)
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--minutes", type=float, default=10, help="steady phase")
    parser.add_argument("--step-seconds", type=float, default=120)
    parser.add_argument("--out", default="/tmp/omnibattery-pd.json")
    parser.add_argument(
        "--battery-host",
        default="172.30.28.21",
        help="Modbus TCP host: the Python model, or 172.30.28.22 for the firmware",
    )
    args = parser.parse_args()
    OMNIBATTERY_STEPS["battery_connection"]["host"] = args.battery_host

    async with aiohttp.ClientSession() as session:
        lab = Lab(session)
        await lab.wait_ready()
        await lab.login()
        paired = await lab.pair_meter()
        await lab.wait_state(POWER, 120)
        await lab.call("POST", f"{VENUS}/load", {"watts": 450})
        battery = (await lab.call("GET", f"{VENUS}/state"))["model"]
        omni = await lab.add_omnibattery()
        print(f"meter {paired}, omnibattery {omni}", flush=True)
        await lab.wait_state(QUALITY, 180)

        recorder = Recorder()
        listener = asyncio.create_task(recorder.run(lab))
        samples: list[dict[str, Any]] = []
        phases: dict[str, tuple[float, float]] = {}

        async def phase(name: str, seconds: float, load: float | None = None) -> None:
            if load is not None:
                await lab.call("POST", f"{VENUS}/load", {"watts": load})
            recorder.phase = name
            start = time.time()
            print(f"phase {name} for {seconds:.0f} s", flush=True)
            samples.extend(await poll_quality(lab, recorder, seconds))
            phases[name] = (start, time.time())

        await phase("steady_450w", args.minutes * 60, 450)
        for load in (700, 250, -600, 450):
            await phase(f"step_{load}w", args.step_seconds, load)
        await lab.call("POST", f"{METER}/fault", {"name": "drop"})
        dropped = time.time()
        await phase("after_drop", args.step_seconds)
        listener.cancel()

    outage = [
        t for t, state in recorder.reports if t >= dropped and state == "unavailable"
    ]
    back = [
        t
        for t, state in recorder.reports
        if t >= dropped and state != "unavailable" and (not outage or t > outage[0])
    ]
    summary = {
        "phases": {
            name: {
                "verdicts": dict(
                    Counter(row["state"] for row in samples if row["phase"] == name)
                ),
                "last": next(
                    (row for row in reversed(samples) if row["phase"] == name), None
                ),
                "grid_sensor": cadence(recorder.reports, start, end),
            }
            for name, (start, end) in phases.items()
        },
        "drop_recovery_s": round(back[0] - dropped, 2) if back else None,
        "verdict_changes": recorder.verdicts,
    }
    report = {
        "summary": summary,
        "samples": samples,
        "battery": battery,
    }
    await asyncio.to_thread(
        Path(args.out).write_text, json.dumps(report, indent=2), "utf-8"
    )
    print(json.dumps(summary["phases"], indent=2))
    print("drop_recovery_s", summary["drop_recovery_s"])
    print("wrote", args.out)


if __name__ == "__main__":
    asyncio.run(main())
