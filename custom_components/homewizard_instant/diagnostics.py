"""Diagnostics support for HomeWizard Instant."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import HomeAssistant

from .coordinator import HomeWizardConfigEntry

TO_REDACT = {
    CONF_IP_ADDRESS,
    "gas_unique_id",
    "id",
    "serial",
    "token",
    "unique_id",
    "unique_meter_id",
    "wifi_ssid",
}


def _ensure_dict(value: Any) -> dict[str, Any]:
    """Ensure the value is returned as a dict."""
    if isinstance(value, Mapping):
        return dict(value)

    return {"value": value}


def _json_ready(value: Any) -> Any:
    """Convert library objects such as AwesomeVersion into JSON values."""
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _as_dict(value: Any) -> dict[str, Any]:
    """Return JSON-ready data as a dict."""
    ready = _json_ready(value)
    if isinstance(ready, dict):
        return ready
    return {"value": ready}


def _serialize_data(data: Any) -> dict[str, Any]:
    """Serialize coordinator data to a dict."""
    if is_dataclass(data) and not isinstance(data, type):
        return _as_dict(asdict(data))

    if hasattr(data, "model_dump"):
        return _as_dict(_ensure_dict(data.model_dump()))

    if hasattr(data, "dict"):
        return _as_dict(_ensure_dict(data.dict()))

    if hasattr(data, "__dict__"):
        return _as_dict(_ensure_dict(data.__dict__))

    return _as_dict(data)


def _without_meter_ids(data: dict[str, Any]) -> dict[str, Any]:
    """List external meters without the dict keys, which embed meter serials."""
    measurement = data.get("measurement")
    if isinstance(measurement, dict) and isinstance(
        external := measurement.get("external_devices"), dict
    ):
        measurement["external_devices"] = list(external.values())
    return data


def _describe_error(err: BaseException | None, ip_address: str | None) -> Any:
    """Keep the failure reason, which aiohttp prefixes with the host address."""
    if err is None:
        return None
    described: dict[str, Any] = {
        "type": type(err).__name__,
        "message": str(err),
        "translation_key": getattr(err, "translation_key", None),
    }
    for key, value in (getattr(err, "translation_placeholders", None) or {}).items():
        described[f"placeholder_{key}"] = str(value)
    if ip_address:
        described = {
            key: value.replace(ip_address, "**REDACTED**")
            if isinstance(value, str)
            else value
            for key, value in described.items()
        }
    return described


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HomeWizardConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    return async_redact_data(
        {
            "entry": {
                "data": async_redact_data(entry.data, TO_REDACT),
                "options": async_redact_data(entry.options, TO_REDACT),
                "title": entry.title,
                "unique_id": entry.unique_id,
            },
            "connection": {
                "last_update_success": coordinator.last_update_success,
                "last_exception": _describe_error(
                    coordinator.last_exception, entry.data.get(CONF_IP_ADDRESS)
                ),
            },
            "data": _without_meter_ids(_serialize_data(coordinator.data)),
        },
        TO_REDACT,
    )
