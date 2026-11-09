"""Authenticated HomeWizard API v2 push transport."""

from __future__ import annotations

import asyncio
import os
import ssl
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import (
    ClientError,
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

from .const import LOGGER, SYSTEM_REFRESH_INTERVAL

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
        _bounds(
            -1,
            1,
            "power_factor",
            "power_factor_l1",
            "power_factor_l2",
            "power_factor_l3",
        )
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
            "protocol_version": (1, 999),
            "frequency_hz": (0, 100),
            "average_power_15m_w": (-1_000_000, 1_000_000),
            "monthly_power_peak_w": (0, 1_000_000),
        }
    )
    return ranges


_MEASUREMENT_RANGES = _measurement_ranges()
# RSSI of -127 is a normal "no signal" reading. Tariff is intentionally absent:
# python-homewizard-energy already nulls values outside 1..4 and keeps the rest
# of the snapshot. Rejecting it here would freeze power for those meters.
_SYSTEM_RANGES = (
    _bounds(-200, 0, "wifi_rssi_db")
    | _bounds(0, 10 * 365 * 24 * 3600, "uptime_s")
    | _bounds(0, 100, "status_led_brightness_pct")
)
_TOPIC_RANGES = {
    "device": {},
    "measurement": _MEASUREMENT_RANGES,
    "system": _SYSTEM_RANGES,
}
_WARNING_INTERVAL = 60.0
_INVALID_LIMIT = 30
# aiohttp pings only after this many seconds without a received frame and gives
# up when the pong is half that late. A half-open link (meter rebooted, Wi-Fi
# gone) is then closed within about 22 s instead of 45 s, well inside the 60 s
# a grid controller still trusts a reading. A DSMR 5 meter sends every second,
# so pings go out only when frames stop; 15 s also stays clear of the 10 s
# cadence of older meters.
_HEARTBEAT = 15.0
# A meter that still answers pings but sends nothing, not even the system reply
# requested every SYSTEM_REFRESH_INTERVAL, is wedged. A silent P1 port alone
# never reaches this: the system reply keeps arriving.
_IDLE_TIMEOUT = SYSTEM_REFRESH_INTERVAL.total_seconds() + 30


def _certificate_bundle(text: str) -> str | None:
    """Return PEM certificates that OpenSSL can parse."""
    marker = "-----END CERTIFICATE-----"
    certificates: list[str] = []
    rest = text
    while True:
        start = rest.find("-----BEGIN CERTIFICATE-----")
        if start < 0:
            break
        end = rest.find(marker, start)
        if end < 0:
            return None
        end += len(marker)
        pem = rest[start:end]
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_verify_locations(cadata=pem)
        except ssl.SSLError:
            return None
        certificates.append(pem)
        rest = rest[end:]
    if not certificates:
        return None
    return "\n".join(certificates) + "\n"


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
        extra = _certificate_bundle(Path(ca_file).read_text(encoding="utf-8"))
    except OSError as err:
        LOGGER.warning("HomeWizard development CA was not read: %s", err)
        return
    if extra is None:
        LOGGER.warning("HomeWizard development CA file does not contain a certificate")
        return
    if extra not in cacert_mod.CACERT:
        cacert_mod.CACERT = f"{cacert_mod.CACERT.rstrip()}\n{extra}"
    v2.CACERT = cacert_mod.CACERT  # type: ignore[attr-defined]
    CACERT = cacert_mod.CACERT
    _APPLIED_DEV_CA = ca_file
    LOGGER.warning(
        "HomeWizard development CA at %s is active. Certificates it signs are "
        "trusted for this process, including by other HomeWizard clients. Unset "
        "HOMEWIZARD_DEV_CA outside the lab",
        ca_file,
    )


def create_ssl_context() -> ssl.SSLContext:
    """Trust the HomeWizard appliance CA and leave the hostname unchecked.

    Identity is the serial carried by the device topic. Real P1 certificates
    use a common name of ``appliance/p1dongle/<serial>`` with no
    subjectAltName, and some meters present a name that does not verify as
    that hostname. The official integration checks the CA chain, then the
    serial from the device.
    """
    trust_development_ca()
    context = ssl.create_default_context(cadata=CACERT)
    context.verify_flags = ssl.VERIFY_X509_PARTIAL_CHAIN
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _number_problem(key: str, value: Any, bounds: tuple[float, float]) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"{key} is {type(value).__name__}, expected a number"
    low, high = bounds
    if value < low or value > high:
        return f"{key}={value} outside {low}..{high}"
    return None


def _external_problem(item: Any) -> str | None:
    if not isinstance(item, dict):
        return "external entry is not an object"
    reading = item.get("value")
    if isinstance(reading, bool) or not isinstance(reading, (int, float)):
        return f"external value {reading!r} is not a number"
    if reading < 0 or reading > 1_000_000_000_000:
        return f"external value {reading} is out of range"
    for key in ("unique_id", "type", "unit"):
        if not isinstance(item.get(key), str) or not item[key]:
            return f"external {key} {item.get(key)!r} is not a string"
    stamp = item.get("timestamp")
    try:
        datetime.fromisoformat(stamp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return f"external timestamp {stamp!r} is not a date"
    return None


def _parse[T: (Device, Measurement, System)](
    model: type[T], data: dict[str, Any], problems: list[str]
) -> T:
    """Parse with the library model, dropping each field it still rejects."""
    while True:
        try:
            return model.from_dict(data)
        except InvalidFieldValue as err:
            key = "external" if err.field_name == "external_devices" else err.field_name
            if key not in data:
                raise
            problems.append(f"{key}={data[key]!r} is invalid")
            data = {name: value for name, value in data.items() if name != key}


def _sanitize(topic: str, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Drop only the fields that fail validation.

    Every field in the library models is optional, so a dropped or null field
    makes one sensor unavailable instead of freezing the whole snapshot.
    """
    ranges = _TOPIC_RANGES[topic]
    clean: dict[str, Any] = {}
    problems: list[str] = []
    for key, value in data.items():
        if value is None:
            continue
        if key == "external" and topic == "measurement":
            if not isinstance(value, list):
                problems.append("external is not a list")
                continue
            kept = []
            for item in value:
                if problem := _external_problem(item):
                    problems.append(problem)
                else:
                    kept.append(item)
            clean[key] = kept
            continue
        if (bounds := ranges.get(key)) is not None and (
            problem := _number_problem(key, value, bounds)
        ):
            problems.append(problem)
            continue
        clean[key] = value
    return clean, problems


class HomeWizardWebSocket:
    """One connection with complete topic snapshots parsed by the official library."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        token: str,
        serial: str,
        context: ssl.SSLContext,
    ) -> None:
        self._session = session
        self._url = URL.build(scheme="wss", host=host, path="/api/ws")
        self._token = token
        self._serial = serial
        self._context = context
        self._socket: ClientWebSocketResponse | None = None
        self._ready = False
        self._topics: dict[str, Any] = {}
        # Survives reconnects: a meter replays its latest measurement on
        # subscribe, which must not count as a fresh one.
        self._measurement: Measurement | None = None
        self._invalid = 0
        self._last_warning: dict[str, float] = {}
        self.last_measurement = time.monotonic()
        # Counts measurement frames on an open stream. The subscribe replay
        # during connect() does not increment it.
        self.reported_generation = 0

    async def connect(self) -> CombinedModels:
        """Authenticate and wait for every initial snapshot before becoming available."""
        await self.close()
        self._topics.clear()
        self._invalid = 0
        try:
            async with asyncio.timeout(30):
                self._socket = await self._session.ws_connect(
                    self._url,
                    ssl=self._context,
                    heartbeat=_HEARTBEAT,
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
                    # A rejected token arrives as a user:unauthorized error and
                    # raises in _receive; anything else is a protocol fault.
                    raise RequestError("Expected authorization confirmation")
                for topic in _TOPICS:
                    await self._socket.send_json({"type": "subscribe", "data": topic})
                while not all(key in self._topics for key in _TOPICS):
                    self._apply(await self._next_message())
                self._ready = True
                return self._snapshot()
        except WSServerHandshakeError as err:
            await self.close()
            if err.status == 401:
                raise UnauthorizedError("WebSocket token rejected") from err
            raise
        except BaseException:
            await self.close()
            raise

    async def request(self, topic: str) -> None:
        """Ask for the latest values of a topic; the reply arrives via receive()."""
        if not self._ready or self._socket is None or self._socket.closed:
            return
        try:
            await self._socket.send_json({"type": topic})
        except (ClientError, ConnectionResetError) as err:
            # The reader notices the broken socket and reconnects.
            LOGGER.debug("Could not request HomeWizard %s data: %s", topic, err)

    def _warn(self, key: str, message: str, *args: object) -> None:
        """Log a repeated socket fault once a minute instead of once a frame."""
        now = time.monotonic()
        previous = self._last_warning.get(key)
        if previous is not None and now - previous < _WARNING_INTERVAL:
            LOGGER.debug(message, *args)
            return
        self._last_warning[key] = now
        LOGGER.warning(message, *args)

    async def _next_message(self) -> dict[str, Any]:
        """Return the next protocol message, skipping junk already logged."""
        while True:
            message = await self._receive()
            if message is not None:
                return message

    async def _receive(self) -> dict[str, Any] | None:
        assert self._socket is not None
        try:
            message = await self._socket.receive(
                timeout=_IDLE_TIMEOUT if self._ready else None
            )
        except TimeoutError as err:
            raise RequestError(
                f"No HomeWizard WebSocket frame for {_IDLE_TIMEOUT:.0f} s"
            ) from err
        if message.type != WSMsgType.TEXT:
            raise RequestError("WebSocket disconnected")
        try:
            payload = message.json()
        except ValueError as err:
            self._warn(
                "malformed",
                "Ignoring malformed HomeWizard WebSocket message: %s",
                err,
            )
            self._reject_invalid()
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            self._warn(
                "shape", "Ignoring HomeWizard WebSocket message with unexpected shape"
            )
            self._reject_invalid()
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
            if not self._ready:
                raise RequestError(f"HomeWizard WebSocket returned an error: {error}")
            # Errors answer a single request; the subscriptions keep streaming.
            self._warn(
                f"error-{error}", "HomeWizard WebSocket returned an error: %s", error
            )
            return None
        return payload

    def _apply(self, message: dict[str, Any]) -> bool:
        topic = message["type"]
        if topic not in _TOPICS:
            LOGGER.debug("Ignoring HomeWizard topic %s", topic)
            return False
        data = message.get("data")
        if not isinstance(data, dict):
            self._warn(
                "payload", "Ignoring HomeWizard %s message with invalid payload", topic
            )
            return self._reject_invalid()
        data, problems = _sanitize(topic, data)
        try:
            if topic == "device":
                device = _parse(Device, data, problems)
                if not device.serial:
                    raise ValueError("device message without serial")
                if device.serial.casefold() != self._serial.casefold():
                    raise RequestError(
                        "WebSocket device identity changed from "
                        f"{self._serial} to {device.serial}"
                    )
                self._topics[topic] = device
            elif topic == "measurement":
                measurement = _parse(Measurement, data, problems)
                # A repeated telegram on the live socket is still a new report.
                # The copy replayed while connecting is not: _ready is false
                # until every initial snapshot has been accepted, and an equal
                # replay must not make a silent meter look fresh.
                changed = (
                    measurement.timestamp is None or measurement != self._measurement
                )
                if changed or self._ready:
                    self.last_measurement = time.monotonic()
                if self._ready:
                    self.reported_generation += 1
                self._topics[topic] = self._measurement = measurement
            else:
                self._topics[topic] = _parse(System, data, problems)
        except (MissingField, InvalidFieldValue, ValueError) as err:
            self._warn(
                "invalid", "Ignoring invalid HomeWizard %s message: %s", topic, err
            )
            return self._reject_invalid()
        finally:
            if problems:
                self._warn(
                    f"fields-{topic}",
                    "Ignoring invalid HomeWizard %s fields: %s",
                    topic,
                    "; ".join(problems),
                )
        self._invalid = 0
        return True

    def _reject_invalid(self) -> bool:
        """Keep the last good snapshot, unless the stream is stuck on junk."""
        self._invalid += 1
        if self._invalid >= _INVALID_LIMIT:
            raise RequestError("HomeWizard WebSocket sent repeated invalid messages")
        return False

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
        self._ready = False
        if self._socket is not None:
            await self._socket.close()
            self._socket = None
