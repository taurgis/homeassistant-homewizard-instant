"""Tests for the coordinator."""

from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace
from time import monotonic
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import ClientWSTimeout, WSMsgType
from homewizard_energy import HomeWizardEnergyV1
from homewizard_energy.errors import DisabledError, RequestError, UnsupportedError
from homewizard_energy.v2 import HomeWizardEnergyV2

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.issue_registry import IssueSeverity
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_instant.coordinator import (
    HWEnergyDeviceUpdateCoordinator,
    WS_CONNECT_TIMEOUT,
    WS_SUBSCRIPTIONS,
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
    delete_issue.assert_not_called()


async def test_coordinator_update_success_clears_disabled_issue(
    hass, mock_config_entry, mock_combined_data
):
    """Test coordinator clears disabled API issue after successful recovery."""
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
    coordinator.api_disabled = True

    with patch(
        "custom_components.homewizard_instant.coordinator.ir.async_delete_issue"
    ) as delete_issue:
        data = await coordinator._async_update_data()

    assert data == mock_combined_data
    assert coordinator.api_disabled is False
    delete_issue.assert_called_once_with(
        hass,
        DOMAIN,
        f"local_api_disabled_{mock_config_entry.entry_id}",
    )


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
    assert create_issue.call_args.args[:3] == (
        hass,
        DOMAIN,
        f"local_api_disabled_{mock_config_entry.entry_id}",
    )
    assert create_issue.call_args.kwargs["severity"] == IssueSeverity.ERROR
    hass.config_entries.async_schedule_reload.assert_called_once_with(
        mock_config_entry.entry_id
    )


async def test_api_disabled_issue_is_scoped_per_entry(
    hass, mock_config_entry, mock_combined_data
):
    """Test API-disabled issue IDs are isolated per config entry."""
    mock_config_entry.add_to_hass(hass)

    second_entry = MockConfigEntry(
        domain=DOMAIN,
        data={**mock_config_entry.data},
        unique_id=f"{DOMAIN}_P1_SERIAL456",
        title="P1 Meter 2",
    )
    second_entry.add_to_hass(hass)

    coordinator_1 = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )
    coordinator_2 = HWEnergyDeviceUpdateCoordinator(
        hass,
        second_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )
    coordinator_1.data = mock_combined_data
    coordinator_2.api_disabled = True

    with patch(
        "custom_components.homewizard_instant.coordinator.ir.async_create_issue"
    ) as create_issue, patch(
        "custom_components.homewizard_instant.coordinator.ir.async_delete_issue"
    ) as delete_issue:
        coordinator_1._set_api_disabled_issue()
        coordinator_2._clear_api_disabled_issue()

    assert create_issue.call_args.args[:3] == (
        hass,
        DOMAIN,
        f"local_api_disabled_{mock_config_entry.entry_id}",
    )
    delete_issue.assert_called_once_with(
        hass,
        DOMAIN,
        f"local_api_disabled_{second_entry.entry_id}",
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


async def test_coordinator_v1_fetches_endpoints_sequentially(
    hass, mock_config_entry, mock_combined_data
):
    """Test v1 updates fetch endpoints one-by-one to limit socket pressure."""

    device_result = mock_combined_data.device
    measurement_result = SimpleNamespace(wifi_ssid=None, wifi_strength=None)
    system_result = mock_combined_data.system

    class _FakeV1Api(HomeWizardEnergyV1):
        def __init__(self) -> None:
            super().__init__("1.2.3.4", clientsession=AsyncMock())
            self.in_flight = 0
            self.max_in_flight = 0
            self.call_order: list[str] = []

        async def _track(self, name: str, result=object(), *, unsupported=False):
            self.call_order.append(name)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0)
            self.in_flight -= 1
            if unsupported:
                raise UnsupportedError(f"{name} unsupported")
            return result

        async def device(self, reset_cache: bool = False):
            return await self._track("device", device_result)

        async def measurement(self):
            return await self._track("measurement", measurement_result)

        async def system(
            self,
            cloud_enabled: bool | None = None,
            status_led_brightness_pct: int | None = None,
            api_v1_enabled: bool | None = None,
        ):
            return await self._track("system", system_result)

        async def state(
            self,
            power_on: bool | None = None,
            switch_lock: bool | None = None,
            brightness: int | None = None,
        ):
            return await self._track("state", unsupported=True)

        async def batteries(self, mode=None):
            return await self._track("batteries", unsupported=True)

    mock_config_entry.add_to_hass(hass)

    api = _FakeV1Api()
    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token=None,
    )

    data = await coordinator._async_update_data()

    assert api.max_in_flight == 1
    assert api.call_order == [
        "device",
        "measurement",
        "system",
        "state",
        "batteries",
    ]
    assert data.device is device_result
    assert data.measurement is measurement_result
    assert data.system is system_result
    assert data.state is None
    assert data.batteries is None


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


async def test_async_start_websocket_creates_background_task(hass, mock_config_entry):
    """Test websocket startup schedules a background task for v2/token entries."""
    mock_config_entry.add_to_hass(hass)

    api = AsyncMock(spec=HomeWizardEnergyV2)
    task = AsyncMock()

    def _create_task(*args, **kwargs):
        coro = args[1]
        coro.close()
        return task

    mock_config_entry.async_create_background_task = Mock(side_effect=_create_task)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token="token123",
    )

    await coordinator.async_start_websocket()

    assert coordinator._ws_task is task
    mock_config_entry.async_create_background_task.assert_called_once()


async def test_async_start_websocket_skips_when_disabled(hass, mock_config_entry):
    """Test websocket startup is skipped for non-v2 entries."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )
    mock_config_entry.async_create_background_task = Mock()

    await coordinator.async_start_websocket()

    assert coordinator._ws_task is None
    mock_config_entry.async_create_background_task.assert_not_called()


async def test_async_shutdown_cancels_task(hass, mock_config_entry):
    """Test shutdown cancels websocket task and closes API."""
    mock_config_entry.add_to_hass(hass)

    api = AsyncMock()
    api.close = AsyncMock()

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    coordinator._ws_task = asyncio.create_task(asyncio.sleep(60))

    await coordinator.async_shutdown()

    assert coordinator._ws_task is None
    api.close.assert_awaited_once()


async def test_websocket_realtime_active_requires_recent_activity(
    hass, mock_config_entry
):
    """Test websocket realtime gate handles no-activity and stale-activity cases."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )

    coordinator._ws_connected = True
    coordinator._last_ws_event = 0
    coordinator._last_ws_refresh = 0
    assert coordinator._websocket_is_realtime_active() is False

    coordinator._last_ws_refresh = monotonic() - 999
    assert coordinator._websocket_is_realtime_active() is False


async def test_set_api_disabled_issue_does_not_reload_without_data(
    hass, mock_config_entry
):
    """Test first-refresh API disabled issue does not schedule an immediate reload."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )
    coordinator.data = None
    hass.config_entries.async_schedule_reload = Mock()

    with patch(
        "custom_components.homewizard_instant.coordinator.ir.async_create_issue"
    ) as create_issue:
        coordinator._set_api_disabled_issue()

    create_issue.assert_called_once()
    hass.config_entries.async_schedule_reload.assert_not_called()


async def test_set_api_disabled_issue_is_idempotent(hass, mock_config_entry):
    """Test API disabled issue is not recreated once already active."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )
    coordinator.api_disabled = True

    with patch(
        "custom_components.homewizard_instant.coordinator.ir.async_create_issue"
    ) as create_issue:
        coordinator._set_api_disabled_issue()

    create_issue.assert_not_called()


async def test_websocket_loop_starts_reauth_on_auth_failure(hass, mock_config_entry):
    """Test websocket loop starts reauth and exits on auth failures."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    coordinator._async_websocket_session = AsyncMock(side_effect=ConfigEntryAuthFailed)
    mock_config_entry.async_start_reauth = Mock()

    await coordinator._async_websocket_loop()

    mock_config_entry.async_start_reauth.assert_called_once_with(hass)


async def test_websocket_loop_retries_after_updatefailed(hass, mock_config_entry):
    """Test websocket loop applies backoff sleep after recoverable failures."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    coordinator._async_websocket_session = AsyncMock(side_effect=UpdateFailed("boom"))

    async def _sleep(_delay: float) -> None:
        coordinator._ws_stop_event.set()

    with (
        patch("custom_components.homewizard_instant.coordinator.random.random", return_value=0),
        patch("custom_components.homewizard_instant.coordinator.asyncio.sleep", new=AsyncMock(side_effect=_sleep)) as sleep,
    ):
        await coordinator._async_websocket_loop()

    sleep.assert_awaited_once()


async def test_websocket_loop_clears_task_reference_on_exit(hass, mock_config_entry):
    """Test websocket loop clears internal task reference when it exits."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )

    async def _session_once() -> None:
        coordinator._ws_stop_event.set()

    coordinator._async_websocket_session = AsyncMock(side_effect=_session_once)

    ws_task = asyncio.create_task(coordinator._async_websocket_loop())
    coordinator._ws_task = ws_task

    await ws_task

    assert coordinator._ws_task is None


async def test_websocket_loop_logs_unexpected_exception(hass, mock_config_entry):
    """Test websocket loop logs and re-raises unexpected exceptions."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    coordinator._async_websocket_session = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch("custom_components.homewizard_instant.coordinator.LOGGER.exception") as log_exception,
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await coordinator._async_websocket_loop()

    log_exception.assert_called_once()


async def test_ws_ssl_context_uses_insecure_mode_when_enabled(
    hass, mock_config_entry
):
    """Test websocket SSL context disables verification in insecure dev mode."""
    mock_config_entry.add_to_hass(hass)

    api = AsyncMock()
    api.host = "dummy-p1.local"

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api,
        clientsession=AsyncMock(),
        ws_token="token123",
    )

    with patch(
        "custom_components.homewizard_instant.coordinator.allow_insecure_v2_for_host",
        return_value=True,
    ):
        context = await coordinator._async_get_ws_ssl_context()

    assert context.check_hostname is False


async def test_websocket_session_requires_token(hass, mock_config_entry):
    """Test websocket session raises when called without a token."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_websocket_session()


async def test_websocket_session_processes_events(hass, mock_config_entry):
    """Test websocket session subscribes and handles payload event types."""
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

    websocket = AsyncMock()

    coordinator._clientsession.ws_connect = AsyncMock(return_value=websocket)
    coordinator._async_get_ws_ssl_context = AsyncMock(
        return_value=SimpleNamespace(check_hostname=True)
    )
    coordinator._async_authorize_websocket = AsyncMock()
    coordinator._async_refresh_from_websocket = AsyncMock()
    coordinator._async_receive_ws_json = AsyncMock(
        side_effect=[
            {},
            {"type": "error", "data": {"message": "boom"}},
            {"type": "measurement"},
            None,
        ]
    )

    await coordinator._async_websocket_session()

    assert websocket.send_json.await_count == len(WS_SUBSCRIPTIONS)
    coordinator._async_refresh_from_websocket.assert_any_await("connected")
    coordinator._async_refresh_from_websocket.assert_any_await("measurement")
    ws_connect_kwargs = coordinator._clientsession.ws_connect.call_args.kwargs
    assert ws_connect_kwargs["timeout"] == ClientWSTimeout(ws_close=10)


async def test_websocket_session_raises_on_unauthorized_event(
    hass, mock_config_entry
):
    """Test websocket session raises auth failure on unauthorized error payloads."""
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

    websocket = AsyncMock()

    coordinator._clientsession.ws_connect = AsyncMock(return_value=websocket)
    coordinator._async_get_ws_ssl_context = AsyncMock(
        return_value=SimpleNamespace(check_hostname=False)
    )
    coordinator._async_authorize_websocket = AsyncMock()
    coordinator._async_refresh_from_websocket = AsyncMock()
    coordinator._async_receive_ws_json = AsyncMock(
        side_effect=[{"type": "error", "data": {"message": "Unauthorized"}}]
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_websocket_session()


async def test_websocket_session_maps_connect_timeout_to_update_failed(
    hass, mock_config_entry
):
    """Test websocket connect timeout is mapped to UpdateFailed."""
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

    coordinator._clientsession.ws_connect = AsyncMock(side_effect=TimeoutError)
    coordinator._async_get_ws_ssl_context = AsyncMock(
        return_value=SimpleNamespace(check_hostname=True)
    )

    with pytest.raises(UpdateFailed, match="WebSocket connect timeout"):
        await coordinator._async_websocket_session()

    ws_connect_kwargs = coordinator._clientsession.ws_connect.call_args.kwargs
    assert ws_connect_kwargs["timeout"] == ClientWSTimeout(ws_close=10)
    assert WS_CONNECT_TIMEOUT > 0


async def test_websocket_authorize_requires_token(hass, mock_config_entry):
    """Test websocket authorization raises when token is missing."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_authorize_websocket(AsyncMock())


async def test_websocket_authorize_closed_before_auth_raises(hass, mock_config_entry):
    """Test websocket authorization handles closed socket before auth."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    coordinator._async_receive_ws_json = AsyncMock(return_value=None)

    with pytest.raises(UpdateFailed):
        await coordinator._async_authorize_websocket(AsyncMock())


async def test_websocket_authorize_success_after_request(hass, mock_config_entry):
    """Test websocket authorization sends token and succeeds on authorized event."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    coordinator._async_receive_ws_json = AsyncMock(
        side_effect=[
            {"type": "authorization_requested"},
            {"type": "authorized"},
        ]
    )
    websocket = AsyncMock()

    await coordinator._async_authorize_websocket(websocket)

    websocket.send_json.assert_awaited_once_with(
        {"type": "authorization", "data": "token123"}
    )


async def test_websocket_authorize_non_auth_error_raises(hass, mock_config_entry):
    """Test websocket authorization maps non-auth errors to UpdateFailed."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    coordinator._async_receive_ws_json = AsyncMock(
        return_value={"type": "error", "data": {"message": "broken"}}
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_authorize_websocket(AsyncMock())


def test_ws_error_message_handles_non_dict_data() -> None:
    """Test websocket error extraction returns empty string for invalid payloads."""
    assert HWEnergyDeviceUpdateCoordinator._ws_error_message({"data": "oops"}) == ""


async def test_receive_ws_json_handles_close_and_non_text(hass, mock_config_entry):
    """Test websocket JSON reader handles close and non-text message frames."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    websocket = AsyncMock()

    websocket.receive = AsyncMock(return_value=SimpleNamespace(type=WSMsgType.CLOSED))
    assert await coordinator._async_receive_ws_json(websocket, timeout=1) is None

    websocket.receive = AsyncMock(return_value=SimpleNamespace(type=WSMsgType.BINARY))
    assert await coordinator._async_receive_ws_json(websocket, timeout=1) == {}


async def test_receive_ws_json_handles_timeout(hass, mock_config_entry):
    """Test websocket JSON reader treats receive timeout as closed connection."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    websocket = AsyncMock()

    websocket.receive = AsyncMock(side_effect=TimeoutError)
    assert await coordinator._async_receive_ws_json(websocket, timeout=1) is None


async def test_receive_ws_json_handles_invalid_or_non_object_json(
    hass, mock_config_entry
):
    """Test websocket JSON reader handles invalid JSON and non-object payloads."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    websocket = AsyncMock()

    websocket.receive = AsyncMock(
        return_value=SimpleNamespace(type=WSMsgType.TEXT, data="{not-json")
    )
    assert await coordinator._async_receive_ws_json(websocket, timeout=1) == {}

    websocket.receive = AsyncMock(
        return_value=SimpleNamespace(type=WSMsgType.TEXT, data='["x"]')
    )
    assert await coordinator._async_receive_ws_json(websocket, timeout=1) == {}


async def test_receive_ws_json_returns_dict_and_updates_metrics(
    hass, mock_config_entry
):
    """Test websocket JSON reader returns dict payload and updates message counters."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    websocket = AsyncMock()
    websocket.receive = AsyncMock(
        return_value=SimpleNamespace(type=WSMsgType.TEXT, data='{"type":"measurement"}')
    )

    payload = await coordinator._async_receive_ws_json(websocket, timeout=1)

    assert payload == {"type": "measurement"}
    assert coordinator._ws_messages_total == 1


async def test_websocket_refresh_update_failed_clears_pending(hass, mock_config_entry):
    """Test websocket refresh clears pending flag after update failures."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    coordinator._ws_refresh_pending = True
    coordinator._async_fetch_combined_data_serialized = AsyncMock(
        side_effect=UpdateFailed("boom")
    )

    await coordinator._async_refresh_from_websocket("measurement")

    assert coordinator._ws_refresh_pending is False


async def test_ws_ssl_context_cached_between_calls(hass, mock_config_entry):
    """Test websocket SSL context is cached and executor is used once."""
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

    with patch.object(
        hass,
        "async_add_executor_job",
        new=AsyncMock(side_effect=lambda fn: fn()),
    ) as add_executor_job:
        context_1 = await coordinator._async_get_ws_ssl_context()
        context_2 = await coordinator._async_get_ws_ssl_context()

    assert context_1 is context_2
    add_executor_job.assert_awaited_once()


async def test_diagnostics_summary_reports_ages_and_trims_metrics(
    hass, mock_config_entry
):
    """Test diagnostics summary includes age fields and trims old metric windows."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        AsyncMock(),
        clientsession=AsyncMock(),
        ws_token="token123",
    )
    now = monotonic()
    coordinator._last_ws_event = now - 1
    coordinator._last_ws_refresh = now - 2
    coordinator._poll_update_timestamps = deque([now - 120, now - 1])
    coordinator._ws_update_timestamps = deque([now - 120, now - 2])
    coordinator._ws_message_timestamps = deque([now - 120, now - 3])

    summary = coordinator.diagnostics_summary()

    assert summary["websocket_last_event_seconds_ago"] is not None
    assert summary["websocket_last_refresh_seconds_ago"] is not None
    assert len(coordinator._poll_update_timestamps) == 1
    assert len(coordinator._ws_update_timestamps) == 1
    assert len(coordinator._ws_message_timestamps) == 1
