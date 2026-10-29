"""WebSocket protocol tests with real library model parsing."""

import json
import ssl
from unittest.mock import AsyncMock, Mock
import pytest
from aiohttp import WSMessage, WSMsgType, WSServerHandshakeError
from homewizard_energy.errors import RequestError, UnauthorizedError
from custom_components.homewizard_instant.websocket import (
    HomeWizardWebSocket,
    create_ssl_context,
)

DEVICE = {
    "product_type": "HWE-P1",
    "product_name": "P1 Meter",
    "serial": "aabbccddeeff",
    "api_version": "2.0.0",
    "firmware_version": "6.0",
}


def frame(kind, data=None):
    return WSMessage(WSMsgType.TEXT, json.dumps({"type": kind, "data": data}), None)


def client(frames):
    socket = AsyncMock()
    socket.receive.side_effect = frames
    session = Mock()
    session.ws_connect = AsyncMock(return_value=socket)
    transport = HomeWizardWebSocket(
        session,
        "192.168.1.2",
        "secret",
        "appliance/p1dongle/aabbccddeeff",
        create_ssl_context(),
    )
    return transport, session, socket


def initial():
    return [
        frame("authorization_requested", {"api_version": "2.0.0"}),
        frame("authorized"),
        frame("system", {"wifi_rssi_db": -60, "uptime_s": 10}),
        frame("device", DEVICE),
        frame("measurement", {"power_w": 100, "energy_import_kwh": 123}),
    ]


async def test_handshake_and_snapshots():
    api, session, socket = client(
        initial() + [frame("future_topic", {}), frame("measurement", {"power_w": 200})]
    )
    first = await api.connect()
    assert first.measurement.power_w == 100
    assert first.system.wifi_strength_pct == 80
    assert [call.args[0] for call in socket.send_json.call_args_list] == [
        {"type": "authorization", "data": "secret"},
        {"type": "subscribe", "data": "device"},
        {"type": "subscribe", "data": "measurement"},
        {"type": "subscribe", "data": "system"},
    ]
    second = await api.receive()
    assert second.measurement.power_w == 200
    assert first.measurement.power_w == 100
    assert second.measurement.energy_import_kwh is None
    assert second.system == first.system
    kwargs = session.ws_connect.call_args.kwargs
    assert kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert kwargs["server_hostname"] == "appliance/p1dongle/aabbccddeeff"
    await api.close()
    socket.close.assert_awaited_once()


@pytest.mark.parametrize(
    ("frames", "error"),
    [
        ([frame("unexpected")], RequestError),
        (
            [
                frame("authorization_requested"),
                frame("error", {"message": "user:unauthorized"}),
            ],
            UnauthorizedError,
        ),
        ([frame("authorization_requested"), frame("denied")], UnauthorizedError),
        ([WSMessage(WSMsgType.CLOSED, None, None)], RequestError),
        (
            [
                WSMessage(WSMsgType.TEXT, "[]", None),
                WSMessage(WSMsgType.CLOSED, None, None),
            ],
            RequestError,
        ),
        (
            [
                WSMessage(WSMsgType.TEXT, "{", None),
                WSMessage(WSMsgType.CLOSED, None, None),
            ],
            RequestError,
        ),
    ],
)
async def test_failed_handshake_closes(frames, error):
    api, _, socket = client(frames)
    with pytest.raises(error):
        await api.connect()
    socket.close.assert_awaited_once()
    assert socket.send_json.await_count <= 1


async def test_runtime_rejection():
    api, _, socket = client(
        initial() + [frame("error", {"message": "user:unauthorized"})]
    )
    await api.connect()
    with pytest.raises(UnauthorizedError):
        await api.receive()
    await api.close()


@pytest.mark.parametrize(
    "message",
    [
        frame("error", {"message": "invalid"}),
        WSMessage(WSMsgType.CLOSE, None, None),
    ],
)
async def test_bad_update(message):
    api, _, _ = client(initial() + [message])
    await api.connect()
    with pytest.raises(RequestError):
        await api.receive()
    await api.close()


async def test_reconnect_resets_snapshots():
    api, _, socket = client(initial())
    await api.connect()
    socket.receive.side_effect = initial()[:2] + [
        frame("measurement", {"power_w": 300}),
        frame("system", {}),
        frame("device", DEVICE),
    ]
    data = await api.connect()
    assert data.measurement.power_w == 300
    assert data.system.uptime_s is None
    assert socket.close.await_count == 1
    await api.close()


async def test_unusable_frames_keep_the_last_snapshot(caplog):
    api, _, socket = client(initial())
    first = await api.connect()
    socket.receive.side_effect = [
        WSMessage(WSMsgType.TEXT, "{", None),
        WSMessage(WSMsgType.TEXT, "[]", None),
        frame("measurement", None),
        frame("device", {"product_type": "HWE-P1"}),
        frame("device", {**DEVICE, "serial": None}),
        frame("future_topic", {}),
        frame("measurement", {"power_w": 250, "energy_import_kwh": 10}),
    ]
    with caplog.at_level("WARNING"):
        updated = await api.receive()
    assert updated.measurement.power_w == 250
    assert updated.measurement.energy_import_kwh == 10
    assert updated.device.serial == "aabbccddeeff"
    assert updated.system.uptime_s == 10
    assert first.measurement.power_w == 100
    socket.close.assert_not_called()
    assert "Ignoring" in caplog.text


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"power_w": None, "energy_import_kwh": 5}, {"power_w": None}),
        ({"power_w": "high", "energy_import_kwh": 5}, {"power_w": None}),
        ({"power_w": True, "energy_import_kwh": 5}, {"power_w": None}),
        (
            {"power_w": 10**12, "frequency_hz": 5000, "energy_import_kwh": 5},
            {"power_w": None, "frequency_hz": None},
        ),
        ({"external": "nope", "energy_import_kwh": 5}, {"external_devices": {}}),
    ],
)
async def test_bad_fields_only_drop_themselves(caplog, data, expected):
    """One bad field must not freeze the other sensors or drop the stream."""
    api, _, socket = client(initial())
    await api.connect()
    socket.receive.side_effect = [frame("measurement", data)]
    with caplog.at_level("WARNING"):
        updated = await api.receive()
    assert updated.measurement.energy_import_kwh == 5
    for key, value in expected.items():
        assert (getattr(updated.measurement, key) or {}) == (value or {})
    if any(value is not None for value in data.values() if value != 5):
        assert "Ignoring invalid HomeWizard measurement fields" in caplog.text
    socket.close.assert_not_called()


async def test_persistent_null_field_keeps_the_stream():
    frames = [
        frame("measurement", {"power_w": 10 + n, "power_l2_w": None}) for n in range(40)
    ]
    api, _, socket = client(initial() + frames)
    await api.connect()
    for n in range(40):
        assert (await api.receive()).measurement.power_w == 10 + n
    socket.close.assert_not_called()


async def test_bad_external_meters_are_dropped():
    gas = {
        "unique_id": "4730303031",
        "type": "gas_meter",
        "timestamp": "2026-10-29T12:00:00",
        "value": 1.5,
        "unit": "m3",
    }
    api, _, socket = client(initial())
    await api.connect()
    socket.receive.side_effect = [
        frame(
            "measurement",
            {
                "power_w": 5,
                "external": [
                    1,
                    {**gas, "value": None},
                    {**gas, "value": 10**15},
                    {**gas, "unique_id": "5730303031", "value": 2.5},
                ],
            },
        )
    ]
    updated = await api.receive()
    assert updated.measurement.power_w == 5
    assert [d.value for d in updated.measurement.external_devices.values()] == [2.5]


async def test_last_measurement_tracks_measurements_only(monkeypatch):
    import custom_components.homewizard_instant.websocket as module

    from types import SimpleNamespace

    clock = iter([50.0, 100.0, 200.0])
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    api, _, socket = client(initial())
    await api.connect()
    assert api.last_measurement == 100.0
    socket.receive.side_effect = [frame("system", {"uptime_s": 20})]
    await api.receive()
    assert api.last_measurement == 100.0
    socket.receive.side_effect = [frame("measurement", {"power_w": 1})]
    await api.receive()
    assert api.last_measurement == 200.0
    await api.close()


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, UnauthorizedError), (500, WSServerHandshakeError)],
)
async def test_websocket_handshake_status(status, error):
    session = Mock()
    info = Mock()
    info.real_url = "wss://192.0.2.1/api/ws"
    session.ws_connect = AsyncMock(
        side_effect=WSServerHandshakeError(info, (), status=status, message="no")
    )
    api = HomeWizardWebSocket(
        session,
        "192.0.2.1",
        "secret",
        "appliance/p1dongle/aabbccddeeff",
        create_ssl_context(),
    )
    with pytest.raises(error):
        await api.connect()


async def test_junk_before_handshake_is_skipped():
    api, _, _socket = client(
        [WSMessage(WSMsgType.TEXT, "{", None), *initial()]
    )
    data = await api.connect()
    assert data.measurement.power_w == 100
    await api.close()


async def test_identity_change_after_connect():
    api, _, _socket = client(initial())
    await api.connect()
    api._socket.receive.side_effect = [frame("device", {**DEVICE, "serial": "other"})]
    with pytest.raises(RequestError, match="identity changed"):
        await api.receive()
    await api.close()


async def test_invalid_tariff_keeps_the_measurement():
    """Meters sometimes report tariff 0. The library clears it and keeps power."""
    api, _, socket = client(initial())
    await api.connect()
    socket.receive.side_effect = [
        frame("measurement", {"power_w": 80, "energy_import_kwh": 4, "tariff": 0})
    ]
    updated = await api.receive()
    assert updated.measurement.power_w == 80
    assert updated.measurement.energy_import_kwh == 4
    assert updated.measurement.tariff is None
    await api.close()


async def test_low_rssi_is_a_valid_system_update():
    api, _, socket = client(initial())
    await api.connect()
    socket.receive.side_effect = [
        frame("system", {"wifi_rssi_db": -127, "uptime_s": 15, "wifi_ssid": "lab"})
    ]
    updated = await api.receive()
    assert updated.system is not None
    assert updated.system.uptime_s == 15
    assert updated.system.wifi_strength_pct == 0
    await api.close()


async def test_repeated_invalid_frames_fail_the_stream():
    from custom_components.homewizard_instant.websocket import _INVALID_LIMIT

    junk = [WSMessage(WSMsgType.TEXT, "{", None) for _ in range(_INVALID_LIMIT)]
    api, _, _socket = client(initial() + junk)
    await api.connect()
    with pytest.raises(RequestError, match="repeated invalid"):
        await api.receive()
    await api.close()


async def test_wrong_identity():
    frames = initial()
    frames[3] = frame("device", {**DEVICE, "serial": "other"})
    api, _, socket = client(frames)
    with pytest.raises(RequestError):
        await api.connect()
    socket.close.assert_awaited_once()
