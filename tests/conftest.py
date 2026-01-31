"""Test fixtures for HomeWizard Instant."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from homewizard_energy.const import Model
from homewizard_energy.models import ExternalDevice

from homeassistant.const import CONF_IP_ADDRESS
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_instant.const import DOMAIN


@dataclass
class FakeDevice:
    """Minimal device model."""

    product_type: str
    product_name: str
    model_name: str
    firmware_version: str
    serial: str | None


@dataclass
class FakeSystem:
    """Minimal system model."""

    wifi_ssid: str | None = None
    wifi_strength_pct: int | None = None
    wifi_rssi_db: int | None = None
    uptime_s: int | None = None


@dataclass
class FakeExternalDevice:
    """Minimal external device model."""

    type: ExternalDevice.DeviceType | None
    unit: str | None
    value: float | int | str | None


@dataclass
class FakeMeasurement:
    """Minimal measurement model."""

    protocol_version: str | None = None
    meter_model: str | None = None
    unique_id: str | None = None
    tariff: int | None = None
    energy_import_kwh: float | None = None
    energy_import_t1_kwh: float | None = None
    energy_import_t2_kwh: float | None = None
    energy_import_t3_kwh: float | None = None
    energy_import_t4_kwh: float | None = None
    energy_export_kwh: float | None = None
    energy_export_t1_kwh: float | None = None
    energy_export_t2_kwh: float | None = None
    energy_export_t3_kwh: float | None = None
    energy_export_t4_kwh: float | None = None
    power_w: float | None = None
    power_l1_w: float | None = None
    power_l2_w: float | None = None
    power_l3_w: float | None = None
    voltage_v: float | None = None
    voltage_l1_v: float | None = None
    voltage_l2_v: float | None = None
    voltage_l3_v: float | None = None
    current_a: float | None = None
    current_l1_a: float | None = None
    current_l2_a: float | None = None
    current_l3_a: float | None = None
    frequency_hz: float | None = None
    apparent_power_va: float | None = None
    apparent_power_l1_va: float | None = None
    apparent_power_l2_va: float | None = None
    apparent_power_l3_va: float | None = None
    reactive_power_var: float | None = None
    reactive_power_l1_var: float | None = None
    reactive_power_l2_var: float | None = None
    reactive_power_l3_var: float | None = None
    power_factor: float | None = None
    power_factor_l1: float | None = None
    power_factor_l2: float | None = None
    power_factor_l3: float | None = None
    voltage_sag_l1_count: int | None = None
    voltage_sag_l2_count: int | None = None
    voltage_sag_l3_count: int | None = None
    voltage_swell_l1_count: int | None = None
    voltage_swell_l2_count: int | None = None
    voltage_swell_l3_count: int | None = None
    any_power_fail_count: int | None = None
    long_power_fail_count: int | None = None
    average_power_15m_w: float | None = None
    monthly_power_peak_w: float | None = None
    external_devices: dict[str, FakeExternalDevice] | None = None


@dataclass
class FakeCombinedModels:
    """Minimal combined model data structure."""

    device: FakeDevice
    measurement: FakeMeasurement
    system: FakeSystem | None = None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Enable custom integrations for all tests."""


@pytest.fixture
def mock_device_info() -> SimpleNamespace:
    """Return a mock device info object for config flow tests."""
    return SimpleNamespace(
        product_type=Model.P1_METER,
        product_name="P1 Meter",
        serial="HW123",
    )


@pytest.fixture
def mock_combined_data() -> FakeCombinedModels:
    """Return combined data with a few populated fields."""
    device = FakeDevice(
        product_type="P1",
        product_name="P1 Meter",
        model_name="P1 Meter",
        firmware_version="1.2.3",
        serial="SERIAL123",
    )
    measurement = FakeMeasurement(
        protocol_version="50",
        meter_model="SMR",
        unique_id="unique",
        tariff=1,
        energy_import_kwh=1.23,
        energy_import_t1_kwh=2.0,
        energy_export_t2_kwh=1.0,
        energy_export_kwh=0,
        power_w=50.0,
        average_power_15m_w=100.0,
        monthly_power_peak_w=200.0,
        external_devices={
            "gas123": FakeExternalDevice(
                type=ExternalDevice.DeviceType.GAS_METER,
                unit="m3",
                value=1.5,
            )
        },
    )
    system = FakeSystem(
        wifi_ssid="wifi",
        wifi_strength_pct=75,
        wifi_rssi_db=-40,
        uptime_s=3600,
    )
    return FakeCombinedModels(device=device, measurement=measurement, system=system)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_IP_ADDRESS: "1.2.3.4"},
        unique_id=f"{DOMAIN}_P1_SERIAL123",
        title="P1 Meter",
    )
