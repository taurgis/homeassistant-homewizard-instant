"""Tests for the coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homewizard_energy.errors import DisabledError, RequestError

from homeassistant.helpers.issue_registry import IssueSeverity
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.homewizard_instant.coordinator import (
    HWEnergyDeviceUpdateCoordinator,
)
from custom_components.homewizard_instant.const import DOMAIN


async def test_coordinator_update_success(hass, mock_config_entry, mock_combined_data):
    """Test coordinator successfully updates data."""
    mock_config_entry.add_to_hass(hass)

    api = AsyncMock()
    api.combined = AsyncMock(return_value=mock_combined_data)

    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)

    with patch(
        "custom_components.homewizard_instant.coordinator.ir.async_delete_issue"
    ) as delete_issue:
        data = await coordinator._async_update_data()

    assert data == mock_combined_data
    assert coordinator.data == mock_combined_data
    assert coordinator.api_disabled is False
    delete_issue.assert_called_once_with(hass, DOMAIN, "local_api_disabled")


async def test_coordinator_request_error(hass, mock_config_entry):
    """Test coordinator handles RequestError."""
    mock_config_entry.add_to_hass(hass)

    api = AsyncMock()
    api.combined = AsyncMock(side_effect=RequestError("boom"))

    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)

    with pytest.raises(UpdateFailed) as err:
        await coordinator._async_update_data()

    assert err.value.translation_key == "communication_error"


async def test_coordinator_disabled_error_triggers_reload(
    hass, mock_config_entry, mock_combined_data
):
    """Test coordinator handles DisabledError and schedules reload."""
    mock_config_entry.add_to_hass(hass)

    api = AsyncMock()
    api.combined = AsyncMock(side_effect=DisabledError("disabled"))

    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)
    coordinator.data = mock_combined_data

    hass.config_entries.async_schedule_reload = Mock()

    with patch(
        "custom_components.homewizard_instant.coordinator.ir.async_create_issue"
    ) as create_issue:
        with pytest.raises(UpdateFailed) as err:
            await coordinator._async_update_data()

    assert err.value.translation_key == "api_disabled"
    assert coordinator.api_disabled is True
    create_issue.assert_called_once()
    assert create_issue.call_args.args[:3] == (hass, DOMAIN, "local_api_disabled")
    assert create_issue.call_args.kwargs["severity"] == IssueSeverity.ERROR
    hass.config_entries.async_schedule_reload.assert_called_once_with(
        mock_config_entry.entry_id
    )
