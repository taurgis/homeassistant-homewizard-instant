"""Replay one house slice (east/west 6 kWp solar plus appliances) against OmniBattery configs.

Needs the OmniBattery lab with the Python Venus model (docs/development.md) and
an OmniBattery entry already set up, for example by omnibattery_pd.py:

    python3 tools/dev/omnibattery_house.py yours,min50,db25

The Venus model is the battery (800 W discharge, 2500 W charge, 0.6 s dead
time, 1000 W/s ramp). HomeWizard Instant reads the P1 mock and OmniBattery
reads HomeWizard. Every config sees the same second-by-second net load: about
12 minutes with a kettle, a washer, an oven, a fridge, low afternoon sun with
one deep cloud, then bright sun with fast cloud edges. A config is scored on
the grid energy a perfect controller within the battery limits would not have
imported or exported, priced at 0.30 EUR/kWh import and 0.03 EUR/kWh injection.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import sys
import time

import aiohttp

from omnibattery_pd import HA, VENUS, Lab

N = "number.omnibattery_pd_"
YOURS = {
    N + "controller_kp": 0.35,
    N + "controller_kd": 0.3,
    N + "controller_max_power_change": 800,
    N + "controller_deadband": 40,
    N + "controller_direction_hysteresis": 60,
    N + "min_charge_power": 90,
    N + "min_discharge_power": 80,
    N + "relay_cooldown": 10,
    N + "target_grid_power": 0,
    N + "min_cycle_interval": 1.0,
}
CONFIGS = {
    "yours": {},
    "kd0.1": {N + "controller_kd": 0.1},
    "kd0": {N + "controller_kd": 0.0},
    "kp0.5_kd0.1": {N + "controller_kp": 0.5, N + "controller_kd": 0.1},
    "kd0.1_db25": {N + "controller_kd": 0.1, N + "controller_deadband": 25},
    "kd0.1_min50": {
        N + "controller_kd": 0.1,
        N + "min_charge_power": 50,
        N + "min_discharge_power": 50,
    },
    "kd0.1_target-20": {N + "controller_kd": 0.1, N + "target_grid_power": -20},
    "db25": {N + "controller_deadband": 25},
    "min50": {N + "min_charge_power": 50, N + "min_discharge_power": 50},
    "target-30": {N + "target_grid_power": -30},
    "cool5": {N + "relay_cooldown": 5},
    "kp0.5": {N + "controller_kp": 0.5},
    "kp0.7": {N + "controller_kp": 0.7},
    "int0.5": {N + "min_cycle_interval": 0.5},
    "kp0.5_int0.5": {N + "controller_kp": 0.5, N + "min_cycle_interval": 0.5},
    "kp0.7_int0.5": {N + "controller_kp": 0.7, N + "min_cycle_interval": 0.5},
}
DIS, CHG = 800, 2500


def scenario(seconds=720, seed=7):
    """Net house load (W, + import) per second: load minus PV."""
    rnd = random.Random(seed)

    def ramp(t, a, b, rise=3):  # 0..1 window with rise/fall seconds
        if t < a or t > b:
            return 0.0
        return min(1.0, (t - a) / rise, (b - t) / rise)

    net = []
    for t in range(seconds):
        load = 280 + 90 * ((t // 180) % 2 == 0)  # base + fridge
        load += 2000 * ramp(t, 60, 200, 1)  # kettle (1 s edges)
        load += 2000 * ramp(t, 240, 330, 1)  # washer heating
        if 330 < t < 360:
            load += 350 + 150 * math.sin(t)  # washer motor
        load += 2400 * ramp(t, 380, 520, 2)  # oven
        load += 150 * (500 < t < 600)  # TV
        if t < 360:  # low afternoon sun, one deep cloud
            pv = 900 + 80 * math.sin(t / 17) - 650 * ramp(t, 120, 175, 6)
        else:  # bright, broken clouds (fast edges)
            pv = (
                3600
                - 2600 * ramp(t, 420, 470, 5)
                - 2400 * ramp(t, 560, 600, 4)
                - 2800 * ramp(t, 650, 680, 3)
            )
        net.append(load - pv + rnd.gauss(0, 15))
    return net


async def run(lab, name, overrides, net):
    cfg = {**YOURS, **overrides}
    for entity, value in cfg.items():
        await lab.call(
            "POST",
            f"{HA}/api/services/number/set_value",
            {"entity_id": entity, "value": value},
        )
    await lab.call("POST", f"{VENUS}/load", {"watts": net[0]})
    await asyncio.sleep(60)
    w0 = (await lab.call("GET", f"{VENUS}/state"))["modbus_writes"]
    samples = []
    start = time.monotonic()
    for t, n in enumerate(net):
        await asyncio.sleep(max(0, start + t - time.monotonic()))
        await lab.call("POST", f"{VENUS}/load", {"watts": round(n, 1)})
        st = await lab.call("GET", f"{VENUS}/state")
        samples.append((n, st["grid_w"], st["ac_power_w"], st["force_mode"]))
    w1 = (await lab.call("GET", f"{VENUS}/state"))["modbus_writes"]
    imp = exp = ideal_imp = ideal_exp = sq = 0.0
    flips, last_dir = 0, 0
    for n, g, ac, mode in samples:
        ideal = n - max(-CHG, min(DIS, n))
        imp += max(g, 0) / 3600
        exp += max(-g, 0) / 3600
        ideal_imp += max(ideal, 0) / 3600
        ideal_exp += max(-ideal, 0) / 3600
        sq += (g - ideal) ** 2
        d = 1 if ac > 20 else -1 if ac < -20 else 0
        if d and last_dir and d != last_dir:
            flips += 1
        if d:
            last_dir = d
    res = {
        "avoidable_import_wh": round(imp - ideal_imp, 1),
        "avoidable_export_wh": round(exp - ideal_exp, 1),
        "rms_vs_ideal_w": round(math.sqrt(sq / len(samples))),
        "charge_discharge_flips": flips,
        "modbus_writes": w1 - w0,
    }
    # Import costs 0.30 EUR/kWh; storable energy exported earns 0.03 instead of saving 0.30.
    res["cost_eurocent"] = round(
        30 * res["avoidable_import_wh"] / 1000 + 27 * res["avoidable_export_wh"] / 1000,
        2,
    )
    print(name, json.dumps(res), flush=True)
    return res


async def main():
    net = scenario()
    async with aiohttp.ClientSession() as s:
        lab = Lab(s)
        for name in sys.argv[1].split(","):
            await lab.login()  # access tokens expire after 30 min
            await run(lab, name, CONFIGS[name], net)
        await lab.login()
        for entity, value in YOURS.items():
            await lab.call(
                "POST",
                f"{HA}/api/services/number/set_value",
                {"entity_id": entity, "value": value},
            )


if __name__ == "__main__":
    if len(sys.argv) != 2 or not set(sys.argv[1].split(",")) <= CONFIGS.keys():
        sys.exit(
            f"usage: omnibattery_house.py NAME[,NAME...]  names: {', '.join(CONFIGS)}"
        )
    asyncio.run(main())
