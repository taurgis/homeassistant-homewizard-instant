"""Push coordinator with bounded reconnect backoff."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homewizard_energy.errors import RequestError, UnauthorizedError
from homewizard_energy.models import CombinedModels
from mashumaro.exceptions import InvalidFieldValue, MissingField

from .const import DOMAIN, LOGGER
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
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="communication_error"
            ) from err

    def async_start(self) -> None:
        """Start a task that HA does not wait for during startup."""
        self._task = self.config_entry.async_create_background_task(
            self.hass, self._listen(), "HomeWizard Instant WebSocket"
        )

    async def _listen(self) -> None:
        delay = 1
        connected = True
        try:
            while True:
                try:
                    if not connected:
                        self.async_set_updated_data(await self.api.connect())
                        connected = True
                    self.async_set_updated_data(await self.api.receive())
                    delay = 1
                except UnauthorizedError:
                    self.async_set_update_error(ConfigEntryAuthFailed("Token rejected"))
                    self.config_entry.async_start_reauth(self.hass)
                    return
                except CONNECTION_ERRORS:
                    self.async_set_update_error(
                        UpdateFailed(
                            translation_domain=DOMAIN,
                            translation_key="communication_error",
                        )
                    )
                    await self.api.close()
                    connected = False
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)
        finally:
            await self.api.close()

    async def async_stop(self) -> None:
        """Cancel and await the reader before closing the transport."""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.api.close()
