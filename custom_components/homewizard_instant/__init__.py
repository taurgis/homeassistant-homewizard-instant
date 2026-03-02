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
from .config_flow import RecoverableError, async_request_token
from .coordinator import HomeWizardConfigEntry, HWEnergyDeviceUpdateCoordinator
from .v2_dev_ssl import InsecureHomeWizardEnergyV2, allow_insecure_v2_for_host


async def async_setup_entry(hass: HomeAssistant, entry: HomeWizardConfigEntry) -> bool:
    """Set up Homewizard from a config entry."""

    clientsession = async_get_clientsession(hass)
    host = entry.data[CONF_IP_ADDRESS]

    token: str | None = entry.data.get(CONF_TOKEN)
    v2_supported: bool | None = None
    created_v2_migration_issue = False
    api: HomeWizardEnergy
    if token is None:
        v2_supported = await has_v2_api(host, websession=clientsession)
        if v2_supported:
            try:
                token = await async_request_token(
                    hass,
                    host,
                    clientsession=clientsession,
                )
            except RecoverableError:
                token = None

            if token is not None:
                hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_TOKEN: token},
                )
            else:
                await async_check_v2_support_and_create_issue(
                    hass,
                    entry,
                    v2_supported=True,
                )
                created_v2_migration_issue = True

    if token is not None:
        v2_class = (
            InsecureHomeWizardEnergyV2 if allow_insecure_v2_for_host(host) else HomeWizardEnergyV2
        )
        api = v2_class(
            host,
            token=token,
            clientsession=clientsession,
        )
    else:
        api = HomeWizardEnergyV1(
            host,
            clientsession=clientsession,
        )

        if v2_supported is None:
            v2_supported = await has_v2_api(host, websession=clientsession)

        if v2_supported and not created_v2_migration_issue:
            await async_check_v2_support_and_create_issue(
                hass,
                entry,
                v2_supported=True,
            )

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
    try:
        await coordinator.async_start_websocket()

        # Finalize
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_shutdown()
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomeWizardConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_shutdown()
    return unload_ok


async def async_check_v2_support_and_create_issue(
    hass: HomeAssistant,
    entry: HomeWizardConfigEntry,
    v2_supported: bool | None = None,
) -> None:
    """Create a repair issue when an entry can migrate from v1 to v2 auth."""
    if v2_supported is None:
        v2_supported = await has_v2_api(
            entry.data[CONF_IP_ADDRESS],
            websession=async_get_clientsession(hass),
        )

    if not v2_supported:
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
