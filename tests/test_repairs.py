"""Tests for repairs flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import CONF_TOKEN
from homeassistant.data_entry_flow import FlowResultType

from custom_components.homewizard_instant.repairs import (
    MigrateToV2ApiRepairFlow,
    async_create_fix_flow,
)


async def test_async_create_fix_flow_returns_migration_flow(hass, mock_config_entry) -> None:
    """Test fix flow resolver returns migration flow for matching issue ID."""
    mock_config_entry.add_to_hass(hass)

    flow = await async_create_fix_flow(
        hass,
        f"migrate_to_v2_api_{mock_config_entry.entry_id}",
        {"entry_id": mock_config_entry.entry_id},
    )

    assert isinstance(flow, MigrateToV2ApiRepairFlow)


async def test_async_create_fix_flow_raises_for_unknown_issue(
    hass, mock_config_entry
) -> None:
    """Test resolver rejects unknown repair issue IDs."""
    mock_config_entry.add_to_hass(hass)

    with pytest.raises(ValueError):
        await async_create_fix_flow(
            hass,
            "unknown_issue",
            {"entry_id": mock_config_entry.entry_id},
        )


async def test_repair_flow_authorize_updates_token(hass, mock_config_entry) -> None:
    """Test repair authorize step stores token and reloads entry."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_reload = AsyncMock()

    flow = MigrateToV2ApiRepairFlow(mock_config_entry)
    flow.hass = hass

    with patch(
        "custom_components.homewizard_instant.repairs.async_request_token",
        new=AsyncMock(return_value="new-token"),
    ):
        result = await flow.async_step_authorize(user_input={})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert mock_config_entry.data[CONF_TOKEN] == "new-token"
    hass.config_entries.async_reload.assert_awaited_once_with(mock_config_entry.entry_id)


async def test_repair_flow_authorize_failed_shows_error(hass, mock_config_entry) -> None:
    """Test repair authorize step returns form with error when no token is issued."""
    mock_config_entry.add_to_hass(hass)

    flow = MigrateToV2ApiRepairFlow(mock_config_entry)
    flow.hass = hass

    with patch(
        "custom_components.homewizard_instant.repairs.async_request_token",
        new=AsyncMock(return_value=None),
    ):
        result = await flow.async_step_authorize(user_input={})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "authorize"
    assert result["errors"] == {"base": "authorization_failed"}


async def test_repair_flow_authorize_shows_form_before_submit(
    hass, mock_config_entry
) -> None:
    """Test repair authorize step only requests token after user submission."""
    mock_config_entry.add_to_hass(hass)

    flow = MigrateToV2ApiRepairFlow(mock_config_entry)
    flow.hass = hass

    with patch(
        "custom_components.homewizard_instant.repairs.async_request_token",
        new=AsyncMock(return_value="new-token"),
    ) as request_token:
        result = await flow.async_step_authorize()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "authorize"
    request_token.assert_not_awaited()


async def test_async_create_fix_flow_raises_on_invalid_context(hass) -> None:
    """Test fix flow resolver rejects missing or invalid issue context."""
    with pytest.raises(ValueError):
        await async_create_fix_flow(hass, "migrate_to_v2_api_missing", None)

    with pytest.raises(ValueError):
        await async_create_fix_flow(
            hass,
            "migrate_to_v2_api_invalid",
            {"entry_id": 123},
        )
