"""Tests for the diagnostics download."""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kubu_ble.const import (
    CONF_DP_KEY,
    CONF_SENSOR_CLASS,
    CONF_SENSOR_TYPE,
    CONF_SERIAL,
    DOMAIN,
)
from custom_components.kubu_ble.diagnostics import async_get_config_entry_diagnostics

from .conftest import ADDRESS, LOCKED, OPEN, TEST_KEY_HEX, inject, make_service_info


async def test_diagnostics_redacts_and_shows_frames(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_DP_KEY: TEST_KEY_HEX,
            CONF_SENSOR_TYPE: 8,
            CONF_SERIAL: "AVR000000001",
            CONF_SENSOR_CLASS: "retroPro",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    inject(hass, make_service_info(LOCKED))
    inject(hass, make_service_info(OPEN, key_hex="ff" * 16))  # does not decode
    inject(hass, make_service_info(OPEN))
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert TEST_KEY_HEX not in text
    assert ADDRESS not in text
    assert "AVR000000001" not in text
    assert diag["entry"][CONF_SENSOR_TYPE] == 8
    assert diag["entry"][CONF_SENSOR_CLASS] == "retroPro"
    assert diag["model"] == "Retro Pro"
    assert diag["model_verified"] is True
    assert diag["last_reading"]["state"] == "open"
    assert diag["last_reading"]["firmware"] == "04.46"
    assert diag["frames_that_did_not_decode"] == 1
    assert [f["frame"] and f["frame"]["state"] for f in diag["recent_frames"]] == [
        "locked",
        None,
        "open",
    ]
