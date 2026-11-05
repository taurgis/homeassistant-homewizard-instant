#!/usr/bin/env python3
"""Emulate a HomeWizard P1 meter local API v1.

``python-homewizard-energy`` reads ``/api``, ``/api/v1/data`` and
``/api/v1/system``. ``POST /control`` changes ``active_power_w`` so a caller
can prove that Home Assistant is polling.

The payload shape matches the library's v1 fixtures: a ``wifi_ssid`` field
selects the v1 field remap, and ``external`` carries the gas meter.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any

HOST = "0.0.0.0"
PORT = 80
INITIAL_POWER_W = 4321
UPDATED_POWER_W = 4567
AVERAGE_POWER_W = 123
GAS_VALUE_M3 = 111.111

DEVICE: dict[str, str] = {
    "product_type": "HWE-P1",
    "product_name": "P1 Meter",
    "serial": "3c39e7aabbcc",
    "firmware_version": "2.11",
    "api_version": "v1",
}

SYSTEM: dict[str, Any] = {
    "wifi_ssid": "Test Wi-Fi",
    "wifi_rssi_db": -40,
    "cloud_enabled": True,
    "uptime_s": 3600,
}


class MeterState:
    """Mutable measurement values served by the emulator."""

    def __init__(self, active_power_w: float = INITIAL_POWER_W) -> None:
        """Start the meter at a known power reading."""
        self._lock = threading.Lock()
        self._active_power_w = active_power_w

    @property
    def active_power_w(self) -> float:
        """Return the current total power."""
        with self._lock:
            return self._active_power_w

    @active_power_w.setter
    def active_power_w(self, value: float) -> None:
        """Replace the current total power."""
        with self._lock:
            self._active_power_w = value


def device_payload() -> dict[str, str]:
    """Return the ``/api`` document."""
    return dict(DEVICE)


def system_payload() -> dict[str, Any]:
    """Return the ``/api/v1/system`` document."""
    return dict(SYSTEM)


def measurement_payload(state: MeterState) -> dict[str, Any]:
    """Return the ``/api/v1/data`` document for the current power."""
    return {
        "wifi_ssid": "Test Wi-Fi",
        "wifi_strength": 80,
        "smr_version": 50,
        "meter_model": "ISKRA 2M550T-101",
        "unique_id": "4E47475955",
        "active_tariff": 1,
        "total_power_import_kwh": 13779.338,
        "total_power_import_t1_kwh": 10830.511,
        "total_power_import_t2_kwh": 2948.827,
        "total_power_export_kwh": 10.5,
        "total_power_export_t1_kwh": 6.0,
        "total_power_export_t2_kwh": 4.5,
        "active_power_w": state.active_power_w,
        "active_power_l1_w": 100,
        "active_power_l2_w": 50,
        "active_power_l3_w": 10,
        "active_voltage_l1_v": 230.0,
        "active_current_l1_a": 1.5,
        "active_frequency_hz": 50.0,
        "active_power_average_w": AVERAGE_POWER_W,
        "montly_power_peak_w": 2500,
        "external": [
            {
                "unique_id": "4E474C5944",
                "type": "gas_meter",
                "timestamp": 210606140010,
                "value": GAS_VALUE_M3,
                "unit": "m3",
            }
        ],
    }


def make_handler(state: MeterState) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to one meter."""

    class Handler(BaseHTTPRequestHandler):
        """Serve the P1 API and the test control endpoint."""

        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            """Log requests to stdout so container logs stay readable."""
            print(f"{self.address_string()} {format % args}", flush=True)

        def do_GET(self) -> None:
            """Serve device, measurement, and system documents."""
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/api":
                self._send(200, device_payload())
                return
            if path == "/api/v1/data":
                self._send(200, measurement_payload(state))
                return
            if path == "/api/v1/system":
                self._send(200, system_payload())
                return
            self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            """Accept a new ``active_power_w`` on ``/control``."""
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            if path != "/control":
                self._send(404, {"error": "not found"})
                return
            try:
                body = json.loads(raw.decode() or "{}")
                state.active_power_w = float(body["active_power_w"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
                self._send(400, {"error": str(err)})
                return
            self._send(200, {"active_power_w": state.active_power_w})

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def serve(state: MeterState | None = None) -> None:
    """Listen until the process is stopped."""
    meter = state if state is not None else MeterState()
    server = ThreadingHTTPServer((HOST, PORT), make_handler(meter))
    print(f"P1 emulator listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    serve()
