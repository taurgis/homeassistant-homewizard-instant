"""Setup and cleanup tests."""

from unittest.mock import AsyncMock, patch
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from custom_components.homewizard_instant import async_setup_entry, async_unload_entry


async def test_legacy_entry_requires_pairing(hass, mock_config_entry):
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, mock_config_entry)


@pytest.mark.parametrize("failure", [None, ConfigEntryNotReady, ConfigEntryAuthFailed])
async def test_setup(hass, mock_config_entry, failure):
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            "ip_address": "1.2.3.4",
            "token": "secret",
            "product_type": "HWE-P1",
            "serial": "aabbccddeeff",
        },
    )
    with (
        patch(
            "custom_components.homewizard_instant.HWEnergyDeviceUpdateCoordinator"
        ) as cls,
        patch.object(
            hass.config_entries, "async_forward_entry_setups", return_value=True
        ),
    ):
        coordinator = cls.return_value
        coordinator.async_config_entry_first_refresh = AsyncMock(side_effect=failure)
        coordinator.async_stop = AsyncMock()
        if failure:
            with pytest.raises(failure):
                await async_setup_entry(hass, mock_config_entry)
            coordinator.async_stop.assert_awaited_once()
        else:
            assert await async_setup_entry(hass, mock_config_entry)
            coordinator.async_start.assert_called_once()
            assert mock_config_entry.runtime_data == coordinator


@pytest.mark.parametrize("success", [False, True])
async def test_unload(hass, mock_config_entry, success):
    coordinator = AsyncMock()
    mock_config_entry.runtime_data = coordinator
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=success
    ):
        assert await async_unload_entry(hass, mock_config_entry) == success
    assert coordinator.async_stop.await_count == int(success)
