"""The "Kubu cloud" device: status sensors and the Check now button."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from homeassistant.const import CONF_ADDRESS, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kubu_ble.cloud import API_BASE
from custom_components.kubu_ble.const import (
    CONF_DP_KEY,
    CONF_SENSOR_TYPE,
    DOMAIN,
)

from .cloud_fixtures import (
    EMAIL,
    PATCH_NEARBY,
    REFRESH_TOKEN,
    RETRO_PRO_NODE,
    WINDOW_NODE,
    account_entry,
    mock_cloud,
    sensor_subentry,
)
from .conftest import ADDRESS, LOCKED, NAME, TEST_KEY_HEX, make_service_info

STATUS = "sensor.kubu_cloud_status"
LAST = "sensor.kubu_cloud_last_check"
LAST_OK = "sensor.kubu_cloud_last_successful_check"
NEXT = "sensor.kubu_cloud_next_check"
SENSORS = "sensor.kubu_cloud_sensors_in_account"
BUTTON = "button.kubu_cloud_check_now"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, nearby=()) -> None:
    entry.add_to_hass(hass)
    with patch(PATCH_NEARBY, return_value=list(nearby)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def _press(hass: HomeAssistant) -> None:
    with patch(PATCH_NEARBY, return_value=[]):
        await hass.services.async_call(
            "button", "press", {"entity_id": BUTTON}, blocking=True
        )
        await hass.async_block_till_done()


async def test_cloud_entities_after_a_good_check(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    entry = account_entry(sensor_subentry())
    before = dt_util.utcnow()
    await _setup(hass, entry, nearby=[make_service_info(LOCKED)])

    assert hass.states.get(STATUS).state == "ok"
    last = datetime.fromisoformat(hass.states.get(LAST).state)
    assert last >= before.replace(microsecond=0)
    assert hass.states.get(LAST).attributes["reason"] == "startup"
    assert hass.states.get(LAST_OK).state == hass.states.get(LAST).state
    next_check = datetime.fromisoformat(hass.states.get(NEXT).state)
    assert timedelta(hours=23) < next_check - last < timedelta(hours=25)

    sensors = hass.states.get(SENSORS)
    assert sensors.state == "2"
    assert sensors.attributes["sensors"] == [
        {"name": "KubuRetroPro-00-01", "set_up": False, "nearby": False},
        {"name": NAME, "set_up": True, "nearby": True},
    ]

    # On their own service device, as diagnostics, without private data.
    ent_reg = er.async_get(hass)
    for entity_id in (STATUS, LAST, LAST_OK, NEXT, SENSORS, BUTTON):
        ent = ent_reg.async_get(entity_id)
        assert ent.entity_category == "diagnostic"
        assert ent.config_subentry_id is None
        state = str(hass.states.get(entity_id))
        for secret in (EMAIL, REFRESH_TOKEN, TEST_KEY_HEX, ADDRESS, "94A081"):
            assert secret not in state
    device = dr.async_get(hass).async_get(ent_reg.async_get(STATUS).device_id)
    assert device.name == "Kubu cloud"
    assert device.entry_type is dr.DeviceEntryType.SERVICE


async def test_cloud_down_keeps_last_good_check(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    last_ok = hass.states.get(LAST_OK).state

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{API_BASE}/oauth", status=503)
    await _press(hass)
    assert hass.states.get(STATUS).state == "cannot_connect"
    assert hass.states.get(LAST).attributes["reason"] == "check_now"
    assert hass.states.get(LAST_OK).state == last_ok


async def test_expired_sign_in_status(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, refresh_status=401)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    assert hass.states.get(STATUS).state == "sign_in_expired"
    assert hass.states.get(LAST_OK).state == STATE_UNKNOWN


async def test_offline_mode_and_check_now(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, {**RETRO_PRO_NODE, "devAlias": "Front door"})
    entry = account_entry(sensor_subentry())
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"offline_mode": True})
    await _setup(hass, entry)
    assert hass.states.get(STATUS).state == "offline_mode"
    assert hass.states.get(NEXT).state == STATE_UNKNOWN
    assert hass.states.get(LAST).state == STATE_UNKNOWN
    assert aioclient_mock.call_count == 0

    # The user can still ask for a check.
    await _press(hass)
    assert aioclient_mock.call_count > 0
    assert hass.states.get(STATUS).state == "offline_mode"
    assert hass.states.get(LAST).attributes["reason"] == "check_now"
    assert hass.states.get(LAST_OK).state != STATE_UNKNOWN
    (subentry,) = entry.subentries.values()
    assert subentry.title == "Front door"
    # Still offline: no automatic checks were started by it.
    assert hass.states.get(NEXT).state == STATE_UNKNOWN


async def test_check_now_picks_up_changes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    aioclient_mock.clear_requests()
    mock_cloud(aioclient_mock, {**RETRO_PRO_NODE, "vibSetting": "low"})
    await _press(hass)
    (subentry,) = entry.subentries.values()
    assert subentry.data["vib_setting"] == "low"
    # The reload after the change keeps the status (no blank values).
    assert hass.states.get(STATUS).state == "ok"
    assert hass.states.get(SENSORS).state == "1"


async def test_last_values_are_restored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Offline mode never checks on its own, so show the last known values."""
    old = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
    sensors_attr = [{"name": NAME, "set_up": True, "nearby": True}]
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(LAST_OK, old.isoformat()),
                {
                    "native_value": {
                        "__type": str(datetime),
                        "isoformat": old.isoformat(),
                    },
                    "native_unit_of_measurement": None,
                },
            ),
            (
                State(SENSORS, "1", {"sensors": sensors_attr}),
                {"native_value": 1, "native_unit_of_measurement": None},
            ),
        ],
    )
    entry = account_entry(sensor_subentry())
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"offline_mode": True})
    await _setup(hass, entry)
    assert datetime.fromisoformat(hass.states.get(LAST_OK).state) == old
    assert hass.states.get(SENSORS).state == "1"
    assert hass.states.get(SENSORS).attributes["sensors"] == sensors_attr
    assert hass.states.get(STATUS).state == "offline_mode"


async def test_legacy_entry_has_no_cloud_entities(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title=NAME,
        data={CONF_ADDRESS: ADDRESS, CONF_DP_KEY: TEST_KEY_HEX, CONF_SENSOR_TYPE: 8},
    )
    await _setup(hass, entry)
    assert hass.states.get(STATUS) is None
    assert hass.states.get(BUTTON) is None
