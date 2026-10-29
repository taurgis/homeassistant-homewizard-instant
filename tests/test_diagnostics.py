"""Tests for diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.const import CONF_IP_ADDRESS

from custom_components.homewizard_instant.diagnostics import (
    _serialize_data,
    async_get_config_entry_diagnostics,
)
from custom_components.homewizard_instant.coordinator import (
    HWEnergyDeviceUpdateCoordinator,
)


async def test_async_get_config_entry_diagnostics_redacts(
    hass, mock_config_entry, mock_combined_data
):
    """Test diagnostics redacts sensitive fields."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"token": "secret", CONF_IP_ADDRESS: "5.6.7.8"}
    )

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data
    mock_config_entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert diagnostics["entry"]["data"][CONF_IP_ADDRESS] == "**REDACTED**"
    assert diagnostics["entry"]["options"][CONF_IP_ADDRESS] == "**REDACTED**"
    assert diagnostics["entry"]["options"]["token"] == "**REDACTED**"
    assert diagnostics["entry"]["unique_id"] == "**REDACTED**"
    assert diagnostics["data"]["device"]["serial"] == "**REDACTED**"


def test_serialize_data_model_dump() -> None:
    """Test _serialize_data handles model_dump."""

    class Dumpable:
        def model_dump(self) -> dict[str, str]:
            return {"key": "value"}

    assert _serialize_data(Dumpable()) == {"key": "value"}


def test_serialize_data_dict_method() -> None:
    """Test _serialize_data handles dict method."""

    class Dictable:
        def dict(self) -> dict[str, str]:
            return {"key": "value"}

    assert _serialize_data(Dictable()) == {"key": "value"}


def test_serialize_data_dunder_dict() -> None:
    """Test _serialize_data handles __dict__."""

    class HasDict:
        def __init__(self) -> None:
            self.value = "value"

    assert _serialize_data(HasDict()) == {"value": "value"}


def test_serialize_data_fallback() -> None:
    """Test _serialize_data fallback wraps value."""

    assert _serialize_data(123) == {"value": 123}


def test_serialize_library_models_are_json_and_redacted() -> None:
    """AwesomeVersion and datetimes must not break the diagnostics download."""
    import json

    from homeassistant.components.diagnostics import async_redact_data
    from homewizard_energy.models import CombinedModels, Device, Measurement, System

    from custom_components.homewizard_instant.diagnostics import TO_REDACT

    device = Device.from_dict(
        {
            "product_type": "HWE-P1",
            "product_name": "P1 Meter",
            "serial": "aabbccddeeff",
            "firmware_version": "6.0204",
            "api_version": "2.0.0",
        }
    )
    measurement = Measurement.from_dict(
        {
            "power_w": 1,
            "energy_import_kwh": 2,
            "unique_id": "4E65766572",
            "timestamp": "2026-10-29T12:00:00",
        }
    )
    system = System.from_dict(
        {"wifi_ssid": "secret-ssid", "wifi_rssi_db": -70, "uptime_s": 5}
    )
    dumped = _serialize_data(CombinedModels(device, measurement, None, system))
    encoded = json.dumps(dumped)
    assert dumped["device"]["api_version"] == "2.0.0"
    assert "2026-10-29" in encoded
    redacted = async_redact_data(dumped, TO_REDACT)
    text = json.dumps(redacted)
    assert redacted["device"]["serial"] == "**REDACTED**"
    assert redacted["system"]["wifi_ssid"] == "**REDACTED**"
    assert "aabbccddeeff" not in text
    assert "secret-ssid" not in text


async def test_diagnostics_hide_external_meter_ids(
    hass, mock_config_entry, mock_combined_data
):
    """External meter dict keys embed meter serials and must not leak."""
    mock_config_entry.add_to_hass(hass)
    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data
    mock_config_entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert "gas123" not in str(diagnostics)
    assert diagnostics["data"]["measurement"]["external_devices"][0]["value"] == 1.5
    assert diagnostics["connection"]["last_update_success"] is True


def test_last_exception_keeps_the_reason_without_the_address() -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed

    from custom_components.homewizard_instant.diagnostics import _describe_error

    err = UpdateFailed(
        translation_domain="homewizard_instant",
        translation_key="communication_error",
        translation_placeholders={"error": "Cannot connect to host 1.2.3.4:443"},
    )
    described = _describe_error(err, "1.2.3.4")
    assert described["type"] == "UpdateFailed"
    assert described["translation_key"] == "communication_error"
    assert described["placeholder_error"] == "Cannot connect to host **REDACTED**:443"
    assert "1.2.3.4" not in str(described)
    assert _describe_error(None, "1.2.3.4") is None
