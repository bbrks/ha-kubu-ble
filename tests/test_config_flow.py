"""Tests for the Kubu BLE config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kubu_ble.const import DOMAIN

from .conftest import ADDRESS, NAME, make_service_info

PATCH_SETUP = "custom_components.kubu_ble.async_setup_entry"
PATCH_DISCOVERED = (
    "custom_components.kubu_ble.config_flow.async_discovered_service_info"
)


async def _start_discovery(hass: HomeAssistant, info=None):
    info = info or make_service_info()
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=info
    )


async def test_discovery_and_confirm(hass: HomeAssistant) -> None:
    result = await _start_discovery(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"

    with patch(PATCH_SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["result"].unique_id == ADDRESS


async def test_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS, data={}).add_to_hass(hass)
    result = await _start_discovery(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_not_a_kubu_device(hass: HomeAssistant) -> None:
    info = make_service_info(name="SomethingElse", address="11:22:33:44:55:66")
    result = await _start_discovery(hass, info)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_user_step_picks_device(hass: HomeAssistant) -> None:
    info = make_service_info()
    other = make_service_info(name="SomethingElse", address="11:22:33:44:55:66")
    with patch(PATCH_DISCOVERED, return_value=[info, other]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["step_id"] == "user"
    assert list(result["data_schema"].schema[CONF_ADDRESS].container) == [ADDRESS]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["step_id"] == "confirm"
    with patch(PATCH_SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_step_no_devices(hass: HomeAssistant) -> None:
    with patch(PATCH_DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"
