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

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data
    mock_config_entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert diagnostics["entry"][CONF_IP_ADDRESS] == "**REDACTED**"
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
