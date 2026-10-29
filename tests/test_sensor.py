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


async def test_async_setup_entry_adds_entities(
    hass, mock_config_entry, mock_combined_data
):
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


async def test_sensor_entity_enabled_default(
    hass, mock_config_entry, mock_combined_data
):
    """Test sensor enabled default respects enabled_fn."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data

    description = next(d for d in SENSORS if d.key == "total_power_export_kwh")

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

    external = HomeWizardExternalSensorEntity(
        coordinator, description, "gas123", via_device_id="parent-device"
    )
    assert external.device_info is not None
    if "via_device_id" in external.device_info:
        assert external.device_info["via_device_id"] == "parent-device"
    else:
        assert external.device_info["via_device"] == (
            "homewizard_instant",
            "homewizard_instant_SERIAL123",
        )

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

    description = EXTERNAL_SENSORS[list(EXTERNAL_SENSORS.keys())[0]]

    external = HomeWizardExternalSensorEntity(coordinator, description, "missing")

    assert external.device is None
    assert external.native_value is None
    assert external.available is False
    assert external.native_unit_of_measurement is None


async def test_external_sensor_missing_device_key(
    hass, mock_config_entry, mock_combined_data
):
    """Test external sensor handles missing device key gracefully."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
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
        hass, mock_config_entry, api=AsyncMock()
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


async def test_external_meter_added_after_setup(
    hass, mock_config_entry, mock_combined_data
):
    """A utility meter that shows up later still gets an entity."""
    from homewizard_energy.models import ExternalDevice

    mock_config_entry.add_to_hass(hass)
    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data
    coordinator.last_update_success = True
    mock_config_entry.runtime_data = coordinator
    added: list[object] = []

    def _add_entities(entities):
        added.extend(entities)

    await async_setup_entry(hass, mock_config_entry, _add_entities)
    before = len(added)
    gas = mock_combined_data.measurement.external_devices["gas123"]
    mock_combined_data.measurement.external_devices["water1"] = type(gas)(
        type=ExternalDevice.DeviceType.WATER_METER,
        unit="m3",
        value=3,
    )
    coordinator.async_set_updated_data(mock_combined_data)
    assert len(added) == before + 1
    assert getattr(added[-1], "_device_id", None) == "water1"


async def test_zero_energy_stays_available(hass, mock_config_entry, mock_combined_data):
    """A reading of 0 kWh is a real total, not a missing value."""
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, AsyncMock())
    coordinator.data = mock_combined_data
    coordinator.last_update_success = True
    coordinator.data.measurement.energy_import_kwh = 0
    coordinator.data.measurement.energy_export_kwh = 0
    description = next(item for item in SENSORS if item.key == "total_power_import_kwh")
    entity = HomeWizardSensorEntity(coordinator, description)
    assert entity.native_value == 0
    assert entity.available is True


async def test_uptime_jitter_stays_on_the_entity(hass, mock_config_entry, mock_combined_data, freezer):
    """A small uptime correction must not move the estimated boot time."""
    from dataclasses import replace
    from datetime import timedelta

    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, AsyncMock())
    coordinator.data = mock_combined_data
    sensor = HomeWizardSensorEntity(
        coordinator, next(item for item in SENSORS if item.key == "uptime")
    )
    initial = sensor.native_value
    freezer.tick(timedelta(seconds=30))
    coordinator.data = replace(
        mock_combined_data, system=replace(mock_combined_data.system, uptime_s=3610)
    )
    assert sensor.native_value == initial


async def test_uptime_is_tracked_per_meter(hass, mock_config_entry, mock_combined_data):
    """Two meters must not share one boot-time estimate."""
    from dataclasses import replace
    from datetime import timedelta

    first = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, AsyncMock())
    second = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, AsyncMock())
    first.data = mock_combined_data
    second.data = replace(
        mock_combined_data, system=replace(mock_combined_data.system, uptime_s=3660)
    )
    description = next(item for item in SENSORS if item.key == "uptime")
    older = HomeWizardSensorEntity(first, description)
    newer_boot = HomeWizardSensorEntity(second, description)
    assert newer_boot.native_value == older.native_value - timedelta(seconds=60)


async def test_uptime_does_not_drift_on_measurement_push(
    hass, mock_config_entry, mock_combined_data, freezer
):
    """The same system snapshot must not move the estimated boot time."""
    from dataclasses import replace
    from datetime import timedelta
    from custom_components.homewizard_instant.coordinator import (
        HWEnergyDeviceUpdateCoordinator,
    )
    from custom_components.homewizard_instant.sensor import (
        HomeWizardSensorEntity,
        SENSORS,
    )

    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, AsyncMock())
    coordinator.data = mock_combined_data
    coordinator.last_update_success = True
    sensor = HomeWizardSensorEntity(
        coordinator, next(item for item in SENSORS if item.key == "uptime")
    )
    initial = sensor.native_value
    freezer.tick(timedelta(minutes=10))
    coordinator.data = replace(mock_combined_data)  # measurement push, same system
    sensor._refresh_uptime()
    assert sensor.available is True
    assert sensor.native_value == initial
    coordinator.data.system.uptime_s = None
    assert sensor.available is False
    coordinator.data = replace(
        mock_combined_data, system=replace(mock_combined_data.system, uptime_s=1)
    )
    sensor._refresh_uptime()
    assert sensor.native_value > initial


async def test_sensors_that_appear_later_are_added(
    hass, mock_config_entry, mock_combined_data
):
    """Fields and meters missing from the first snapshot get entities later."""
    from dataclasses import replace

    from tests.conftest import FakeExternalDevice
    from homewizard_energy.models import ExternalDevice

    mock_config_entry.add_to_hass(hass)
    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.async_set_updated_data(mock_combined_data)
    mock_config_entry.runtime_data = coordinator
    added = []
    await async_setup_entry(hass, mock_config_entry, added.extend)
    initial = len(added)
    assert not any(e.entity_description.key == "active_voltage_v" for e in added)

    measurement = replace(
        mock_combined_data.measurement,
        voltage_v=230.0,
        external_devices={
            **mock_combined_data.measurement.external_devices,
            "water1": FakeExternalDevice(
                type=ExternalDevice.DeviceType.WATER_METER, unit="m3", value=3.0
            ),
        },
    )
    coordinator.async_set_updated_data(replace(mock_combined_data, measurement=measurement))
    new = added[initial:]
    assert [e.entity_description.key for e in new] == ["active_voltage_v", "water_meter"]

    # A repeated push does not create duplicates.
    coordinator.async_set_updated_data(replace(mock_combined_data, measurement=measurement))
    assert len(added) == initial + 2


async def test_uptime_refreshes_on_coordinator_update(
    hass, mock_config_entry, mock_combined_data
):
    from dataclasses import replace
    from unittest.mock import patch

    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, AsyncMock())
    coordinator.data = mock_combined_data
    sensor = HomeWizardSensorEntity(
        coordinator, next(item for item in SENSORS if item.key == "uptime")
    )
    initial = sensor.native_value
    coordinator.data = replace(
        mock_combined_data, system=replace(mock_combined_data.system, uptime_s=1)
    )
    with patch.object(sensor, "async_write_ha_state") as write:
        sensor._handle_coordinator_update()
    write.assert_called_once()
    assert sensor.native_value > initial
