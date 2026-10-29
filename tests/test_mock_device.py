"""Emulated P1 meter pairing, snapshots and fault payloads."""

from __future__ import annotations

import asyncio
import socket
import ssl
import sys
import threading
from pathlib import Path

import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from mock_device.certs import ensure_ca, hostname, issue_cert
from mock_device.meter import Meter
from mock_device.server import MeterServer


def _enable_sockets() -> None:
    """pytest-socket's marker is undone by the Home Assistant plugin hook."""
    try:
        from pytest_socket import enable_socket
    except ImportError:
        return
    enable_socket()


def test_button_then_token_and_fault_payloads() -> None:
    meter = Meter("AABBCCDDEEFF", "3phase")
    status, body = meter.issue_token("local/homewizard-instant#abcdef123456")
    assert status == 403
    assert body["error"] == "user:creation-not-enabled"
    meter.press_button()
    status, body = meter.issue_token("local/homewizard-instant#abcdef123456")
    assert status == 200
    assert len(body["token"]) == 32
    assert meter.authorized(f"Bearer {body['token']}")
    meter.steady = True
    reading = meter.measurement_payload()
    assert reading["power_l1_w"] == 400
    assert reading["external"][0]["type"] == "gas_meter"
    assert reading["external"][1]["type"] == "water_meter"
    meter.set_profile("export")
    exporting = meter.measurement_payload()
    assert exporting["power_w"] < 0
    assert exporting["current_l1_a"] < 0
    assert exporting["current_l2_a"] > 0
    meter.set_profile("1phase")
    single = meter.measurement_payload()
    assert "power_l2_w" not in single
    assert single["power_l1_w"] > 0
    other = Meter("112233445566", "3phase")
    assert single["external"][0]["unique_id"] != other.measurement_payload()["external"][0]["unique_id"]
    meter.apply_fault("malformed_json")
    assert isinstance(meter.fault_message(), str)
    meter.clear_faults()
    meter.apply_fault("null_fields")
    assert meter.fault_message()["data"]["power_w"] is None
    meter.clear_faults()
    meter.apply_fault("identity_change")
    assert meter.device_payload()["serial"] != meter.serial
    first = body["token"]
    status, body = meter.issue_token("local/homewizard-instant#abcdef123456")
    assert status == 200
    assert body["token"] != first
    assert meter.authorized(f"Bearer {first}") is False
    meter.button_until = 0
    status, expired = meter.issue_token("local/homewizard-instant#abcdef123456")
    assert status == 403
    assert expired["error"] == "user:creation-not-enabled"
    meter.revoke()
    assert meter.authorized(f"Bearer {body['token']}") is False


@pytest.mark.enable_socket
async def test_http_pairing_and_websocket_snapshots() -> None:
    _enable_sockets()
    meter = Meter("112233445566", "1phase", push_interval=30)
    server = MeterServer(meter, Path("/tmp"), Path("/tmp"))
    async with (
        TestClient(TestServer(server.control_app())) as control,
        TestClient(TestServer(server.https_app())) as api,
    ):
        blocked = await api.post("/api/user", json={"name": "local/app"})
        assert blocked.status == 403
        assert (await control.post("/button")).status == 200
        paired = await api.post("/api/user", json={"name": "local/app"})
        token = (await paired.json())["token"]
        denied = await api.get("/api")
        assert denied.status == 401
        device = await api.get("/api", headers={"Authorization": f"Bearer {token}"})
        assert (await device.json())["serial"] == "112233445566"
        async with api.ws_connect("/api/ws") as websocket:
            assert (await websocket.receive_json())["type"] == "authorization_requested"
            await websocket.send_json({"type": "authorization", "data": token})
            assert (await websocket.receive_json())["type"] == "authorized"
            seen: dict[str, dict] = {}
            for topic in ("device", "measurement", "system"):
                await websocket.send_json({"type": "subscribe", "data": topic})
                frame = await websocket.receive_json()
                seen[frame["type"]] = frame["data"]
        assert seen["device"]["product_type"] == "HWE-P1"
        assert seen["measurement"]["power_w"] == 678
        assert "power_l2_w" not in seen["measurement"]
        assert seen["system"]["wifi_rssi_db"] == -60
        state = await (await control.get("/state")).json()
        assert state["websockets"] == 0

        async with api.ws_connect("/api/ws") as websocket:
            await websocket.receive_json()
            await websocket.send_json({"type": "subscribe", "data": token})
            rejected = await websocket.receive_json()
            assert rejected["type"] == "error"
            assert rejected["data"]["message"] == "user:unauthorized"


@pytest.mark.enable_socket
async def test_socket_waits_for_subscribe_and_auth_timeout() -> None:
    """Pushes start after subscribe, and a silent client is dropped at 40s."""
    _enable_sockets()
    meter = Meter("aabbccddeeff", "3phase", push_interval=0.05)
    meter.auth_timeout = 0.05
    meter.press_button()
    _status, body = meter.issue_token("local/app")
    server = MeterServer(meter, Path("/tmp"), Path("/tmp"))
    async with TestClient(TestServer(server.https_app())) as api:
        async with api.ws_connect("/api/ws") as quiet:
            assert (await quiet.receive_json())["type"] == "authorization_requested"
            closing = await quiet.receive()
            assert closing.type in {WSMsgType.CLOSE, WSMsgType.CLOSED}
        meter.auth_timeout = 5
        async with api.ws_connect("/api/ws") as websocket:
            await websocket.receive_json()
            await websocket.send_json({"type": "authorization", "data": body["token"]})
            assert (await websocket.receive_json())["type"] == "authorized"
            try:
                await asyncio.wait_for(websocket.receive(), 0.2)
            except TimeoutError:
                return
            raise AssertionError("the meter pushed a topic before subscribe")


@pytest.mark.enable_socket
def test_device_certificate_matches_appliance_hostname(tmp_path: Path) -> None:
    _enable_sockets()
    if not Path("/usr/bin/openssl").exists():
        pytest.skip("openssl is required to mint the lab certificate")
    ca_cert, ca_key = ensure_ca(tmp_path / "ca")
    serial = "aabbccddeeff"
    cert, key = issue_cert(ca_cert, ca_key, serial, tmp_path / "leaf")
    context = ssl.create_default_context(cadata=ca_cert.read_text(encoding="utf-8"))
    context.verify_flags = ssl.VERIFY_X509_PARTIAL_CHAIN
    context.hostname_checks_common_name = True
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)

    def _serve() -> None:
        incoming, _address = server.accept()
        wrapped = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        wrapped.load_cert_chain(cert, key)
        try:
            peer = wrapped.wrap_socket(incoming, server_side=True)
            peer.recv(16)
            peer.close()
        except ssl.SSLError:
            incoming.close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    client = context.wrap_socket(
        socket.create_connection(("127.0.0.1", port), timeout=5),
        server_hostname=hostname(serial),
    )
    client.send(b"ping")
    client.close()
    thread.join(timeout=5)
    server.close()
