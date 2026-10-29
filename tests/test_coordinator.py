"""Coordinator lifecycle and connection recovery tests."""

import asyncio
import logging
from unittest.mock import AsyncMock, Mock, patch
import pytest
from homewizard_energy.errors import RequestError, UnauthorizedError
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.homewizard_instant.coordinator import (
    HWEnergyDeviceUpdateCoordinator,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RequestError(), UpdateFailed),
        (UnauthorizedError(), ConfigEntryAuthFailed),
        (TimeoutError(), UpdateFailed),
    ],
)
async def test_initial_error(hass, mock_config_entry, error, expected, caplog):
    api = AsyncMock()
    api.connect.side_effect = error
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)
    with caplog.at_level(logging.WARNING), pytest.raises(expected):
        await coordinator._async_update_data()
    assert coordinator.update_interval is None
    if expected is UpdateFailed:
        assert "connection failed" in caplog.text


async def test_initial_snapshot(hass, mock_config_entry, mock_combined_data):
    api = AsyncMock()
    api.connect.return_value = mock_combined_data
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)
    assert await coordinator._async_update_data() == mock_combined_data
    assert coordinator.update_interval is None


async def test_reconnect(hass, mock_config_entry, mock_combined_data, caplog):
    api = AsyncMock()
    api.receive.side_effect = [RequestError(), asyncio.CancelledError()]
    api.connect.return_value = mock_combined_data
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)
    with (
        patch(
            "custom_components.homewizard_instant.coordinator.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep,
        patch.object(coordinator, "async_set_updated_data") as update,
        patch.object(coordinator, "async_set_update_error") as unavailable,
    ):
        with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
            await coordinator._listen()
    assert "retrying in 1 s" in caplog.text
    unavailable.assert_called_once()
    update.assert_called_once_with(mock_combined_data)
    sleep.assert_awaited_once_with(1)
    assert api.close.await_count == 2


async def test_revoked_token(hass, mock_config_entry, caplog):
    api = AsyncMock()
    api.receive.side_effect = UnauthorizedError()
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)
    with (
        caplog.at_level(logging.WARNING),
        patch.object(mock_config_entry, "async_start_reauth") as reauth,
    ):
        await coordinator._listen()
    assert "token was rejected" in caplog.text
    reauth.assert_called_once_with(hass)
    assert not coordinator.last_update_success
    api.close.assert_awaited_once()


async def test_refresh_while_listener_is_running(
    hass, mock_config_entry, mock_combined_data
):
    api = AsyncMock()
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)
    coordinator._task = Mock()
    coordinator.data = mock_combined_data
    coordinator.last_update_success = True
    assert await coordinator._async_update_data() is mock_combined_data
    coordinator.last_update_success = False
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    api.connect.assert_not_called()


async def test_successful_push_resets_backoff(hass, mock_config_entry, mock_combined_data):
    api = AsyncMock()
    api.receive.side_effect = [
        mock_combined_data,
        RequestError(),
        asyncio.CancelledError(),
    ]
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)
    with (
        patch(
            "custom_components.homewizard_instant.coordinator.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep,
        patch.object(coordinator, "async_set_updated_data"),
        patch.object(coordinator, "async_set_update_error"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await coordinator._listen()
    sleep.assert_awaited_once_with(1)


async def test_stop_cancels_reader(hass, mock_config_entry):
    api = AsyncMock()
    ready = asyncio.Event()

    async def receive():
        ready.set()
        await asyncio.Event().wait()

    api.receive.side_effect = receive
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)
    coordinator.async_start()
    await ready.wait()
    await coordinator.async_stop()
    assert coordinator._task is None
    assert api.close.await_count >= 1
