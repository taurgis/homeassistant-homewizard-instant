"""Tests for sensor platform."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.const import UnitOfVolume
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_instant.const import DOMAIN
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
from homeassistant.components.sensor import SensorStateClass


async def test_async_setup_entry_adds_entities(hass, mock_config_entry, mock_combined_data):
    """Test async_setup_entry creates sensors."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api=AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
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
        hass,
        mock_config_entry,
        api=AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
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
        hass,
        mock_config_entry,
        api=AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
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
        hass,
        mock_config_entry,
        api=AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
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
        hass,
        mock_config_entry,
        api=AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
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


async def test_external_sensor_missing_device_key(hass, mock_config_entry, mock_combined_data):
    """Test external sensor handles missing device key gracefully."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api=AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )
    coordinator.data = mock_combined_data

    from custom_components.homewizard_instant.sensor import EXTERNAL_SENSORS

    description = EXTERNAL_SENSORS[
        coordinator.data.measurement.external_devices["gas123"].type
    ]

    external = HomeWizardExternalSensorEntity(coordinator, description, "missing")

    assert external.device is None
    assert external.native_value is None
    assert external.available is False
    assert external.native_unit_of_measurement is None
    assert external.device_class is None


async def test_import_tariff_t1_sensor_does_not_depend_on_export_t2(
    hass, mock_config_entry, mock_combined_data
):
    """Test import tariff 1 sensor creation depends on import data only."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api=AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )
    coordinator.data = mock_combined_data
    coordinator.data.measurement.energy_import_t1_kwh = 2.0
    coordinator.data.measurement.energy_import_t2_kwh = 1.0
    coordinator.data.measurement.energy_export_t2_kwh = None
    mock_config_entry.runtime_data = coordinator

    added = []

    def _add_entities(entities):
        added.extend(entities)

    await async_setup_entry(hass, mock_config_entry, _add_entities)

    assert any(
        isinstance(entity, HomeWizardSensorEntity)
        and entity.entity_description.key == "total_power_import_t1_kwh"
        for entity in added
    )


async def test_external_sensor_unique_id_is_scoped_per_config_entry(
    hass, mock_config_entry, mock_combined_data
):
    """Test external sensor IDs are unique across multiple configured P1 devices."""
    mock_config_entry.add_to_hass(hass)

    coordinator_1 = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api=AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )
    coordinator_1.data = mock_combined_data

    second_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_IP_ADDRESS: "5.6.7.8"},
        unique_id=f"{DOMAIN}_P1_SERIAL456",
        title="P1 Meter 2",
    )
    second_entry.add_to_hass(hass)

    coordinator_2 = HWEnergyDeviceUpdateCoordinator(
        hass,
        second_entry,
        api=AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )
    coordinator_2.data = mock_combined_data

    from custom_components.homewizard_instant.sensor import EXTERNAL_SENSORS

    description = EXTERNAL_SENSORS[
        coordinator_1.data.measurement.external_devices["gas123"].type
    ]

    external_1 = HomeWizardExternalSensorEntity(coordinator_1, description, "gas123")
    external_2 = HomeWizardExternalSensorEntity(coordinator_2, description, "gas123")

    assert external_1.unique_id != external_2.unique_id
    assert external_1.device_info["identifiers"] != external_2.device_info["identifiers"]


async def test_energy_sensor_zero_value_is_available(
    hass, mock_config_entry, mock_combined_data
):
    """Test energy sensors keep valid 0 values instead of becoming unavailable."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api=AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )
    coordinator.data = mock_combined_data
    coordinator.data.measurement.energy_import_kwh = 0.0

    description = next(d for d in SENSORS if d.key == "total_power_import_kwh")
    entity = HomeWizardSensorEntity(coordinator, description)

    assert entity.native_value == 0.0
    assert entity.available is True


def test_power_average_and_peak_have_measurement_state_class() -> None:
    """Test average and monthly peak power sensors expose measurement state class."""
    average = next(d for d in SENSORS if d.key == "active_power_average_w")
    monthly_peak = next(d for d in SENSORS if d.key == "monthly_power_peak_w")

    assert average.state_class == SensorStateClass.MEASUREMENT
    assert monthly_peak.state_class == SensorStateClass.MEASUREMENT
