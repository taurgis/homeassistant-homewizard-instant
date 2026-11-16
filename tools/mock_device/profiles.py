"""Realistic meter snapshots and the device each one belongs to.

P1 snapshots follow the HomeWizard API v2 measurement examples. The
``kwh3`` snapshot is a frame captured from a physical SDM630-wifi on
firmware 5.0305; ``kwh3_solar`` and ``kwh1`` reuse its field layout with
made-up readings, ``kwh1`` following the documented 1-phase field list.
"""

from __future__ import annotations

from dataclasses import dataclass

GAS_UNIQUE_ID = "4730303031"  # hex for G0001
GAS_UNIQUE_ID_1PHASE = "4730303032"  # hex for G0002
WATER_UNIQUE_ID = "5730303031"  # hex for W0001


@dataclass(frozen=True)
class Device:
    """What the device topic and the TLS certificate say about a meter."""

    product_type: str
    product_name: str
    firmware_version: str
    api_version: str
    # Middle part of the certificate name appliance/<type>/<serial>.
    certificate_type: str
    # A P1 meter stamps measurements with the smart meter's clock and
    # reports its LED; a kWh meter does neither.
    p1: bool


P1_METER = Device("HWE-P1", "P1 Meter", "6.0204", "2.0.0", "p1dongle", True)
KWH_3PHASE = Device(
    "SDM630-wifi", "kWh Meter 3-phase", "5.0305", "2.3.0", "energymeter", False
)
KWH_1PHASE = Device(
    "HWE-KWH1", "kWh Meter 1-phase", "5.0305", "2.3.0", "energymeter", False
)


def _gas(value: float = 2569.646, unique_id: str = GAS_UNIQUE_ID) -> dict[str, object]:
    return {
        "unique_id": unique_id,
        "type": "gas_meter",
        "timestamp": "2026-10-29T12:00:00",
        "value": value,
        "unit": "m3",
    }


def _water(value: float = 123.456) -> dict[str, object]:
    return {
        "unique_id": WATER_UNIQUE_ID,
        "type": "water_meter",
        "timestamp": "2026-10-29T12:05:00",
        "value": value,
        "unit": "m3",
    }


def three_phase() -> dict[str, object]:
    """Importing 3-phase house with gas and water."""
    return {
        "protocol_version": 50,
        "meter_model": "ISKRA 2M550T-101",
        "unique_id": "4E6576657220476F6E6E61204C657420596F7520446F776E",
        "tariff": 2,
        "energy_import_kwh": 13779.338,
        "energy_import_t1_kwh": 10830.511,
        "energy_import_t2_kwh": 2948.827,
        "energy_export_kwh": 4162.461,
        "energy_export_t1_kwh": 1285.951,
        "energy_export_t2_kwh": 2876.510,
        "power_w": 543,
        "power_l1_w": 400,
        "power_l2_w": 133,
        "power_l3_w": 10,
        "voltage_l1_v": 230.1,
        "voltage_l2_v": 229.4,
        "voltage_l3_v": 231.0,
        "current_a": 2.36,
        "current_l1_a": 1.74,
        "current_l2_a": 0.58,
        "current_l3_a": 0.04,
        "frequency_hz": 50.0,
        "apparent_power_va": 620,
        "apparent_power_l1_va": 400,
        "apparent_power_l2_va": 150,
        "apparent_power_l3_va": 70,
        "reactive_power_var": 80,
        "reactive_power_l1_var": 30,
        "reactive_power_l2_var": 40,
        "reactive_power_l3_var": 10,
        "power_factor": 0.97,
        "power_factor_l1": 0.98,
        "power_factor_l2": 0.95,
        "power_factor_l3": 0.90,
        "voltage_sag_l1_count": 1,
        "voltage_sag_l2_count": 1,
        "voltage_sag_l3_count": 0,
        "voltage_swell_l1_count": 0,
        "voltage_swell_l2_count": 1,
        "voltage_swell_l3_count": 0,
        "any_power_fail_count": 4,
        "long_power_fail_count": 2,
        "average_power_15m_w": 640,
        "monthly_power_peak_w": 4100,
        "monthly_power_peak_timestamp": "2026-10-04T10:11:22",
        "external": [_gas(), _water()],
    }


def one_phase() -> dict[str, object]:
    """Importing single-phase meter with gas and no L2/L3 readings."""
    return {
        "protocol_version": 50,
        "meter_model": "ISKRA 2M550T-101",
        "unique_id": "4E6576657220476F6E6E61204769766520596F75205570",
        "tariff": 1,
        "energy_import_kwh": 10830.511,
        "energy_import_t1_kwh": 8000.1,
        "energy_import_t2_kwh": 2830.411,
        "energy_export_kwh": 1285.951,
        "energy_export_t1_kwh": 800.0,
        "energy_export_t2_kwh": 485.951,
        "power_w": 678,
        "power_l1_w": 676,
        "voltage_v": 229.6,
        "voltage_l1_v": 229.6,
        "current_a": 2.95,
        "current_l1_a": 2.95,
        "frequency_hz": 50.0,
        "apparent_power_va": 690,
        "apparent_power_l1_va": 690,
        "reactive_power_var": 40,
        "reactive_power_l1_var": 40,
        "power_factor": 0.99,
        "power_factor_l1": 0.99,
        "voltage_sag_l1_count": 2,
        "voltage_swell_l1_count": 0,
        "any_power_fail_count": 3,
        "long_power_fail_count": 1,
        "average_power_15m_w": 500,
        "monthly_power_peak_w": 2400,
        "monthly_power_peak_timestamp": "2026-10-11T18:04:00",
        "external": [_gas(111.5, GAS_UNIQUE_ID_1PHASE)],
    }


def export_three_phase() -> dict[str, object]:
    """3-phase feed-in: negative power and current, with a phase still importing."""
    data = three_phase()
    data.update(
        {
            "power_w": -543,
            "power_l1_w": -676,
            "power_l2_w": 133,
            "power_l3_w": 0,
            "current_a": 6,
            "current_l1_a": -4,
            "current_l2_a": 2,
            "current_l3_a": 0,
        }
    )
    return data


def export_one_phase() -> dict[str, object]:
    """Single-phase feed-in."""
    data = one_phase()
    data.update({"power_w": -678, "power_l1_w": -676, "current_a": 2.94, "current_l1_a": -2.94})
    return data


def kwh_three_phase() -> dict[str, object]:
    """SDM630-wifi at night: no load, only reactive standby current.

    Captured from a physical meter. Every frame carried these 33 fields and
    nothing else: no timestamp, tariff, DSMR or external-meter fields, no
    aggregate voltage_v or power_factor. energy_import_kwh is a real 0 on a
    meter that only measures solar export.
    """
    return {
        "energy_import_kwh": 0,
        "energy_export_kwh": 13532.589,
        "power_w": 0,
        "power_l1_w": 0,
        "power_l2_w": 0,
        "power_l3_w": 0,
        "voltage_l1_v": 229.597,
        "voltage_l2_v": 228.838,
        "voltage_l3_v": 225.772,
        "current_a": 0.905,
        "current_l1_a": 0.328,
        "current_l2_a": 0.257,
        "current_l3_a": 0.32,
        "apparent_current_a": 68.007,
        "apparent_current_l1_a": 32.979,
        "apparent_current_l2_a": 16.503,
        "apparent_current_l3_a": 18.526,
        "reactive_current_a": 68.001,
        "reactive_current_l1_a": 32.977,
        "reactive_current_l2_a": 16.501,
        "reactive_current_l3_a": 18.523,
        "apparent_power_va": 205.307,
        "apparent_power_l1_va": 75.289,
        "apparent_power_l2_va": 58.506,
        "apparent_power_l3_va": 71.512,
        "reactive_power_var": 205.286,
        "reactive_power_l1_var": -75.285,
        "reactive_power_l2_var": -58.499,
        "reactive_power_l3_var": -71.501,
        "power_factor_l1": 0.01,
        "power_factor_l2": 0.016,
        "power_factor_l3": 0.017,
        "frequency_hz": 49.949,
    }


def kwh_three_phase_solar() -> dict[str, object]:
    """The same SDM630-wifi in daylight: a 3-phase inverter exporting 2.4 kW."""
    data = kwh_three_phase()
    data.update(
        {
            "power_w": -2400,
            "power_l1_w": -800,
            "power_l2_w": -801,
            "power_l3_w": -799,
            "voltage_l1_v": 232.4,
            "voltage_l2_v": 231.9,
            "voltage_l3_v": 230.8,
            "current_a": 10.39,
            "current_l1_a": -3.46,
            "current_l2_a": -3.47,
            "current_l3_a": -3.46,
            "apparent_current_a": 10.44,
            "apparent_current_l1_a": 3.48,
            "apparent_current_l2_a": 3.48,
            "apparent_current_l3_a": 3.48,
            "reactive_current_a": 0.96,
            "reactive_current_l1_a": 0.32,
            "reactive_current_l2_a": 0.32,
            "reactive_current_l3_a": 0.32,
            "apparent_power_va": 2412,
            "apparent_power_l1_va": 804,
            "apparent_power_l2_va": 804,
            "apparent_power_l3_va": 804,
            "reactive_power_var": 222,
            "reactive_power_l1_var": 74,
            "reactive_power_l2_var": 74,
            "reactive_power_l3_var": 74,
            "power_factor_l1": 0.995,
            "power_factor_l2": 0.996,
            "power_factor_l3": 0.994,
            "frequency_hz": 50.02,
        }
    )
    return data


def kwh_one_phase() -> dict[str, object]:
    """1-phase kWh meter on a heat pump, with the documented HWE-KWH1 fields."""
    return {
        "energy_import_kwh": 2214.073,
        "energy_export_kwh": 0.012,
        "power_w": 1210,
        "voltage_v": 230.4,
        "current_a": 5.36,
        "apparent_current_a": 5.47,
        "reactive_current_a": 1.09,
        "apparent_power_va": 1260,
        "reactive_power_var": 251,
        "power_factor": 0.96,
        "frequency_hz": 50.0,
    }


PROFILES = {
    "3phase": three_phase,
    "1phase": one_phase,
    "export": export_three_phase,
    "export_1phase": export_one_phase,
    "kwh3": kwh_three_phase,
    "kwh3_solar": kwh_three_phase_solar,
    "kwh1": kwh_one_phase,
}

PROFILE_DEVICES = {
    "3phase": P1_METER,
    "1phase": P1_METER,
    "export": P1_METER,
    "export_1phase": P1_METER,
    "kwh3": KWH_3PHASE,
    "kwh3_solar": KWH_3PHASE,
    "kwh1": KWH_1PHASE,
}
