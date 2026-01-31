"""Tests for base entity."""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.homewizard_instant.const import DOMAIN
from custom_components.homewizard_instant.coordinator import (
    HWEnergyDeviceUpdateCoordinator,
)
from custom_components.homewizard_instant.entity import HomeWizardEntity


async def test_entity_device_info_identifiers(hass, mock_config_entry, mock_combined_data):
    """Test entity uses DOMAIN-prefixed identifiers."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data

    entity = HomeWizardEntity(coordinator)

    identifiers = entity.device_info["identifiers"]
    assert (DOMAIN, f"{DOMAIN}_{mock_combined_data.device.serial}") in identifiers
