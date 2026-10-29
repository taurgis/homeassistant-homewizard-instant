"""Setup and cleanup tests."""

from unittest.mock import AsyncMock, patch
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from custom_components.homewizard_instant import async_setup_entry, async_unload_entry


async def test_legacy_entry_requires_pairing(hass, mock_config_entry):
    with pytest.raises(ConfigEntryAuthFailed) as err:
        await async_setup_entry(hass, mock_config_entry)
    assert err.value.translation_key == "pairing_required"


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
    # The reader stops through the entry's unload callback, not twice.
    coordinator.async_stop.assert_not_awaited()


@pytest.mark.parametrize("token", [None, "secret"])
async def test_remove_revokes_token(hass, mock_config_entry, token):
    from custom_components.homewizard_instant import async_remove_entry

    data = {"ip_address": "1.2.3.4", "product_type": "HWE-P1", "serial": "aabbccddeeff"}
    if token:
        data["token"] = token
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, data=data)
    with patch(
        "custom_components.homewizard_instant.async_revoke_token"
    ) as revoke:
        await async_remove_entry(hass, mock_config_entry)
    if token:
        revoke.assert_awaited_once_with(
            hass, "1.2.3.4", token, product_type="HWE-P1", serial="aabbccddeeff"
        )
    else:
        revoke.assert_not_called()


async def test_only_unreported_devices_can_be_removed(
    hass, mock_config_entry, mock_combined_data
):
    from types import SimpleNamespace

    from custom_components.homewizard_instant import async_remove_config_entry_device
    from custom_components.homewizard_instant.const import DOMAIN

    def device(key):
        return SimpleNamespace(identifiers={(DOMAIN, f"{DOMAIN}_{key}")})

    assert await async_remove_config_entry_device(hass, mock_config_entry, device("x"))
    mock_config_entry.runtime_data = SimpleNamespace(data=mock_combined_data)
    serial = mock_combined_data.device.serial
    external = next(iter(mock_combined_data.measurement.external_devices))
    assert not await async_remove_config_entry_device(
        hass, mock_config_entry, device(serial)
    )
    assert not await async_remove_config_entry_device(
        hass, mock_config_entry, device(external)
    )
    assert await async_remove_config_entry_device(
        hass, mock_config_entry, device("gas_meter_gone")
    )
