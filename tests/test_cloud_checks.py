"""When the account entry checks the Kubu cloud: daily, and on new sensors."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kubu_ble.const import DOMAIN

from .cloud_fixtures import (
    PATCH_NEARBY,
    RETRO_PRO_NODE,
    WINDOW_ADDRESS,
    WINDOW_NODE,
    account_entry,
    mock_cloud,
    sensor_subentry,
)
from .conftest import ADDRESS, LOCKED, inject, make_service_info

OPEN_FRAME = "10010B3101AF392FFFFF000010FF042E"
WINDOW = make_service_info(LOCKED, address=WINDOW_ADDRESS, name="KubuRetroPro-00-01")
NEIGHBOUR = make_service_info(
    LOCKED, address="11:22:33:44:55:66", name="KubuRetroPro-55-66"
)
SECOND = {**RETRO_PRO_NODE, "sNo": "94A081000002", "devAlias": "KubuRetroPro-00-02"}
SECOND_INFO = make_service_info(
    LOCKED, address="94:A0:81:00:00:02", name="KubuRetroPro-00-02"
)


def _checks(aioclient_mock: AiohttpClientMocker) -> int:
    """How many cloud checks ran (each starts with a token refresh)."""
    return sum(
        1 for call in aioclient_mock.mock_calls if str(call[1]).endswith("/oauth")
    )


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    with patch(PATCH_NEARBY, return_value=[]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def _wait(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _addresses(entry) -> set[str | None]:
    return {s.unique_id for s in entry.subentries.values()}


async def test_new_sensor_heard_is_added_soon(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A sensor just added in the Kubu app shows up without the daily check."""
    mock_cloud(aioclient_mock, RETRO_PRO_NODE)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    assert _checks(aioclient_mock) == 1  # the startup check

    aioclient_mock.clear_requests()
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    with patch(PATCH_NEARBY, return_value=[WINDOW]):
        inject(hass, WINDOW)
        await hass.async_block_till_done()
        # Right after the startup check: waits for the cooldown first.
        await _wait(hass, freezer, 31)
        assert _checks(aioclient_mock) == 0
        await _wait(hass, freezer, 600)
    assert _checks(aioclient_mock) == 1
    assert _addresses(entry) == {ADDRESS, WINDOW_ADDRESS}
    # The discovery card for it is gone too.
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_new_sensors_share_one_check(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE, SECOND)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    await _wait(hass, freezer, 700)  # past the cooldown
    aioclient_mock.clear_requests()
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE, SECOND)
    with patch(PATCH_NEARBY, return_value=[WINDOW, SECOND_INFO]):
        inject(hass, WINDOW)
        inject(hass, SECOND_INFO)
        await hass.async_block_till_done()
        await _wait(hass, freezer, 31)
    assert _checks(aioclient_mock) == 1
    assert len(entry.subentries) == 3


async def test_neighbours_sensor_is_checked_once_a_day(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    mock_cloud(aioclient_mock, RETRO_PRO_NODE)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    await _wait(hass, freezer, 700)
    aioclient_mock.clear_requests()
    mock_cloud(aioclient_mock, RETRO_PRO_NODE)
    with patch(PATCH_NEARBY, return_value=[NEIGHBOUR]):
        inject(hass, NEIGHBOUR)
        await hass.async_block_till_done()
        await _wait(hass, freezer, 31)
        assert _checks(aioclient_mock) == 1
        # Heard again later: not ours, so no more checks today.
        await _wait(hass, freezer, 3600)
        inject(hass, make_service_info(LOCKED, address="11:22:33:44:55:66"))
        await hass.async_block_till_done()
        await _wait(hass, freezer, 700)
        assert _checks(aioclient_mock) == 1
        # A day later the daily check looks at it again (still not ours),
        # so hearing it after that starts no extra check.
        await _wait(hass, freezer, 24 * 3600)
        checks = _checks(aioclient_mock)
        inject(hass, make_service_info(OPEN_FRAME, address="11:22:33:44:55:66"))
        await hass.async_block_till_done()
        await _wait(hass, freezer, 700)
    assert checks == 2  # the trigger's check, then the daily one
    assert _checks(aioclient_mock) == checks
    assert len(entry.subentries) == 1


async def test_set_up_sensor_does_not_start_a_check(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    aioclient_mock.clear_requests()
    mock_cloud(aioclient_mock)
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    await _wait(hass, freezer, 700)
    assert _checks(aioclient_mock) == 0


async def test_sensor_in_setup_mode_is_tried_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    """A sensor being added in the Kubu app is not written off for a day."""
    mock_cloud(aioclient_mock, RETRO_PRO_NODE)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    await _wait(hass, freezer, 700)
    pairing = make_service_info(
        address=WINDOW_ADDRESS,
        name="KubuRetroPro-00-01",
        payload=bytes.fromhex("0F01042E00000000000094A081000001"),
    )
    aioclient_mock.clear_requests()
    mock_cloud(aioclient_mock, RETRO_PRO_NODE)  # not in the account yet
    with patch(PATCH_NEARBY, return_value=[pairing]):
        inject(hass, pairing)
        await hass.async_block_till_done()
        await _wait(hass, freezer, 31)
    assert _checks(aioclient_mock) == 1

    # Now it is set up in the app, and sends its state.
    aioclient_mock.clear_requests()
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    with patch(PATCH_NEARBY, return_value=[WINDOW]):
        inject(hass, WINDOW)
        await hass.async_block_till_done()
        await _wait(hass, freezer, 700)
    assert _checks(aioclient_mock) == 1
    assert _addresses(entry) == {ADDRESS, WINDOW_ADDRESS}


async def test_offline_mode_ignores_new_sensors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    entry = account_entry(sensor_subentry())
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"offline_mode": True})
    await _setup(hass, entry)
    with patch(PATCH_NEARBY, return_value=[WINDOW]):
        inject(hass, WINDOW)
        await hass.async_block_till_done()
        await _wait(hass, freezer, 700)
    assert aioclient_mock.call_count == 0
    assert _addresses(entry) == {ADDRESS}


async def test_discovery_looks_again_for_a_sensor_just_added(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    mock_cloud(aioclient_mock, RETRO_PRO_NODE)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    await _wait(hass, freezer, 61)  # the startup list is now a minute old
    aioclient_mock.clear_requests()
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    with patch(PATCH_NEARBY, return_value=[WINDOW]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=WINDOW
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "sensor_added"
    assert _addresses(entry) == {ADDRESS, WINDOW_ADDRESS}
