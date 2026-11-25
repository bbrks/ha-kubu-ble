"""Repairs for Kubu BLE.

Old one-sensor entries (set up before 1.1.0) can't reach the Kubu cloud.
This repair signs in once and hands over to the config flow, which makes
the account entry; the old entries' sensors then move into it.
"""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .config_flow import (
    IMPORT_NODES,
    IMPORT_USER_ID,
    LOGIN_SCHEMA,
    async_sign_in,
    legacy_sensors,
)
from .const import CONF_REFRESH_TOKEN, DOMAIN, ISSUE_LEGACY_ENTRIES


class LegacyEntriesRepairFlow(RepairsFlow):
    """Sign in to move old one-sensor entries into an account entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Start the fix (user_input is the issue's data here)."""
        return await self.async_step_sign_in()

    async def async_step_sign_in(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Ask for the Kubu sign-in."""
        errors: dict[str, str] = {}
        if user_input is not None:
            sign_in, error = await async_sign_in(
                self.hass, user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            elif not set(legacy_sensors(self.hass)) & set(sign_in.nodes):
                errors["base"] = "no_sensors_to_move"
            else:
                await self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": SOURCE_IMPORT},
                    data={
                        CONF_EMAIL: sign_in.email,
                        CONF_REFRESH_TOKEN: sign_in.refresh_token,
                        IMPORT_USER_ID: sign_in.user_id,
                        IMPORT_NODES: sign_in.nodes,
                    },
                )
                # Sensors from other accounts raise this notice again once
                # the move is done.
                return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="sign_in",
            data_schema=self.add_suggested_values_to_schema(
                LOGIN_SCHEMA,
                {CONF_EMAIL: user_input[CONF_EMAIL]} if user_input else {},
            ),
            errors=errors,
            description_placeholders={"count": str(len(legacy_sensors(self.hass)))},
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for an issue."""
    assert issue_id == ISSUE_LEGACY_ENTRIES
    return LegacyEntriesRepairFlow()
