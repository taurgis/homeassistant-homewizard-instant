"""Tests for sensor platform."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfVolume

from custom_components.homewizard_instant.coordinator import (
    HWEnergyDeviceUpdateCoordinator,
)
from custom_components.homewizard_instant.sensor import (
    HomeWizardExternalSensorEntity,
    HomeWizardSensorEntity,
    async_setup_entry,
)
from custom_components.homewizard_instant.sensor import (
    SENSORS,
    to_percentage,
    uptime_to_datetime,
)


async def test_async_setup_entry_adds_entities(hass, mock_config_entry, mock_combined_data):
    """Test async_setup_entry creates sensors."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data

    mock_config_entry.runtime_data = coordinator

    added = []

    def _add_entities(entities):
        added.extend(entities)

    await async_setup_entry(hass, mock_config_entry, _add_entities)

    assert added
    assert any(isinstance(entity, HomeWizardSensorEntity) for entity in added)
    assert any(isinstance(entity, HomeWizardExternalSensorEntity) for entity in added)


async def test_sensor_entity_enabled_default(hass, mock_config_entry, mock_combined_data):
    """Test sensor enabled default respects enabled_fn."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data

    description = next(
        d for d in SENSORS if d.key == "total_power_export_kwh"
    )

    entity = HomeWizardSensorEntity(coordinator, description)

    assert entity.entity_registry_enabled_default is False


async def test_sensor_entity_available(hass, mock_config_entry, mock_combined_data):
    """Test sensor availability depends on value."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data
    coordinator.data.measurement.power_w = None

    description = next(d for d in SENSORS if d.key == "active_power_w")
    entity = HomeWizardSensorEntity(coordinator, description)

    assert entity.native_value is None
    assert entity.available is False


async def test_external_sensor_unit_and_device_class(
    hass, mock_config_entry, mock_combined_data
):
    """Test external sensor unit normalization and device class handling."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data

    from custom_components.homewizard_instant.sensor import EXTERNAL_SENSORS

    description = EXTERNAL_SENSORS[
        coordinator.data.measurement.external_devices["gas123"].type
    ]

    external = HomeWizardExternalSensorEntity(coordinator, description, "gas123")

    assert external.native_unit_of_measurement == UnitOfVolume.CUBIC_METERS
    assert external.device_class == SensorDeviceClass.GAS

    coordinator.data.measurement.external_devices["gas123"].unit = "invalid"
    assert external.device_class is None


def test_to_percentage_and_uptime() -> None:
    """Test sensor helpers."""
    assert to_percentage(None) is None
    assert to_percentage(0.5) == 50

    dt = uptime_to_datetime(10)
    assert dt.tzinfo is not None


async def test_external_sensor_no_device(hass, mock_config_entry, mock_combined_data):
    """Test external sensor handles missing device gracefully."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data
    coordinator.data.measurement.external_devices = None

    from custom_components.homewizard_instant.sensor import EXTERNAL_SENSORS

    description = EXTERNAL_SENSORS[
        list(EXTERNAL_SENSORS.keys())[0]
    ]

    external = HomeWizardExternalSensorEntity(coordinator, description, "missing")

    assert external.device is None
    assert external.native_value is None
    assert external.available is False
    assert external.native_unit_of_measurement is None
