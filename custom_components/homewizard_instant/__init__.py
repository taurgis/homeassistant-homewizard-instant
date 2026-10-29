"""HomeWizard Instant using API v2 WebSockets."""

from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homewizard_energy.models import get_verification_hostname

from .config_flow import async_revoke_token
from .const import CONF_PRODUCT_TYPE, CONF_SERIAL, DOMAIN, PLATFORMS
from .coordinator import HomeWizardConfigEntry, HWEnergyDeviceUpdateCoordinator
from .websocket import HomeWizardWebSocket, create_ssl_context


async def async_setup_entry(hass: HomeAssistant, entry: HomeWizardConfigEntry) -> bool:
    """Set up push updates; legacy entries pair once through reauthentication."""
    if not all(
        entry.data.get(key) for key in (CONF_TOKEN, CONF_PRODUCT_TYPE, CONF_SERIAL)
    ):
        raise ConfigEntryAuthFailed("Pair this P1 meter with API v2")
    api = HomeWizardWebSocket(
        async_get_clientsession(hass),
        entry.data[CONF_IP_ADDRESS],
        entry.data[CONF_TOKEN],
        get_verification_hostname(
            entry.data[CONF_PRODUCT_TYPE], entry.data[CONF_SERIAL]
        ),
        await hass.async_add_executor_job(create_ssl_context),
    )
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, entry, api)
    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        coordinator.async_start()
    except BaseException:
        await coordinator.async_stop()
        raise
    entry.async_on_unload(coordinator.async_stop)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomeWizardConfigEntry) -> bool:
    """Unload platforms; the reader stops through the unload callback."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: HomeWizardConfigEntry) -> None:
    """Revoke the pairing token so it does not linger on the meter."""
    if token := entry.data.get(CONF_TOKEN):
        await async_revoke_token(
            hass,
            entry.data[CONF_IP_ADDRESS],
            token,
            product_type=entry.data.get(CONF_PRODUCT_TYPE),
            serial=entry.data.get(CONF_SERIAL),
        )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: HomeWizardConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Let users delete external meters the P1 meter no longer reports."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None or coordinator.data is None:
        return True
    data = coordinator.data
    reported = {
        (DOMAIN, f"{DOMAIN}_{key}")
        for key in [data.device.serial, *(data.measurement.external_devices or {})]
    }
    return not device.identifiers & reported
