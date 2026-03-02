"""Tests for the dummy P1 meter API emulator."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from ipaddress import IPv4Address
import time
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient

from tools.dummy_p1_meter import P1Simulation, create_app
from tools.dummy_p1_meter.api import broadcast_topic
from tools.dummy_p1_meter.discovery import SERVICE_TYPES, ZeroconfPublisher

pytestmark = pytest.mark.enable_socket


@pytest.fixture
async def dummy_client(
    aiohttp_client,
) -> AsyncGenerator[TestClient, None]:
    """Create a test client for the dummy P1 API app."""
    simulation = P1Simulation(
        seed=123,
        timezone_name="Europe/Amsterdam",
        latitude=52.3676,
        pv_peak_w=4200,
        serial="P1SIMTEST",
        api_enabled=True,
        v2_auto_authorize=True,
    )
    simulation.start()

    try:
        client = await aiohttp_client(create_app(simulation))
        yield client
    finally:
        simulation.stop()


async def test_v1_data_and_api_enabled_toggle(dummy_client: TestClient) -> None:
    """Test v1 data endpoint and API-disabled behavior."""
    response = await dummy_client.get("/api/v1/data")
    assert response.status == 200
    payload = await response.json()
    assert "active_power_w" in payload
    assert "total_power_import_kwh" in payload

    disable = await dummy_client.put("/sim/api_enabled", json={"enabled": False})
    assert disable.status == 200

    disabled_response = await dummy_client.get("/api/v1/data")
    assert disabled_response.status == 403
    disabled_payload = await disabled_response.json()
    assert disabled_payload == {"error": {"id": 202, "description": "API not enabled"}}


async def test_v2_auth_token_and_data(dummy_client: TestClient) -> None:
    """Test v2 authorization flow and authenticated data endpoints."""
    unauthorized = await dummy_client.get("/api")
    assert unauthorized.status == 401
    assert await unauthorized.json() == {"error": "user:unauthorized"}

    token_response = await dummy_client.post("/api/user", json={"name": "local/tester"})
    assert token_response.status == 200
    token_payload = await token_response.json()
    token = token_payload["token"]

    authorized = await dummy_client.get(
        "/api", headers={"Authorization": f"Bearer {token}"}
    )
    assert authorized.status == 200
    device_payload = await authorized.json()
    assert device_payload["api_version"] == "2.0.0"

    measurement = await dummy_client.get(
        "/api/measurement", headers={"Authorization": f"Bearer {token}"}
    )
    assert measurement.status == 200
    measurement_payload = await measurement.json()
    assert "power_w" in measurement_payload

    disable_auto = await dummy_client.put(
        "/sim/v2_auto_authorize", json={"enabled": False}
    )
    assert disable_auto.status == 200

    blocked_token = await dummy_client.post("/api/user", json={"name": "local/blocked"})
    assert blocked_token.status == 403
    assert await blocked_token.json() == {"error": "user:creation-not-enabled"}


async def test_v2_websocket_auth_and_subscription(dummy_client: TestClient) -> None:
    """Test websocket auth handshake and measurement subscription events."""
    token_response = await dummy_client.post("/api/user", json={"name": "local/ws"})
    token_payload = await token_response.json()
    token = token_payload["token"]

    invalid_ws = await dummy_client.ws_connect("/api/ws")
    first = await invalid_ws.receive_json(timeout=2)
    assert first["type"] == "authorization_requested"

    await invalid_ws.send_json({"type": "authorization", "data": "invalid-token"})
    invalid_reply = await invalid_ws.receive_json(timeout=2)
    assert invalid_reply == {"type": "error", "data": {"message": "unauthorized"}}
    await invalid_ws.close()

    ws = await dummy_client.ws_connect("/api/ws")
    auth_prompt = await ws.receive_json(timeout=2)
    assert auth_prompt["type"] == "authorization_requested"

    await ws.send_json({"type": "authorization", "data": token})
    authorized = await ws.receive_json(timeout=2)
    assert authorized == {"type": "authorized"}

    await ws.send_json({"type": "subscribe", "data": "measurement"})
    event = await ws.receive_json(timeout=3)
    assert event["type"] == "measurement"
    assert event["data"] == {}

    await ws.close()


async def test_broadcast_topic_closes_stale_client_on_send_failure() -> None:
    """Ensure failed websocket sends close and remove stale clients."""
    ws = Mock()
    ws.closed = False
    ws.send_json = AsyncMock(side_effect=RuntimeError("boom"))
    ws.close = AsyncMock()

    app = web.Application()
    app["ws_clients"] = {ws: {"measurement"}}

    await broadcast_topic(app, "measurement")

    ws.close.assert_awaited_once()
    assert ws not in app["ws_clients"]


async def test_websocket_ignores_unknown_subscriptions(dummy_client: TestClient) -> None:
    """Ensure unknown subscription topics are ignored to keep memory bounded."""
    token_response = await dummy_client.post("/api/user", json={"name": "local/unknown-sub"})
    token_payload = await token_response.json()
    token = token_payload["token"]

    ws = await dummy_client.ws_connect("/api/ws")
    await ws.receive_json(timeout=2)
    await ws.send_json({"type": "authorization", "data": token})
    assert await ws.receive_json(timeout=2) == {"type": "authorized"}

    for idx in range(64):
        await ws.send_json({"type": "subscribe", "data": f"unknown-{idx}"})

    await ws.send_json({"type": "subscribe", "data": "measurement"})
    subscriptions: set[str] = set()
    for _ in range(50):
        await asyncio.sleep(0.01)
        subscriptions = next(iter(dummy_client.app["ws_clients"].values()))
        if "measurement" in subscriptions:
            break

    assert subscriptions == {"measurement"}

    await ws.close()


async def test_websocket_subscription_limit_closes_connection(
    dummy_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure per-client subscription limits close abusive websocket sessions."""
    monkeypatch.setattr(
        "tools.dummy_p1_meter.api.WS_MAX_SUBSCRIPTIONS_PER_CLIENT",
        1,
    )

    token_response = await dummy_client.post("/api/user", json={"name": "local/sub-limit"})
    token_payload = await token_response.json()
    token = token_payload["token"]

    ws = await dummy_client.ws_connect("/api/ws")
    await ws.receive_json(timeout=2)
    await ws.send_json({"type": "authorization", "data": token})
    assert await ws.receive_json(timeout=2) == {"type": "authorized"}

    await ws.send_json({"type": "subscribe", "data": "measurement"})
    await ws.send_json({"type": "subscribe", "data": "system"})

    error = await ws.receive_json(timeout=2)
    assert error == {"type": "error", "data": {"message": "too-many-subscriptions"}}

    closed = await ws.receive(timeout=2)
    assert closed.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}


async def test_websocket_repeated_connect_disconnect_cleans_client_registry(
    dummy_client: TestClient,
) -> None:
    """Ensure repeated websocket sessions do not accumulate tracked clients."""
    token_response = await dummy_client.post("/api/user", json={"name": "local/loop"})
    token_payload = await token_response.json()
    token = token_payload["token"]

    for _ in range(40):
        ws = await dummy_client.ws_connect("/api/ws")
        await ws.receive_json(timeout=2)
        await ws.send_json({"type": "authorization", "data": token})
        assert await ws.receive_json(timeout=2) == {"type": "authorized"}
        await ws.send_json({"type": "subscribe", "data": "measurement"})
        await ws.close()

    await asyncio.sleep(0)
    assert dummy_client.app["ws_clients"] == {}


def test_token_store_is_bounded_for_long_running_sessions() -> None:
    """Ensure repeated user creation does not grow token memory unbounded."""
    simulation = P1Simulation(
        seed=123,
        timezone_name="Europe/Amsterdam",
        latitude=52.3676,
        pv_peak_w=4200,
        serial="P1SIMTEST",
        api_enabled=True,
        v2_auto_authorize=True,
    )

    oldest_token = simulation.issue_token("local/user-0")
    newest_token = oldest_token
    for idx in range(1, 320):
        newest_token = simulation.issue_token(f"local/user-{idx}")

    state = simulation.get_debug_state()

    assert state["token_count"] == 256
    assert simulation.is_valid_token(oldest_token) is False
    assert simulation.is_valid_token(newest_token) is True


def test_load_profile_has_hourly_and_day_of_year_shape() -> None:
    """Validate Belgian-inspired hourly and seasonal load factors are applied."""
    simulation = P1Simulation(
        seed=123,
        timezone_name="Europe/Amsterdam",
        latitude=52.3676,
        pv_peak_w=0,
        serial="P1SIMTEST",
        api_enabled=True,
        v2_auto_authorize=True,
    )

    weekday_night = simulation._interpolate_hourly_load_factor(hour=2.0, weekend=False)
    weekday_morning = simulation._interpolate_hourly_load_factor(hour=9.0, weekend=False)
    weekend_morning = simulation._interpolate_hourly_load_factor(hour=9.0, weekend=True)

    assert weekday_morning > weekday_night
    assert weekday_morning > weekend_morning

    tz = ZoneInfo("Europe/Amsterdam")
    january_factor = simulation._interpolate_monthly_load_factor(
        now=datetime(2026, 1, 15, 12, 0, tzinfo=tz),
        hour=12.0,
    )
    july_factor = simulation._interpolate_monthly_load_factor(
        now=datetime(2026, 7, 15, 12, 0, tzinfo=tz),
        hour=12.0,
    )

    assert january_factor > july_factor


def test_month_factor_interpolation_is_smooth_across_boundaries() -> None:
    """Ensure the month profile does not jump abruptly on day transitions."""
    simulation = P1Simulation(
        seed=123,
        timezone_name="Europe/Amsterdam",
        latitude=52.3676,
        pv_peak_w=0,
        serial="P1SIMTEST",
        api_enabled=True,
        v2_auto_authorize=True,
    )

    tz = ZoneInfo("Europe/Amsterdam")
    jan_end = simulation._interpolate_monthly_load_factor(
        now=datetime(2026, 1, 31, 23, 0, tzinfo=tz),
        hour=23.0,
    )
    feb_start = simulation._interpolate_monthly_load_factor(
        now=datetime(2026, 2, 1, 0, 0, tzinfo=tz),
        hour=0.0,
    )

    assert abs(jan_end - feb_start) < 0.02


def test_simulation_can_restart_same_instance() -> None:
    """Ensure start->stop->start resumes ticking on the same simulation object."""
    simulation = P1Simulation(
        seed=123,
        timezone_name="Europe/Amsterdam",
        latitude=52.3676,
        pv_peak_w=0,
        serial="P1SIMTEST",
        api_enabled=True,
        v2_auto_authorize=True,
    )

    simulation.start()
    time.sleep(1.1)
    first_sample = simulation.get_debug_state()["last_sample"]
    simulation.stop()

    simulation.start()
    time.sleep(1.1)
    second_sample = simulation.get_debug_state()["last_sample"]
    simulation.stop()

    assert first_sample != second_sample


def test_zeroconf_publisher_registers_homewizard_services(monkeypatch) -> None:
    """Ensure simulator advertises both HomeWizard service types with expected TXT keys."""

    created_infos: list[object] = []
    zeroconf_instances: list[object] = []

    class FakeServiceInfo:
        """Capture ServiceInfo constructor inputs."""

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            created_infos.append(self)

    class FakeZeroconf:
        """Capture registration lifecycle operations."""

        def __init__(self) -> None:
            self.registered: list[object] = []
            self.unregistered: list[object] = []
            self.closed = False
            zeroconf_instances.append(self)

        def register_service(self, info: object) -> None:
            self.registered.append(info)

        def unregister_service(self, info: object) -> None:
            self.unregistered.append(info)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("tools.dummy_p1_meter.discovery.ServiceInfo", FakeServiceInfo)
    monkeypatch.setattr("tools.dummy_p1_meter.discovery.Zeroconf", FakeZeroconf)
    monkeypatch.setattr(
        "tools.dummy_p1_meter.discovery._resolve_ipv4_addresses",
        lambda _: [IPv4Address("10.0.0.10")],
    )

    publisher = ZeroconfPublisher(
        host="0.0.0.0",
        port=15510,
        product_name="P1 Meter",
        product_type="HWE-P1",
        serial="P1SIMTEST",
    )

    assert publisher.start() is True
    assert len(created_infos) == len(SERVICE_TYPES)
    advertised_types = {info.kwargs["type_"] for info in created_infos}
    assert advertised_types == set(SERVICE_TYPES)
    for info in created_infos:
        assert info.kwargs["properties"] == {
            "product_name": "P1 Meter",
            "product_type": "HWE-P1",
            "serial": "P1SIMTEST",
        }

    publisher.stop()
    fake_zeroconf = zeroconf_instances[0]
    assert len(fake_zeroconf.registered) == len(SERVICE_TYPES)
    assert len(fake_zeroconf.unregistered) == len(SERVICE_TYPES)
    assert fake_zeroconf.closed is True


def test_zeroconf_publisher_handles_missing_dependency(monkeypatch) -> None:
    """Ensure publisher degrades gracefully when zeroconf isn't installed."""
    monkeypatch.setattr("tools.dummy_p1_meter.discovery.ServiceInfo", None)
    monkeypatch.setattr("tools.dummy_p1_meter.discovery.Zeroconf", None)

    publisher = ZeroconfPublisher(
        host="0.0.0.0",
        port=15510,
        product_name="P1 Meter",
        product_type="HWE-P1",
        serial="P1SIMTEST",
    )

    assert publisher.start() is False
    publisher.stop()
