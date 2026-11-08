"""Synthetic Modbus TCP stand-in for a Venus battery.

This is not Marstek firmware and it does not run VNSEE3-0 151. It only
answers the holding registers OmniBattery 1.5.0 polls, follows charge and
discharge setpoints, and writes the resulting grid power to the P1 emulator.

Sign, matching OmniBattery's Marstek driver: battery_power is positive when
charging and negative when discharging. Grid power sent to the meter is
``house_load_w + battery_power`` (import positive).

Set P1_CONTROL_URL empty to skip the meter update.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import urllib.request

HOUSE_LOAD_W = int(os.environ.get("HOUSE_LOAD_W", "600"))
P1_CONTROL_URL = os.environ.get("P1_CONTROL_URL", "http://127.0.0.1:8082")
LISTEN = os.environ.get("MODBUS_LISTEN", "0.0.0.0")
PORT = int(os.environ.get("MODBUS_PORT", "502"))

# Addresses are the numbers OmniBattery sends. Values are uint16 on the wire.
# EMS is 100 so a reader cannot mistake this process for firmware 151.
DEFAULTS = {
    30001: 0,  # battery_power, int16
    30100: 5200,  # 52.00 V
    30200: 100,  # synthetic EMS version, not 151
    32105: 1000,
    37005: 60,  # SOC %
    42000: 21930,  # RS485 control enabled (0x55AA)
    42010: 0,  # force_mode
    42020: 0,  # set charge W
    42021: 0,  # set discharge W
    43000: 0,
    44002: 2500,
    44003: 800,
}


def _u16(value: int) -> int:
    return value & 0xFFFF


class Bank:
    """Holding registers with a grid-power side effect."""

    def __init__(self) -> None:
        self.values = dict(DEFAULTS)
        self._last_grid: int | None = None

    def get(self, address: int) -> int:
        return self.values.get(address, 0) & 0xFFFF

    def set(self, address: int, value: int) -> None:
        self.values[address] = value & 0xFFFF
        if address in {42010, 42020, 42021}:
            self._follow_setpoint()

    def _follow_setpoint(self) -> None:
        mode = self.get(42010)
        charge = self.get(42020)
        discharge = self.get(42021)
        if mode == 1:
            power = charge
        elif mode == 2:
            power = -discharge
        else:
            power = 0
        self.values[30001] = _u16(power)
        grid = HOUSE_LOAD_W + power
        if grid == self._last_grid or not P1_CONTROL_URL:
            self._last_grid = grid
            return
        self._last_grid = grid
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _post_grid(grid)
            return
        loop.create_task(asyncio.to_thread(_post_grid, grid))


def _post_grid(grid: int) -> None:
    payload = json.dumps({"power_w": grid}).encode()
    request = urllib.request.Request(
        f"{P1_CONTROL_URL}/overlay",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=2).read()
    except OSError as err:
        print(f"grid overlay failed: {err}", flush=True)


def _read(bank: Bank, start: int, count: int) -> bytes:
    return b"".join(
        struct.pack(">H", bank.get(start + offset)) for offset in range(count)
    )


def _handle(bank: Bank, pdu: bytes) -> bytes:
    if not pdu:
        return bytes([0x80 | 0x03, 0x03])
    function = pdu[0]
    if function == 3 and len(pdu) >= 5:
        start, count = struct.unpack(">HH", pdu[1:5])
        if count < 1 or count > 125:
            return bytes([0x83, 0x03])
        body = _read(bank, start, count)
        return bytes([3, len(body)]) + body
    if function == 6 and len(pdu) >= 5:
        address, value = struct.unpack(">HH", pdu[1:5])
        bank.set(address, value)
        return pdu[:5]
    if function == 16 and len(pdu) >= 6:
        start, count, byte_count = struct.unpack(">HHB", pdu[1:6])
        raw = pdu[6 : 6 + byte_count]
        if len(raw) < count * 2:
            return bytes([0x90, 0x03])
        for offset in range(count):
            (value,) = struct.unpack(">H", raw[offset * 2 : offset * 2 + 2])
            bank.set(start + offset, value)
        return bytes([16]) + pdu[1:5]
    return bytes([function | 0x80, 0x01])


async def _client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, bank: Bank
) -> None:
    try:
        while True:
            header = await reader.readexactly(7)
            _tx, protocol, length, _unit = struct.unpack(">HHHB", header)
            if protocol != 0 or length < 1:
                break
            pdu = await reader.readexactly(length - 1)
            reply = _handle(bank, pdu)
            writer.write(
                header[:4] + struct.pack(">H", len(reply) + 1) + header[6:7] + reply
            )
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


async def main() -> None:
    bank = Bank()
    server = await asyncio.start_server(
        lambda reader, writer: _client(reader, writer, bank), LISTEN, PORT
    )
    print(
        f"synthetic Venus Modbus on {LISTEN}:{PORT} "
        f"house_load={HOUSE_LOAD_W}W ems_register=100 (not firmware)",
        flush=True,
    )
    await asyncio.to_thread(_post_grid, HOUSE_LOAD_W)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
