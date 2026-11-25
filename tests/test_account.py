"""Account entries: sensors as subentries, kept up to date from the cloud."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kubu_ble.cloud import API_BASE
from custom_components.kubu_ble.const import CONF_IGNORED, DOMAIN
from custom_components.kubu_ble.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)

from .cloud_fixtures import (
    EMAIL,
    PATCH_NEARBY,
    REFRESH_TOKEN,
    RETRO_PRO_NODE,
    WINDOW_ADDRESS,
    WINDOW_NODE,
    account_entry,
    mock_cloud,
    sensor_subentry,
)
from .conftest import ADDRESS, LOCKED, NAME, TEST_KEY_HEX, inject, make_service_info

LOCK = "binary_sensor.kuburetropro_34_56_lock"
SENSITIVITY = "sensor.kuburetropro_34_56_vibration_sensitivity"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, nearby=()) -> None:
    entry.add_to_hass(hass)
    with patch(PATCH_NEARBY, return_value=list(nearby)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _sensors(entry: MockConfigEntry) -> dict[str, str]:
    return {s.unique_id: s.title for s in entry.subentries.values()}


async def test_sensor_entities_belong_to_their_subentry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    (subentry_id,) = entry.subentries

    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_OFF

    ent = er.async_get(hass).async_get(LOCK)
    assert ent.config_entry_id == entry.entry_id
    assert ent.config_subentry_id == subentry_id
    sensitivity = er.async_get(hass).async_get(SENSITIVITY)
    assert sensitivity.config_subentry_id == subentry_id
    device = dr.async_get(hass).async_get(ent.device_id)
    assert device.config_entry_id == entry.entry_id
    assert device.config_subentry_id == subentry_id
    assert device.name == NAME


async def test_two_sensors_in_one_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    entry = account_entry(
        sensor_subentry(),
        sensor_subentry(
            WINDOW_ADDRESS, "KubuRetroPro-00-01", sensor_type=9, product_type="tt-w"
        ),
    )
    await _setup(hass, entry)
    sensors = entry.runtime_data.sensors
    assert len(sensors) == 2
    # Each sensor saves its last data under its own key.
    assert len({s.coordinator.restore_key for s in sensors.values()}) == 2

    inject(hass, make_service_info(LOCKED))
    inject(
        hass,
        make_service_info(LOCKED, address=WINDOW_ADDRESS, name="KubuRetroPro-00-01"),
    )
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_OFF
    assert hass.states.get("binary_sensor.kuburetropro_00_01_window") is not None


async def test_cloud_refresh_updates_name_and_settings(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A change in the Kubu app shows up without signing in again."""
    mock_cloud(aioclient_mock, {**RETRO_PRO_NODE, "devAlias": "Front door"})
    entry = account_entry(sensor_subentry(vib_setting="low"))
    await _setup(hass, entry)

    (subentry,) = entry.subentries.values()
    assert subentry.title == "Front door"
    assert subentry.data["vib_setting"] == "high"
    # The entry reloaded, so the sensor shows the new value.
    assert hass.states.get(SENSITIVITY).state == "high"


async def test_cloud_refresh_adds_sensors_seen_nearby(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    entry = account_entry()
    # The window sensor is in the account but not in range.
    await _setup(hass, entry, nearby=[make_service_info(LOCKED)])
    assert _sensors(entry) == {ADDRESS: NAME}

    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_OFF


async def test_nearby_sensor_with_wrong_key_is_not_added(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, {**RETRO_PRO_NODE, "dpKey": "AB" * 16})
    entry = account_entry()
    await _setup(hass, entry, nearby=[make_service_info(LOCKED)])
    assert _sensors(entry) == {}


async def test_removed_sensor_is_not_added_back(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(sensor_subentry())
    nearby = [make_service_info(LOCKED)]
    await _setup(hass, entry, nearby=nearby)
    (subentry_id,) = entry.subentries

    with patch(PATCH_NEARBY, return_value=nearby):
        hass.config_entries.async_remove_subentry(entry, subentry_id)
        await hass.async_block_till_done()
        assert entry.data[CONF_IGNORED] == ["94A081123456"]
        await entry.runtime_data.account.async_refresh()
        await hass.async_block_till_done()
    assert _sensors(entry) == {}
    assert er.async_get(hass).async_get(LOCK) is None


async def test_sensor_gone_from_account_raises_issue(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, WINDOW_NODE)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    (subentry_id,) = entry.subentries
    # Named by subentry: diagnostics list issue IDs, and must not show MACs.
    issue_id = f"not_in_account_{subentry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    # It keeps working with its saved key.
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_OFF

    aioclient_mock.clear_requests()
    mock_cloud(aioclient_mock, RETRO_PRO_NODE)
    await entry.runtime_data.account.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_expired_sign_in_asks_again_but_sensors_work(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, refresh_status=401)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]

    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_OFF


async def test_cloud_down_does_not_stop_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{API_BASE}/oauth", status=503)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.account.last_refresh_ok is False
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_OFF


async def test_refreshes_every_few_hours(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    calls = aioclient_mock.call_count
    with patch(PATCH_NEARBY, return_value=[]):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=6, minutes=1))
        await hass.async_block_till_done()
    assert aioclient_mock.call_count > calls
    # Only the refresh token is used, never a password sign-in.
    assert all("/oauth/login" not in str(call[1]) for call in aioclient_mock.mock_calls)


async def test_bad_key_asks_to_sign_in_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    for i in range(5):
        frame = f"10010B29{i:04X}2CA7FFEA000010FF042E"
        inject(hass, make_service_info(frame, key_hex="ff" * 16))
        await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_diagnostics_redacts_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    for secret in (EMAIL, REFRESH_TOKEN, TEST_KEY_HEX, ADDRESS, "AVR000000001"):
        assert secret not in text
    assert diag["last_cloud_refresh_ok"] is True
    (sensor,) = diag["sensors"]
    assert sensor["last_reading"]["state"] == "locked"

    device = dr.async_get(hass).async_get(er.async_get(hass).async_get(LOCK).device_id)
    device_diag = await async_get_device_diagnostics(hass, entry, device)
    assert device_diag["model"] == "Retro Pro"
    assert ADDRESS not in str(device_diag)


async def test_empty_cloud_answer_is_not_a_removal(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{API_BASE}/oauth", status=202, json={"idToken": "x"})
    aioclient_mock.get(f"{API_BASE}/users", json={"records": []})
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    assert entry.runtime_data.account.last_refresh_ok is True
    assert not [i for i in ir.async_get(hass).issues if i[0] == DOMAIN]


async def test_rooms_forbidden_is_not_an_expired_sign_in(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only a rejected refresh means the sign-in expired."""
    aioclient_mock.post(f"{API_BASE}/oauth", status=202, json={"idToken": "x"})
    aioclient_mock.get(
        f"{API_BASE}/users", json={"records": [{"homeId": "h", "role": "guest"}]}
    )
    aioclient_mock.get(f"{API_BASE}/view/rooms", status=403, json={})
    entry = account_entry(sensor_subentry())
    await _setup(hass, entry)
    assert entry.runtime_data.account.last_refresh_ok is False
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_bad_key_fetches_new_key_without_sign_in(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The refresh token gets the new key; no password needed."""
    mock_cloud(aioclient_mock)
    entry = account_entry(sensor_subentry(dp_key="00" * 16))
    with patch(PATCH_NEARBY, return_value=[]):
        # Cloud is down at startup, so the old key stays for now.
        aioclient_mock.post(f"{API_BASE}/oauth", status=503)
        await _setup(hass, entry)
        aioclient_mock.clear_requests()
        mock_cloud(aioclient_mock)
        for i in range(5):
            frame = f"10010B29{i:04X}2CA7FFEA000010FF042E"
            inject(hass, make_service_info(frame))
            await hass.async_block_till_done()
        await hass.async_block_till_done()
    (subentry,) = entry.subentries.values()
    assert subentry.data["dp_key"] == TEST_KEY_HEX
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_one_reload_per_change(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    import custom_components.kubu_ble as kubu

    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    entry = account_entry(
        sensor_subentry(), sensor_subentry(WINDOW_ADDRESS, "KubuRetroPro-00-01")
    )
    await _setup(hass, entry)
    setups = 0
    original = kubu.async_setup_entry

    async def counting(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
        nonlocal setups
        setups += 1
        return await original(hass, entry)

    window = next(s for s in entry.subentries.values() if s.unique_id == WINDOW_ADDRESS)
    with (
        patch(PATCH_NEARBY, return_value=[]),
        patch.object(kubu, "async_setup_entry", counting),
    ):
        hass.config_entries.async_remove_subentry(entry, window.subentry_id)
        await hass.async_block_till_done()
    assert setups == 1
    assert entry.data[CONF_IGNORED] == ["94A0810000 01".replace(" ", "")]


async def test_sensor_in_setup_mode_is_not_added_yet(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, caplog
) -> None:
    mock_cloud(aioclient_mock)
    setup_mode = make_service_info(payload=bytes.fromhex("0F01042E" + "00" * 12))
    entry = account_entry()
    await _setup(hass, entry, nearby=[setup_mode])
    assert _sensors(entry) == {}
    assert "does not decode" not in caplog.text
