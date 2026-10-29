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
    with caplog.at_level(logging.DEBUG), pytest.raises(expected) as raised:
        await coordinator._async_update_data()
    assert coordinator.update_interval is None
    if expected is UpdateFailed:
        # HA logs setup retries; the integration only adds detail at debug.
        assert "connection failed" in caplog.text
        assert all(r.levelno < logging.WARNING for r in caplog.records)
        assert raised.value.translation_placeholders["error"]


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
        with caplog.at_level(logging.INFO), pytest.raises(asyncio.CancelledError):
            await coordinator._listen()
    assert "reconnecting in 1 s" in caplog.text
    assert "reconnected" in caplog.text
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


async def test_unexpected_error_keeps_the_reader_alive(
    hass, mock_config_entry, mock_combined_data, caplog
):
    api = AsyncMock()
    api.receive.side_effect = [KeyError("bug"), asyncio.CancelledError()]
    api.connect.side_effect = [KeyError("bug"), mock_combined_data]
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)
    coordinator.data = mock_combined_data
    with (
        patch(
            "custom_components.homewizard_instant.coordinator.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep,
        caplog.at_level(logging.WARNING),
        pytest.raises(asyncio.CancelledError),
    ):
        await coordinator._listen()
    # One traceback per outage, not one per retry.
    assert caplog.text.count("Unexpected HomeWizard WebSocket error") == 1
    assert [call.args[0] for call in sleep.await_args_list] == [1, 2]
    assert api.connect.await_count == 2


async def test_stale_measurements_mark_unavailable(
    hass, mock_config_entry, mock_combined_data, caplog
):
    from dataclasses import replace

    api = Mock()
    api.last_measurement = 0.0
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)
    coordinator.async_set_updated_data(mock_combined_data)
    with patch(
        "custom_components.homewizard_instant.coordinator.time.monotonic",
        return_value=30.0,
    ):
        coordinator._async_check_stale()
    assert coordinator.last_update_success
    with (
        caplog.at_level(logging.INFO),
        patch(
            "custom_components.homewizard_instant.coordinator.time.monotonic",
            return_value=61.0,
        ),
    ):
        coordinator._async_check_stale()
        coordinator._async_check_stale()
    assert not coordinator.last_update_success
    assert caplog.text.count("No HomeWizard measurement") == 1
    assert coordinator.last_exception.translation_key == "stale_data"
    # A system-only push does not hide the stale measurement.
    coordinator._async_publish(
        replace(mock_combined_data, system=replace(mock_combined_data.system))
    )
    assert not coordinator.last_update_success
    fresh = replace(mock_combined_data, measurement=replace(mock_combined_data.measurement))
    with caplog.at_level(logging.INFO):
        coordinator._async_publish(fresh)
    assert coordinator.last_update_success
    assert coordinator.data is fresh
    assert "measurements resumed" in caplog.text


async def test_unchanged_push_is_skipped(hass, mock_config_entry, mock_combined_data):
    from dataclasses import replace

    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, Mock())
    coordinator.async_set_updated_data(mock_combined_data)
    listener = Mock()
    coordinator.async_add_listener(listener)
    coordinator._async_publish(replace(mock_combined_data))
    listener.assert_not_called()
    changed = replace(
        mock_combined_data,
        measurement=replace(mock_combined_data.measurement, power_w=999),
    )
    coordinator._async_publish(changed)
    listener.assert_called_once()


async def test_firmware_update_reaches_device_registry(
    hass, mock_config_entry, mock_combined_data
):
    from dataclasses import replace

    from homeassistant.helpers import device_registry as dr

    from custom_components.homewizard_instant.const import DOMAIN

    mock_config_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, f"{DOMAIN}_SERIAL123")},
        sw_version="1.2.3",
    )
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, Mock())
    coordinator.async_set_updated_data(mock_combined_data)
    coordinator._async_publish(
        replace(
            mock_combined_data,
            device=replace(mock_combined_data.device, firmware_version="6.1"),
        )
    )
    assert registry.async_get(device.id).sw_version == "6.1"


async def test_start_and_stop_manage_the_watchdog(hass, mock_config_entry):
    api = AsyncMock()

    async def receive():
        await asyncio.Event().wait()

    api.receive.side_effect = receive
    mock_config_entry.add_to_hass(hass)
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, api)
    coordinator.async_start()
    assert coordinator._unsub_stale_check is not None
    await coordinator.async_stop()
    assert coordinator._unsub_stale_check is None
