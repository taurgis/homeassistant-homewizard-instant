"""Tests for helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homewizard_energy.errors import DisabledError, RequestError

from homeassistant.exceptions import HomeAssistantError

from custom_components.homewizard_instant.helpers import homewizard_exception_handler


class DummyCoordinator:
    """Minimal coordinator for testing."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry


class DummyEntity:
    """Minimal entity to test decorator."""

    def __init__(self, hass, config_entry) -> None:
        self.hass = hass
        self.coordinator = DummyCoordinator(config_entry)

    @homewizard_exception_handler
    async def raise_request_error(self) -> None:
        raise RequestError("boom")

    @homewizard_exception_handler
    async def raise_disabled_error(self) -> None:
        raise DisabledError("disabled")


async def test_homewizard_exception_handler_request_error(hass, mock_config_entry):
    """Test RequestError is converted to HomeAssistantError."""
    mock_config_entry.add_to_hass(hass)

    entity = DummyEntity(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await entity.raise_request_error()

    assert err.value.translation_key == "communication_error"


async def test_homewizard_exception_handler_disabled_error(hass, mock_config_entry):
    """Test DisabledError triggers reload and raises HomeAssistantError."""
    mock_config_entry.add_to_hass(hass)

    hass.config_entries.async_reload = AsyncMock()

    entity = DummyEntity(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await entity.raise_disabled_error()

    assert err.value.translation_key == "api_disabled"
    hass.config_entries.async_reload.assert_awaited_once_with(mock_config_entry.entry_id)
