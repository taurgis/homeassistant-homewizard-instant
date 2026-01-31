"""Tests for integration setup/unload."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.homewizard_instant import async_setup_entry, async_unload_entry
from custom_components.homewizard_instant.const import DOMAIN, PLATFORMS


async def test_async_setup_entry_success(hass, mock_config_entry) -> None:
    """Test setup entry success."""
    mock_config_entry.add_to_hass(hass)

    mock_api = AsyncMock()
    mock_api.close = AsyncMock()

    with (
        patch(
            "custom_components.homewizard_instant.HomeWizardEnergyV1",
            return_value=mock_api,
        ),
        patch(
            "custom_components.homewizard_instant.async_get_clientsession",
            return_value=AsyncMock(),
        ),
        patch(
            "custom_components.homewizard_instant.HWEnergyDeviceUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            return_value=True,
        ) as forward_setups,
    ):
        assert await async_setup_entry(hass, mock_config_entry)

    assert mock_config_entry.runtime_data is not None
    forward_setups.assert_called_once_with(mock_config_entry, PLATFORMS)


async def test_async_setup_entry_not_ready_triggers_reauth(hass, mock_config_entry):
    """Test ConfigEntryNotReady with API disabled triggers reauth."""
    mock_config_entry.add_to_hass(hass)

    mock_api = AsyncMock()
    mock_api.close = AsyncMock()

    with (
        patch(
            "custom_components.homewizard_instant.HomeWizardEnergyV1",
            return_value=mock_api,
        ),
        patch(
            "custom_components.homewizard_instant.async_get_clientsession",
            return_value=AsyncMock(),
        ),
        patch(
            "custom_components.homewizard_instant.HWEnergyDeviceUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(side_effect=ConfigEntryNotReady),
        ),
        patch(
            "custom_components.homewizard_instant.HWEnergyDeviceUpdateCoordinator.api_disabled",
            True,
        ),
        patch.object(mock_config_entry, "async_start_reauth") as start_reauth,
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, mock_config_entry)

    start_reauth.assert_called_once_with(hass)


async def test_async_unload_entry(hass, mock_config_entry) -> None:
    """Test unloading a config entry."""
    mock_config_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=True
    ) as unload_platforms:
        assert await async_unload_entry(hass, mock_config_entry)

    unload_platforms.assert_called_once_with(mock_config_entry, PLATFORMS)
