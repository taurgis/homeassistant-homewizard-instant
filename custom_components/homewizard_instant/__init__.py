"""The Homewizard integration."""

from homewizard_energy import HomeWizardEnergyV1

from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, PLATFORMS
from .coordinator import HomeWizardConfigEntry, HWEnergyDeviceUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: HomeWizardConfigEntry) -> bool:
    """Set up Homewizard from a config entry."""

    api = HomeWizardEnergyV1(
        entry.data[CONF_IP_ADDRESS],
        clientsession=async_get_clientsession(hass),
    )

    coordinator = HWEnergyDeviceUpdateCoordinator(hass, entry, api)
    try:
        await coordinator.async_config_entry_first_refresh()

    except ConfigEntryNotReady:
        await coordinator.api.close()

        if coordinator.api_disabled:
            entry.async_start_reauth(hass)

        raise

    entry.runtime_data = coordinator

    # Finalize
    entry.async_on_unload(coordinator.api.close)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomeWizardConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
