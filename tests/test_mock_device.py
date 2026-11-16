"""Emulated P1 meter pairing, snapshots and fault payloads."""

from __future__ import annotations

import asyncio
import socket
import ssl
import subprocess
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
    meter.clear_faults()
    last = meter.topic_payload("measurement")
    meter.apply_fault("silent")
    assert meter.topic_payload("measurement") == last
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


def _certificate_text(cert: Path) -> str:
    """Return the subject in RFC2253 form, which does not depend on OpenSSL's default spacing.

    OpenSSL 3.0 prints ``CN = name``. OpenSSL 3.2 and later print ``CN=name``.
    ``-nameopt RFC2253`` is the same string on both.
    """
    completed = subprocess.run(
        [
            "openssl",
            "x509",
            "-in",
            str(cert),
            "-noout",
            "-subject",
            "-nameopt",
            "RFC2253",
            "-ext",
            "subjectAltName",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout + completed.stderr


def _handshake(cert: Path, key: Path, ca_cert: Path, server_hostname: str) -> None:
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
    try:
        client = context.wrap_socket(
            socket.create_connection(("127.0.0.1", port), timeout=5),
            server_hostname=server_hostname,
        )
        client.send(b"ping")
        client.close()
    finally:
        thread.join(timeout=5)
        server.close()


@pytest.mark.enable_socket
def test_device_certificate_matches_a_real_p1_meter(tmp_path: Path) -> None:
    """Lab leaves match a real P1 cert: appliance CN, no subjectAltName.

    A certificate whose common name is only the serial does not verify as
    ``appliance/p1dongle/<serial>``. Hostname pinning rejects that meter.
    """
    _enable_sockets()
    if not Path("/usr/bin/openssl").exists():
        pytest.skip("openssl is required to mint the lab certificate")
    ca_cert, ca_key = ensure_ca(tmp_path / "ca")
    serial = "aabbccddeeff"
    cert, key = issue_cert(ca_cert, ca_key, serial, tmp_path / "leaf")
    text = _certificate_text(cert)
    assert f"CN={hostname(serial)}" in text
    assert "Subject Alternative Name" not in text
    assert "DNS:" not in text
    _handshake(cert, key, ca_cert, hostname(serial))

    bare, bare_key = issue_cert(
        ca_cert,
        ca_key,
        serial,
        tmp_path / "bare",
        name="bare",
        common_name=serial,
    )
    with pytest.raises(ssl.SSLCertVerificationError, match="Hostname mismatch"):
        _handshake(bare, bare_key, ca_cert, hostname(serial))


# Every field a physical SDM630-wifi (firmware 5.0305) sent in its measurements.
SDM630_FIELDS = {
    "energy_import_kwh",
    "energy_export_kwh",
    "power_w",
    *(f"power_l{phase}_w" for phase in (1, 2, 3)),
    *(f"voltage_l{phase}_v" for phase in (1, 2, 3)),
    "current_a",
    *(f"current_l{phase}_a" for phase in (1, 2, 3)),
    "apparent_current_a",
    *(f"apparent_current_l{phase}_a" for phase in (1, 2, 3)),
    "reactive_current_a",
    *(f"reactive_current_l{phase}_a" for phase in (1, 2, 3)),
    "apparent_power_va",
    *(f"apparent_power_l{phase}_va" for phase in (1, 2, 3)),
    "reactive_power_var",
    *(f"reactive_power_l{phase}_var" for phase in (1, 2, 3)),
    *(f"power_factor_l{phase}" for phase in (1, 2, 3)),
    "frequency_hz",
}
# The 1-phase kWh meter fields in the HomeWizard API v2 measurement docs.
KWH1_FIELDS = {
    "energy_import_kwh",
    "energy_export_kwh",
    "power_w",
    "voltage_v",
    "current_a",
    "apparent_current_a",
    "reactive_current_a",
    "apparent_power_va",
    "reactive_power_var",
    "power_factor",
    "frequency_hz",
}


@pytest.mark.parametrize("steady", [True, False])
def test_kwh_meter_matches_a_real_sdm630(steady: bool) -> None:
    meter = Meter("5c2faf000001", "kwh3")
    meter.steady = steady
    assert meter.device_payload() == {
        "product_type": "SDM630-wifi",
        "product_name": "kWh Meter 3-phase",
        "serial": "5c2faf000001",
        "firmware_version": "5.0305",
        "api_version": "2.3.0",
    }
    reading = meter.measurement_payload()
    # No smart-meter clock, tariff, DSMR counters or external meters.
    assert set(reading) == SDM630_FIELDS
    assert reading["energy_import_kwh"] == 0
    assert "status_led_brightness_pct" not in meter.system_payload()

    meter.set_profile("kwh3_solar")
    assert set(meter.measurement_payload()) == SDM630_FIELDS
    assert meter.measurement_payload()["power_w"] < 0
    with pytest.raises(ValueError, match="HWE-P1"):
        meter.set_profile("3phase")
    meter.apply_fault("missing_fields")
    assert meter.fault_message()["data"]["product_type"] == "SDM630-wifi"


def test_kwh1_has_the_documented_fields() -> None:
    meter = Meter("5c2faf000002", "kwh1")
    assert meter.device_payload()["product_type"] == "HWE-KWH1"
    assert set(meter.measurement_payload()) == KWH1_FIELDS
    with pytest.raises(ValueError, match="SDM630-wifi"):
        meter.set_profile("kwh3")


def test_p1_meter_keeps_its_identity_and_clock() -> None:
    meter = Meter("aabbccddeeff", "3phase")
    assert meter.device_payload()["product_type"] == "HWE-P1"
    assert "timestamp" in meter.measurement_payload()
    assert meter.system_payload()["status_led_brightness_pct"] == 80
    with pytest.raises(ValueError, match="SDM630-wifi"):
        meter.set_profile("kwh3")


def test_kwh_meter_certificate_name(tmp_path: Path) -> None:
    name = hostname("5c2faf000001", "energymeter")
    assert name == "appliance/energymeter/5c2faf000001"
    server = MeterServer(Meter("5c2faf000001", "kwh3"), tmp_path / "ca", tmp_path)
    server.build_contexts()
    subject = ssl._ssl._test_decode_cert(str(tmp_path / "device.pem"))["subject"]
    assert (("commonName", "appliance/energymeter/5c2faf000001"),) in subject


async def test_integration_parses_the_kwh_stream(caplog) -> None:
    """Every captured field passes validation; extra current fields are ignored."""
    from test_websocket import client, frame

    meter = Meter("5c2faf000001", "kwh3")
    meter.steady = False
    frames = [
        frame("authorization_requested", {"api_version": "2.0.0"}),
        frame("authorized"),
        frame("device", meter.device_payload()),
        frame("system", meter.system_payload()),
        frame("measurement", meter.measurement_payload()),
        frame("measurement", meter.measurement_payload()),
    ]
    api, _, _ = client(frames, serial=meter.serial)
    with caplog.at_level("WARNING"):
        first = await api.connect()
        second = await api.receive()
    assert first.device.product_type == "SDM630-wifi"
    assert second.measurement.voltage_l3_v == 225.772
    assert second.measurement.reactive_power_l1_var == -75.285
    assert second.measurement.energy_import_kwh == 0
    assert second.measurement.tariff is None
    assert second.measurement.timestamp is None
    assert "Ignoring" not in caplog.text
    await api.close()
