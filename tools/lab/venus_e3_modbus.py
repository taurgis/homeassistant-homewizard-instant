"""Modbus TCP model of a Venus E 3.0 on Control firmware 151, plus a house.

Two modes:

* **Model** (default): this script is the battery. It serves a Python copy of
  the Modbus surface on ``MODBUS_PORT``.
* **Firmware** (``INVERTER_URL`` set): the real Control 151 image runs in the
  Renode emulator of taurgis/has-marstek-local-api with ``--ethernet`` and
  serves Modbus TCP itself, from socket 0 of its emulated CH395. This script
  then serves no Modbus. It polls the emulator's grid meter
  (``can_peers.py --meter-port``, started with ``--house-load 0,0,0``) for the
  inverter output and only plays the house.

In both modes grid power is ``house load - ac_power`` (import positive) and is
written to the P1 emulator without forcing a push, so the meter reports it on
its own telegram cadence, like a real P1 port.

What the model copies from the 151 image (VNSEE3-0_ems_app_0151_0928_094421.bin,
SHA-256 a2f566eb...0148), checked against that image in the emulator:

* device name ``VNSE3-0`` (31000) and EMS version 151 (30200);
* ``ac_power`` (30006) int16, positive while discharging; SoC in 32104;
* force mode (42010) only takes effect while RS485 control (42000) is 0x55AA;
* 44002/44003 store any value; discharge is still capped at 800 W (the image
  rejects "max discharge power ... over 800W"), charge at 2500 W.

OmniBattery 1.5.0's v3 map reads SoC from 37005 and battery power from 30001.
The emulated 151 image answers 0 for both while 32104 and the Local API report
the real SoC. The model fills them, so OmniBattery runs against it.

The plant: the inverter follows the set-point after ``DEAD_TIME_S`` and ramps
at ``RAMP_W_PER_S``. ``MODBUS_LATENCY_S`` delays every Modbus reply, to copy a
battery that is slow to answer.

Control on ``HTTP_PORT`` (plain HTTP, no auth, lab only):

* ``GET /state`` - plant and register summary
* ``POST /load`` ``{"watts": 900}`` - set the house load
* ``GET /trace`` - timestamped load changes and set-point writes
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import struct
import time
import urllib.request
from collections import deque
from typing import Any

EMS_VERSION = int(os.environ.get("EMS_VERSION", "151"))
HOUSE_LOAD_W = float(os.environ.get("HOUSE_LOAD_W", "450"))
NOISE_W = float(os.environ.get("NOISE_W", "8"))
P1_CONTROL_URL = os.environ.get("P1_CONTROL_URL", "http://127.0.0.1:8082")
LISTEN = os.environ.get("MODBUS_LISTEN", "0.0.0.0")
PORT = int(os.environ.get("MODBUS_PORT", "502"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8090"))
INVERTER_URL = os.environ.get("INVERTER_URL", "")
DEAD_TIME_S = float(os.environ.get("DEAD_TIME_S", "0.6"))
# Time the battery takes to answer one Modbus request. The model answers at once
# by default; the emulated 151 image took about 0.3 s per request.
MODBUS_LATENCY_S = float(os.environ.get("MODBUS_LATENCY_S", "0"))
RAMP_W_PER_S = float(os.environ.get("RAMP_W_PER_S", "1000"))
TICK_S = 0.1
SOC_START = float(os.environ.get("SOC", "62"))
CAPACITY_WH = 5120.0
MAX_CHARGE_W = 2500
MAX_DISCHARGE_W = 800
RS485_ON = 0x55AA

# uint16 on the wire; int16 values are stored two's complement.
DEFAULTS: dict[int, int] = {
    30100: 5230,  # 52.30 V
    30200: EMS_VERSION,
    30202: 151,  # VMS
    30204: 215,  # BMS
    30300: 1,  # Wi-Fi connected
    30302: 1,  # cloud connected
    30303: 55,  # -55 dBm
    32105: 5120,  # 5.120 kWh
    32302: 0,  # off-grid port
    34003: 42,  # cycles
    34009: 0,
    35000: 251,  # 25.1 degC
    35100: 1,  # standby
    37007: 3312,  # mV
    37008: 3301,
    41200: 1,  # backup off
    42000: 0x55BB,  # RS485 control off until the controller enables it
    42010: 0,
    42020: 0,
    42021: 0,
    43000: 1,  # anti_feed
    44002: MAX_CHARGE_W,
    44003: MAX_DISCHARGE_W,
}


def _ascii(start: int, count: int, text: str) -> dict[int, int]:
    raw = text.encode().ljust(count * 2, b"\x00")[: count * 2]
    return {start + i: (raw[2 * i] << 8) | raw[2 * i + 1] for i in range(count)}


DEFAULTS |= _ascii(31000, 10, "VNSE3-0")
DEFAULTS |= _ascii(30350, 6, "FC41D")
DEFAULTS |= _ascii(30304, 6, "02e100000070")


def _s16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


class Venus:
    """Registers, inverter dynamics and the house behind one P1 meter."""

    def __init__(self) -> None:
        self.regs = dict(DEFAULTS)
        self.house_w = HOUSE_LOAD_W
        self.ac_w = 0.0  # + discharge
        self.soc_wh = CAPACITY_WH * SOC_START / 100
        self.charged_kwh = 1234.56
        self.discharged_kwh = 1100.12
        self.grid_w = HOUSE_LOAD_W
        self._commands: deque[tuple[float, float]] = deque()
        self._target = 0.0
        self.writes = 0
        self.reads = 0
        self.clients = 0
        self._noise = 0.0
        self._noise_at = 0.0
        # (monotonic time, event, value) for load changes and set-point writes,
        # read by tools/dev/omnibattery_latency.py through GET /trace.
        self.trace: deque[tuple[float, str, float]] = deque(maxlen=2000)

    # -- registers ---------------------------------------------------------
    def read(self, address: int) -> int:
        return self.regs.get(address, 0) & 0xFFFF

    def write(self, address: int, value: int) -> bool:
        """Store a holding register; False means exception 3."""
        value &= 0xFFFF
        self.regs[address] = value
        self.writes += 1
        if address in (42000, 42010, 42020, 42021, 44002, 44003):
            now = time.monotonic()
            command = self._command()
            self._commands.append((now + DEAD_TIME_S, command))
            self.trace.append((now, f"write {address}", float(value)))
            self.trace.append((now, "command", command))
        return True

    def _command(self) -> float:
        """Signed AC target from the registers, + discharge."""
        if self.read(42000) != RS485_ON:
            return 0.0
        mode = self.read(42010)
        if mode == 1:
            return -float(min(self.read(42020), self.read(44002), MAX_CHARGE_W))
        if mode == 2:
            return float(min(self.read(42021), self.read(44003), MAX_DISCHARGE_W))
        return 0.0

    # -- plant ---------------------------------------------------------------
    def step(self, dt: float) -> None:
        now = time.monotonic()
        while self._commands and self._commands[0][0] <= now:
            self._target = self._commands.popleft()[1]
        target = self._target
        soc = 100 * self.soc_wh / CAPACITY_WH
        if (target > 0 and soc <= 11) or (target < 0 and soc >= 100):
            target = 0.0
        delta = target - self.ac_w
        limit = RAMP_W_PER_S * dt
        self.ac_w += max(-limit, min(limit, delta))
        if abs(self.ac_w) < 1:
            self.ac_w = 0.0
        energy_wh = self.ac_w * dt / 3600
        if self.ac_w > 0:
            self.soc_wh -= energy_wh / 0.92
            self.discharged_kwh += energy_wh / 1000
        else:
            self.soc_wh -= energy_wh * 0.92
            self.charged_kwh -= energy_wh / 1000
        self.soc_wh = max(0.0, min(CAPACITY_WH, self.soc_wh))
        self._publish()

    def follow(self, url: str) -> None:
        """Firmware mode: take ``ac_power`` from the emulator's grid meter."""
        with urllib.request.urlopen(url, timeout=1) as response:
            phases = json.loads(response.read())
        # can_peers serves house load minus inverter output; the load is 0.
        self.ac_w = -float(sum(phases[p] for p in "abc"))

    def _publish(self) -> None:
        ac = round(self.ac_w)
        cell = -round(self.ac_w / 0.95) if ac > 0 else -round(self.ac_w * 0.95)
        self.regs[30006] = ac & 0xFFFF
        self.regs[30001] = cell & 0xFFFF
        self.regs[37005] = round(100 * self.soc_wh / CAPACITY_WH)
        self.regs[35100] = 3 if ac > 0 else 2 if ac < 0 else 1
        charged = round(self.charged_kwh * 100)
        discharged = round(self.discharged_kwh * 100)
        self.regs[33000], self.regs[33001] = charged >> 16, charged & 0xFFFF
        self.regs[33002], self.regs[33003] = discharged >> 16, discharged & 0xFFFF

    def house(self, now: float) -> float:
        """House load with jitter redrawn once a second, like real appliances."""
        if now - self._noise_at >= 1.0:
            self._noise_at = now
            self._noise = random.gauss(0, NOISE_W) if NOISE_W else 0.0
        return self.house_w + self._noise

    def state(self) -> dict[str, Any]:
        if INVERTER_URL:
            # The firmware owns the registers; only the plant is known here.
            return {
                "model": f"Control firmware 151 in Renode, inverter from {INVERTER_URL}",
                "house_w": self.house_w,
                "ac_power_w": round(self.ac_w),
                "grid_w": round(self.grid_w, 1),
            }
        return {
            "model": "Venus E 3.0 Modbus model (Python, not firmware)",
            "ems_version_register": self.read(30200),
            "house_w": self.house_w,
            "ac_power_w": round(self.ac_w),
            "battery_power_w": _s16(self.read(30001)),
            "grid_w": round(self.grid_w, 1),
            "soc_pct": self.read(37005),
            "rs485_control": self.read(42000) == RS485_ON,
            "force_mode": self.read(42010),
            "set_charge_w": self.read(42020),
            "set_discharge_w": self.read(42021),
            "modbus_reads": self.reads,
            "modbus_writes": self.writes,
            "modbus_clients": self.clients,
        }


# -- Modbus TCP ------------------------------------------------------------------
def _pdu(venus: Venus, pdu: bytes) -> bytes:
    if not pdu:
        return bytes([0x83, 0x03])
    function = pdu[0]
    if function in (3, 4) and len(pdu) >= 5:
        start, count = struct.unpack(">HH", pdu[1:5])
        if not 1 <= count <= 125:
            return bytes([function | 0x80, 0x03])
        venus.reads += 1
        body = b"".join(struct.pack(">H", venus.read(start + i)) for i in range(count))
        return bytes([function, len(body)]) + body
    if function == 6 and len(pdu) >= 5:
        address, value = struct.unpack(">HH", pdu[1:5])
        if not venus.write(address, value):
            return bytes([0x86, 0x03])
        return pdu[:5]
    if function == 16 and len(pdu) >= 6:
        start, count, size = struct.unpack(">HHB", pdu[1:6])
        raw = pdu[6 : 6 + size]
        if len(raw) < count * 2:
            return bytes([0x90, 0x03])
        for i in range(count):
            if not venus.write(
                start + i, struct.unpack(">H", raw[2 * i : 2 * i + 2])[0]
            ):
                return bytes([0x90, 0x03])
        return bytes([16]) + pdu[1:5]
    return bytes([function | 0x80, 0x01])


class ModbusServer:
    """One TCP slot, like the v3 MCU: a new client replaces the old one."""

    def __init__(self, venus: Venus) -> None:
        self.venus = venus
        self._current: asyncio.StreamWriter | None = None

    async def client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._current is not None and not self._current.is_closing():
            print("modbus: new client replaces the previous one", flush=True)
            self._current.close()
        self._current = writer
        self.venus.clients += 1
        try:
            while True:
                header = await reader.readexactly(7)
                _tx, protocol, length, _unit = struct.unpack(">HHHB", header)
                if protocol != 0 or length < 2:
                    break
                pdu = await reader.readexactly(length - 1)
                if MODBUS_LATENCY_S:
                    await asyncio.sleep(MODBUS_LATENCY_S)
                reply = _pdu(self.venus, pdu)
                writer.write(
                    header[:4] + struct.pack(">H", len(reply) + 1) + header[6:7] + reply
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


# -- P1 meter link and control HTTP ---------------------------------------------
def _post(url: str, payload: dict[str, Any]) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=2).read()


async def plant_loop(venus: Venus) -> None:
    last_sent: float | None = None
    last_error = 0.0
    previous = time.monotonic()
    while True:
        await asyncio.sleep(TICK_S)
        now = time.monotonic()
        if INVERTER_URL:
            try:
                await asyncio.to_thread(venus.follow, INVERTER_URL)
            except (OSError, ValueError, KeyError) as err:
                if now - last_error > 30:
                    print(f"inverter poll failed: {err}", flush=True)
                    last_error = now
                continue
        else:
            venus.step(now - previous)
        previous = now
        venus.grid_w = venus.house(now) - venus.ac_w
        grid = round(venus.grid_w, 1)
        if not P1_CONTROL_URL or (
            last_sent is not None and abs(grid - last_sent) < 0.5
        ):
            continue
        payload = {
            "power_w": grid,
            "power_l1_w": grid,
            "current_l1_a": round(abs(grid) / 230, 2),
            "push": False,
        }
        try:
            await asyncio.to_thread(_post, f"{P1_CONTROL_URL}/overlay", payload)
            last_sent = grid
        except OSError as err:
            if now - last_error > 30:
                print(f"P1 overlay failed: {err}", flush=True)
                last_error = now


async def http_control(
    venus: Venus, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, path, _ = lines[0].split(" ", 2)
        length = 0
        for line in lines[1:]:
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1])
        body = json.loads(await reader.readexactly(length) or b"{}") if length else {}
        status, reply = "200 OK", venus.state()
        if method == "POST" and path == "/load":
            venus.house_w = float(body["watts"])
            venus.trace.append((time.monotonic(), "load", venus.house_w))
            reply = venus.state()
        elif method == "GET" and path == "/trace":
            reply = {"now": time.monotonic(), "events": list(venus.trace)}
        elif not (method == "GET" and path == "/state"):
            status, reply = "404 Not Found", {"error": "unknown path"}
        data = json.dumps(reply).encode()
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
            + data
        )
        await writer.drain()
    except (ValueError, KeyError, asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        writer.close()


async def main() -> None:
    venus = Venus()
    control = await asyncio.start_server(
        lambda r, w: http_control(venus, r, w), LISTEN, HTTP_PORT
    )
    if INVERTER_URL:
        print(
            f"House for Control firmware 151, inverter from {INVERTER_URL}, "
            f"control :{HTTP_PORT}, house {HOUSE_LOAD_W:.0f} W.",
            flush=True,
        )
        async with control:
            await plant_loop(venus)
        return
    modbus = ModbusServer(venus)
    server = await asyncio.start_server(modbus.client, LISTEN, PORT)
    print(
        f"Venus E 3.0 Modbus model on {LISTEN}:{PORT}, control :{HTTP_PORT}, "
        f"EMS register {EMS_VERSION}, house {HOUSE_LOAD_W:.0f} W. "
        "Python model, not Control firmware.",
        flush=True,
    )
    async with server, control:
        await plant_loop(venus)


if __name__ == "__main__":
    asyncio.run(main())
