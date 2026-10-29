"""State, pairing and fault injection for one emulated P1 meter."""

from __future__ import annotations

import copy
import json
import math
import re
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .certs import hostname
from .profiles import PROFILES

PAIRING_WINDOW_SECONDS = 30
IDENTITY_SERIAL = "ffffffffffff"
_USER_RE = re.compile(r"local/[a-zA-Z0-9\-_/\\# ]{1,40}")
FAULT_NAMES = (
    "drop",
    "hang",
    "refuse_auth",
    "revoke_token",
    "malformed_json",
    "unknown_topic",
    "missing_fields",
    "null_fields",
    "wrong_types",
    "out_of_range",
    "identity_change",
    "slow",
    "restart",
    "tls_change",
)


@dataclass
class Faults:
    """Sticky fault flags. Clear them together from the control API."""

    hang: bool = False
    refuse_auth: bool = False
    revoked: bool = False
    malformed_json: bool = False
    unknown_topic: bool = False
    missing_fields: bool = False
    null_fields: bool = False
    wrong_types: bool = False
    out_of_range: bool = False
    identity_change: bool = False
    slow_interval: float | None = None
    tls_untrusted: bool = False

    def public(self) -> dict[str, Any]:
        return asdict(self)


class Meter:
    """One P1 meter: button pairing, snapshots and fault payloads."""

    def __init__(
        self,
        serial: str,
        profile: str = "3phase",
        push_interval: float = 1.0,
        token_path: Path | None = None,
    ) -> None:
        if profile not in PROFILES:
            raise ValueError(f"Unknown profile {profile}")
        self.serial = serial.lower()
        self.push_interval = push_interval
        self.profile_name = profile
        self.template = PROFILES[profile]()
        self.energy = {
            key: value
            for key, value in self.template.items()
            if key.startswith("energy_") and isinstance(value, (int, float))
        }
        self.overlay: dict[str, Any] = {}
        self.steady = True
        self.faults = Faults()
        self.tokens: dict[str, str] = {}
        self.token_path = token_path
        self._load_tokens()
        self.button_until = 0.0
        self.boot = time.monotonic()
        self.uptime_offset = 3600
        self.uptime_override: int | None = None
        self.tick = 0
        self.pushes = 0
        self.gas_value = self._gas_baseline()
        self.last_measurement: dict[str, Any] = {}
        self.last_system: dict[str, Any] = {}
        self.sockets: set[Any] = set()
        self.transports: set[Any] = set()
        self._gas_value_set = False

    def _gas_baseline(self) -> float:
        external = self.template.get("external")
        if isinstance(external, list):
            for item in external:
                if isinstance(item, dict) and item.get("type") == "gas_meter":
                    value = item.get("value")
                    if isinstance(value, (int, float)):
                        return float(value)
        return 0.0

    def press_button(self) -> None:
        self.button_until = time.monotonic() + PAIRING_WINDOW_SECONDS

    def button_open(self) -> bool:
        return time.monotonic() < self.button_until

    def issue_token(self, name: str) -> tuple[int, dict[str, Any]]:
        if not isinstance(name, str) or not _valid_user(name):
            return 400, {"error": "user:invalid-name"}
        if not self.button_open():
            return 403, {"error": "user:creation-not-enabled"}
        self.tokens = {token: user for token, user in self.tokens.items() if user != name}
        token = secrets.token_hex(16).upper()
        self.tokens[token] = name
        self._save_tokens()
        return 200, {"token": token, "name": name}

    def authorized(self, header: str | None) -> bool:
        if self.faults.refuse_auth or self.faults.revoked:
            return False
        if not header or not header.startswith("Bearer "):
            return False
        return header.removeprefix("Bearer ").strip() in self.tokens

    def revoke(self) -> None:
        self.faults.revoked = True
        self.tokens.clear()
        self._save_tokens()

    def _load_tokens(self) -> None:
        if self.token_path is None or not self.token_path.exists():
            return
        loaded = json.loads(self.token_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            self.tokens = {str(token): str(name) for token, name in loaded.items()}

    def _save_tokens(self) -> None:
        if self.token_path is None:
            return
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(self.tokens), encoding="utf-8")

    def set_profile(self, name: str) -> None:
        if name not in PROFILES:
            raise ValueError(f"Unknown profile {name}")
        self.profile_name = name
        self.template = PROFILES[name]()
        for key, value in self.template.items():
            if key.startswith("energy_") and isinstance(value, (int, float)):
                self.energy[key] = max(float(self.energy.get(key, value)), float(value))
        self.gas_value = max(self.gas_value, self._gas_baseline())
        self.overlay.clear()

    def device_payload(self) -> dict[str, Any]:
        serial = IDENTITY_SERIAL if self.faults.identity_change else self.serial
        return {
            "product_type": "HWE-P1",
            "product_name": "P1 Meter",
            "serial": serial,
            "firmware_version": "6.0204",
            "api_version": "2.0.0",
        }

    def system_payload(self) -> dict[str, Any]:
        if self.uptime_override is None:
            uptime = self.uptime_offset + int(time.monotonic() - self.boot)
        else:
            uptime = self.uptime_override
        payload = {
            "wifi_ssid": "HomeWizard-Lab",
            "wifi_rssi_db": -60,
            "cloud_enabled": False,
            "uptime_s": uptime,
            "status_led_brightness_pct": 80,
            "api_v1_enabled": False,
        }
        self.last_system = payload
        return payload

    def measurement_payload(self) -> dict[str, Any]:
        payload = copy.deepcopy(self.template)
        for key, value in self.energy.items():
            if key in payload:
                payload[key] = value
        payload.update(self.overlay)
        if not self.steady:
            self._advance(payload)
        self._stamp(payload)
        self.last_measurement = payload
        return payload

    def _advance(self, payload: dict[str, Any]) -> None:
        self.tick += 1
        if "power_w" not in self.overlay and isinstance(payload.get("power_w"), (int, float)):
            delta = round(math.sin(self.tick / 3) * 12, 1)
            payload["power_w"] = round(float(payload["power_w"]) + delta, 1)
            if "power_l1_w" in payload and isinstance(payload["power_l1_w"], (int, float)):
                payload["power_l1_w"] = round(float(payload["power_l1_w"]) + delta, 1)
        power = payload.get("power_w")
        if isinstance(power, (int, float)):
            kwh = round(abs(float(power)) * self.push_interval / 3_600_000, 5)
            tariff = payload.get("tariff")
            if power >= 0:
                self._add_energy("energy_import_kwh", kwh, payload)
                if isinstance(tariff, int):
                    self._add_energy(f"energy_import_t{tariff}_kwh", kwh, payload)
            else:
                self._add_energy("energy_export_kwh", kwh, payload)
                if isinstance(tariff, int):
                    self._add_energy(f"energy_export_t{tariff}_kwh", kwh, payload)
        external = payload.get("external")
        if isinstance(external, list):
            self.gas_value = round(self.gas_value + 0.001, 3)
            for item in external:
                if isinstance(item, dict) and item.get("type") == "gas_meter":
                    item["value"] = self.gas_value

    def _add_energy(self, key: str, kwh: float, payload: dict[str, Any]) -> None:
        if key not in payload or not isinstance(payload[key], (int, float)):
            return
        updated = round(float(self.energy.get(key, payload[key])) + kwh, 5)
        self.energy[key] = updated
        payload[key] = updated

    def _stamp(self, payload: dict[str, Any]) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        payload["timestamp"] = stamp
        external = payload.get("external")
        if isinstance(external, list):
            for item in external:
                if isinstance(item, dict):
                    item["timestamp"] = stamp

    def topic_payload(self, topic: str) -> dict[str, Any] | None:
        if topic == "device":
            return self.device_payload()
        if topic == "measurement":
            return self.measurement_payload()
        if topic == "system":
            return self.system_payload()
        return None

    def fault_message(self) -> dict[str, Any] | str | None:
        """Return a bad push, or None when the meter should send real data."""
        if self.faults.malformed_json:
            return '{"type": "measurement", "data": '
        if self.faults.unknown_topic:
            return {"type": "batteries", "data": {"mode": "zero"}}
        if self.faults.missing_fields:
            return {"type": "device", "data": {"product_type": "HWE-P1", "product_name": "P1 Meter"}}
        if self.faults.null_fields:
            return {"type": "measurement", "data": {"power_w": None, "energy_import_kwh": None}}
        if self.faults.wrong_types:
            return {"type": "measurement", "data": {"power_w": "high", "tariff": "two"}}
        if self.faults.out_of_range:
            return {
                "type": "measurement",
                "data": {"power_w": 1e12, "voltage_l1_v": -50, "frequency_hz": 5000, "tariff": 9},
            }
        if self.faults.identity_change:
            return {"type": "device", "data": self.device_payload()}
        return None

    def apply_fault(self, name: str, interval: float | None = None) -> None:
        if name not in FAULT_NAMES:
            raise ValueError(f"Unknown fault {name}")
        if name == "slow":
            self.faults.slow_interval = 12.0 if interval is None else interval
            return
        if name == "revoke_token":
            self.revoke()
            return
        if name == "tls_change":
            self.faults.tls_untrusted = True
            return
        if name in {"drop", "restart"}:
            return
        setattr(self.faults, name, True)

    def clear_faults(self) -> None:
        self.faults = Faults()

    def pause_reads(self) -> None:
        for transport in list(self.transports):
            pause = getattr(transport, "pause_reading", None)
            if pause is not None:
                pause()

    def resume_reads(self) -> None:
        for transport in list(self.transports):
            resume = getattr(transport, "resume_reading", None)
            if resume is not None:
                resume()

    def state(self) -> dict[str, Any]:
        return {
            "serial": self.serial,
            "hostname": hostname(self.serial),
            "profile": self.profile_name,
            "steady": self.steady,
            "push_interval": self.faults.slow_interval or self.push_interval,
            "button_open": self.button_open(),
            "tokens": len(self.tokens),
            "websockets": len(self.sockets),
            "pushes": self.pushes,
            "faults": self.faults.public(),
            "device": self.device_payload(),
            "measurement": self.last_measurement or self.measurement_payload(),
            "system": self.last_system or self.system_payload(),
        }


def _valid_user(name: str) -> bool:
    return _USER_RE.fullmatch(name) is not None
