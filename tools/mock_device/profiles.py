"""Realistic P1 snapshots based on the HomeWizard API v2 measurement examples."""

from __future__ import annotations

GAS_UNIQUE_ID = "4730303031"  # hex for G0001
WATER_UNIQUE_ID = "5730303031"  # hex for W0001


def _gas(value: float = 2569.646) -> dict[str, object]:
    return {
        "unique_id": GAS_UNIQUE_ID,
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
        "external": [_gas(111.5)],
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


PROFILES = {
    "3phase": three_phase,
    "1phase": one_phase,
    "export": export_three_phase,
    "export_1phase": export_one_phase,
}
