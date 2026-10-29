"""Tests for sensor platform."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfVolume
from homeassistant.helpers import device_registry as dr
from homewizard_energy.models import ExternalDevice

from custom_components.homewizard_instant.const import DOMAIN
from custom_components.homewizard_instant.coordinator import (
    HWEnergyDeviceUpdateCoordinator,
)
from custom_components.homewizard_instant.entity import meter_identifier
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


async def test_external_sensor_missing_device_key(hass, mock_config_entry, mock_combined_data):
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


def _via_device_deprecations(records: list[logging.LogRecord]) -> list[str]:
    """Return log messages that still mention the deprecated via device API."""
    return [
        record.getMessage()
        for record in records
        if "via_device" in record.getMessage().lower()
        and "deprecat" in record.getMessage().lower()
    ]


async def test_external_meter_links_with_via_device_id(
    hass, mock_config_entry, mock_combined_data, caplog
):
    """External meters link to the P1 meter by device id, without a deprecation."""
    mock_config_entry.add_to_hass(hass)
    external_cls = type(mock_combined_data.measurement.external_devices["gas123"])
    mock_combined_data.measurement.external_devices["water456"] = external_cls(
        type=ExternalDevice.DeviceType.WATER_METER,
        unit="m3",
        value=2.0,
    )

    mock_api = AsyncMock()
    mock_api.combined = AsyncMock(return_value=mock_combined_data)
    mock_api.close = AsyncMock()

    caplog.set_level(logging.DEBUG)
    with (
        patch(
            "custom_components.homewizard_instant.HomeWizardEnergyV1",
            return_value=mock_api,
        ),
        patch(
            "custom_components.homewizard_instant.async_get_clientsession",
            return_value=AsyncMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    registry = dr.async_get(hass)
    entry_id = mock_config_entry.entry_id
    parent = registry.async_get_device_by_identifier(
        meter_identifier(mock_combined_data.device.serial), entry_id
    )
    gas = registry.async_get_device_by_identifier(
        (DOMAIN, f"{DOMAIN}_gas123"), entry_id
    )
    water = registry.async_get_device_by_identifier(
        (DOMAIN, f"{DOMAIN}_water456"), entry_id
    )

    assert parent is not None
    assert gas is not None
    assert water is not None
    assert gas.via_device_id == parent.id
    assert water.via_device_id == parent.id
    assert parent.via_device_id is None
    assert _via_device_deprecations(caplog.records) == []


async def test_external_sensor_device_info_uses_via_device_id(
    hass, mock_config_entry, mock_combined_data, caplog
):
    """The external sensor device info stores via_device_id and not via_device."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data

    from custom_components.homewizard_instant.sensor import EXTERNAL_SENSORS

    description = EXTERNAL_SENSORS[
        coordinator.data.measurement.external_devices["gas123"].type
    ]

    caplog.set_level(logging.DEBUG)
    external = HomeWizardExternalSensorEntity(coordinator, description, "gas123")
    device_info = external.device_info
    assert device_info is not None

    registry = dr.async_get(hass)
    parent = registry.async_get_device_by_identifier(
        meter_identifier(mock_combined_data.device.serial),
        mock_config_entry.entry_id,
    )
    assert parent is not None
    assert device_info["via_device_id"] == parent.id
    assert "via_device" not in device_info
    assert _via_device_deprecations(caplog.records) == []


async def test_external_sensor_without_parent_serial(
    hass, mock_config_entry, mock_combined_data
):
    """An external meter does not link when the P1 meter has no serial."""
    mock_config_entry.add_to_hass(hass)
    mock_combined_data.device.serial = None

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data

    from custom_components.homewizard_instant.sensor import EXTERNAL_SENSORS

    description = EXTERNAL_SENSORS[
        coordinator.data.measurement.external_devices["gas123"].type
    ]
    external = HomeWizardExternalSensorEntity(coordinator, description, "gas123")
    device_info = external.device_info
    assert device_info is not None
    assert "via_device_id" not in device_info
    assert "via_device" not in device_info


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
