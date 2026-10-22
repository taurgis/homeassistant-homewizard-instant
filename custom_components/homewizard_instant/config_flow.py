"""UI configuration and physical-button pairing for HomeWizard API v2."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import instance_id
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector
from homewizard_energy import HomeWizardEnergyV2
from homewizard_energy.const import Model
from homewizard_energy.errors import (
    DisabledError,
    NotFoundError,
    RequestError,
    UnauthorizedError,
    UnsupportedError,
)
from homewizard_energy.models import Device

from .const import CONF_PRODUCT_NAME, CONF_PRODUCT_TYPE, CONF_SERIAL, DOMAIN


class HomeWizardConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a P1 meter without falling back to API v1."""

    VERSION = 1
    ip_address: str | None = None
    product_name: str | None = None
    product_type: str | None = None
    serial: str | None = None
    _token: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the host, then open the device's pairing window."""
        if user_input is not None:
            self.ip_address = user_input[CONF_IP_ADDRESS]
            return await self.async_step_authorize()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_IP_ADDRESS): TextSelector(),
                }
            ),
        )

    async def async_step_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Request a token before prompting for the physical button press."""
        assert self.ip_address is not None
        errors: dict[str, str] = {}
        try:
            if self._token is None:
                self._token = await async_request_token(self.hass, self.ip_address)
            if self._token is None:
                if user_input is not None:
                    errors["base"] = "authorization_failed"
            else:
                device = await async_try_connect(
                    self.hass, self.ip_address, token=self._token
                )
                if device.product_type != Model.P1_METER:
                    return self.async_abort(reason="device_not_supported")
                if not device.serial:
                    return self.async_abort(reason="unknown_error")
                unique_id = f"{DOMAIN}_{device.product_type}_{device.serial}"
                # Discovery must not silently pair with a different device at a reused IP.
                if self.unique_id is not None and self.unique_id != unique_id:
                    return self.async_abort(reason="wrong_device")
                await self.async_set_unique_id(unique_id)
                data = {
                    CONF_IP_ADDRESS: self.ip_address,
                    CONF_TOKEN: self._token,
                    CONF_PRODUCT_TYPE: device.product_type,
                    CONF_SERIAL: device.serial,
                }
                if self.source == "reauth":
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data_updates=data
                    )
                self._abort_if_unique_id_configured(updates=data)
                return self.async_create_entry(title=device.product_name, data=data)
        except RecoverableError as err:
            errors["base"] = err.error_code
            if err.error_code == "invalid_auth":
                self._token = None
        return self.async_show_form(
            step_id="authorize",
            errors=errors,
            description_placeholders={CONF_IP_ADDRESS: self.ip_address},
        )

    async def async_step_zeroconf(self, discovery_info: Any) -> ConfigFlowResult:
        """Discover supported P1 meters and update existing hosts."""
        properties = discovery_info.properties
        if not all(
            key in properties
            for key in (CONF_PRODUCT_NAME, CONF_PRODUCT_TYPE, CONF_SERIAL)
        ):
            return self.async_abort(reason="invalid_discovery_parameters")
        if properties[CONF_PRODUCT_TYPE] != Model.P1_METER:
            return self.async_abort(reason="device_not_supported")
        self.ip_address = discovery_info.host
        self.product_name = properties[CONF_PRODUCT_NAME]
        self.product_type = properties[CONF_PRODUCT_TYPE]
        self.serial = properties[CONF_SERIAL]
        await self.async_set_unique_id(f"{DOMAIN}_{self.product_type}_{self.serial}")
        self._abort_if_unique_id_configured(updates={CONF_IP_ADDRESS: self.ip_address})
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require confirmation before requesting a pairing token."""
        if user_input is not None:
            return await self.async_step_authorize()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={
                CONF_PRODUCT_TYPE: self.product_type or "",
                CONF_SERIAL: self.serial or "",
                CONF_IP_ADDRESS: self.ip_address or "",
            },
        )

    async def async_step_dhcp(self, discovery_info: Any) -> ConfigFlowResult:
        """Update a known meter's IP without making unauthenticated v1 requests."""
        serial = discovery_info.macaddress.replace(":", "").replace("-", "").lower()
        for entry in self._async_current_entries():
            expected = f"{DOMAIN}_{Model.P1_METER}_{serial}"
            if entry.unique_id and entry.unique_id.lower() == expected.lower():
                await self.async_set_unique_id(entry.unique_id)
                self._abort_if_unique_id_configured(
                    updates={CONF_IP_ADDRESS: discovery_info.ip}
                )
        return self.async_abort(reason="unknown_error")

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Pair legacy entries or replace revoked tokens, preserving entity IDs."""
        self.ip_address = entry_data[CONF_IP_ADDRESS]
        return await self.async_step_authorize()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the device identity before accepting a changed IP."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                device = await async_try_connect(
                    self.hass,
                    user_input[CONF_IP_ADDRESS],
                    token=entry.data.get(CONF_TOKEN),
                )
            except RecoverableError as err:
                errors["base"] = err.error_code
            else:
                await self.async_set_unique_id(
                    f"{DOMAIN}_{device.product_type}_{device.serial}"
                )
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_IP_ADDRESS, default=entry.data[CONF_IP_ADDRESS]
                    ): TextSelector(),
                }
            ),
            description_placeholders={"title": entry.title},
            errors=errors,
        )


async def async_try_connect(
    hass: HomeAssistant,
    ip_address: str,
    clientsession: ClientSession | None = None,
    token: str | None = None,
) -> Device:
    """Read v2 identity with the Home Assistant-managed session."""
    api = HomeWizardEnergyV2(
        ip_address,
        token=token,
        clientsession=clientsession or async_get_clientsession(hass),
    )
    try:
        return await api.device()
    except UnauthorizedError as err:
        raise RecoverableError("Token rejected", "invalid_auth") from err
    except (NotFoundError, UnsupportedError) as err:
        raise RecoverableError("API v2 required", "unsupported_api_version") from err
    except (RequestError, DisabledError) as err:
        raise RecoverableError("Cannot connect", "network_error") from err
    finally:
        await api.close()


async def async_request_token(hass: HomeAssistant, ip_address: str) -> str | None:
    """Use a distinct user name so the official integration's token stays valid."""
    uuid = await instance_id.async_get(hass)
    api = HomeWizardEnergyV2(ip_address, clientsession=async_get_clientsession(hass))
    try:
        return await api.get_token(f"homewizard-instant#{uuid[:12]}")
    except DisabledError:
        return None
    except (NotFoundError, UnsupportedError) as err:
        raise RecoverableError("API v2 required", "unsupported_api_version") from err
    except RequestError as err:
        raise RecoverableError("Cannot connect", "network_error") from err
    finally:
        await api.close()


class RecoverableError(HomeAssistantError):
    """A setup error the user can retry."""

    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code
