"""WebSocket protocol tests with real library model parsing."""

import json
import ssl
from unittest.mock import AsyncMock, Mock
import pytest
from aiohttp import WSMessage, WSMsgType
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
        ([WSMessage(WSMsgType.TEXT, "[]", None)], RequestError),
        ([WSMessage(WSMsgType.TEXT, "{", None)], ValueError),
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
        frame("measurement", None),
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


async def test_wrong_identity():
    frames = initial()
    frames[3] = frame("device", {**DEVICE, "serial": "other"})
    api, _, socket = client(frames)
    with pytest.raises(RequestError):
        await api.connect()
    socket.close.assert_awaited_once()
