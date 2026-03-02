"""Edge-case tests for config flow branches and error handling."""

from __future__ import annotations

from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homewizard_energy.const import Model
from homewizard_energy.errors import UnauthorizedError

from homeassistant.components.dhcp import DhcpServiceInfo
from homeassistant.components.zeroconf import ZeroconfServiceInfo
from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_instant.config_flow import (
    HomeWizardConfigFlow,
    RecoverableError,
)
from custom_components.homewizard_instant.const import (
    CONF_PRODUCT_NAME,
    CONF_PRODUCT_TYPE,
    CONF_SERIAL,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def mock_has_v2_api_false() -> None:
    """Default tests to v1 behavior unless explicitly overridden."""
    with patch(
        "custom_components.homewizard_instant.config_flow.has_v2_api",
        new=AsyncMock(return_value=False),
    ):
        yield


def _supported_device(serial: str = "SERIAL123") -> SimpleNamespace:
    """Build a supported P1 device payload for config flow tests."""
    return SimpleNamespace(
        product_type=Model.P1_METER,
        product_name="P1 Meter",
        serial=serial,
    )


async def _init_authorize_step(hass) -> str:
    """Start a user flow and transition it to the authorize step."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        side_effect=UnauthorizedError("unauthorized"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_IP_ADDRESS: "1.2.3.4"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "authorize"
    return result["flow_id"]


def test_token_for_discovery_mac_skips_invalid_unique_ids(hass) -> None:
    """Test token lookup ignores entries without a parseable unique ID."""
    no_unique_id = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_IP_ADDRESS: "1.2.3.4", CONF_TOKEN: "ignored-token"},
        unique_id=None,
    )
    wrong_format = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_IP_ADDRESS: "1.2.3.5", CONF_TOKEN: "ignored-token-2"},
        unique_id="invalid-format",
    )
    no_unique_id.add_to_hass(hass)
    wrong_format.add_to_hass(hass)

    flow = HomeWizardConfigFlow()
    flow.hass = hass

    assert flow._token_for_discovery_mac("AA:BB:CC:DD:EE:FF") is None


async def test_authorize_step_aborts_when_ip_missing(hass) -> None:
    """Test authorize step aborts when no IP has been set in flow state."""
    flow = HomeWizardConfigFlow()
    flow.hass = hass

    result = await flow.async_step_authorize(user_input={})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unknown_error"


async def test_authorize_step_shows_error_when_token_not_granted(hass) -> None:
    """Test authorize step shows authorization_failed when token request returns None."""
    flow_id = await _init_authorize_step(hass)

    with patch(
        "custom_components.homewizard_instant.config_flow.async_request_token",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "authorize"
    assert result["errors"] == {"base": "authorization_failed"}


@pytest.mark.parametrize(
    ("connect_side_effect", "expected_error"),
    [
        (RecoverableError("boom", "network_error"), "network_error"),
        (UnauthorizedError("unauthorized"), "authorization_failed"),
    ],
    ids=["recoverable", "unauthorized"],
)
async def test_authorize_step_handles_connect_failures(
    hass, connect_side_effect, expected_error
) -> None:
    """Test authorize step maps post-token connection failures to form errors."""
    flow_id = await _init_authorize_step(hass)

    with (
        patch(
            "custom_components.homewizard_instant.config_flow.async_request_token",
            new=AsyncMock(return_value="token123"),
        ),
        patch(
            "custom_components.homewizard_instant.config_flow.async_try_connect",
            side_effect=connect_side_effect,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "authorize"
    assert result["errors"] == {"base": expected_error}


async def test_authorize_step_aborts_when_device_not_supported(hass) -> None:
    """Test authorize step aborts when tokenized device is not a supported model."""
    flow_id = await _init_authorize_step(hass)

    unsupported_device = SimpleNamespace(
        product_type="other",
        product_name="Other",
        serial="SERIAL123",
    )

    with (
        patch(
            "custom_components.homewizard_instant.config_flow.async_request_token",
            new=AsyncMock(return_value="token123"),
        ),
        patch(
            "custom_components.homewizard_instant.config_flow.async_try_connect",
            new=AsyncMock(return_value=unsupported_device),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(flow_id, {})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "device_not_supported"


async def test_dhcp_valid_unknown_device_aborts_unknown_error(hass) -> None:
    """Test DHCP discovery aborts unknown_error for non-existing matching entries."""
    discovery_info = DhcpServiceInfo(
        ip="1.2.3.4",
        hostname="hw",
        macaddress="AA:BB:CC:DD:EE:FF",
    )

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        new=AsyncMock(return_value=_supported_device()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=discovery_info
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unknown_error"


async def test_discovery_confirm_unauthorized_routes_to_authorize(hass) -> None:
    """Test discovery confirmation switches to authorize flow on UnauthorizedError."""
    discovery_info = ZeroconfServiceInfo(
        ip_address=ip_address("1.2.3.4"),
        ip_addresses=[ip_address("1.2.3.4")],
        port=80,
        hostname="hw.local.",
        type="_hwenergy._tcp.local.",
        name="hwenergy",
        properties={
            CONF_PRODUCT_NAME: "P1 Meter",
            CONF_PRODUCT_TYPE: Model.P1_METER,
            CONF_SERIAL: "SERIAL123",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovery_info
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        side_effect=UnauthorizedError("unauthorized"),
    ):
        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "authorize"


async def test_reauth_confirm_update_token_aborts_when_ip_missing(hass) -> None:
    """Test reauth token refresh aborts when flow state misses IP address."""
    flow = HomeWizardConfigFlow()
    flow.hass = hass

    result = await flow.async_step_reauth_confirm_update_token(user_input={})

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unknown_error"


async def test_reauth_confirm_update_token_shows_authorization_failed(hass) -> None:
    """Test reauth token refresh shows authorization_failed when token is unavailable."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_IP_ADDRESS: "1.2.3.4", CONF_TOKEN: "old-token"},
        unique_id=f"{DOMAIN}_{Model.P1_METER}_SERIAL123",
        title="P1 Meter",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm_update_token"

    with patch(
        "custom_components.homewizard_instant.config_flow.async_request_token",
        new=AsyncMock(return_value=None),
    ):
        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "reauth_confirm_update_token"
    assert result2["errors"] == {"base": "authorization_failed"}


@pytest.mark.parametrize(
    ("connect_side_effect", "expected_error"),
    [
        (RecoverableError("boom", "network_error"), "network_error"),
        (UnauthorizedError("unauthorized"), "authorization_failed"),
    ],
    ids=["recoverable", "unauthorized"],
)
async def test_reconfigure_flow_maps_connection_errors(
    hass, connect_side_effect, expected_error
) -> None:
    """Test reconfigure step maps connectivity/auth failures to user-facing form errors."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_IP_ADDRESS: "1.2.3.4"},
        unique_id=f"{DOMAIN}_{Model.P1_METER}_SERIAL123",
        title="P1 Meter",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
        data=None,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        side_effect=connect_side_effect,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_IP_ADDRESS: "2.3.4.5"}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "reconfigure"
    assert result2["errors"] == {"base": expected_error}
