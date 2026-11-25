"""Tests for the availability grace period."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from bluetooth_data_tools import monotonic_time_coarse
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_ADDRESS, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kubu_ble.const import CONF_DP_KEY, CONF_SENSOR_TYPE, DOMAIN

from .conftest import ADDRESS, LOCKED, TEST_KEY_HEX, inject, make_service_info

LOCK = "binary_sensor.kuburetropro_34_56_lock"
PRESENT = "custom_components.kubu_ble.coordinator.async_address_present"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_DP_KEY: TEST_KEY_HEX, CONF_SENSOR_TYPE: 8},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == "off"
    return entry


def _last_seen(seconds_ago: float):
    info = make_service_info(LOCKED)
    info.time = monotonic_time_coarse() - seconds_ago
    return info


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_short_gap_stays_available(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data.sensors[None].coordinator
    # HA's tracker says "unavailable" after a 40 s gap...
    coordinator._async_handle_unavailable(_last_seen(40))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == "off"
    # ...the sensor is heard again, so the grace check finds it present.
    with patch(PRESENT, return_value=True):
        await _tick(hass, freezer, 300)
    assert hass.states.get(LOCK).state == "off"


async def test_long_silence_goes_unavailable(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    coordinator = entry.runtime_data.sensors[None].coordinator
    coordinator._async_handle_unavailable(_last_seen(40))
    await hass.async_block_till_done()
    with patch(PRESENT, return_value=False):
        await _tick(hass, freezer, 200)
        assert hass.states.get(LOCK).state == "off"  # still within grace
        await _tick(hass, freezer, 100)
    assert hass.states.get(LOCK).state == STATE_UNAVAILABLE


async def test_already_past_grace_goes_unavailable_now(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    entry.runtime_data.sensors[None].coordinator._async_handle_unavailable(
        _last_seen(600)
    )
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_UNAVAILABLE


async def test_unload_cancels_grace_timer(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _setup(hass)
    entry.runtime_data.sensors[None].coordinator._async_handle_unavailable(
        _last_seen(40)
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch(PRESENT, return_value=False):
        await _tick(hass, freezer, 400)  # must not raise or touch states
