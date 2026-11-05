"""The P1 emulator payload must parse with python-homewizard-energy."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from homewizard_energy.const import Model
from homewizard_energy.models import Device, Measurement, System


def _emulator():
    """Load the emulator module without making scripts a package."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "p1_emulator.py"
    spec = importlib.util.spec_from_file_location("p1_emulator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_emulator_payloads_parse() -> None:
    """Device, measurement, and system documents match the v1 models."""
    emulator = _emulator()
    device = Device.from_json(json.dumps(emulator.device_payload()))
    assert device.product_type == Model.P1_METER
    assert device.serial == "3c39e7aabbcc"

    state = emulator.MeterState(emulator.INITIAL_POWER_W)
    measurement = Measurement.from_json(json.dumps(emulator.measurement_payload(state)))
    assert measurement.power_w == emulator.INITIAL_POWER_W
    assert measurement.average_power_15m_w == emulator.AVERAGE_POWER_W
    assert measurement.external_devices is not None
    gas = next(iter(measurement.external_devices.values()))
    assert gas.value == emulator.GAS_VALUE_M3
    assert gas.unit == "m3"

    system = System.from_json(json.dumps(emulator.system_payload()))
    assert system.uptime_s == 3600
    assert system.wifi_ssid == "Test Wi-Fi"
