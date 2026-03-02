"""aiohttp routes and websocket behavior for the dummy P1 meter."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import WSMsgType, web

from .simulation import P1Simulation

WS_ALLOWED_SUBSCRIPTIONS = frozenset({"*", "measurement", "device", "system", "batteries"})
WS_MAX_SUBSCRIPTIONS_PER_CLIENT = 8
WS_SEND_TIMEOUT_SECONDS = 1.0
WS_MAX_CONCURRENT_SENDS = 16


def json_response(payload: dict[str, Any], status: int = 200) -> web.Response:
    """Create compact JSON response."""
    return web.json_response(
        payload,
        status=status,
        dumps=lambda x: json.dumps(x, ensure_ascii=True, separators=(",", ":")),
    )


def parse_json_dict(payload: str) -> dict[str, Any]:
    """Parse JSON payload to dict."""
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}

    return parsed if isinstance(parsed, dict) else {}


def auth_token(request: web.Request) -> str | None:
    """Extract bearer token from Authorization header."""
    authorization = request.headers.get("Authorization")
    if authorization is None:
        return None

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None

    token = authorization[len(prefix) :].strip()
    return token or None


def require_v2_token(simulation: P1Simulation, request: web.Request) -> web.Response | str:
    """Validate bearer token for v2 endpoints."""
    token = auth_token(request)
    if not simulation.is_valid_token(token):
        return json_response({"error": "user:unauthorized"}, status=401)

    if not simulation.api_enabled:
        return json_response({"error": "api:disabled"}, status=403)

    return token


async def broadcast_topic(app: web.Application, topic: str) -> None:
    """Broadcast websocket event to clients subscribed to topic."""
    clients: dict[web.WebSocketResponse, set[str]] = app["ws_clients"]
    stale: list[web.WebSocketResponse] = []
    targets: list[web.WebSocketResponse] = []
    semaphore = asyncio.Semaphore(WS_MAX_CONCURRENT_SENDS)

    async def _send_with_timeout(ws: web.WebSocketResponse) -> bool:
        """Send a topic event frame with timeout and bounded concurrency."""
        try:
            async with semaphore:
                async with asyncio.timeout(WS_SEND_TIMEOUT_SECONDS):
                    await ws.send_json({"type": topic, "data": {}})
            return True
        except Exception:
            return False

    for ws, subscriptions in list(clients.items()):
        if ws.closed:
            stale.append(ws)
            continue

        if topic not in subscriptions and "*" not in subscriptions:
            continue

        targets.append(ws)

    if targets:
        send_results = await asyncio.gather(
            *(_send_with_timeout(ws) for ws in targets),
            return_exceptions=False,
        )
        for ws, sent in zip(targets, send_results, strict=True):
            if not sent:
                stale.append(ws)

    for ws in stale:
        clients.pop(ws, None)
        if not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass


async def ws_broadcast_loop(app: web.Application) -> None:
    """Emit websocket topics periodically so coordinator gets refresh triggers."""
    tick = 0
    while True:
        await asyncio.sleep(1)
        tick += 1
        await broadcast_topic(app, "measurement")
        if tick % 30 == 0:
            await broadcast_topic(app, "system")
        if tick % 120 == 0:
            await broadcast_topic(app, "device")


async def on_startup(app: web.Application) -> None:
    """Start websocket broadcaster task."""
    app["ws_task"] = asyncio.create_task(ws_broadcast_loop(app))


async def on_cleanup(app: web.Application) -> None:
    """Stop websocket broadcaster and close websocket clients."""
    task: asyncio.Task[None] | None = app.get("ws_task")
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    clients: dict[web.WebSocketResponse, set[str]] = app["ws_clients"]
    for ws in list(clients):
        await ws.close()
    clients.clear()


class DummyP1Api:
    """Route handlers for the dummy P1 meter API."""

    def __init__(self, simulation: P1Simulation) -> None:
        self.simulation = simulation

    async def get_v2_device(self, request: web.Request) -> web.Response:
        """Handle GET /api."""
        auth_result = require_v2_token(self.simulation, request)
        if isinstance(auth_result, web.Response):
            return auth_result
        return json_response(self.simulation.get_device_v2_payload())

    async def post_v2_user(self, _request: web.Request) -> web.Response:
        """Handle POST /api/user with automatic authorization support."""
        if not self.simulation.api_enabled or not self.simulation.v2_auto_authorize:
            return json_response({"error": "user:creation-not-enabled"}, status=403)

        try:
            payload = await _request.json()
        except Exception:
            payload = {}

        name = payload.get("name")
        if not isinstance(name, str) or not name:
            return json_response({"error": "user:invalid-name"}, status=400)

        token = self.simulation.issue_token(name)
        return json_response({"token": token, "name": name})

    async def delete_v2_user(self, request: web.Request) -> web.Response:
        """Handle DELETE /api/user."""
        auth_result = require_v2_token(self.simulation, request)
        if isinstance(auth_result, web.Response):
            return auth_result

        token = auth_result
        name: str | None = None
        try:
            payload = await request.json()
            if isinstance(payload.get("name"), str):
                name = payload["name"]
        except Exception:
            pass

        self.simulation.revoke_token(token=token if name is None else None, name=name)
        return web.Response(status=204)

    async def get_v2_measurement(self, request: web.Request) -> web.Response:
        """Handle GET /api/measurement."""
        auth_result = require_v2_token(self.simulation, request)
        if isinstance(auth_result, web.Response):
            return auth_result
        return json_response(self.simulation.get_measurement_v2_payload())

    async def get_v2_system(self, request: web.Request) -> web.Response:
        """Handle GET /api/system."""
        auth_result = require_v2_token(self.simulation, request)
        if isinstance(auth_result, web.Response):
            return auth_result
        return json_response(self.simulation.get_system_payload())

    async def put_v2_system(self, request: web.Request) -> web.Response:
        """Handle PUT /api/system."""
        auth_result = require_v2_token(self.simulation, request)
        if isinstance(auth_result, web.Response):
            return auth_result

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        if isinstance(payload, dict):
            self.simulation.update_system_settings(payload)
        return json_response(self.simulation.get_system_payload())

    async def get_v2_telegram(self, request: web.Request) -> web.Response:
        """Handle GET /api/telegram."""
        auth_result = require_v2_token(self.simulation, request)
        if isinstance(auth_result, web.Response):
            return auth_result
        return web.Response(
            text=self.simulation.get_telegram_payload(), content_type="text/plain"
        )

    async def put_v2_identify(self, request: web.Request) -> web.Response:
        """Handle PUT /api/system/identify."""
        auth_result = require_v2_token(self.simulation, request)
        if isinstance(auth_result, web.Response):
            return auth_result
        return json_response({})

    async def put_v2_reboot(self, request: web.Request) -> web.Response:
        """Handle PUT /api/system/reboot."""
        auth_result = require_v2_token(self.simulation, request)
        if isinstance(auth_result, web.Response):
            return auth_result
        return json_response({})

    async def v2_batteries_not_supported(self, _request: web.Request) -> web.Response:
        """Handle unsupported batteries endpoint."""
        return json_response({"error": "resource:not-found"}, status=404)

    async def websocket_v2(self, request: web.Request) -> web.StreamResponse:
        """Handle /api/ws websocket handshake and subscriptions."""
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        await ws.send_json(
            {"type": "authorization_requested", "data": {"api_version": "2.0.0"}}
        )

        try:
            auth_message = await ws.receive(timeout=40)
        except asyncio.TimeoutError:
            await ws.close()
            return ws

        if auth_message.type != WSMsgType.TEXT:
            await ws.close()
            return ws

        payload = parse_json_dict(auth_message.data)
        token = payload.get("data") if payload.get("type") == "authorization" else None

        if not isinstance(token, str) or not self.simulation.is_valid_token(token):
            await ws.send_json({"type": "error", "data": {"message": "unauthorized"}})
            await ws.close()
            return ws

        await ws.send_json({"type": "authorized"})

        subscriptions: set[str] = set()
        clients: dict[web.WebSocketResponse, set[str]] = request.app["ws_clients"]
        clients[ws] = subscriptions

        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue

                body = parse_json_dict(message.data)
                if body.get("type") == "subscribe" and isinstance(body.get("data"), str):
                    topic = body["data"]
                    if topic not in WS_ALLOWED_SUBSCRIPTIONS:
                        continue

                    if (
                        len(subscriptions) >= WS_MAX_SUBSCRIPTIONS_PER_CLIENT
                        and topic not in subscriptions
                    ):
                        await ws.send_json(
                            {
                                "type": "error",
                                "data": {"message": "too-many-subscriptions"},
                            }
                        )
                        await ws.close()
                        break

                    subscriptions.add(topic)
                elif body.get("type") == "authorization":
                    candidate = body.get("data")
                    if not isinstance(candidate, str) or not self.simulation.is_valid_token(candidate):
                        await ws.send_json(
                            {"type": "error", "data": {"message": "unauthorized"}}
                        )
                        await ws.close()
                        break
        finally:
            clients.pop(ws, None)

        return ws

    async def get_v1_data(self, _request: web.Request) -> web.Response:
        """Handle GET /api/v1/data."""
        if not self.simulation.api_enabled:
            return json_response(
                {"error": {"id": 202, "description": "API not enabled"}},
                status=403,
            )
        return json_response(self.simulation.get_measurement_v1_payload())

    async def get_v1_system(self, _request: web.Request) -> web.Response:
        """Handle GET /api/v1/system."""
        if not self.simulation.api_enabled:
            return json_response(
                {"error": {"id": 202, "description": "API not enabled"}},
                status=403,
            )
        return json_response(self.simulation.get_system_payload())

    async def put_v1_system(self, request: web.Request) -> web.Response:
        """Handle PUT /api/v1/system."""
        if not self.simulation.api_enabled:
            return json_response(
                {"error": {"id": 202, "description": "API not enabled"}},
                status=403,
            )

        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            self.simulation.update_system_settings(payload)
        return json_response(self.simulation.get_system_payload())

    async def get_v1_telegram(self, _request: web.Request) -> web.Response:
        """Handle GET /api/v1/telegram."""
        if not self.simulation.api_enabled:
            return json_response(
                {"error": {"id": 202, "description": "API not enabled"}},
                status=403,
            )
        return web.Response(
            text=self.simulation.get_telegram_payload(), content_type="text/plain"
        )

    async def put_v1_identify(self, _request: web.Request) -> web.Response:
        """Handle PUT /api/v1/identify."""
        if not self.simulation.api_enabled:
            return json_response(
                {"error": {"id": 202, "description": "API not enabled"}},
                status=403,
            )
        return json_response({})

    async def get_sim_state(self, _request: web.Request) -> web.Response:
        """Handle GET /sim/state."""
        return json_response(self.simulation.get_debug_state())

    async def put_sim_api_enabled(self, request: web.Request) -> web.Response:
        """Handle PUT /sim/api_enabled."""
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            return json_response(
                {"error": "payload must contain boolean field 'enabled'"}, status=400
            )

        self.simulation.set_api_enabled(enabled)
        return json_response({"api_enabled": enabled})

    async def put_sim_v2_auto_authorize(self, request: web.Request) -> web.Response:
        """Handle PUT /sim/v2_auto_authorize."""
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            return json_response(
                {"error": "payload must contain boolean field 'enabled'"}, status=400
            )

        self.simulation.set_v2_auto_authorize(enabled)
        return json_response({"v2_auto_authorize": enabled})


def create_app(simulation: P1Simulation) -> web.Application:
    """Create aiohttp app with v1, v2 and websocket routes."""
    app = web.Application()
    app["simulation"] = simulation
    app["ws_clients"] = {}
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    api = DummyP1Api(simulation)

    app.router.add_get("/api", api.get_v2_device)
    app.router.add_post("/api/user", api.post_v2_user)
    app.router.add_delete("/api/user", api.delete_v2_user)
    app.router.add_get("/api/measurement", api.get_v2_measurement)
    app.router.add_get("/api/system", api.get_v2_system)
    app.router.add_put("/api/system", api.put_v2_system)
    app.router.add_put("/api/system/identify", api.put_v2_identify)
    app.router.add_put("/api/system/reboot", api.put_v2_reboot)
    app.router.add_get("/api/telegram", api.get_v2_telegram)
    app.router.add_get("/api/batteries", api.v2_batteries_not_supported)
    app.router.add_put("/api/batteries", api.v2_batteries_not_supported)
    app.router.add_get("/api/ws", api.websocket_v2)

    app.router.add_get("/api/v1/data", api.get_v1_data)
    app.router.add_get("/api/v1/system", api.get_v1_system)
    app.router.add_put("/api/v1/system", api.put_v1_system)
    app.router.add_get("/api/v1/telegram", api.get_v1_telegram)
    app.router.add_put("/api/v1/identify", api.put_v1_identify)

    app.router.add_get("/sim/state", api.get_sim_state)
    app.router.add_put("/sim/api_enabled", api.put_sim_api_enabled)
    app.router.add_put("/sim/v2_auto_authorize", api.put_sim_v2_auto_authorize)

    return app
