"""Config flow for HomeWizard."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from homewizard_energy import (
    HomeWizardEnergy,
    HomeWizardEnergyV1,
    HomeWizardEnergyV2,
    has_v2_api,
)
from homewizard_energy.const import Model
from homewizard_energy.errors import DisabledError, RequestError, UnauthorizedError
from homewizard_energy.models import Device
import voluptuous as vol

from homeassistant.components import onboarding
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import instance_id
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from aiohttp import ClientSession
from homeassistant.helpers.selector import TextSelector
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import CONF_PRODUCT_NAME, CONF_PRODUCT_TYPE, CONF_SERIAL, DOMAIN, LOGGER
from .v2_dev_ssl import InsecureHomeWizardEnergyV2, allow_insecure_v2_for_host

# Only support P1 meter
SUPPORTED_PRODUCT_TYPES = [Model.P1_METER]


class HomeWizardConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for P1 meter."""

    VERSION = 1

    ip_address: str | None = None
    product_name: str | None = None
    product_type: str | None = None
    serial: str | None = None

    @staticmethod
    def _normalize_serial(value: str) -> str:
        """Normalize serial/MAC strings for matching."""
        return value.replace(":", "").replace("-", "").lower()

    @staticmethod
    def _serial_from_unique_id(unique_id: str | None) -> str | None:
        """Extract serial part from a DOMAIN-prefixed unique id."""
        if unique_id is None:
            return None

        prefix = f"{DOMAIN}_"
        if not unique_id.startswith(prefix):
            return None

        raw_suffix = unique_id.removeprefix(prefix)
        if "_" not in raw_suffix:
            return None

        _product_type, serial = raw_suffix.rsplit("_", 1)
        return serial

    def _token_for_discovery_mac(self, mac_address: str) -> str | None:
        """Return token for an already-known entry matching discovered MAC/serial."""
        normalized_mac = self._normalize_serial(mac_address)
        for entry in self._async_current_entries():
            entry_serial = self._serial_from_unique_id(entry.unique_id)
            if entry_serial is None:
                continue

            if self._normalize_serial(entry_serial) != normalized_mac:
                continue

            token = entry.data.get(CONF_TOKEN)
            if isinstance(token, str):
                return token

        return None

    @staticmethod
    def _entry_unique_id(product_type: str, serial: str) -> str:
        """Build the integration unique id for a discovered device."""
        return f"{DOMAIN}_{product_type}_{serial}"

    @staticmethod
    def _device_validation_error(device_info: Device) -> str | None:
        """Return abort reason when a discovered device is not valid."""
        if device_info.product_type not in SUPPORTED_PRODUCT_TYPES:
            return "device_not_supported"

        if device_info.serial is None:
            return "unknown_error"

        return None

    async def _async_validate_and_set_unique_id(
        self, device_info: Device
    ) -> ConfigFlowResult | None:
        """Validate device compatibility and set the flow unique id."""
        if (abort_reason := self._device_validation_error(device_info)) is not None:
            return self.async_abort(reason=abort_reason)

        serial = device_info.serial
        if serial is None:
            return self.async_abort(reason="unknown_error")

        await self.async_set_unique_id(
            self._entry_unique_id(device_info.product_type, serial)
        )
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""
        errors: dict[str, str] | None = None
        if user_input is not None:
            try:
                device_info = await async_try_connect(self.hass, user_input[CONF_IP_ADDRESS])
            except RecoverableError as ex:
                LOGGER.debug("User step connection check failed: %s", ex)
                errors = {"base": ex.error_code}
            except UnauthorizedError:
                self.ip_address = user_input[CONF_IP_ADDRESS]
                return await self.async_step_authorize()
            else:
                if (result := await self._async_validate_and_set_unique_id(device_info)) is not None:
                    return result

                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(
                    title=f"{device_info.product_name}",
                    data=user_input,
                )

        user_input = user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_IP_ADDRESS, default=user_input.get(CONF_IP_ADDRESS)
                    ): TextSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Request and confirm a v2 authorization token."""
        if self.ip_address is None:
            return self.async_abort(reason="unknown_error")

        errors: dict[str, str] | None = None
        if user_input is not None:
            try:
                token = await async_request_token(self.hass, self.ip_address)
            except RecoverableError as ex:
                LOGGER.debug("Authorization token request failed: %s", ex)
                errors = {"base": ex.error_code}
                return self.async_show_form(step_id="authorize", errors=errors)

            if token is None:
                errors = {"base": "authorization_failed"}
                return self.async_show_form(step_id="authorize", errors=errors)

            try:
                device_info = await async_try_connect(
                    self.hass, self.ip_address, token=token
                )
            except RecoverableError as ex:
                LOGGER.debug("Authorization step connection check failed: %s", ex)
                errors = {"base": ex.error_code}
                return self.async_show_form(step_id="authorize", errors=errors)
            except UnauthorizedError:
                errors = {"base": "authorization_failed"}
                return self.async_show_form(step_id="authorize", errors=errors)

            if (result := await self._async_validate_and_set_unique_id(device_info)) is not None:
                return result

            data = {
                CONF_IP_ADDRESS: self.ip_address,
                CONF_TOKEN: token,
            }

            self._abort_if_unique_id_configured(updates=data)
            return self.async_create_entry(
                title=f"{device_info.product_name}",
                data=data,
            )

        return self.async_show_form(step_id="authorize", errors=errors)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle zeroconf discovery."""

        if (
            CONF_PRODUCT_NAME not in discovery_info.properties
            or CONF_PRODUCT_TYPE not in discovery_info.properties
            or CONF_SERIAL not in discovery_info.properties
        ):
            return self.async_abort(reason="invalid_discovery_parameters")

        product_type = discovery_info.properties[CONF_PRODUCT_TYPE]

        # Only support P1 meter
        if product_type not in SUPPORTED_PRODUCT_TYPES:
            return self.async_abort(reason="device_not_supported")

        self.ip_address = discovery_info.host
        self.product_type = product_type
        self.product_name = discovery_info.properties[CONF_PRODUCT_NAME]
        self.serial = discovery_info.properties[CONF_SERIAL]

        await self.async_set_unique_id(f"{DOMAIN}_{self.product_type}_{self.serial}")
        self._abort_if_unique_id_configured(
            updates={CONF_IP_ADDRESS: discovery_info.host}
        )

        return await self.async_step_discovery_confirm()

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Handle dhcp discovery to update existing entries.

        This flow is triggered only by DHCP discovery of known devices.
        """
        token = self._token_for_discovery_mac(discovery_info.macaddress)
        try:
            device = await async_try_connect(
                self.hass,
                discovery_info.ip,
                token=token,
            )
        except RecoverableError as ex:
            LOGGER.debug("DHCP discovery connection check failed: %s", ex)
            return self.async_abort(reason="unknown_error")
        except UnauthorizedError:
            return self.async_abort(reason="unknown_error")

        if (result := await self._async_validate_and_set_unique_id(device)) is not None:
            return result

        self._abort_if_unique_id_configured(
            updates={CONF_IP_ADDRESS: discovery_info.ip}
        )

        # This situation should never happen, as Home Assistant will only
        # send updates for existing entries. In case it does, we'll just
        # abort the flow with an unknown error.
        return self.async_abort(reason="unknown_error")

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""
        ip_address = self.ip_address
        product_name = self.product_name
        product_type = self.product_type
        serial = self.serial

        if (
            ip_address is None
            or product_name is None
            or product_type is None
            or serial is None
        ):
            return self.async_abort(reason="unknown_error")

        errors: dict[str, str] | None = None
        if user_input is not None or not onboarding.async_is_onboarded(self.hass):
            try:
                await async_try_connect(self.hass, ip_address)
            except RecoverableError as ex:
                LOGGER.debug("Discovery confirmation connection check failed: %s", ex)
                errors = {"base": ex.error_code}
            except UnauthorizedError:
                self.ip_address = ip_address
                return await self.async_step_authorize()
            else:
                return self.async_create_entry(
                    title=product_name,
                    data={CONF_IP_ADDRESS: ip_address},
                )

        self._set_confirm_only()

        # P1 meter doesn't need serial in name as users generally only have one
        self.context["title_placeholders"] = {"name": product_name}

        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={
                CONF_PRODUCT_TYPE: product_type,
                CONF_SERIAL: serial,
                CONF_IP_ADDRESS: ip_address,
            },
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-auth if API was disabled."""
        self.ip_address = entry_data[CONF_IP_ADDRESS]

        if entry_data.get(CONF_TOKEN):
            return await self.async_step_reauth_confirm_update_token()

        return await self.async_step_reauth_enable_api()

    async def async_step_reauth_enable_api(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth dialog, where user is asked to re-enable the HomeWizard API."""
        errors: dict[str, str] | None = None
        if user_input is not None:
            reauth_entry = self._get_reauth_entry()
            try:
                await async_try_connect(self.hass, reauth_entry.data[CONF_IP_ADDRESS])
            except RecoverableError as ex:
                LOGGER.debug("Reauth connection check failed: %s", ex)
                errors = {"base": ex.error_code}
            except UnauthorizedError:
                # Some reauth flows may hit v2-only auth on entries that were
                # previously configured without a token. Continue with token refresh.
                return await self.async_step_reauth_confirm_update_token()
            else:
                await self.hass.config_entries.async_reload(reauth_entry.entry_id)
                return self.async_abort(reason="reauth_enable_api_successful")

        return self.async_show_form(step_id="reauth_enable_api", errors=errors)

    async def async_step_reauth_confirm_update_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth and refresh an expired v2 token."""
        if self.ip_address is None:
            return self.async_abort(reason="unknown_error")

        errors: dict[str, str] | None = None
        if user_input is not None:
            try:
                token = await async_request_token(self.hass, self.ip_address)
            except RecoverableError as ex:
                LOGGER.debug("Reauth token request failed: %s", ex)
                errors = {"base": ex.error_code}
                return self.async_show_form(
                    step_id="reauth_confirm_update_token", errors=errors
                )

            if token is None:
                errors = {"base": "authorization_failed"}
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={
                        CONF_TOKEN: token,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm_update_token", errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input:
            try:
                device_info = await async_try_connect(
                    self.hass,
                    user_input[CONF_IP_ADDRESS],
                    token=reconfigure_entry.data.get(CONF_TOKEN),
                )
            except RecoverableError as ex:
                LOGGER.debug("Reconfigure connection check failed: %s", ex)
                errors = {"base": ex.error_code}
            except UnauthorizedError:
                errors = {"base": "authorization_failed"}
            else:
                if (result := await self._async_validate_and_set_unique_id(device_info)) is not None:
                    return result

                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(),
                    data_updates=user_input,
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_IP_ADDRESS,
                        default=reconfigure_entry.data.get(CONF_IP_ADDRESS),
                    ): TextSelector(),
                }
            ),
            description_placeholders={
                "title": reconfigure_entry.title,
            },
            errors=errors,
        )


async def async_try_connect(
    hass: HomeAssistant,
    ip_address: str,
    token: str | None = None,
    clientsession: ClientSession | None = None,
) -> Device:
    """Try to connect.

    Make connection with device to test the connection
    and to get info for unique_id.
    """

    session = clientsession or async_get_clientsession(hass)
    energy_api: HomeWizardEnergy

    if await has_v2_api(ip_address, websession=session):
        v2_class = (
            InsecureHomeWizardEnergyV2
            if allow_insecure_v2_for_host(ip_address)
            else HomeWizardEnergyV2
        )
        energy_api = v2_class(
            ip_address,
            token=token,
            clientsession=session,
        )
    else:
        energy_api = HomeWizardEnergyV1(
            ip_address,
            clientsession=session,
        )

    try:
        return await energy_api.device()

    except DisabledError as ex:
        raise RecoverableError(
            "API disabled, API must be enabled in the app", "api_not_enabled"
        ) from ex

    except RequestError as ex:
        raise RecoverableError(
            "Device unreachable or unexpected response", "network_error"
        ) from ex

    except UnauthorizedError:
        raise

    except asyncio.CancelledError:
        raise

    finally:
        await energy_api.close()


async def async_request_token(
    hass: HomeAssistant,
    ip_address: str,
    clientsession: ClientSession | None = None,
) -> str | None:
    """Request a v2 token from the device.

    Returns None when user creation is not currently enabled on the device.
    """
    v2_class = (
        InsecureHomeWizardEnergyV2
        if allow_insecure_v2_for_host(ip_address)
        else HomeWizardEnergyV2
    )

    api = v2_class(
        ip_address,
        clientsession=clientsession or async_get_clientsession(hass),
    )

    uuid = await instance_id.async_get(hass)

    try:
        return await api.get_token(f"home-assistant#{uuid[:6]}")
    except DisabledError:
        return None
    except RequestError as ex:
        raise RecoverableError(
            "Device unreachable or unexpected response", "network_error"
        ) from ex
    except asyncio.CancelledError:
        raise
    finally:
        await api.close()


class RecoverableError(HomeAssistantError):
    """Raised when a connection has been failed but can be retried."""

    def __init__(self, message: str, error_code: str) -> None:
        """Init RecoverableError."""
        super().__init__(message)
        self.error_code = error_code
