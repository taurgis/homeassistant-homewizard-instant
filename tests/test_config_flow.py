"""Tests for config flow."""

from __future__ import annotations

from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homewizard_energy.const import Model

from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.components.dhcp import DhcpServiceInfo
from homeassistant.components.zeroconf import ZeroconfServiceInfo

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_instant.config_flow import RecoverableError, async_try_connect
from custom_components.homewizard_instant.const import (
    CONF_PRODUCT_NAME,
    CONF_PRODUCT_TYPE,
    CONF_SERIAL,
    DOMAIN,
)


async def test_async_try_connect_success(hass, mock_device_info) -> None:
    """Test async_try_connect returns device info and closes session."""
    mock_api = AsyncMock()
    mock_api.device = AsyncMock(return_value=mock_device_info)
    mock_api.close = AsyncMock()

    with patch(
        "custom_components.homewizard_instant.config_flow.HomeWizardEnergyV1",
        return_value=mock_api,
    ):
        result = await async_try_connect(hass, "1.2.3.4", clientsession=AsyncMock())

    assert result == mock_device_info
    mock_api.close.assert_awaited_once()


async def test_async_try_connect_disabled_error(hass) -> None:
    """Test async_try_connect maps DisabledError to RecoverableError."""
    from homewizard_energy.errors import DisabledError

    mock_api = AsyncMock()
    mock_api.device = AsyncMock(side_effect=DisabledError("disabled"))
    mock_api.close = AsyncMock()

    with patch(
        "custom_components.homewizard_instant.config_flow.HomeWizardEnergyV1",
        return_value=mock_api,
    ):
        with pytest.raises(RecoverableError) as err:
            await async_try_connect(hass, "1.2.3.4", clientsession=AsyncMock())

    assert err.value.error_code == "api_not_enabled"
    mock_api.close.assert_awaited_once()


async def test_async_try_connect_request_error(hass) -> None:
    """Test async_try_connect maps RequestError to RecoverableError."""
    from homewizard_energy.errors import RequestError

    mock_api = AsyncMock()
    mock_api.device = AsyncMock(side_effect=RequestError("boom"))
    mock_api.close = AsyncMock()

    with patch(
        "custom_components.homewizard_instant.config_flow.HomeWizardEnergyV1",
        return_value=mock_api,
    ):
        with pytest.raises(RecoverableError) as err:
            await async_try_connect(hass, "1.2.3.4", clientsession=AsyncMock())

    assert err.value.error_code == "network_error"
    mock_api.close.assert_awaited_once()


async def test_async_try_connect_unexpected_error(hass) -> None:
    """Test async_try_connect aborts on unexpected error."""
    mock_api = AsyncMock()
    mock_api.device = AsyncMock(side_effect=Exception("boom"))
    mock_api.close = AsyncMock()

    from homeassistant.data_entry_flow import AbortFlow

    with patch(
        "custom_components.homewizard_instant.config_flow.HomeWizardEnergyV1",
        return_value=mock_api,
    ):
        with pytest.raises(AbortFlow):
            await async_try_connect(hass, "1.2.3.4", clientsession=AsyncMock())

    mock_api.close.assert_awaited_once()


async def test_user_flow_success(hass, mock_device_info) -> None:
    """Test user flow success."""
    with patch(
        "custom_components.homewizard_instant.async_setup_entry",
        new=AsyncMock(return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == FlowResultType.FORM

        with patch(
            "custom_components.homewizard_instant.config_flow.async_try_connect",
            return_value=mock_device_info,
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_IP_ADDRESS: "1.2.3.4"}
            )

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "P1 Meter"
    assert result2["data"] == {CONF_IP_ADDRESS: "1.2.3.4"}


async def test_user_flow_device_not_supported(hass) -> None:
    """Test user flow aborts for unsupported device."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    unsupported = SimpleNamespace(
        product_type="other",
        product_name="Other",
        serial="SERIAL",
    )

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        return_value=unsupported,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_IP_ADDRESS: "1.2.3.4"}
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "device_not_supported"


async def test_user_flow_recoverable_error(hass) -> None:
    """Test user flow handles recoverable errors."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        side_effect=RecoverableError("boom", "network_error"),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_IP_ADDRESS: "1.2.3.4"}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "network_error"}


async def test_user_flow_already_configured(hass, mock_device_info) -> None:
    """Test user flow aborts when already configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_IP_ADDRESS: "1.2.3.4"},
        unique_id=f"{DOMAIN}_{Model.P1_METER}_SERIAL123",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    mock_device_info.serial = "SERIAL123"
    mock_device_info.product_type = Model.P1_METER

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        return_value=mock_device_info,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_IP_ADDRESS: "1.2.3.4"}
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_zeroconf_invalid_parameters(hass) -> None:
    """Test zeroconf aborts on invalid parameters."""
    discovery_info = ZeroconfServiceInfo(
        ip_address=ip_address("1.2.3.4"),
        ip_addresses=[ip_address("1.2.3.4")],
        port=80,
        hostname="hw.local.",
        type="_hwenergy._tcp.local.",
        name="hwenergy",
        properties={},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_parameters"


async def test_zeroconf_discovery_confirm(hass) -> None:
    """Test zeroconf discovery confirm form."""
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
            CONF_SERIAL: "SERIAL",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"


async def test_discovery_confirm_creates_entry(hass, mock_device_info) -> None:
    """Test discovery confirm creates entry."""
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
            CONF_SERIAL: "SERIAL",
        },
    )

    with patch(
        "custom_components.homewizard_instant.async_setup_entry",
        new=AsyncMock(return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "zeroconf"}, data=discovery_info
        )

        with patch(
            "custom_components.homewizard_instant.config_flow.async_try_connect",
            return_value=mock_device_info,
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"], {}
            )

    assert result2["type"] == FlowResultType.CREATE_ENTRY


async def test_zeroconf_device_not_supported(hass) -> None:
    """Test zeroconf aborts when device not supported."""
    discovery_info = ZeroconfServiceInfo(
        ip_address=ip_address("1.2.3.4"),
        ip_addresses=[ip_address("1.2.3.4")],
        port=80,
        hostname="hw.local.",
        type="_hwenergy._tcp.local.",
        name="hwenergy",
        properties={
            CONF_PRODUCT_NAME: "Other",
            CONF_PRODUCT_TYPE: "other",
            CONF_SERIAL: "SERIAL",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovery_info
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "device_not_supported"


async def test_reauth_flow_success(hass, mock_config_entry, mock_device_info) -> None:
    """Test reauth flow reloads entry."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": mock_config_entry.entry_id},
        data=mock_config_entry.data,
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_enable_api"

    hass.config_entries.async_reload = AsyncMock()

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        return_value=mock_device_info,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_enable_api_successful"
    hass.config_entries.async_reload.assert_awaited_once_with(mock_config_entry.entry_id)


async def test_reauth_flow_error(hass, mock_config_entry) -> None:
    """Test reauth flow shows form on error."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": mock_config_entry.entry_id},
        data=mock_config_entry.data,
    )

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        side_effect=RecoverableError("boom", "network_error"),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "network_error"}


async def test_reconfigure_flow_updates_entry(hass, mock_config_entry, mock_device_info):
    """Test reconfigure flow updates entry."""
    mock_config_entry.add_to_hass(hass)

    hass.config_entries.async_reload = AsyncMock()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )

    assert result["type"] == FlowResultType.FORM

    device_info = SimpleNamespace(
        product_type="P1",
        product_name="P1 Meter",
        serial="SERIAL123",
    )

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        return_value=device_info,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_IP_ADDRESS: "2.3.4.5"}
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"


async def test_reconfigure_flow_wrong_device(hass, mock_config_entry):
    """Test reconfigure flow aborts on wrong device."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": mock_config_entry.entry_id},
        data=None,
    )

    device_info = SimpleNamespace(
        product_type="P1",
        product_name="P1 Meter",
        serial="OTHER",
    )

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        return_value=device_info,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_IP_ADDRESS: "2.3.4.5"}
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "wrong_device"


async def test_dhcp_unknown_device(hass) -> None:
    """Test dhcp flow aborts on unknown device."""
    discovery_info = DhcpServiceInfo(
        ip="1.2.3.4",
        hostname="hw",
        macaddress="AA:BB:CC:DD:EE:FF",
    )

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        side_effect=RecoverableError("boom", "network_error"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=discovery_info
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unknown"


async def test_dhcp_device_not_supported(hass) -> None:
    """Test dhcp flow aborts on unsupported device."""
    discovery_info = DhcpServiceInfo(
        ip="1.2.3.4",
        hostname="hw",
        macaddress="AA:BB:CC:DD:EE:FF",
    )

    device_info = SimpleNamespace(
        product_type="other",
        product_name="Other",
        serial="SERIAL",
    )

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        return_value=device_info,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=discovery_info
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "device_not_supported"


async def test_dhcp_serial_missing_aborts_unknown(hass) -> None:
    """Test dhcp flow aborts when serial is missing."""
    discovery_info = DhcpServiceInfo(
        ip="1.2.3.4",
        hostname="hw",
        macaddress="AA:BB:CC:DD:EE:FF",
    )

    device_info = SimpleNamespace(
        product_type=Model.P1_METER,
        product_name="P1 Meter",
        serial=None,
    )

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        return_value=device_info,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=discovery_info
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unknown"


async def test_discovery_confirm_error(hass) -> None:
    """Test discovery confirm shows errors on connection failure."""
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
            CONF_SERIAL: "SERIAL",
        },
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovery_info
    )

    with patch(
        "custom_components.homewizard_instant.config_flow.async_try_connect",
        side_effect=RecoverableError("boom", "network_error"),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "network_error"}
