"""Tests for the burst-triggered active scan."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.kubu_ble.const import CONF_DP_KEY, CONF_SENSOR_TYPE, DOMAIN

from .conftest import ADDRESS, LOCKED, OPEN, TEST_KEY_HEX, inject, make_service_info


@pytest.fixture
async def request_scan(hass: HomeAssistant):
    """Set up the integration with the active-scan request mocked."""
    mock = AsyncMock()
    with patch(
        "custom_components.kubu_ble.scan_trigger.async_request_active_scan", mock
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=ADDRESS,
            data={
                CONF_ADDRESS: ADDRESS,
                CONF_DP_KEY: TEST_KEY_HEX,
                CONF_SENSOR_TYPE: 8,
            },
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield mock


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_advert_without_state_requests_scan(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, request_scan: AsyncMock
) -> None:
    """Passive adapter: burst advert seen, no scan response -> ask for a window."""
    inject(hass, make_service_info(None))
    await hass.async_block_till_done()
    request_scan.assert_not_called()  # waits for the settle time first

    await _tick(hass, freezer, 1.1)
    request_scan.assert_awaited_once_with(hass, 5.0)


async def test_scan_response_arrives_no_request(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, request_scan: AsyncMock
) -> None:
    """Active adapter: scan response follows the advert -> nothing to do."""
    # On a real active adapter the scan response follows within ~10 ms.
    inject(hass, make_service_info(None))
    inject(hass, make_service_info(LOCKED))
    await _tick(hass, freezer, 1.5)
    request_scan.assert_not_called()
    assert (
        hass.states.get("binary_sensor.kuburetropro_34_56_lock").state == "off"
    )  # locked


async def test_cooldown(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, request_scan: AsyncMock
) -> None:
    # A 5 s burst of name-only adverts: one request only (the window covers it).
    for _ in range(10):
        inject(hass, make_service_info(None))
        await _tick(hass, freezer, 0.5)
    assert request_scan.await_count == 1

    # Still inside the cooldown (about 5 s since the request).
    inject(hass, make_service_info(None))
    await _tick(hass, freezer, 0.4)
    assert request_scan.await_count == 1

    # Just past the cooldown: a new burst gets its own window.
    await _tick(hass, freezer, 1.0)
    inject(hass, make_service_info(None))
    await _tick(hass, freezer, 0.4)
    assert request_scan.await_count == 2


async def test_event_soon_after_idle_advert_gets_window(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, request_scan: AsyncMock
) -> None:
    """Regression: an idle advert's window must not block a real event 8 s later."""
    inject(hass, make_service_info(None))  # idle advert
    await _tick(hass, freezer, 0.4)
    assert request_scan.await_count == 1
    await _tick(hass, freezer, 8)
    inject(hass, make_service_info(None))  # key turned: burst starts
    await _tick(hass, freezer, 0.4)
    assert request_scan.await_count == 2


async def test_burst_then_window_catches_state(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, request_scan: AsyncMock
) -> None:
    """End to end: burst advert, window requested, state arrives in the window."""
    inject(hass, make_service_info(LOCKED))
    await _tick(hass, freezer, 30)
    inject(hass, make_service_info(None))  # door opened: burst starts
    await _tick(hass, freezer, 1.1)
    request_scan.assert_awaited_once()
    inject(hass, make_service_info(OPEN))  # scan response during the window
    await hass.async_block_till_done()
    assert (
        hass.states.get("binary_sensor.kuburetropro_34_56_door").state == "on"
    )  # open


async def test_no_raw_data_still_triggers(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, request_scan: AsyncMock
) -> None:
    """Backends without raw packets: treat as advert, rely on the cooldown."""
    inject(hass, make_service_info(None, raw=None))
    await _tick(hass, freezer, 1.1)
    request_scan.assert_awaited_once()


async def test_request_failure_is_swallowed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, request_scan: AsyncMock
) -> None:
    request_scan.side_effect = RuntimeError("no scanners")
    inject(hass, make_service_info(None))
    await _tick(hass, freezer, 1.1)
    request_scan.assert_awaited_once()  # and no exception escaped


async def test_unload_stops_trigger(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, request_scan: AsyncMock
) -> None:
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    inject(hass, make_service_info(None))  # check pending
    assert await hass.config_entries.async_unload(entry.entry_id)
    await _tick(hass, freezer, 2)
    inject(hass, make_service_info(None))
    await _tick(hass, freezer, 2)
    request_scan.assert_not_called()
