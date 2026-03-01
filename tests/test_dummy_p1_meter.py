"""Tests for the dummy P1 meter API emulator."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient

from tools.dummy_p1_meter import P1Simulation, create_app

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
