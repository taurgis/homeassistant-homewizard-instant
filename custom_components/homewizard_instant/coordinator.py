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

from .const import DOMAIN, LOGGER, STALE_AFTER
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


def _reason(err: BaseException) -> str:
    return str(err).strip() or type(err).__name__


def _communication_error(err: BaseException) -> UpdateFailed:
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="communication_error",
        translation_placeholders={"error": _reason(err)},
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
        self._unsub_stale_check: Callable[[], None] | None = None
        self._stale = False

    async def _async_update_data(self) -> CombinedModels:
        """Connect initially; subsequent refresh requests use cached push data."""
        if self._task is not None:
            if not self.last_update_success:
                raise UpdateFailed("Waiting for WebSocket reconnection")
            return self.data
        try:
            return await self.api.connect()
        except UnauthorizedError as err:
            raise ConfigEntryAuthFailed from err
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
        self._unsub_stale_check = async_track_time_interval(
            self.hass, self._async_check_stale, _STALE_CHECK_INTERVAL
        )

    @callback
    def _async_check_stale(self, _now: datetime | None = None) -> None:
        """Mark entities unavailable when measurements stop arriving."""
        if self._stale or not self.last_update_success:
            return
        silent = time.monotonic() - self.api.last_measurement
        if silent < STALE_AFTER.total_seconds():
            return
        self._stale = True
        LOGGER.warning(
            "No HomeWizard measurement received for %d s; marking sensors unavailable",
            silent,
        )
        self.async_set_update_error(
            UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="stale_data",
                translation_placeholders={
                    "seconds": str(int(STALE_AFTER.total_seconds()))
                },
            )
        )

    @callback
    def _async_publish(self, data: CombinedModels) -> None:
        """Forward a snapshot, skipping pushes that change nothing."""
        if self._stale:
            if data.measurement is self.data.measurement:
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
        try:
            while True:
                try:
                    if not connected:
                        data = await self.api.connect()
                        connected = True
                        self._stale = False
                        LOGGER.info("HomeWizard WebSocket reconnected")
                        self._async_publish(data)
                    self._async_publish(await self.api.receive())
                    delay = 1
                except UnauthorizedError as err:
                    LOGGER.warning("HomeWizard token was rejected: %s", err)
                    self.async_set_update_error(ConfigEntryAuthFailed("Token rejected"))
                    self.config_entry.async_start_reauth(self.hass)
                    return
                except CONNECTION_ERRORS as err:
                    await self._async_connection_lost(err, delay, unexpected=False)
                    connected = False
                    delay = min(delay * 2, 60)
                except Exception as err:  # noqa: BLE001 - keep the reader alive
                    await self._async_connection_lost(err, delay, unexpected=True)
                    connected = False
                    delay = min(delay * 2, 60)
        finally:
            await self.api.close()

    async def _async_connection_lost(
        self, err: Exception, delay: int, *, unexpected: bool
    ) -> None:
        """Log once per outage, mark entities unavailable, then back off."""
        if self.last_update_success:
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
        self.async_set_update_error(_communication_error(err))
        await self.api.close()
        await asyncio.sleep(delay)

    async def async_stop(self) -> None:
        """Cancel and await the reader before closing the transport."""
        if self._unsub_stale_check is not None:
            self._unsub_stale_check()
            self._unsub_stale_check = None
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.api.close()
