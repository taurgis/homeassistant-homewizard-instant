"""Update coordinator for HomeWizard."""

from __future__ import annotations

import asyncio
import json
import random
import ssl
from time import monotonic

from aiohttp import ClientSession, ClientWebSocketResponse, WSMessage, WSMsgType
from homewizard_energy import HomeWizardEnergy
from homewizard_energy.errors import DisabledError, RequestError, UnauthorizedError
from homewizard_energy.models import CombinedModels as DeviceResponseEntry
from homewizard_energy.v2 import HomeWizardEnergyV2
from homewizard_energy.v2.cacert import CACERT

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, LOGGER, UPDATE_INTERVAL

type HomeWizardConfigEntry = ConfigEntry[HWEnergyDeviceUpdateCoordinator]

WS_AUTH_TIMEOUT = 40
WS_RECEIVE_TIMEOUT = 90
WS_INITIAL_RETRY_DELAY = 1.0
WS_MAX_RETRY_DELAY = 60.0
WS_MIN_REFRESH_GAP_SECONDS = 0.5
WS_SUBSCRIPTIONS = ("measurement", "device", "system", "batteries")
WS_REFRESH_EVENTS = frozenset({"measurement", "device", "system", "batteries"})


class HWEnergyDeviceUpdateCoordinator(DataUpdateCoordinator[DeviceResponseEntry]):
    """Gather data for the energy device."""

    api: HomeWizardEnergy
    api_disabled: bool = False

    config_entry: HomeWizardConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: HomeWizardConfigEntry,
        api: HomeWizardEnergy,
        clientsession: ClientSession,
        ws_token: str | None,
    ) -> None:
        """Initialize update coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.api = api
        self._clientsession = clientsession
        self._ws_token = ws_token
        self._ws_task: asyncio.Task[None] | None = None
        self._ws_stop_event = asyncio.Event()
        self._ws_ssl_context: ssl.SSLContext | None = None
        self._ws_connected = False
        self._last_ws_refresh = 0.0

    @property
    def websocket_enabled(self) -> bool:
        """Return whether websocket updates can be started."""
        return isinstance(self.api, HomeWizardEnergyV2) and self._ws_token is not None

    async def async_start_websocket(self) -> None:
        """Start websocket listener for v2 devices."""
        if not self.websocket_enabled or self._ws_task is not None:
            return

        self._ws_stop_event.clear()
        self._ws_task = self.hass.async_create_task(self._async_websocket_loop())

    async def async_shutdown(self) -> None:
        """Stop websocket listener and close API resources."""
        self._ws_stop_event.set()
        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

        await self.api.close()

    async def _async_update_data(self) -> DeviceResponseEntry:
        """Fetch all device and sensor data from api."""
        data = await self._async_fetch_combined_data()
        self.data = data
        return data

    async def _async_fetch_combined_data(self) -> DeviceResponseEntry:
        """Fetch combined data and map library errors to HA errors."""
        try:
            data = await self.api.combined()

        except RequestError as ex:
            raise UpdateFailed(
                ex, translation_domain=DOMAIN, translation_key="communication_error"
            ) from ex

        except DisabledError as ex:
            self._set_api_disabled_issue()

            raise UpdateFailed(
                ex, translation_domain=DOMAIN, translation_key="api_disabled"
            ) from ex

        except UnauthorizedError as ex:
            raise ConfigEntryAuthFailed("Device authorization rejected") from ex

        self._clear_api_disabled_issue()
        return data

    def _set_api_disabled_issue(self) -> None:
        """Create disabled API issue and schedule reauth recovery."""
        if not self.api_disabled:
            self.api_disabled = True

            ir.async_create_issue(
                self.hass,
                DOMAIN,
                "local_api_disabled",
                is_fixable=True,
                severity=ir.IssueSeverity.ERROR,
                translation_key="local_api_disabled",
                data={"entry_id": self.config_entry.entry_id},
            )

            # Do not reload while first refresh is still in progress.
            if self.data is not None:
                self.hass.config_entries.async_schedule_reload(
                    self.config_entry.entry_id
                )

    def _clear_api_disabled_issue(self) -> None:
        """Clear disabled API issue after successful update."""
        self.api_disabled = False
        ir.async_delete_issue(self.hass, DOMAIN, "local_api_disabled")

    async def _async_websocket_loop(self) -> None:
        """Maintain websocket connection and trigger fresh coordinator updates."""
        retry_delay = WS_INITIAL_RETRY_DELAY

        while not self._ws_stop_event.is_set():
            try:
                await self._async_websocket_session()
                retry_delay = WS_INITIAL_RETRY_DELAY
            except ConfigEntryAuthFailed:
                self._ws_connected = False
                self.config_entry.async_start_reauth(self.hass)
                return
            except asyncio.CancelledError:
                raise
            except Exception as err:  # pylint: disable=broad-except
                LOGGER.debug("HomeWizard websocket disconnected: %s", err)
            finally:
                self._ws_connected = False

            if self._ws_stop_event.is_set():
                break

            await asyncio.sleep(retry_delay + random.random())
            retry_delay = min(retry_delay * 2, WS_MAX_RETRY_DELAY)

    async def _async_websocket_session(self) -> None:
        """Run a single websocket session until it closes."""
        assert self._ws_token

        websocket_url = f"wss://{self.api.host}/api/ws"
        ssl_context = await self._async_get_ws_ssl_context()

        async with self._clientsession.ws_connect(
            websocket_url,
            ssl=ssl_context,
            heartbeat=30,
        ) as websocket:
            await self._async_authorize_websocket(websocket)

            for topic in WS_SUBSCRIPTIONS:
                await websocket.send_json({"type": "subscribe", "data": topic})

            self._ws_connected = True
            await self._async_refresh_from_websocket("connected")

            while not self._ws_stop_event.is_set():
                payload = await self._async_receive_ws_json(websocket, WS_RECEIVE_TIMEOUT)
                if payload is None:
                    return

                event_type = payload.get("type")
                if not isinstance(event_type, str):
                    continue

                if event_type == "error":
                    data = payload.get("data")
                    message = ""
                    if isinstance(data, dict):
                        message_value = data.get("message", "")
                        message = str(message_value)
                    if "unauthorized" in message.lower():
                        raise ConfigEntryAuthFailed("WebSocket authorization rejected")
                    LOGGER.debug("HomeWizard websocket error: %s", payload)
                    continue

                if event_type in WS_REFRESH_EVENTS:
                    await self._async_refresh_from_websocket(event_type)

    async def _async_authorize_websocket(
        self, websocket: ClientWebSocketResponse
    ) -> None:
        """Perform websocket authorization handshake."""
        assert self._ws_token

        while True:
            payload = await self._async_receive_ws_json(websocket, WS_AUTH_TIMEOUT)
            if payload is None:
                raise UpdateFailed("WebSocket closed before authorization")

            event_type = payload.get("type")
            if event_type == "authorization_requested":
                await websocket.send_json(
                    {"type": "authorization", "data": self._ws_token}
                )
                continue

            if event_type == "authorized":
                return

            if event_type == "error":
                data = payload.get("data")
                message = ""
                if isinstance(data, dict):
                    message_value = data.get("message", "")
                    message = str(message_value)
                if "unauthorized" in message.lower():
                    raise ConfigEntryAuthFailed("WebSocket authorization rejected")
                raise UpdateFailed(f"WebSocket authorization failed: {message}")

    async def _async_receive_ws_json(
        self, websocket: ClientWebSocketResponse, timeout: int
    ) -> dict[str, object] | None:
        """Receive a websocket JSON frame and decode it."""
        message: WSMessage = await websocket.receive(timeout=timeout)
        if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
            return None
        if message.type != WSMsgType.TEXT:
            return {}

        try:
            parsed = json.loads(message.data)
        except json.JSONDecodeError:
            LOGGER.debug("Ignoring non-JSON websocket payload")
            return {}

        if isinstance(parsed, dict):
            return parsed

        return {}

    async def _async_refresh_from_websocket(self, event_type: str) -> None:
        """Fetch fresh data after websocket events and push into coordinator."""
        now = monotonic()
        if now - self._last_ws_refresh < WS_MIN_REFRESH_GAP_SECONDS:
            return

        self._last_ws_refresh = now
        try:
            data = await self._async_fetch_combined_data()
        except UpdateFailed as err:
            LOGGER.debug("WebSocket-triggered refresh failed on %s: %s", event_type, err)
            return

        self.async_set_updated_data(data)

    async def _async_get_ws_ssl_context(self) -> ssl.SSLContext:
        """Build and cache SSL context used for websocket v2 endpoint."""
        if self._ws_ssl_context is not None:
            return self._ws_ssl_context

        def _build_context() -> ssl.SSLContext:
            context = ssl.create_default_context(cadata=CACERT)
            context.verify_flags = ssl.VERIFY_X509_PARTIAL_CHAIN  # pylint: disable=no-member
            context.check_hostname = False
            context.verify_mode = ssl.CERT_REQUIRED
            return context

        self._ws_ssl_context = await self.hass.async_add_executor_job(_build_context)
        return self._ws_ssl_context
