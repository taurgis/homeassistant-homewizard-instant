"""Time each link from a house load step to the battery delivering it.

Needs the OmniBattery lab with the Python Venus model (docs/development.md) and
an OmniBattery entry already set up, for example by omnibattery_pd.py:

    python3 tools/dev/omnibattery_latency.py [steps] [controller_kp=0.7,...]

The optional second argument sets OmniBattery number entities
(``number.omnibattery_pd_<name>``, or a full entity id) for the run, and
``<switch entity>=on`` turns a switch on. Numbers go back to the values in
omnibattery_house.YOURS afterwards and switches turned on go back off.

The house steps between 250 W and 650 W, inside the 800 W discharge limit, so
every step can be closed fully. For each step the script records, on the
host's monotonic clock (which the containers share):

* ``sensor``: the load change reaching ``sensor.p1_meter_power`` in Home
  Assistant (P1 telegram cadence plus HomeWizard Instant);
* ``first_write`` / ``command``: the first set-point register write reaching
  the battery, and the write that completes the new command;
* ``t90``: the battery output covering 90 % of the step;
* ``settled``: the grid staying within 50 W of zero for 5 s.

Set ``MODBUS_LATENCY_S`` on the venus-e3 service to copy a battery that is slow
to answer Modbus requests.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time

import aiohttp

from omnibattery_house import YOURS, N
from omnibattery_pd import HA, POWER, VENUS, Lab

LOW, HIGH = 250.0, 650.0
SETTLE_BAND_W, SETTLE_HOLD_S = 50.0, 5.0


async def sensor_changes(lab: Lab, out: list[tuple[float, float]]) -> None:
    """Receive time and value of every state change of the grid sensor."""
    async with lab.session.ws_connect(f"{HA}/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": lab.token})
        await ws.receive_json()
        await ws.send_json(
            {
                "id": 1,
                "type": "subscribe_trigger",
                "trigger": {"platform": "state", "entity_id": POWER},
            }
        )
        async for message in ws:
            payload = json.loads(message.data)
            if payload.get("type") != "event":
                continue
            new = payload["event"]["variables"]["trigger"]["to_state"]
            try:
                out.append((time.monotonic(), float(new["state"])))
            except (TypeError, ValueError):
                pass


async def plant(lab: Lab, out: list[tuple[float, float, float]]) -> None:
    """Battery output and grid power, ten times a second."""
    while True:
        st = await lab.call("GET", f"{VENUS}/state")
        out.append((time.monotonic(), float(st["ac_power_w"]), float(st["grid_w"])))
        await asyncio.sleep(0.1)


async def step(lab, load, sensor, samples, hold):
    before = (await lab.call("GET", f"{VENUS}/state"))["ac_power_w"]
    t0 = time.monotonic()
    await lab.call("POST", f"{VENUS}/load", {"watts": load})
    await asyncio.sleep(hold)
    delta = load - (HIGH if load == LOW else LOW)
    trace = (await lab.call("GET", f"{VENUS}/trace"))["events"]

    seen = next(
        (t for t, v in sensor if t > t0 and abs(v - (load - before)) < abs(delta) / 2),
        None,
    )
    writes = [(t, ev, v) for t, ev, v in trace if t > t0 and ev.startswith("write")]
    commands = [(t, v) for t, ev, v in trace if t > t0 and ev == "command"]
    # The command that completes the first set-point sent after the step: the
    # last command of the first burst of writes (writes less than 0.9 s apart).
    first_burst_end = None
    if commands:
        first_burst_end = commands[0][0]
        for t, _ in commands[1:]:
            if t - first_burst_end > 0.9:
                break
            first_burst_end = t
    target = before + delta
    t90 = next(
        (t for t, ac, _ in samples if t > t0 and abs(ac - before) >= 0.9 * abs(delta)),
        None,
    )
    settled = None
    for t, _, grid in samples:
        if t <= t0:
            continue
        if abs(grid) > SETTLE_BAND_W:
            settled = None
        elif settled is None:
            settled = t
        if settled is not None and t - settled >= SETTLE_HOLD_S:
            break
    else:
        settled = None

    def rel(t):
        return None if t is None else round(t - t0, 2)

    return {
        "load_w": load,
        "target_ac_w": round(target),
        "sensor": rel(seen),
        "first_write": rel(writes[0][0] if writes else None),
        "command": rel(first_burst_end),
        "t90": rel(t90),
        "settled": rel(settled),
        "writes": len(writes),
    }


async def set_numbers(lab: Lab, values: dict[str, float | str]) -> None:
    """Set number entities; a value of on/off switches a switch entity."""
    for entity, value in values.items():
        if value in ("on", "off"):
            await lab.call(
                "POST",
                f"{HA}/api/services/switch/turn_{value}",
                {"entity_id": entity},
            )
            continue
        await lab.call(
            "POST",
            f"{HA}/api/services/number/set_value",
            {"entity_id": entity, "value": value},
        )


def parse(arg: str) -> dict[str, float | str]:
    """``controller_kp=0.7,switch.omnibattery_no_pd_mode=on`` to entity values."""
    values: dict[str, float | str] = {}
    for pair in filter(None, arg.split(",")):
        key, value = pair.split("=")
        entity = key if "." in key else N + key
        values[entity] = value if value in ("on", "off") else float(value)
    return values


async def main(steps: int, overrides: dict[str, float | str]) -> None:
    async with aiohttp.ClientSession() as session:
        lab = Lab(session)
        await lab.login()
        await set_numbers(lab, {**YOURS, **overrides})
        sensor: list[tuple[float, float]] = []
        samples: list[tuple[float, float, float]] = []
        tasks = [
            asyncio.create_task(sensor_changes(lab, sensor)),
            asyncio.create_task(plant(lab, samples)),
        ]
        await lab.call("POST", f"{VENUS}/load", {"watts": LOW})
        await asyncio.sleep(40)
        rows = []
        for i in range(steps):
            load = HIGH if i % 2 == 0 else LOW
            row = await step(lab, load, sensor, samples, hold=35)
            print(json.dumps(row), flush=True)
            rows.append(row)
        for task in tasks:
            task.cancel()
        summary = {}
        for key in ("sensor", "first_write", "command", "t90", "settled"):
            values = [r[key] for r in rows if r[key] is not None]
            summary[key] = round(statistics.median(values), 2) if values else None
        summary["missing_settled"] = sum(r["settled"] is None for r in rows)
        print("median", json.dumps(summary), flush=True)
        await lab.login()  # access tokens expire after 30 min
        switches = {k: "off" for k, v in overrides.items() if v == "on"}
        await set_numbers(lab, {**YOURS, **switches})


if __name__ == "__main__":
    asyncio.run(
        main(
            int(sys.argv[1]) if len(sys.argv) > 1 else 8,
            parse(sys.argv[2] if len(sys.argv) > 2 else ""),
        )
    )
