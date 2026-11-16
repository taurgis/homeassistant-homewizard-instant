"""UI configuration and physical-button pairing for HomeWizard API v2."""

from __future__ import annotations

import asyncio
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
from homewizard_energy.errors import (
    DisabledError,
    HomeWizardEnergyException,
    NotFoundError,
    RequestError,
    UnauthorizedError,
    UnsupportedError,
)
from homewizard_energy.models import Device

from .const import (
    CONF_PRODUCT_NAME,
    CONF_PRODUCT_TYPE,
    CONF_SERIAL,
    DOMAIN,
    LOGGER,
    SUPPORTED_MODELS,
)
from .websocket import trust_development_ca

_REVOKE_TIMEOUT = 8


def identity_from_unique_id(unique_id: str | None) -> tuple[str | None, str | None]:
    """Return the product type and serial stored in a config-entry unique id."""
    prefix = f"{DOMAIN}_"
    if unique_id is None or not unique_id.startswith(prefix):
        return None, None
    product_type, separator, serial = unique_id.removeprefix(prefix).rpartition("_")
    if not separator or not product_type or not serial:
        return None, None
    return product_type, serial


def same_meter(unique_id: str | None, product_type: str, serial: str) -> bool:
    """Return True when the stored unique id is this meter, ignoring case."""
    expected_type, expected_serial = identity_from_unique_id(unique_id)
    if expected_type is None or expected_serial is None:
        return False
    return (
        expected_type.casefold() == product_type.casefold()
        and expected_serial.casefold() == serial.casefold()
    )


def _cause_chain(err: BaseException) -> str:
    """Return the exception and its causes without a traceback."""
    parts: list[str] = []
    current: BaseException | None = err
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip() or type(current).__name__
        parts.append(f"{type(current).__name__}: {text}")
        current = current.__cause__
    return "; caused by ".join(parts)


def _recover(step: str, err: Exception, code: str, message: str) -> RecoverableError:
    """Log a pairing failure with its step and the underlying error class."""
    LOGGER.warning(
        "HomeWizard pairing step %s failed (%s): %s",
        step,
        type(err).__name__,
        _cause_chain(err),
    )
    return RecoverableError(message, code)


def _log_wrong_device(
    step: str, host: str, expected: str | None, device: Device
) -> None:
    """Log a serial comparison that rejected the meter at this address."""
    LOGGER.warning(
        "HomeWizard pairing step %s at %s reported %s serial %s; expected unique id %s",
        step,
        host,
        device.product_type,
        device.serial,
        expected,
    )


class HomeWizardConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a P1 or kWh meter without falling back to API v1."""

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
            LOGGER.debug(
                "HomeWizard config flow step user source=%s host=%s",
                self.source,
                self.ip_address,
            )
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
        step = f"{self.source}:authorize"
        LOGGER.debug(
            "HomeWizard config flow step %s host=%s",
            step,
            self.ip_address,
        )
        errors: dict[str, str] = {}
        try:
            if self._token is None:
                self._token = await async_request_token(
                    self.hass, self.ip_address, step=step
                )
            if self._token is None:
                if user_input is not None:
                    errors["base"] = "authorization_failed"
            else:
                device = await async_try_connect(
                    self.hass,
                    self.ip_address,
                    token=self._token,
                    step=step,
                )
                if device.product_type not in SUPPORTED_MODELS:
                    return await self._async_abort_and_revoke("device_not_supported")
                if not device.serial:
                    return await self._async_abort_and_revoke("unknown_error")
                if self.source == "reauth":
                    entry = self._get_reauth_entry()
                    if not same_meter(
                        entry.unique_id, device.product_type, device.serial
                    ):
                        _log_wrong_device(step, self.ip_address, entry.unique_id, device)
                        return await self._async_abort_and_revoke("wrong_device")
                    LOGGER.debug(
                        "HomeWizard pairing step %s accepted serial %s for unique id %s",
                        step,
                        device.serial,
                        entry.unique_id,
                    )
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates=_entry_data(
                            self.ip_address, self._token, device
                        ),
                    )
                # Discovery must not silently pair with a different device at a reused IP.
                if self.unique_id is not None and not same_meter(
                    self.unique_id, device.product_type, device.serial
                ):
                    _log_wrong_device(
                        step, self.ip_address, self.unique_id, device
                    )
                    return await self._async_abort_and_revoke("wrong_device")
                # A pending discovery card for this meter must not abort a
                # manual setup after the button press; creating the entry
                # dismisses that card. Keep the unique id already chosen so a
                # v1 entry or discovery record is not rewritten when only the
                # serial's letter case differs.
                if self.unique_id is None:
                    await self.async_set_unique_id(
                        f"{DOMAIN}_{device.product_type}_{device.serial}",
                        raise_on_progress=False,
                    )
                LOGGER.debug(
                    "HomeWizard pairing step %s accepted serial %s for unique id %s",
                    step,
                    device.serial,
                    self.unique_id,
                )
                data = _entry_data(self.ip_address, self._token, device)
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
            LOGGER.exception("Unexpected error in HomeWizard pairing step %s", step)
            errors["base"] = "unknown"
        return self.async_show_form(
            step_id="authorize",
            errors=errors,
            description_placeholders={CONF_IP_ADDRESS: self.ip_address},
        )

    async def _async_abort_and_revoke(self, reason: str) -> ConfigFlowResult:
        """Abort without leaving the new token behind on the device."""
        assert self.ip_address is not None and self._token is not None
        await async_revoke_token(self.hass, self.ip_address, self._token)
        return self.async_abort(reason=reason)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Discover supported P1 and kWh meters and update existing hosts."""
        properties = discovery_info.properties
        if not all(
            key in properties
            for key in (CONF_PRODUCT_NAME, CONF_PRODUCT_TYPE, CONF_SERIAL)
        ):
            return self.async_abort(reason="invalid_discovery_parameters")
        if properties[CONF_PRODUCT_TYPE] not in SUPPORTED_MODELS:
            return self.async_abort(reason="device_not_supported")
        self.ip_address = discovery_info.host
        self.product_name = properties[CONF_PRODUCT_NAME]
        self.product_type = properties[CONF_PRODUCT_TYPE]
        self.serial = properties[CONF_SERIAL]
        LOGGER.debug(
            "HomeWizard config flow step zeroconf host=%s serial=%s",
            self.ip_address,
            self.serial,
        )
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
            product_type, _ = identity_from_unique_id(entry.unique_id)
            if (
                product_type is not None
                and product_type in SUPPORTED_MODELS
                and same_meter(entry.unique_id, product_type, serial)
            ):
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
        self.product_type, self.serial = identity_from_unique_id(entry.unique_id)
        LOGGER.debug(
            "HomeWizard config flow step reauth host=%s unique_id=%s",
            self.ip_address,
            entry.unique_id,
        )
        return await self.async_step_authorize()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the device serial before accepting a changed IP."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            LOGGER.debug(
                "HomeWizard config flow step reconfigure host=%s unique_id=%s",
                user_input[CONF_IP_ADDRESS],
                entry.unique_id,
            )
            try:
                device = await async_try_connect(
                    self.hass,
                    user_input[CONF_IP_ADDRESS],
                    token=entry.data.get(CONF_TOKEN),
                    step="reconfigure",
                )
            except RecoverableError as err:
                # A rejected token does not identify the meter. The CA check
                # only shows that some HomeWizard answered.
                errors["base"] = err.error_code
            except Exception:  # noqa: BLE001
                LOGGER.exception(
                    "Unexpected error in HomeWizard pairing step reconfigure"
                )
                errors["base"] = "unknown"
            else:
                if same_meter(entry.unique_id, device.product_type, device.serial):
                    await self.async_set_unique_id(entry.unique_id)
                    LOGGER.debug(
                        "HomeWizard pairing step reconfigure accepted serial %s "
                        "for unique id %s",
                        device.serial,
                        entry.unique_id,
                    )
                else:
                    _log_wrong_device(
                        "reconfigure",
                        user_input[CONF_IP_ADDRESS],
                        entry.unique_id,
                        device,
                    )
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


def _entry_data(ip_address: str, token: str, device: Device) -> dict[str, Any]:
    """Return config-entry data without changing the stored unique id."""
    return {
        CONF_IP_ADDRESS: ip_address,
        CONF_TOKEN: token,
        CONF_PRODUCT_TYPE: device.product_type,
        CONF_SERIAL: device.serial,
    }


async def async_try_connect(
    hass: HomeAssistant,
    ip_address: str,
    clientsession: ClientSession | None = None,
    token: str | None = None,
    *,
    step: str = "connect",
) -> Device:
    """Read v2 identity with the Home Assistant-managed session.

    The HomeWizard CA chain is verified. The certificate hostname is not:
    ``python-homewizard-energy`` disables that check unless an identifier is
    passed, and the official integration never passes one. The caller compares
    ``device.serial`` with the expected unique id.
    """
    await hass.async_add_executor_job(trust_development_ca)
    api = HomeWizardEnergyV2(
        ip_address,
        token=token,
        clientsession=clientsession or async_get_clientsession(hass),
    )
    try:
        return await api.device()
    except UnauthorizedError as err:
        raise _recover(step, err, "invalid_auth", "Token rejected") from err
    except (NotFoundError, UnsupportedError) as err:
        raise _recover(step, err, "unsupported_api_version", "API v2 required") from err
    except RequestError as err:
        raise _recover(step, err, "network_error", "Cannot connect") from err
    except HomeWizardEnergyException as err:
        raise _recover(step, err, "invalid_response", "Unexpected response") from err
    finally:
        await api.close()


async def async_request_token(
    hass: HomeAssistant,
    ip_address: str,
    *,
    step: str = "pair",
) -> str | None:
    """Use a distinct user name so the official integration's token stays valid."""
    await hass.async_add_executor_job(trust_development_ca)
    uuid = await instance_id.async_get(hass)
    api = HomeWizardEnergyV2(
        ip_address,
        clientsession=async_get_clientsession(hass),
    )
    try:
        return await api.get_token(f"homewizard-instant#{uuid[:12]}")
    except DisabledError:
        LOGGER.debug(
            "HomeWizard pairing step %s is waiting for the button press at %s",
            step,
            ip_address,
        )
        return None
    except UnauthorizedError as err:
        raise _recover(step, err, "invalid_auth", "Token rejected") from err
    except (NotFoundError, UnsupportedError) as err:
        raise _recover(step, err, "unsupported_api_version", "API v2 required") from err
    except RequestError as err:
        raise _recover(step, err, "network_error", "Cannot connect") from err
    except HomeWizardEnergyException as err:
        raise _recover(step, err, "invalid_response", "Unexpected response") from err
    finally:
        await api.close()


async def async_revoke_token(
    hass: HomeAssistant,
    ip_address: str,
    token: str,
) -> None:
    """Delete this integration's local user from the meter, best effort.

    The bearer token identifies the user. A different meter rejects it, so
    revoking does not need a TLS hostname pin.
    """
    await hass.async_add_executor_job(trust_development_ca)
    api = HomeWizardEnergyV2(
        ip_address,
        token=token,
        clientsession=async_get_clientsession(hass),
        timeout=5,
    )
    try:
        # The library retries failed requests; cap the total wait so removing
        # an entry for an offline meter does not hang.
        async with asyncio.timeout(_REVOKE_TIMEOUT):
            await api.delete_token()
    except (HomeWizardEnergyException, TimeoutError, ValueError) as err:
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
