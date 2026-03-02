"""Tests for diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.const import CONF_IP_ADDRESS

from custom_components.homewizard_instant.diagnostics import (
    REDACTED,
    _redact_by_key_pattern,
    _serialize_data,
    async_get_config_entry_diagnostics,
)
from custom_components.homewizard_instant.coordinator import (
    HWEnergyDeviceUpdateCoordinator,
)


async def test_async_get_config_entry_diagnostics_redacts(
    hass, mock_config_entry, mock_combined_data
):
    """Test diagnostics redacts sensitive fields."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={"token": "secret", CONF_IP_ADDRESS: "5.6.7.8"}
    )

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        mock_config_entry,
        api=AsyncMock(),
        clientsession=AsyncMock(),
        ws_token=None,
    )
    coordinator.data = mock_combined_data
    mock_config_entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert diagnostics["entry"]["data"][CONF_IP_ADDRESS] == "**REDACTED**"
    assert diagnostics["entry"]["options"][CONF_IP_ADDRESS] == "**REDACTED**"
    assert diagnostics["entry"]["options"]["token"] == "**REDACTED**"
    assert diagnostics["entry"]["unique_id"] == "**REDACTED**"
    assert diagnostics["runtime"]["websocket_enabled"] is False
    assert diagnostics["runtime"]["websocket_connected"] is False
    assert diagnostics["runtime"]["poll_updates_total"] == 0
    assert diagnostics["runtime"]["websocket_updates_total"] == 0
    assert diagnostics["runtime"]["websocket_messages_total"] == 0
    assert diagnostics["runtime"]["poll_updates_per_second"] == 0.0
    assert diagnostics["runtime"]["websocket_updates_per_second"] == 0.0
    assert diagnostics["runtime"]["websocket_messages_per_second"] == 0.0
    assert diagnostics["data"]["device"]["serial"] == "**REDACTED**"


def test_serialize_data_model_dump() -> None:
    """Test _serialize_data handles model_dump."""

    class Dumpable:
        def model_dump(self) -> dict[str, str]:
            return {"key": "value"}

    assert _serialize_data(Dumpable()) == {"key": "value"}


def test_serialize_data_dict_method() -> None:
    """Test _serialize_data handles dict method."""

    class Dictable:
        def dict(self) -> dict[str, str]:
            return {"key": "value"}

    assert _serialize_data(Dictable()) == {"key": "value"}


def test_serialize_data_dunder_dict() -> None:
    """Test _serialize_data handles __dict__."""

    class HasDict:
        def __init__(self) -> None:
            self.value = "value"

    assert _serialize_data(HasDict()) == {"value": "value"}


def test_serialize_data_fallback() -> None:
    """Test _serialize_data fallback wraps value."""

    assert _serialize_data(123) == {"value": 123}


def test_redact_by_key_pattern_redacts_nested_sensitive_keys() -> None:
    """Test key-pattern redaction catches nested token and host style fields."""
    payload = {
        "safe": "value",
        "nested": {
            "api_token": "secret",
            "host_name": "p1.local",
            "items": [
                {"serial_number": "ABC123"},
                {"ok": True},
            ],
        },
    }

    redacted = _redact_by_key_pattern(payload)

    assert redacted["safe"] == "value"
    assert redacted["nested"]["api_token"] == REDACTED
    assert redacted["nested"]["host_name"] == REDACTED
    assert redacted["nested"]["items"][0]["serial_number"] == REDACTED
    assert redacted["nested"]["items"][1]["ok"] is True
