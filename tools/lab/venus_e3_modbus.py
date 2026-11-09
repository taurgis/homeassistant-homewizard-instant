"""Modbus TCP model of a Venus E 3.0 on Control firmware 151, plus a house.

This is Python, not the firmware. Control 151 serves Modbus TCP from socket 0
of its CH395 Ethernet chip (``Task_Modbus_tcp``, ``CH395TCPListen``). The
Renode emulator in taurgis/has-marstek-local-api runs that image but does not
bridge TCP, so a Modbus client cannot reach the firmware's own register code.
The BMS is not involved: it talks to the Control board over CAN, and the
emulator models it in Python as well.

What is copied from the 151 image (VNSEE3-0_ems_app_0151_0928_094421.bin,
SHA-256 a2f566eb...0148):

* device name ``VNSE3-0`` and EMS version 151 (catalog version);
* discharge capped at 800 W (the image rejects "max discharge power ... over
  800W"); charge at 2500 W;
* 44002/44003 accept only 800 or 2500 and answer anything else with Modbus
  exception 3, as OmniBattery documents for v2/v3.

What is modelled from OmniBattery 1.5.0's v3 driver (``registers_v3.py``,
``drivers/marstek.py``): the register numbers, int16 power, ``battery_power``
positive while charging, ``ac_power`` positive while discharging, and force
mode only taking effect while RS485 control (0x55AA) is on.

The plant: the inverter follows the set-point after ``DEAD_TIME_S`` and ramps
at ``RAMP_W_PER_S``. Grid power is ``house load - ac_power`` (import positive)
and is written to the P1 emulator without forcing a push, so the meter reports
it on its own telegram cadence, like a real P1 port.

Control on ``HTTP_PORT`` (plain HTTP, no auth, lab only):

* ``GET /state`` - plant and register summary
* ``POST /load`` ``{"watts": 900}`` - set the house load
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
DEAD_TIME_S = float(os.environ.get("DEAD_TIME_S", "0.6"))
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

    # -- registers ---------------------------------------------------------
    def read(self, address: int) -> int:
        return self.regs.get(address, 0) & 0xFFFF

    def write(self, address: int, value: int) -> bool:
        """Store a holding register; False means exception 3."""
        value &= 0xFFFF
        if address in (44002, 44003) and value not in (800, 2500):
            return False
        self.regs[address] = value
        self.writes += 1
        if address in (42000, 42010, 42020, 42021, 44002, 44003):
            self._commands.append((time.monotonic() + DEAD_TIME_S, self._command()))
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
            reply = venus.state()
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
    modbus = ModbusServer(venus)
    server = await asyncio.start_server(modbus.client, LISTEN, PORT)
    control = await asyncio.start_server(
        lambda r, w: http_control(venus, r, w), LISTEN, HTTP_PORT
    )
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
