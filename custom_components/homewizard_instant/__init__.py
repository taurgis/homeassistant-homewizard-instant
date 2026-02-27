"""The Homewizard integration."""

from homewizard_energy import (
    HomeWizardEnergy,
    HomeWizardEnergyV1,
    HomeWizardEnergyV2,
    has_v2_api,
)

from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue

from .const import DOMAIN, PLATFORMS
from .coordinator import HomeWizardConfigEntry, HWEnergyDeviceUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: HomeWizardConfigEntry) -> bool:
    """Set up Homewizard from a config entry."""

    clientsession = async_get_clientsession(hass)

    token: str | None = entry.data.get(CONF_TOKEN)
    api: HomeWizardEnergy
    if token is not None:
        api = HomeWizardEnergyV2(
            entry.data[CONF_IP_ADDRESS],
            token=token,
            clientsession=clientsession,
        )
    else:
        api = HomeWizardEnergyV1(
            entry.data[CONF_IP_ADDRESS],
            clientsession=clientsession,
        )
        await async_check_v2_support_and_create_issue(hass, entry)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass,
        entry,
        api,
        clientsession=clientsession,
        ws_token=token,
    )
    try:
        await coordinator.async_config_entry_first_refresh()

    except (ConfigEntryNotReady, ConfigEntryAuthFailed):
        await coordinator.async_shutdown()

        if coordinator.api_disabled:
            entry.async_start_reauth(hass)

        raise

    entry.runtime_data = coordinator
    await coordinator.async_start_websocket()

    # Finalize
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomeWizardConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown()
    return unload_ok


async def async_check_v2_support_and_create_issue(
    hass: HomeAssistant, entry: HomeWizardConfigEntry
) -> None:
    """Create a repair issue when an entry can migrate from v1 to v2 auth."""
    if not await has_v2_api(entry.data[CONF_IP_ADDRESS], websession=async_get_clientsession(hass)):
        return

    async_create_issue(
        hass,
        DOMAIN,
        f"migrate_to_v2_api_{entry.entry_id}",
        is_fixable=True,
        is_persistent=False,
        learn_more_url="https://www.home-assistant.io/integrations/homewizard/#which-button-do-i-need-to-press-to-configure-the-device",
        translation_key="migrate_to_v2_api",
        translation_placeholders={
            "title": entry.title,
        },
        severity=IssueSeverity.WARNING,
        data={"entry_id": entry.entry_id},
    )
