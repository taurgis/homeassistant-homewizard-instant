"""Push coordinator with bounded reconnect backoff."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homewizard_energy.errors import RequestError, UnauthorizedError
from homewizard_energy.models import CombinedModels
from mashumaro.exceptions import InvalidFieldValue, MissingField

from .const import DOMAIN, LOGGER, STALE_AFTER, SYSTEM_REFRESH_INTERVAL
from .websocket import HomeWizardWebSocket

type HomeWizardConfigEntry = ConfigEntry[HWEnergyDeviceUpdateCoordinator]
CONNECTION_ERRORS = (
    ClientError,
    RequestError,
    TimeoutError,
    ValueError,
    MissingField,
    InvalidFieldValue,
)
_STALE_CHECK_INTERVAL = timedelta(seconds=10)
# A connection must stay up this long before the reconnect delay starts at 1 s
# again; a meter that accepts and then drops every connection backs off to 60 s.
_STABLE_AFTER = 60
_MAX_DELAY = 60


def _reason(err: BaseException) -> str:
    return str(err).strip() or type(err).__name__


def _communication_error(err: BaseException) -> UpdateFailed:
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="communication_error",
        translation_placeholders={"error": _reason(err)},
    )


def _token_rejected() -> ConfigEntryAuthFailed:
    return ConfigEntryAuthFailed(
        translation_domain=DOMAIN, translation_key="token_rejected"
    )


class HWEnergyDeviceUpdateCoordinator(DataUpdateCoordinator[CombinedModels]):
    """Publish device snapshots without scheduled polling."""

    config_entry: HomeWizardConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: HomeWizardConfigEntry,
        api: HomeWizardWebSocket,
    ) -> None:
        super().__init__(hass, LOGGER, config_entry=config_entry, name=DOMAIN)
        self.api = api
        self._task: asyncio.Task[None] | None = None
        self._unsubs: list[Callable[[], None]] = []
        self._stale = False
        self._connected_at = time.monotonic()

    async def _async_update_data(self) -> CombinedModels:
        """Connect initially; subsequent refresh requests use cached push data."""
        if self._task is not None:
            if not self.last_update_success:
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="reconnecting"
                )
            return self.data
        try:
            return await self.api.connect()
        except UnauthorizedError as err:
            raise _token_rejected() from err
        except CONNECTION_ERRORS as err:
            # Home Assistant logs setup retries itself; the reason travels in
            # the translated message.
            LOGGER.debug("HomeWizard WebSocket connection failed: %s", _reason(err))
            raise _communication_error(err) from err

    def async_start(self) -> None:
        """Start a task that HA does not wait for during startup."""
        self._task = self.config_entry.async_create_background_task(
            self.hass, self._listen(), "HomeWizard Instant WebSocket"
        )
        self._unsubs = [
            async_track_time_interval(
                self.hass, self._async_check_stale, _STALE_CHECK_INTERVAL
            ),
            async_track_time_interval(
                self.hass, self._async_refresh_system, SYSTEM_REFRESH_INTERVAL
            ),
        ]

    async def _async_refresh_system(self, _now: datetime | None = None) -> None:
        """Read Wi-Fi signal and uptime, which the meter does not push."""
        await self.api.request("system")

    @callback
    def _async_check_stale(self, _now: datetime | None = None) -> None:
        """Mark entities unavailable when measurements stop arriving."""
        if self._stale or not self.last_update_success:
            return
        # A fresh connection gets the full window before the replayed
        # snapshot counts as silence.
        silent = time.monotonic() - max(self.api.last_measurement, self._connected_at)
        if silent < STALE_AFTER.total_seconds():
            return
        self._stale = True
        LOGGER.warning(
            "No HomeWizard measurement received for %d s; marking sensors unavailable",
            silent,
        )
        self._async_set_unavailable(self._stale_error())

    @staticmethod
    def _stale_error() -> UpdateFailed:
        return UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="stale_data",
            translation_placeholders={"seconds": str(int(STALE_AFTER.total_seconds()))},
        )

    @callback
    def _async_set_unavailable(self, err: Exception) -> None:
        """Like async_set_update_error, without a second ERROR log line.

        Every caller has already logged the outage with its own context.
        """
        self.last_exception = err
        if self.last_update_success:
            self.last_update_success = False
            self.async_update_listeners()

    @callback
    def _async_publish(self, data: CombinedModels) -> None:
        """Forward a snapshot, skipping pushes that change nothing."""
        if self._stale:
            if (
                time.monotonic() - self.api.last_measurement
                >= STALE_AFTER.total_seconds()
            ):
                # Still silent, including the snapshot replayed on reconnect.
                self._async_set_unavailable(self._stale_error())
                return
            self._stale = False
            LOGGER.info("HomeWizard measurements resumed")
        if self.last_update_success and data == self.data:
            return
        previous = self.data
        self.async_set_updated_data(data)
        if (
            previous is not None
            and data.device.firmware_version != previous.device.firmware_version
        ):
            self._async_update_firmware(data)

    @callback
    def _async_update_firmware(self, data: CombinedModels) -> None:
        registry = dr.async_get(self.hass)
        if device := registry.async_get_device(
            identifiers={(DOMAIN, f"{DOMAIN}_{data.device.serial}")}
        ):
            registry.async_update_device(
                device.id, sw_version=data.device.firmware_version
            )

    async def _listen(self) -> None:
        delay = 1
        connected = True
        self._connected_at = time.monotonic()
        try:
            while True:
                try:
                    if not connected:
                        data = await self.api.connect()
                        connected = True
                        self._connected_at = time.monotonic()
                        LOGGER.info("HomeWizard WebSocket reconnected")
                        self._async_publish(data)
                    self._async_publish(await self.api.receive())
                except UnauthorizedError as err:
                    LOGGER.warning("HomeWizard token was rejected: %s", err)
                    self._async_set_unavailable(_token_rejected())
                    self.config_entry.async_start_reauth(self.hass)
                    return
                except Exception as err:  # noqa: BLE001 - keep the reader alive
                    was_connected = connected
                    if (
                        connected
                        and time.monotonic() - self._connected_at >= _STABLE_AFTER
                    ):
                        delay = 1
                    connected = False
                    await self._async_connection_lost(
                        err,
                        delay,
                        was_connected=was_connected,
                        unexpected=not isinstance(err, CONNECTION_ERRORS),
                    )
                    delay = min(delay * 2, _MAX_DELAY)
        finally:
            await self.api.close()

    async def _async_connection_lost(
        self, err: Exception, delay: int, *, was_connected: bool, unexpected: bool
    ) -> None:
        """Log once per outage, mark entities unavailable, then back off."""
        if was_connected:
            if unexpected:
                LOGGER.exception(
                    "Unexpected HomeWizard WebSocket error; reconnecting in %s s",
                    delay,
                )
            else:
                LOGGER.warning(
                    "HomeWizard WebSocket unavailable (%s); reconnecting in %s s",
                    _reason(err),
                    delay,
                )
        else:
            LOGGER.debug(
                "HomeWizard WebSocket still unavailable (%s); retrying in %s s",
                _reason(err),
                delay,
                exc_info=unexpected,
            )
        self._async_set_unavailable(_communication_error(err))
        await self.api.close()
        await asyncio.sleep(delay)

    async def async_stop(self) -> None:
        """Cancel and await the reader before closing the transport."""
        while self._unsubs:
            self._unsubs.pop()()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.api.close()
