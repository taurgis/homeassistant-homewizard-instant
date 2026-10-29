"""Tests for API v2 pairing, migration, discovery and identity checks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homewizard_energy.errors import (
    DisabledError,
    RequestError,
    UnauthorizedError,
    NotFoundError,
)
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homewizard_instant.config_flow import (
    async_request_token,
    async_try_connect,
    RecoverableError,
)
from custom_components.homewizard_instant.const import DOMAIN

PREFIX = "custom_components.homewizard_instant.config_flow."
DEVICE = SimpleNamespace(
    product_type="HWE-P1", product_name="P1 Meter", serial="aabbccddeeff"
)
DATA = {
    "ip_address": "1.2.3.4",
    "token": "secret",
    "product_type": "HWE-P1",
    "serial": DEVICE.serial,
}


@pytest.fixture(autouse=True)
def no_setup():
    with patch(
        "custom_components.homewizard_instant.async_setup_entry", return_value=True
    ):
        yield


@pytest.mark.parametrize(
    ("exception", "code"),
    [
        (RequestError(), "network_error"),
        (UnauthorizedError(), "invalid_auth"),
        (NotFoundError(), "unsupported_api_version"),
    ],
)
async def test_connect_errors(hass, exception, code):
    api = AsyncMock()
    api.device.side_effect = exception
    with patch(PREFIX + "HomeWizardEnergyV2", return_value=api):
        with pytest.raises(RecoverableError) as err:
            await async_try_connect(hass, "1.2.3.4", token="secret")
    assert err.value.error_code == code
    api.close.assert_awaited_once()


async def test_connect_success(hass):
    api = AsyncMock()
    api.device.return_value = DEVICE
    with patch(PREFIX + "HomeWizardEnergyV2", return_value=api) as ctor:
        assert await async_try_connect(hass, "1.2.3.4", token="secret") == DEVICE
    api.close.assert_awaited_once()
    assert ctor.call_args.kwargs["identifier"] is None


async def test_known_serial_pins_tls_hostname(hass):
    """Reauth must not send the token to a different HomeWizard certificate."""
    api = AsyncMock()
    api.device.return_value = DEVICE
    api.get_token.return_value = "secret"
    with (
        patch(PREFIX + "HomeWizardEnergyV2", return_value=api) as ctor,
        patch(PREFIX + "instance_id.async_get", return_value="abcdef1234567890"),
    ):
        await async_request_token(
            hass, "1.2.3.4", product_type="HWE-P1", serial=DEVICE.serial
        )
        await async_try_connect(
            hass,
            "1.2.3.4",
            token="secret",
            product_type="HWE-P1",
            serial=DEVICE.serial,
        )
    assert ctor.call_args.kwargs["identifier"] == "appliance/p1dongle/aabbccddeeff"


async def test_token_request_unauthorized(hass):
    api = AsyncMock()
    api.get_token.side_effect = UnauthorizedError()
    with (
        patch(PREFIX + "HomeWizardEnergyV2", return_value=api),
        patch(PREFIX + "instance_id.async_get", return_value="abcdef1234567890"),
    ):
        with pytest.raises(RecoverableError) as err:
            await async_request_token(hass, "1.2.3.4")
    assert err.value.error_code == "invalid_auth"
    api.close.assert_awaited_once()


@pytest.mark.parametrize("blocked", [False, True])
async def test_token_request(hass, blocked):
    api = AsyncMock()
    api.get_token.return_value = "secret"
    if blocked:
        api.get_token.side_effect = DisabledError()
    with (
        patch(PREFIX + "HomeWizardEnergyV2", return_value=api),
        patch(PREFIX + "instance_id.async_get", return_value="abcdef1234567890"),
    ):
        assert await async_request_token(hass, "1.2.3.4") == (
            None if blocked else "secret"
        )
    api.get_token.assert_awaited_once_with("homewizard-instant#abcdef123456")
    api.close.assert_awaited_once()


async def start_pairing(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["step_id"] == "user"
    with patch(PREFIX + "async_request_token", return_value=None) as request:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"ip_address": "1.2.3.4"}
        )
    request.assert_awaited_once()
    assert result["step_id"] == "authorize"
    return result


async def test_pairing(hass):
    result = await start_pairing(hass)
    with (
        patch(PREFIX + "async_request_token", return_value="secret"),
        patch(PREFIX + "async_try_connect", return_value=DEVICE),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == DATA
    assert result["result"].unique_id == f"{DOMAIN}_HWE-P1_{DEVICE.serial}"


@pytest.mark.parametrize(
    ("token", "error"),
    [
        (None, "authorization_failed"),
        (RecoverableError("offline", "network_error"), "network_error"),
    ],
)
async def test_pairing_retry(hass, token, error):
    result = await start_pairing(hass)
    with patch(
        PREFIX + "async_request_token",
        side_effect=token if isinstance(token, Exception) else None,
        return_value=token,
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": error}


@pytest.mark.parametrize("legacy", [False, True])
async def test_reauth_preserves_entry(hass, legacy):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"ip_address": "1.2.3.4"} if legacy else DATA,
        unique_id=f"{DOMAIN}_HWE-P1_{DEVICE.serial}",
    )
    entry.add_to_hass(hass)
    with patch(PREFIX + "async_request_token", return_value=None):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
    with (
        patch(PREFIX + "async_request_token", return_value="secret") as request,
        patch(PREFIX + "async_try_connect", return_value=DEVICE) as connect,
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert request.await_args.kwargs["serial"] == DEVICE.serial
    assert connect.await_args.kwargs["product_type"] == "HWE-P1"
    assert result["reason"] == "reauth_successful"
    assert entry.data == DATA


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
async def test_wrong_device(hass, source):
    entry = MockConfigEntry(
        domain=DOMAIN, data=DATA, unique_id=f"{DOMAIN}_HWE-P1_other"
    )
    entry.add_to_hass(hass)
    with patch(PREFIX + "async_request_token", return_value=None):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": source, "entry_id": entry.entry_id},
            data=DATA if source == "reauth" else None,
        )
    with (
        patch(PREFIX + "async_request_token", return_value="secret"),
        patch(PREFIX + "async_try_connect", return_value=DEVICE),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {} if source == "reauth" else {"ip_address": "2.3.4.5"}
        )
    assert result["reason"] == "wrong_device"
    assert entry.data == DATA


async def test_reconfigure(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data=DATA, unique_id=f"{DOMAIN}_HWE-P1_{DEVICE.serial}"
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    with (
        patch(PREFIX + "async_try_connect", return_value=DEVICE),
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"ip_address": "2.3.4.5"}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["token"] == "secret"
    assert entry.data["ip_address"] == "2.3.4.5"


async def test_duplicate(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data=DATA, unique_id=f"{DOMAIN}_HWE-P1_{DEVICE.serial}"
    )
    entry.add_to_hass(hass)
    result = await start_pairing(hass)
    with (
        patch(PREFIX + "async_request_token", return_value="secret"),
        patch(PREFIX + "async_try_connect", return_value=DEVICE),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["reason"] == "already_configured"


async def test_unsupported(hass):
    result = await start_pairing(hass)
    with (
        patch(PREFIX + "async_request_token", return_value="secret"),
        patch(
            PREFIX + "async_try_connect",
            return_value=SimpleNamespace(product_type="HWE-SKT"),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["reason"] == "device_not_supported"


@pytest.mark.parametrize("known", [False, True])
async def test_dhcp(hass, known):
    entry = MockConfigEntry(
        domain=DOMAIN, data=DATA, unique_id=f"{DOMAIN}_HWE-P1_{DEVICE.serial}"
    )
    if known:
        entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "dhcp"},
        data=SimpleNamespace(ip="2.3.4.5", macaddress="AA:BB:CC:DD:EE:FF"),
    )
    assert result["reason"] == ("already_configured" if known else "unknown_error")
    if known:
        assert entry.data["ip_address"] == "2.3.4.5"
        assert entry.data["token"] == "secret"


async def test_zeroconf(hass):
    discovery = SimpleNamespace(
        host="1.2.3.4",
        properties={
            "product_type": "HWE-P1",
            "product_name": "P1 Meter",
            "serial": DEVICE.serial,
        },
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "zeroconf"}, data=discovery
    )
    assert result["step_id"] == "discovery_confirm"
    with patch(PREFIX + "async_request_token", return_value=None) as request:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert request.await_args.kwargs["serial"] == DEVICE.serial
    assert result["step_id"] == "authorize"
