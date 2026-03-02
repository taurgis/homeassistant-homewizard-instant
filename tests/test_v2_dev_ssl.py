"""Tests for v2 dev TLS helpers."""

from __future__ import annotations

import ssl
from unittest.mock import AsyncMock, patch

from custom_components.homewizard_instant.v2_dev_ssl import (
    ALLOW_INSECURE_V2_ENV,
    InsecureHomeWizardEnergyV2,
    _env_enabled,
    _host_without_port,
    allow_insecure_v2_for_host,
)


def test_host_without_port_normalizes_variants() -> None:
    """Test host normalization removes ports and brackets consistently."""
    assert _host_without_port("LOCALHOST") == "localhost"
    assert _host_without_port("127.0.0.1:15510") == "127.0.0.1"
    assert _host_without_port("[::1]:15510") == "::1"
    assert _host_without_port("homewizard.local") == "homewizard.local"
    assert _host_without_port("homewizard:abc") == "homewizard:abc"


def test_env_enabled_truthy_values(monkeypatch) -> None:
    """Test environment flag parsing accepts known truthy values."""
    for value in ("1", "true", "yes", "on", "y"):
        monkeypatch.setenv(ALLOW_INSECURE_V2_ENV, value)
        assert _env_enabled(ALLOW_INSECURE_V2_ENV) is True



def test_allow_insecure_v2_for_host_respects_env_and_allowlist(monkeypatch) -> None:
    """Test insecure host allowance requires both env opt-in and allowlisted host."""
    monkeypatch.delenv(ALLOW_INSECURE_V2_ENV, raising=False)
    assert allow_insecure_v2_for_host("localhost") is False

    monkeypatch.setenv(ALLOW_INSECURE_V2_ENV, "true")
    assert allow_insecure_v2_for_host("localhost:15510") is True
    assert allow_insecure_v2_for_host("example.com") is False


async def test_insecure_v2_client_builds_insecure_ssl_context() -> None:
    """Test the dev v2 client builds a non-validating SSL context."""
    fake_loop = AsyncMock()
    fake_loop.run_in_executor = AsyncMock(side_effect=lambda _pool, fn: fn())

    client = InsecureHomeWizardEnergyV2.__new__(InsecureHomeWizardEnergyV2)

    with patch(
        "custom_components.homewizard_instant.v2_dev_ssl.asyncio.get_running_loop",
        return_value=fake_loop,
    ):
        context = await client._get_ssl_context()

    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE
