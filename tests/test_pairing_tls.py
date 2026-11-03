"""Migration reauth against certificates a real P1 meter can present.

Hostname pinning maps ``ClientConnectorCertificateError`` to ``wrong_device``.
A certificate whose common name is only the serial does not verify as
``appliance/p1dongle/<serial>`` (the name this integration used to pin).
The official integration never passes that identifier: it checks the
HomeWizard CA and then the serial from ``/api``.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import sys
import threading
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from mock_device.certs import ensure_ca, hostname, issue_cert

from custom_components.homewizard_instant.const import DOMAIN

SERIAL = "aabbccddeeff"


def _enable_sockets() -> None:
    try:
        from pytest_socket import enable_socket
    except ImportError:
        return
    enable_socket()


def _restore_trust():
    import homewizard_energy.v2 as v2
    import homewizard_energy.v2.cacert as cacert_mod

    from custom_components.homewizard_instant import websocket as ws

    saved = (cacert_mod.CACERT, v2.CACERT, ws.CACERT, ws._APPLIED_DEV_CA)
    ws._APPLIED_DEV_CA = None
    return cacert_mod, v2, ws, saved


def _reset_trust(cacert_mod, v2, ws, saved) -> None:
    cacert_mod.CACERT, v2.CACERT, ws.CACERT, ws._APPLIED_DEV_CA = saved


async def _serve(cert: Path, key: Path, *, serial: str = SERIAL):
    state = {"button": False, "token": "meter-token-0123456789abcdef"}

    async def create_user(_request: web.Request) -> web.Response:
        if not state["button"]:
            return web.json_response({"error": "user:creation-not-enabled"}, status=403)
        return web.json_response({"token": state["token"]})

    async def delete_user(_request: web.Request) -> web.Response:
        return web.Response(status=204)

    async def device(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {state['token']}":
            return web.json_response({"error": "user:unauthorized"}, status=401)
        return web.json_response(
            {
                "product_name": "P1 Meter",
                "product_type": "HWE-P1",
                "serial": serial,
                "firmware_version": "6.0207",
                "api_version": "2.0.0",
            }
        )

    app = web.Application()
    app.router.add_post("/api/user", create_user)
    app.router.add_delete("/api/user", delete_user)
    app.router.add_get("/api", device)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
    await site.start()
    sockets = site._server.sockets
    assert sockets
    port = sockets[0].getsockname()[1]
    return runner, port, state


@pytest.mark.enable_socket
@pytest.mark.parametrize(
    ("common_name", "stored_serial"),
    [
        (SERIAL, SERIAL),
        (hostname(SERIAL.upper()), SERIAL.upper()),
    ],
    ids=["bare-serial-cn", "realistic-cn-and-v1-case"],
)
async def test_migration_reauth_accepts_real_device_certificates(
    hass, tmp_path, monkeypatch, caplog, common_name, stored_serial
):
    """v1 reauth pairs and keeps the stored unique id.

    ``bare-serial-cn`` is the certificate hostname pinning rejects.
    ``realistic-cn-and-v1-case`` is the common name from API discussion 214
    with the v1 unique id in a different case from the v2 serial.
    """
    _enable_sockets()
    if not Path("/usr/bin/openssl").exists():
        pytest.skip("openssl is required to mint the lab certificate")
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    ca_cert, ca_key = ensure_ca(tmp_path / "ca")
    cert, key = issue_cert(
        ca_cert,
        ca_key,
        SERIAL,
        tmp_path / "leaf",
        common_name=common_name,
    )
    if common_name == SERIAL:
        context = ssl.create_default_context(cadata=ca_cert.read_text(encoding="utf-8"))
        context.verify_flags = ssl.VERIFY_X509_PARTIAL_CHAIN
        context.hostname_checks_common_name = True
        # Pinning this certificate is the failure the owner hit.
        with pytest.raises(ssl.SSLCertVerificationError):
            _reject_pinned_name(cert, key, context, hostname(SERIAL))

    monkeypatch.setenv("HOMEWIZARD_DEV_CA", str(ca_cert))
    cacert_mod, v2, ws, saved = _restore_trust()
    runner, port, state = await _serve(cert, key)
    unique_id = f"{DOMAIN}_HWE-P1_{stored_serial}"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="P1 Meter",
        data={"ip_address": f"127.0.0.1:{port}"},
        unique_id=unique_id,
    )
    entry.add_to_hass(hass)
    try:
        with (
            caplog.at_level(logging.DEBUG),
            patch_setup(),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "reauth", "entry_id": entry.entry_id},
                data=entry.data,
            )
            assert result["step_id"] == "authorize"
            assert result.get("errors") in (None, {})
            state["button"] = True
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
            await hass.async_block_till_done()
        assert result["reason"] == "reauth_successful"
        assert entry.unique_id == unique_id
        assert entry.data["serial"] == SERIAL
        assert entry.data["token"]
        assert "wrong_device" not in caplog.text
        assert "accepted serial" in caplog.text
        assert SERIAL in caplog.text
    finally:
        await runner.cleanup()
        _reset_trust(cacert_mod, v2, ws, saved)


def _serve_in_thread(cert: Path, key: Path):
    """Serve HTTPS off the Home Assistant loop.

    A rejected handshake otherwise shows up as an unhandled connection error
    on that loop and fails the test teardown.
    """
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder: dict = {}

    def _run() -> None:
        asyncio.set_event_loop(loop)

        async def _start() -> None:
            runner, port, state = await _serve(cert, key)
            holder.update(runner=runner, port=port, state=state)

        loop.run_until_complete(_start())
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert ready.wait(5)
    return holder, loop, thread


def _stop_thread(
    _holder: dict, loop: asyncio.AbstractEventLoop, thread: threading.Thread
) -> None:
    # A failed handshake can leave the server cleanup waiting. Stopping the
    # loop is enough; the thread is a daemon.
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2)


@pytest.mark.enable_socket
async def test_tls_failure_is_a_connection_error(hass, tmp_path, monkeypatch, caplog):
    """An untrusted certificate is a connection error, with the TLS reason logged."""
    _enable_sockets()
    if not Path("/usr/bin/openssl").exists():
        pytest.skip("openssl is required to mint the lab certificate")
    monkeypatch.delenv("HOMEWIZARD_DEV_CA", raising=False)
    ca_cert, ca_key = ensure_ca(tmp_path / "untrusted")
    cert, key = issue_cert(ca_cert, ca_key, SERIAL, tmp_path / "leaf")
    holder, loop, thread = _serve_in_thread(cert, key)
    try:
        with caplog.at_level(logging.WARNING), patch_setup():
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"ip_address": f"127.0.0.1:{holder['port']}"}
            )
        assert result["step_id"] == "user"
        assert result["errors"]["base"] == "network_error"
        assert "user:authorize" in caplog.text
        assert "ClientConnectorCertificateError" in caplog.text
        assert "CERTIFICATE_VERIFY_FAILED" in caplog.text
        assert "wrong_device" not in caplog.text
    finally:
        _stop_thread(holder, loop, thread)


def patch_setup():
    from unittest.mock import patch

    return patch(
        "custom_components.homewizard_instant.async_setup_entry",
        return_value=True,
    )


def _reject_pinned_name(cert, key, context, server_hostname: str) -> None:
    import socket
    import threading

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)

    def _run() -> None:
        incoming, _address = server.accept()
        wrapped = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        wrapped.load_cert_chain(cert, key)
        try:
            peer = wrapped.wrap_socket(incoming, server_side=True)
            peer.recv(8)
            peer.close()
        except ssl.SSLError:
            incoming.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    try:
        client = context.wrap_socket(
            socket.create_connection(("127.0.0.1", port), timeout=5),
            server_hostname=server_hostname,
        )
        client.close()
    finally:
        thread.join(timeout=5)
        server.close()
