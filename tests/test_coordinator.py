"""Tests for the coordinator."""

from __future__ import annotations

import asyncio
from time import monotonic
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homewizard_energy.errors import DisabledError, RequestError

from homeassistant.exceptions import ConfigEntryAuthFailed
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

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token=None,
    )

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

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token=None,
    )

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

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token=None,
    )
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


async def test_coordinator_unauthorized_error_raises_auth_failed(
    hass, mock_config_entry
):
    """Test coordinator maps UnauthorizedError to ConfigEntryAuthFailed."""
    from homewizard_energy.errors import UnauthorizedError

    mock_config_entry.add_to_hass(hass)

    api = AsyncMock()
    api.combined = AsyncMock(side_effect=UnauthorizedError("unauthorized"))

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token=None,
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_coordinator_skips_poll_fetch_when_websocket_is_healthy(
    hass, mock_config_entry, mock_combined_data
):
    """Test poll refresh is skipped while websocket updates are active."""
    mock_config_entry.add_to_hass(hass)

    api = AsyncMock()
    api.combined = AsyncMock(return_value=mock_combined_data)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    coordinator.data = mock_combined_data
    coordinator._ws_connected = True
    coordinator._last_ws_refresh = monotonic()

    data = await coordinator._async_update_data()

    assert data == mock_combined_data
    api.combined.assert_not_awaited()


async def test_websocket_refresh_coalesces_overlapping_events(
    hass, mock_config_entry, mock_combined_data
):
    """Test overlapping websocket events coalesce to one extra refresh."""
    mock_config_entry.add_to_hass(hass)

    api = AsyncMock()
    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token="token123",
    )

    started = asyncio.Event()
    release_first = asyncio.Event()
    call_count = 0

    async def fake_fetch():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            started.set()
            await release_first.wait()
        return mock_combined_data

    coordinator._async_fetch_combined_data = AsyncMock(side_effect=fake_fetch)
    coordinator.async_set_updated_data = Mock()

    task_first = asyncio.create_task(
        coordinator._async_refresh_from_websocket("measurement")
    )
    await started.wait()

    task_second = asyncio.create_task(
        coordinator._async_refresh_from_websocket("measurement")
    )
    await asyncio.sleep(0)
    assert coordinator._ws_refresh_pending is True

    release_first.set()
    await asyncio.gather(task_first, task_second)

    assert call_count == 2
    assert coordinator.async_set_updated_data.call_count == 2


async def test_ws_ssl_context_uses_hostname_verification_for_dns_host(
    hass, mock_config_entry
):
    """Test websocket SSL context checks hostname when using DNS hostnames."""
    mock_config_entry.add_to_hass(hass)

    api = AsyncMock()
    api.host = "homewizard.local"

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token="token123",
    )

    context = await coordinator._async_get_ws_ssl_context()

    assert context.check_hostname is True


async def test_ws_ssl_context_disables_hostname_verification_for_ip_host(
    hass, mock_config_entry
):
    """Test websocket SSL context disables hostname checks for IP endpoints."""
    mock_config_entry.add_to_hass(hass)

    api = AsyncMock()
    api.host = "1.2.3.4"

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token="token123",
    )

    context = await coordinator._async_get_ws_ssl_context()

    assert context.check_hostname is False


async def test_websocket_refresh_respects_min_refresh_gap(
    hass, mock_config_entry, mock_combined_data
):
    """Test websocket refresh is skipped when called inside the refresh gap."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    coordinator._async_fetch_combined_data = AsyncMock(return_value=mock_combined_data)
    coordinator.async_set_updated_data = Mock()

    await coordinator._async_refresh_from_websocket("measurement")
    await coordinator._async_refresh_from_websocket("measurement")

    assert coordinator._async_fetch_combined_data.await_count == 1
    assert coordinator.async_set_updated_data.call_count == 1


async def test_poll_and_websocket_fetches_do_not_overlap(
    hass, mock_config_entry, mock_combined_data
):
    """Test poll and websocket refreshes serialize shared API fetches."""
    mock_config_entry.add_to_hass(hass)

    api = AsyncMock()
    started = asyncio.Event()
    release = asyncio.Event()
    in_flight = 0
    max_in_flight = 0

    async def fake_combined():
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        if in_flight == 1:
            started.set()
            await release.wait()
        in_flight -= 1
        return mock_combined_data

    api.combined = AsyncMock(side_effect=fake_combined)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token="token123",
    )

    poll_task = asyncio.create_task(coordinator._async_update_data())
    await started.wait()

    ws_task = asyncio.create_task(
        coordinator._async_refresh_from_websocket("measurement")
    )
    await asyncio.sleep(0)

    release.set()
    await asyncio.gather(poll_task, ws_task)

    assert api.combined.await_count == 2
    assert max_in_flight == 1


async def test_websocket_authorize_unauthorized_error_raises_auth_failed(
    hass, mock_config_entry
):
    """Test unauthorized websocket auth payload triggers reauth path."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    coordinator._async_receive_ws_json = AsyncMock(
        return_value={"type": "error", "data": {"message": "unauthorized"}}
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_authorize_websocket(AsyncMock())
