"""Diagnostics support for P1 Monitor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any, cast

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import HomeAssistant

from .coordinator import HomeWizardConfigEntry

TO_REDACT = {
    CONF_IP_ADDRESS,
    "gas_unique_id",
    "serial",
    "token",
    "unique_id",
    "unique_meter_id",
    "wifi_ssid",
}

REDACTED = "**REDACTED**"
SENSITIVE_KEYWORDS = (
    "token",
    "serial",
    "unique_id",
    "ssid",
    "ip",
    "host",
    "mac",
)


def _ensure_dict(value: Any) -> dict[str, Any]:
    """Ensure the value is returned as a dict."""
    if isinstance(value, Mapping):
        return dict(value)

    return {"value": value}


def _serialize_data(data: Any) -> dict[str, Any]:
    """Serialize coordinator data to a dict."""
    if is_dataclass(data) and not isinstance(data, type):
        return _ensure_dict(asdict(data))

    if hasattr(data, "model_dump"):
        return _ensure_dict(data.model_dump())

    if hasattr(data, "dict"):
        return _ensure_dict(data.dict())

    if hasattr(data, "__dict__"):
        return _ensure_dict(data.__dict__)

    return {"value": data}


def _redact_by_key_pattern(value: Any) -> Any:
    """Recursively redact values where the key looks sensitive."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, nested_value in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if any(keyword in lowered for keyword in SENSITIVE_KEYWORDS):
                redacted[key] = REDACTED
            else:
                redacted[key] = _redact_by_key_pattern(nested_value)
        return redacted

    if isinstance(value, list):
        return [_redact_by_key_pattern(item) for item in value]

    if isinstance(value, tuple):
        return tuple(_redact_by_key_pattern(item) for item in value)

    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HomeWizardConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    diagnostics = async_redact_data(
        {
            "entry": {
                "data": async_redact_data(entry.data, TO_REDACT),
                "options": async_redact_data(entry.options, TO_REDACT),
                "title": entry.title,
                "unique_id": entry.unique_id,
            },
            "runtime": coordinator.diagnostics_summary(),
            "data": _serialize_data(data),
        },
        TO_REDACT,
    )

    # Keep the explicit redaction list and also protect future fields
    # that may include sensitive tokens/IDs in nested payloads.
    return cast(dict[str, Any], _redact_by_key_pattern(diagnostics))
