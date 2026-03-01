"""Repairs for HomeWizard Instant integration."""

from __future__ import annotations

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .config_flow import async_request_token


class MigrateToV2ApiRepairFlow(RepairsFlow):
    """Handle migration from v1 API auth to v2 token auth."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize repair flow."""
        self.entry = entry

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        """Handle the first step of the fix flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Handle the confirm step of the fix flow."""
        if user_input is not None:
            return await self.async_step_authorize()

        return self.async_show_form(
            step_id="confirm", description_placeholders={"title": self.entry.title}
        )

    async def async_step_authorize(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Request a v2 token and migrate the existing config entry."""
        if user_input is None:
            return self.async_show_form(step_id="authorize")

        ip_address = self.entry.data[CONF_IP_ADDRESS]
        token = await async_request_token(self.hass, ip_address)

        if token is None:
            return self.async_show_form(
                step_id="authorize", errors={"base": "authorization_failed"}
            )

        data = {**self.entry.data, CONF_TOKEN: token}
        self.hass.config_entries.async_update_entry(self.entry, data=data)
        await self.hass.config_entries.async_reload(self.entry.entry_id)
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create a repair flow for a known issue."""
    entry_id = data.get("entry_id") if data is not None else None
    if not isinstance(entry_id, str):
        raise ValueError("unknown repair context")

    if issue_id.startswith("migrate_to_v2_api_") and (
        entry := hass.config_entries.async_get_entry(entry_id)
    ):
        return MigrateToV2ApiRepairFlow(entry)

    raise ValueError(f"unknown repair {issue_id}")
