"""UI configuration and physical-button pairing for HomeWizard API v2."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession
from homeassistant.config_entries import SOURCE_USER, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import instance_id
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homewizard_energy import HomeWizardEnergyV2
from homewizard_energy.const import Model
from homewizard_energy.errors import (
    DisabledError,
    HomeWizardEnergyException,
    NotFoundError,
    RequestError,
    UnauthorizedError,
    UnsupportedError,
)
from homewizard_energy.models import Device, get_verification_hostname

from .const import CONF_PRODUCT_NAME, CONF_PRODUCT_TYPE, CONF_SERIAL, DOMAIN, LOGGER
from .websocket import trust_development_ca


def identity_from_unique_id(unique_id: str | None) -> tuple[str | None, str | None]:
    """Return the product type and serial stored in a config-entry unique id."""
    prefix = f"{DOMAIN}_"
    if unique_id is None or not unique_id.startswith(prefix):
        return None, None
    product_type, separator, serial = unique_id.removeprefix(prefix).rpartition("_")
    if not separator or not product_type or not serial:
        return None, None
    return product_type, serial


def pinned_identity(
    product_type: str | None,
    serial: str | None,
    unique_id: str | None,
) -> tuple[str | None, str | None]:
    """Prefer the unique id; it is the identity reauth and DHCP compare."""
    parsed_type, parsed_serial = identity_from_unique_id(unique_id)
    if parsed_type and parsed_serial:
        return parsed_type, parsed_serial
    if product_type and serial:
        return product_type, serial
    return None, None


def verification_hostname(product_type: str | None, serial: str | None) -> str | None:
    """Return the TLS hostname, or None when the device is not known yet."""
    if not product_type or not serial:
        return None
    try:
        return get_verification_hostname(product_type, serial)
    except ValueError:
        return None


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
            self._async_abort_entries_match(
                {CONF_IP_ADDRESS: user_input[CONF_IP_ADDRESS]}
            )
            if user_input[CONF_IP_ADDRESS] != self.ip_address:
                self._token = None
            self.ip_address = user_input[CONF_IP_ADDRESS]
            return await self.async_step_authorize()
        return self._show_user_form()

    def _show_user_form(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({vol.Required(CONF_IP_ADDRESS): TextSelector()}),
                {CONF_IP_ADDRESS: self.ip_address} if self.ip_address else {},
            ),
            errors=errors,
        )

    async def async_step_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Request a token before prompting for the physical button press."""
        assert self.ip_address is not None
        errors: dict[str, str] = {}
        product_type, serial = pinned_identity(
            self.product_type, self.serial, self.unique_id
        )
        try:
            if self._token is None:
                self._token = await async_request_token(
                    self.hass,
                    self.ip_address,
                    product_type=product_type,
                    serial=serial,
                )
            if self._token is None:
                if user_input is not None:
                    errors["base"] = "authorization_failed"
            else:
                device = await async_try_connect(
                    self.hass,
                    self.ip_address,
                    token=self._token,
                    product_type=product_type,
                    serial=serial,
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
            # A wrong address cannot be fixed on the pairing form.
            if self.source == SOURCE_USER and err.error_code in (
                "network_error",
                "unsupported_api_version",
            ):
                return self._show_user_form(errors)
        except AbortFlow:
            raise
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected error while pairing the HomeWizard meter")
            errors["base"] = "unknown"
        return self.async_show_form(
            step_id="authorize",
            errors=errors,
            description_placeholders={CONF_IP_ADDRESS: self.ip_address},
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
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

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Update a known meter's IP without making unauthenticated v1 requests."""
        serial = discovery_info.macaddress.replace(":", "").replace("-", "").lower()
        for entry in self._async_current_entries():
            expected = f"{DOMAIN}_{Model.P1_METER}_{serial}"
            if entry.unique_id and entry.unique_id.lower() == expected.lower():
                await self.async_set_unique_id(entry.unique_id)
                self._abort_if_unique_id_configured(
                    updates={CONF_IP_ADDRESS: discovery_info.ip}
                )
        return self.async_abort(reason="unknown_device")

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Pair legacy entries or replace revoked tokens, preserving entity IDs."""
        entry = self._get_reauth_entry()
        self.ip_address = entry_data[CONF_IP_ADDRESS]
        self.product_type, self.serial = pinned_identity(
            entry_data.get(CONF_PRODUCT_TYPE),
            entry_data.get(CONF_SERIAL),
            entry.unique_id,
        )
        return await self.async_step_authorize()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the device identity before accepting a changed IP."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        product_type, serial = pinned_identity(
            entry.data.get(CONF_PRODUCT_TYPE),
            entry.data.get(CONF_SERIAL),
            entry.unique_id,
        )
        if user_input is not None:
            try:
                device = await async_try_connect(
                    self.hass,
                    user_input[CONF_IP_ADDRESS],
                    token=entry.data.get(CONF_TOKEN),
                    product_type=product_type,
                    serial=serial,
                )
            except RecoverableError as err:
                if (
                    err.error_code == "invalid_auth"
                    and entry.data.get(CONF_TOKEN)
                    and verification_hostname(product_type, serial) is not None
                ):
                    # The 401 came over TLS pinned to this meter's identity, so
                    # the address is right. Reloading starts reauthentication.
                    return self.async_update_reload_and_abort(
                        entry, data_updates=user_input
                    )
                errors["base"] = err.error_code
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error while reconfiguring HomeWizard")
                errors["base"] = "unknown"
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
    *,
    product_type: str | None = None,
    serial: str | None = None,
) -> Device:
    """Read v2 identity with the Home Assistant-managed session."""
    await hass.async_add_executor_job(trust_development_ca)
    api = HomeWizardEnergyV2(
        ip_address,
        identifier=verification_hostname(product_type, serial),
        token=token,
        clientsession=clientsession or async_get_clientsession(hass),
    )
    try:
        return await api.device()
    except UnauthorizedError as err:
        raise RecoverableError("Token rejected", "invalid_auth") from err
    except (NotFoundError, UnsupportedError) as err:
        raise RecoverableError("API v2 required", "unsupported_api_version") from err
    except RequestError as err:
        raise RecoverableError("Cannot connect", "network_error") from err
    finally:
        await api.close()


async def async_request_token(
    hass: HomeAssistant,
    ip_address: str,
    *,
    product_type: str | None = None,
    serial: str | None = None,
) -> str | None:
    """Use a distinct user name so the official integration's token stays valid."""
    await hass.async_add_executor_job(trust_development_ca)
    uuid = await instance_id.async_get(hass)
    api = HomeWizardEnergyV2(
        ip_address,
        identifier=verification_hostname(product_type, serial),
        clientsession=async_get_clientsession(hass),
    )
    try:
        return await api.get_token(f"homewizard-instant#{uuid[:12]}")
    except DisabledError:
        return None
    except UnauthorizedError as err:
        raise RecoverableError("Token rejected", "invalid_auth") from err
    except (NotFoundError, UnsupportedError) as err:
        raise RecoverableError("API v2 required", "unsupported_api_version") from err
    except RequestError as err:
        raise RecoverableError("Cannot connect", "network_error") from err
    finally:
        await api.close()


async def async_revoke_token(
    hass: HomeAssistant,
    ip_address: str,
    token: str,
    *,
    product_type: str | None = None,
    serial: str | None = None,
) -> None:
    """Delete this integration's local user from the meter, best effort."""
    await hass.async_add_executor_job(trust_development_ca)
    api = HomeWizardEnergyV2(
        ip_address,
        identifier=verification_hostname(product_type, serial),
        token=token,
        clientsession=async_get_clientsession(hass),
        timeout=5,
    )
    try:
        await api.delete_token()
    except (HomeWizardEnergyException, ValueError) as err:
        LOGGER.info(
            "Could not remove the Home Assistant user from the HomeWizard meter at "
            "%s (%s); remove it in the HomeWizard Energy app if needed",
            ip_address,
            str(err) or type(err).__name__,
        )
    finally:
        await api.close()


class RecoverableError(HomeAssistantError):
    """A setup error the user can retry."""

    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code
