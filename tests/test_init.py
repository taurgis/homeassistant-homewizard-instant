"""Tests for integration setup/unload."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_instant import async_setup_entry, async_unload_entry
from custom_components.homewizard_instant.const import DOMAIN, PLATFORMS


@pytest.fixture(autouse=True)
def mock_has_v2_api_false() -> None:
    """Default setup tests to v1 path unless explicitly overridden."""
    with patch(
        "custom_components.homewizard_instant.has_v2_api",
        new=AsyncMock(return_value=False),
    ):
        yield


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
    mock_config_entry.runtime_data = AsyncMock(async_shutdown=AsyncMock())

    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=True
    ) as unload_platforms:
        assert await async_unload_entry(hass, mock_config_entry)

    mock_config_entry.runtime_data.async_shutdown.assert_awaited_once()
    unload_platforms.assert_called_once_with(mock_config_entry, PLATFORMS)


async def test_async_unload_entry_no_shutdown_when_unload_fails(
    hass, mock_config_entry
) -> None:
    """Test runtime shutdown is skipped when platform unload fails."""
    mock_config_entry.add_to_hass(hass)
    mock_config_entry.runtime_data = AsyncMock(async_shutdown=AsyncMock())

    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ) as unload_platforms:
        assert not await async_unload_entry(hass, mock_config_entry)

    mock_config_entry.runtime_data.async_shutdown.assert_not_awaited()
    unload_platforms.assert_called_once_with(mock_config_entry, PLATFORMS)


async def test_async_setup_entry_auth_failed_closes_api(hass, mock_config_entry) -> None:
    """Test ConfigEntryAuthFailed during first refresh closes resources."""
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
            new=AsyncMock(side_effect=ConfigEntryAuthFailed),
        ),
    ):
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, mock_config_entry)

    mock_api.close.assert_awaited_once()


async def test_async_setup_entry_creates_migration_issue(hass, mock_config_entry) -> None:
    """Test setup creates migration repair issue for v1 entries supporting v2."""
    mock_config_entry.add_to_hass(hass)

    mock_api = AsyncMock()
    mock_api.close = AsyncMock()

    with (
        patch(
            "custom_components.homewizard_instant.HomeWizardEnergyV1",
            return_value=mock_api,
        ),
        patch(
            "custom_components.homewizard_instant.has_v2_api",
            new=AsyncMock(return_value=True),
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
        ),
    ):
        assert await async_setup_entry(hass, mock_config_entry)

    issue = ir.async_get(hass).async_get_issue(
        DOMAIN,
        f"migrate_to_v2_api_{mock_config_entry.entry_id}",
    )
    assert issue is not None
    assert issue.translation_placeholders == {"title": mock_config_entry.title}


async def test_async_setup_entry_with_token_skips_migration_issue(hass) -> None:
    """Test setup does not create migration issue for already-tokenized entries."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_IP_ADDRESS: "1.2.3.4", CONF_TOKEN: "token123"},
        unique_id=f"{DOMAIN}_P1_SERIAL123",
        title="P1 Meter",
    )
    entry.add_to_hass(hass)

    mock_api = AsyncMock()
    mock_api.close = AsyncMock()

    with (
        patch(
            "custom_components.homewizard_instant.HomeWizardEnergyV2",
            return_value=mock_api,
        ),
        patch(
            "custom_components.homewizard_instant.has_v2_api",
            new=AsyncMock(return_value=True),
        ) as has_v2_mock,
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
        ),
    ):
        assert await async_setup_entry(hass, entry)

    has_v2_mock.assert_not_awaited()
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN,
        f"migrate_to_v2_api_{entry.entry_id}",
    )
    assert issue is None
