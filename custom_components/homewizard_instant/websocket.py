"""Authenticated HomeWizard API v2 push transport."""

from __future__ import annotations

import asyncio
import os
import ssl
from pathlib import Path
from typing import Any

from aiohttp import (
    ClientSession,
    ClientWebSocketResponse,
    WSMsgType,
    WSServerHandshakeError,
)
from homewizard_energy import v2
from homewizard_energy.errors import RequestError, UnauthorizedError
from homewizard_energy.models import CombinedModels, Device, Measurement, System
from homewizard_energy.v2 import cacert as cacert_mod
from homewizard_energy.v2.cacert import CACERT
from mashumaro.exceptions import InvalidFieldValue, MissingField
from yarl import URL

from .const import LOGGER

_APPLIED_DEV_CA: str | None = None
_TOPICS = ("device", "measurement", "system")


def _bounds(low: float, high: float, *names: str) -> dict[str, tuple[float, float]]:
    return {name: (low, high) for name in names}


def _phases(
    stem: str, unit: str, low: float, high: float
) -> dict[str, tuple[float, float]]:
    names = [f"{stem}_{unit}"]
    names.extend(f"{stem}_l{phase}_{unit}" for phase in (1, 2, 3))
    return _bounds(low, high, *names)


def _measurement_ranges() -> dict[str, tuple[float, float]]:
    ranges: dict[str, tuple[float, float]] = {}
    ranges.update(_phases("power", "w", -1_000_000, 1_000_000))
    ranges.update(_phases("voltage", "v", 0, 1_000))
    ranges.update(_phases("current", "a", -1_000, 1_000))
    ranges.update(_phases("apparent_power", "va", 0, 1_000_000))
    ranges.update(_phases("reactive_power", "var", -1_000_000, 1_000_000))
    ranges.update(
        _bounds(-1, 1, "power_factor", "power_factor_l1", "power_factor_l2", "power_factor_l3")
    )
    for direction in ("import", "export"):
        ranges.update(
            _bounds(
                0,
                1_000_000_000,
                f"energy_{direction}_kwh",
                *(f"energy_{direction}_t{tariff}_kwh" for tariff in range(1, 5)),
            )
        )
    ranges.update(
        _bounds(
            0,
            1_000_000_000,
            "any_power_fail_count",
            "long_power_fail_count",
            *(
                f"{kind}_l{phase}_count"
                for kind in ("voltage_sag", "voltage_swell")
                for phase in (1, 2, 3)
            ),
        )
    )
    ranges.update(
        {
            "tariff": (1, 4),
            "protocol_version": (1, 999),
            "frequency_hz": (0, 100),
            "average_power_15m_w": (-1_000_000, 1_000_000),
            "monthly_power_peak_w": (0, 1_000_000),
        }
    )
    return ranges


_MEASUREMENT_RANGES = _measurement_ranges()
_SYSTEM_RANGES = _bounds(-120, 0, "wifi_rssi_db") | _bounds(
    0, 10 * 365 * 24 * 3600, "uptime_s"
) | _bounds(0, 100, "status_led_brightness_pct")
_TOPIC_RANGES = {
    "device": {},
    "measurement": _MEASUREMENT_RANGES,
    "system": _SYSTEM_RANGES,
}


def trust_development_ca() -> None:
    """Append HOMEWIZARD_DEV_CA to the library trust store.

    python-homewizard-energy only trusts HomeWizard's appliance CA. The lab
    emulator signs device certificates with a local CA because that vendor
    key is not public. Production starts leave the variable unset.
    """
    global CACERT, _APPLIED_DEV_CA
    ca_file = os.environ.get("HOMEWIZARD_DEV_CA")
    if not ca_file or _APPLIED_DEV_CA == ca_file:
        return
    try:
        extra = Path(ca_file).read_text(encoding="utf-8").strip()
    except OSError as err:
        LOGGER.warning("HomeWizard development CA was not read: %s", err)
        return
    if "BEGIN CERTIFICATE" not in extra:
        LOGGER.warning("HomeWizard development CA file does not contain a certificate")
        return
    if extra not in cacert_mod.CACERT:
        cacert_mod.CACERT = f"{cacert_mod.CACERT.rstrip()}\n{extra}\n"
    v2.CACERT = cacert_mod.CACERT  # type: ignore[attr-defined]
    CACERT = cacert_mod.CACERT
    _APPLIED_DEV_CA = ca_file


def create_ssl_context() -> ssl.SSLContext:
    """Trust the HomeWizard CA and verify the device certificate identity."""
    trust_development_ca()
    context = ssl.create_default_context(cadata=CACERT)
    context.verify_flags = ssl.VERIFY_X509_PARTIAL_CHAIN
    context.hostname_checks_common_name = True
    return context


def _contains_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_contains_null(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_null(item) for item in value)
    return False


def _range_problem(topic: str, data: dict[str, Any]) -> str | None:
    ranges = _TOPIC_RANGES[topic]
    for key, value in data.items():
        if key == "external":
            if not isinstance(value, list):
                return "external is not a list"
            for item in value:
                if not isinstance(item, dict):
                    return "external entry is not an object"
                reading = item.get("value")
                if isinstance(reading, bool) or not isinstance(reading, (int, float)):
                    continue
                if reading < 0 or reading > 1_000_000_000_000:
                    return f"external value {reading} is out of range"
            continue
        bounds = ranges.get(key)
        if bounds is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{key} is {type(value).__name__}, expected a number"
        low, high = bounds
        if value < low or value > high:
            return f"{key}={value} outside {low}..{high}"
    return None


class HomeWizardWebSocket:
    """One connection with complete topic snapshots parsed by the official library."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        token: str,
        identifier: str,
        context: ssl.SSLContext,
    ) -> None:
        self._session = session
        self._url = URL.build(scheme="wss", host=host, path="/api/ws")
        self._token = token
        self._identifier = identifier
        self._context = context
        self._socket: ClientWebSocketResponse | None = None
        self._topics: dict[str, Any] = {}

    async def connect(self) -> CombinedModels:
        """Authenticate and wait for every initial snapshot before becoming available."""
        await self.close()
        self._topics.clear()
        try:
            async with asyncio.timeout(30):
                self._socket = await self._session.ws_connect(
                    self._url,
                    ssl=self._context,
                    server_hostname=self._identifier,
                    heartbeat=30,
                    headers={"X-Api-Version": "2"},
                )
                message = await self._next_message()
                if message.get("type") != "authorization_requested":
                    raise RequestError("Expected authorization request")
                await self._socket.send_json(
                    {"type": "authorization", "data": self._token}
                )
                message = await self._next_message()
                if message.get("type") != "authorized":
                    raise UnauthorizedError("WebSocket authorization rejected")
                for topic in _TOPICS:
                    await self._socket.send_json({"type": "subscribe", "data": topic})
                while not all(key in self._topics for key in _TOPICS):
                    self._apply(await self._next_message())
                return self._snapshot()
        except WSServerHandshakeError as err:
            await self.close()
            if err.status == 401:
                raise UnauthorizedError("WebSocket token rejected") from err
            raise
        except BaseException:
            await self.close()
            raise

    async def _next_message(self) -> dict[str, Any]:
        """Return the next protocol message, skipping junk already logged."""
        while True:
            message = await self._receive()
            if message is not None:
                return message

    async def _receive(self) -> dict[str, Any] | None:
        assert self._socket is not None
        message = await self._socket.receive()
        if message.type != WSMsgType.TEXT:
            raise RequestError("WebSocket disconnected")
        try:
            payload = message.json()
        except ValueError as err:
            LOGGER.warning("Ignoring malformed HomeWizard WebSocket message: %s", err)
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            LOGGER.warning("Ignoring HomeWizard WebSocket message with unexpected shape")
            return None
        if payload["type"] == "error":
            data = payload.get("data", {})
            error = (
                data.get("message", data.get("error", ""))
                if isinstance(data, dict)
                else ""
            )
            if error == "user:unauthorized":
                raise UnauthorizedError("WebSocket token rejected")
            raise RequestError("HomeWizard WebSocket returned an error")
        return payload

    def _apply(self, message: dict[str, Any]) -> bool:
        topic = message["type"]
        if topic not in _TOPICS:
            LOGGER.debug("Ignoring HomeWizard topic %s", topic)
            return False
        data = message.get("data")
        if not isinstance(data, dict):
            LOGGER.warning("Ignoring HomeWizard %s message with invalid payload", topic)
            return False
        if _contains_null(data):
            LOGGER.warning("Ignoring HomeWizard %s message with null fields", topic)
            return False
        if problem := _range_problem(topic, data):
            kind = "wrong type" if "expected a number" in problem else "out-of-range"
            LOGGER.warning("Ignoring %s HomeWizard %s value: %s", kind, topic, problem)
            return False
        try:
            if topic == "device":
                device = Device.from_dict(data)
                if device.id != self._identifier:
                    raise RequestError(
                        f"WebSocket device identity changed to {device.id}"
                    )
                self._topics[topic] = device
            elif topic == "measurement":
                self._topics[topic] = Measurement.from_dict(data)
            else:
                self._topics[topic] = System.from_dict(data)
        except (MissingField, InvalidFieldValue, ValueError) as err:
            LOGGER.warning("Ignoring invalid HomeWizard %s message: %s", topic, err)
            return False
        return True

    def _snapshot(self) -> CombinedModels:
        return CombinedModels(
            device=self._topics["device"],
            measurement=self._topics["measurement"],
            system=self._topics["system"],
            state=None,
        )

    async def receive(self) -> CombinedModels:
        """Read the next known topic, ignoring future topics and invalid payloads."""
        while not self._apply(await self._next_message()):
            pass
        return self._snapshot()

    async def close(self) -> None:
        """Close only our socket; the session belongs to Home Assistant."""
        if self._socket is not None:
            await self._socket.close()
            self._socket = None
