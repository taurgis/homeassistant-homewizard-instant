"""Authenticated HomeWizard API v2 push transport."""

from __future__ import annotations

import asyncio
import ssl
from typing import Any

from aiohttp import (
    ClientSession,
    ClientWebSocketResponse,
    WSMsgType,
    WSServerHandshakeError,
)
from homewizard_energy.errors import RequestError, UnauthorizedError
from homewizard_energy.models import CombinedModels, Device, Measurement, System
from homewizard_energy.v2.cacert import CACERT
from yarl import URL


def create_ssl_context() -> ssl.SSLContext:
    """Trust the HomeWizard CA and verify the device certificate identity."""
    context = ssl.create_default_context(cadata=CACERT)
    context.verify_flags = ssl.VERIFY_X509_PARTIAL_CHAIN
    context.hostname_checks_common_name = True
    return context


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
                message = await self._receive()
                if message.get("type") != "authorization_requested":
                    raise RequestError("Expected authorization request")
                await self._socket.send_json(
                    {"type": "authorization", "data": self._token}
                )
                message = await self._receive()
                if message.get("type") != "authorized":
                    raise UnauthorizedError("WebSocket authorization rejected")
                for topic in ("device", "measurement", "system"):
                    await self._socket.send_json({"type": "subscribe", "data": topic})
                while not all(
                    key in self._topics for key in ("device", "measurement", "system")
                ):
                    self._apply(await self._receive())
                return self._snapshot()
        except WSServerHandshakeError as err:
            await self.close()
            if err.status == 401:
                raise UnauthorizedError("WebSocket token rejected") from err
            raise
        except BaseException:
            await self.close()
            raise

    async def _receive(self) -> dict[str, Any]:
        assert self._socket is not None
        message = await self._socket.receive()
        if message.type != WSMsgType.TEXT:
            raise RequestError("WebSocket disconnected")
        payload = message.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            raise RequestError("Invalid WebSocket message")
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
        if topic not in ("device", "measurement", "system"):
            return False
        data = message.get("data")
        if not isinstance(data, dict):
            raise RequestError("Invalid topic payload")
        if topic == "device":
            device = Device.from_dict(data)
            if device.id != self._identifier:
                raise RequestError("WebSocket device identity changed")
            self._topics[topic] = device
        elif topic == "measurement":
            self._topics[topic] = Measurement.from_dict(data)
        else:
            self._topics[topic] = System.from_dict(data)
        return True

    def _snapshot(self) -> CombinedModels:
        return CombinedModels(
            device=self._topics["device"],
            measurement=self._topics["measurement"],
            system=self._topics["system"],
            state=None,
        )

    async def receive(self) -> CombinedModels:
        """Read the next known topic, ignoring future topics."""
        while not self._apply(await self._receive()):
            pass
        return self._snapshot()

    async def close(self) -> None:
        """Close only our socket; the session belongs to Home Assistant."""
        if self._socket is not None:
            await self._socket.close()
            self._socket = None
