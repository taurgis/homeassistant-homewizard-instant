"""HTTPS API v2 server and the plain-HTTP fault control API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from .certs import ensure_ca, issue_cert
from .meter import FAULT_NAMES, Meter

LOGGER = logging.getLogger("mock_device")


class MeterServer:
    """Serve one meter on HTTPS and its control API on a second port."""

    def __init__(self, meter: Meter, ca_dir: Path, work_dir: Path) -> None:
        self.meter = meter
        if meter.token_path is None:
            meter.token_path = work_dir / f"{meter.serial}.tokens.json"
            meter._load_tokens()
        self.ca_dir = ca_dir
        self.work_dir = work_dir
        self._https_runner: web.AppRunner | None = None
        self._control_runner: web.AppRunner | None = None
        self._ssl: ssl.SSLContext | None = None
        self._host = "0.0.0.0"
        self._https_port = 443

    def build_contexts(self) -> None:
        ca_cert, ca_key = ensure_ca(self.ca_dir)
        cert, key = issue_cert(ca_cert, ca_key, self.meter.serial, self.work_dir, name="device")
        bad_ca, bad_key = ensure_ca(self.work_dir / "untrusted-ca")
        bad_cert, bad_key_path = issue_cert(
            bad_ca, bad_key, self.meter.serial, self.work_dir, name="untrusted"
        )
        self._trusted = _server_context(cert, key)
        self._untrusted = _server_context(bad_cert, bad_key_path)
        self._ssl = self._trusted

    async def start(self, host: str, https_port: int, control_port: int) -> None:
        self._host = host
        self._https_port = https_port
        if self._ssl is None:
            self.build_contexts()
        self._control_runner = web.AppRunner(self.control_app())
        await self._control_runner.setup()
        await web.TCPSite(self._control_runner, host, control_port).start()
        await self._start_https()
        LOGGER.info(
            "P1 %s https://%s:%s profile=%s control=:%s",
            self.meter.serial,
            host,
            https_port,
            self.meter.profile_name,
            control_port,
        )

    async def _start_https(self) -> None:
        assert self._ssl is not None
        self._https_runner = web.AppRunner(self.https_app())
        await self._https_runner.setup()
        await web.TCPSite(
            self._https_runner, self._host, self._https_port, ssl_context=self._ssl
        ).start()

    async def restart_https(self) -> None:
        await self._close_sockets()
        if self._https_runner is not None:
            try:
                await asyncio.wait_for(self._https_runner.cleanup(), timeout=5)
            except TimeoutError:
                LOGGER.warning("HTTPS cleanup timed out for %s", self.meter.serial)
            self._https_runner = None
        self.meter.sockets.clear()
        self.meter.transports.clear()
        await self._start_https()
        LOGGER.info("HTTPS restarted for %s", self.meter.serial)

    def https_app(self) -> web.Application:
        meter = self.meter

        @web.middleware
        async def hang_middleware(
            request: web.Request, handler: Any
        ) -> web.StreamResponse:
            if request.path != "/api/ws":
                while meter.faults.hang:
                    await asyncio.sleep(0.2)
            return await handler(request)

        app = web.Application(middlewares=[hang_middleware])
        app.router.add_get("/api", self._device)
        app.router.add_get("/api/measurement", self._measurement)
        app.router.add_get("/api/system", self._system)
        app.router.add_post("/api/user", self._create_user)
        app.router.add_delete("/api/user", self._delete_user)
        app.router.add_get("/api/ws", self._websocket)
        return app

    def control_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/health", self._health)
        app.router.add_get("/state", self._state)
        app.router.add_post("/button", self._button)
        app.router.add_post("/fault", self._fault)
        app.router.add_post("/clear", self._clear)
        app.router.add_post("/profile", self._profile)
        app.router.add_post("/steady", self._steady)
        app.router.add_post("/overlay", self._overlay)
        app.router.add_post("/uptime", self._uptime)
        app.router.add_post("/push", self._push_now)
        return app

    async def _device(self, request: web.Request) -> web.Response:
        if not self.meter.authorized(request.headers.get("Authorization")):
            return _json({"error": "user:unauthorized"}, status=401)
        return _json(self.meter.device_payload())

    async def _measurement(self, request: web.Request) -> web.Response:
        if not self.meter.authorized(request.headers.get("Authorization")):
            return _json({"error": "user:unauthorized"}, status=401)
        return _json(self.meter.measurement_payload())

    async def _system(self, request: web.Request) -> web.Response:
        if not self.meter.authorized(request.headers.get("Authorization")):
            return _json({"error": "user:unauthorized"}, status=401)
        return _json(self.meter.system_payload())

    async def _create_user(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return _json({"error": "user:invalid-name"}, status=400)
        name = body.get("name") if isinstance(body, dict) else None
        status, payload = self.meter.issue_token(name if isinstance(name, str) else "")
        LOGGER.info("pairing %s -> %s", name, status)
        return _json(payload, status=status)

    async def _delete_user(self, request: web.Request) -> web.Response:
        if not self.meter.delete_token(request.headers.get("Authorization")):
            return _json({"error": "user:unauthorized"}, status=401)
        LOGGER.info("deleted user on %s", self.meter.serial)
        return web.Response(status=204)

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        self.meter.sockets.add(socket)
        if request.transport is not None:
            self.meter.transports.add(request.transport)
        LOGGER.info("websocket opened (%s)", len(self.meter.sockets))
        try:
            await self._session(socket)
        finally:
            self.meter.sockets.discard(socket)
            if request.transport is not None:
                self.meter.transports.discard(request.transport)
            if not socket.closed:
                await socket.close()
            LOGGER.info("websocket closed (%s)", len(self.meter.sockets))
        return socket

    async def _session(self, socket: web.WebSocketResponse) -> None:
        await socket.send_json(
            {"type": "authorization_requested", "data": {"api_version": "2.0.0"}}
        )
        try:
            incoming = await asyncio.wait_for(
                socket.receive(), timeout=self.meter.auth_timeout
            )
        except TimeoutError:
            await socket.close()
            return
        if incoming.type != WSMsgType.TEXT:
            return
        try:
            payload = incoming.json()
        except ValueError:
            return
        token = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("type") != "authorization":
            await socket.send_json(
                {"type": "error", "data": {"message": "user:unauthorized"}}
            )
            await socket.close()
            return
        header = f"Bearer {token}" if isinstance(token, str) else None
        if not self.meter.authorized(header):
            await socket.send_json({"type": "error", "data": {"message": "user:unauthorized"}})
            await socket.close()
            return
        await socket.send_json({"type": "authorized"})
        subscribed: set[str] = set()
        pusher = asyncio.create_task(self._push_loop(socket, subscribed))
        try:
            async for message in socket:
                if message.type != WSMsgType.TEXT or self.meter.faults.hang:
                    if message.type != WSMsgType.TEXT:
                        break
                    continue
                try:
                    client = message.json()
                except ValueError:
                    continue
                if isinstance(client, dict) and client.get("type") == "subscribe":
                    topic = client.get("data")
                    if isinstance(topic, str) and topic in {"device", "measurement", "system"}:
                        subscribed.add(topic)
                        await self._send_topic(socket, topic)
        finally:
            pusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pusher

    async def _send_topic(self, socket: web.WebSocketResponse, topic: str) -> None:
        data = self.meter.topic_payload(topic)
        if data is None:
            return
        await socket.send_json({"type": topic, "data": data})
        self.meter.pushes += 1

    async def _push_loop(
        self, socket: web.WebSocketResponse, subscribed: set[str]
    ) -> None:
        next_system = asyncio.get_running_loop().time() + 30
        while not socket.closed:
            interval = self.meter.faults.slow_interval or self.meter.push_interval
            await asyncio.sleep(interval)
            if socket.closed or self.meter.faults.hang or not subscribed:
                continue
            fault = self.meter.fault_message()
            try:
                if isinstance(fault, str):
                    await socket.send_str(fault)
                    self.meter.pushes += 1
                elif fault is not None:
                    await socket.send_json(fault)
                    self.meter.pushes += 1
                else:
                    now = asyncio.get_running_loop().time()
                    topic = (
                        "system"
                        if now >= next_system and "system" in subscribed
                        else "measurement"
                    )
                    if topic not in subscribed:
                        continue
                    if topic == "system":
                        next_system = now + 30
                    await self._send_topic(socket, topic)
            except (ConnectionResetError, RuntimeError):
                return

    async def _health(self, _request: web.Request) -> web.Response:
        return _json({"ok": True, "serial": self.meter.serial})

    async def _state(self, _request: web.Request) -> web.Response:
        return _json(self.meter.state())

    async def _button(self, _request: web.Request) -> web.Response:
        self.meter.press_button()
        LOGGER.info("button pressed on %s", self.meter.serial)
        return _json({"button_open": True, "seconds": 30})

    async def _fault(self, request: web.Request) -> web.Response:
        body = await _body(request)
        name = body.get("name")
        if not isinstance(name, str) or name not in FAULT_NAMES:
            return _json({"error": "unknown fault", "faults": FAULT_NAMES}, status=400)
        interval = body.get("interval")
        self.meter.apply_fault(name, float(interval) if isinstance(interval, (int, float)) else None)
        LOGGER.info("fault %s on %s", name, self.meter.serial)
        if name == "hang":
            self.meter.pause_reads()
        if name == "drop":
            await self._close_sockets()
        if name in {"restart", "tls_change"}:
            self._ssl = self._untrusted if self.meter.faults.tls_untrusted else self._trusted
            await self.restart_https()
        if name in {"revoke_token", "refuse_auth"}:
            await self._reject_sockets()
        return _json({"fault": name, "faults": self.meter.faults.public()})

    async def _clear(self, _request: web.Request) -> web.Response:
        restore_tls = self.meter.faults.tls_untrusted
        self.meter.clear_faults()
        self.meter.resume_reads()
        if restore_tls:
            self._ssl = self._trusted
            await self.restart_https()
        LOGGER.info("faults cleared on %s", self.meter.serial)
        return _json({"cleared": True})

    async def _profile(self, request: web.Request) -> web.Response:
        body = await _body(request)
        name = body.get("name")
        if not isinstance(name, str):
            return _json({"error": "name required"}, status=400)
        try:
            self.meter.set_profile(name)
        except ValueError as err:
            return _json({"error": str(err)}, status=400)
        await self._broadcast("measurement")
        return _json({"profile": name})

    async def _steady(self, request: web.Request) -> web.Response:
        body = await _body(request)
        self.meter.steady = bool(body.get("enabled", True))
        return _json({"steady": self.meter.steady})

    async def _overlay(self, request: web.Request) -> web.Response:
        body = await _body(request)
        if body.get("reset"):
            self.meter.overlay.clear()
        else:
            self.meter.overlay.update({key: value for key, value in body.items() if key != "reset"})
        await self._broadcast("measurement")
        return _json({"overlay": self.meter.overlay})

    async def _uptime(self, request: web.Request) -> web.Response:
        body = await _body(request)
        seconds = body.get("seconds")
        if seconds is None:
            self.meter.uptime_override = None
        elif isinstance(seconds, int) and not isinstance(seconds, bool):
            self.meter.uptime_override = seconds
        else:
            return _json({"error": "seconds must be an integer"}, status=400)
        await self._broadcast("system")
        return _json({"uptime_s": self.meter.system_payload()["uptime_s"]})

    async def _push_now(self, request: web.Request) -> web.Response:
        body = await _body(request)
        topic = body.get("topic", "measurement")
        if topic not in {"device", "measurement", "system"}:
            return _json({"error": "unknown topic"}, status=400)
        await self._broadcast(str(topic))
        return _json({"pushed": topic})

    async def _broadcast(self, topic: str) -> None:
        for socket in list(self.meter.sockets):
            if socket.closed:
                continue
            with contextlib.suppress(ConnectionResetError, RuntimeError):
                await self._send_topic(socket, topic)

    async def _close_sockets(self) -> None:
        for socket in list(self.meter.sockets):
            with contextlib.suppress(ConnectionResetError, RuntimeError):
                await socket.close()

    async def _reject_sockets(self) -> None:
        for socket in list(self.meter.sockets):
            with contextlib.suppress(ConnectionResetError, RuntimeError):
                await socket.send_json(
                    {"type": "error", "data": {"message": "user:unauthorized"}}
                )
                await socket.close()


def _server_context(cert: Path, key: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


def _json(payload: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


async def _body(request: web.Request) -> dict[str, Any]:
    if request.content_length in (0, None) and request.can_read_body is False:
        return {}
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
