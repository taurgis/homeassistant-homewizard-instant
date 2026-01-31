"""Tests for diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.const import CONF_IP_ADDRESS

from custom_components.homewizard_instant.diagnostics import (
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
