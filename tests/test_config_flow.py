"""Tests for the Kubu BLE config flow (account entries) and the sensor flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kubu_ble.cloud import API_BASE
from custom_components.kubu_ble.const import (
    CONF_DP_KEY,
    CONF_PRODUCT_TYPE,
    CONF_REFRESH_TOKEN,
    CONF_SENSOR_TYPE,
    CONF_VIB_SETTING,
    DOMAIN,
)

from .cloud_fixtures import (
    DOOR_SENSOR_NODE,
    EMAIL,
    LOGIN_RESPONSE,
    PATCH_NEARBY,
    REFRESH_TOKEN,
    RETRO_PRO_NODE,
    SENSOR_DATA,
    USER_ID,
    WINDOW_ADDRESS,
    WINDOW_NODE,
    account_entry,
    make_jwt,
    mock_cloud,
    sensor_subentry,
)
from .conftest import ADDRESS, LOCKED, NAME, TEST_KEY_HEX, make_service_info

PATCH_SETUP = "custom_components.kubu_ble.async_setup_entry"
LOGIN = {CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"}
ACCOUNT_DATA = {CONF_EMAIL: EMAIL, CONF_REFRESH_TOKEN: REFRESH_TOKEN}


async def _start_discovery(hass: HomeAssistant, info=None):
    info = info or make_service_info(LOCKED)
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=info
    )


async def _start_user(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _sign_in(hass: HomeAssistant, flow_id: str, nearby=()):
    with (
        patch(PATCH_NEARBY, return_value=list(nearby)),
        patch(PATCH_SETUP, return_value=True),
    ):
        return await hass.config_entries.flow.async_configure(flow_id, LOGIN)


def _sensors(entry: config_entries.ConfigEntry) -> dict[str | None, dict]:
    return {s.unique_id: dict(s.data) for s in entry.subentries.values()}


# --- Setting up an account ------------------------------------------------------


async def test_sign_in_adds_sensors_in_range(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """One sign-in sets up the account with every sensor HA can hear."""
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    result = await _start_user(hass)
    assert result["step_id"] == "login"
    # The window sensor is in the account but out of range.
    result = await _sign_in(hass, result["flow_id"], [make_service_info(LOCKED)])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == EMAIL
    assert entry.unique_id == USER_ID
    assert dict(entry.data) == ACCOUNT_DATA
    assert "secret" not in str(entry.data)  # the password is never stored
    assert _sensors(entry) == {ADDRESS: SENSOR_DATA}
    (subentry,) = entry.subentries.values()
    assert subentry.title == NAME


async def test_sign_in_with_no_sensors_in_range(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    result = await _start_user(hass)
    result = await _sign_in(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].subentries == {}


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"message": "nope"}, "invalid_auth"),
        (503, {"message": "down"}, "cannot_connect"),
        (403, {"code": 1105}, "too_many_attempts"),
        (401, {"code": 1141}, "email_not_verified"),
    ],
)
async def test_sign_in_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    body: dict,
    error: str,
) -> None:
    aioclient_mock.post(f"{API_BASE}/oauth/login", status=status, json=body)
    result = await _start_user(hass)
    result = await _sign_in(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_sign_in_without_refresh_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    login = {k: v for k, v in LOGIN_RESPONSE.items() if k != "refreshToken"}
    aioclient_mock.post(f"{API_BASE}/oauth/login", json=login)
    aioclient_mock.get(f"{API_BASE}/view/rooms", json={"bleNodes": []})
    result = await _start_user(hass)
    result = await _sign_in(hass, result["flow_id"])
    assert result["errors"] == {"base": "cannot_connect"}


async def test_untested_models_ask_first(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Untested models get an extra step asking for reports."""
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    window = make_service_info(
        LOCKED, address=WINDOW_ADDRESS, name="KubuRetroPro-00-01"
    )
    result = await _start_user(hass)
    result = await _sign_in(
        hass, result["flow_id"], [make_service_info(LOCKED), window]
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "unverified"
    assert result["description_placeholders"]["sensors"] == (
        "- KubuRetroPro-00-01: Works with Kubu tilt & turn window"
    )
    with patch(PATCH_SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sensors = _sensors(result["result"])
    assert set(sensors) == {ADDRESS, WINDOW_ADDRESS}
    assert sensors[WINDOW_ADDRESS][CONF_SENSOR_TYPE] == 9
    assert sensors[WINDOW_ADDRESS][CONF_PRODUCT_TYPE] == "tt-w"


async def test_odd_sensor_type_in_account_is_skipped(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    other = {**RETRO_PRO_NODE, "sNo": "112233445566", "type": "keypad"}
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, other)
    odd = make_service_info(LOCKED, address="11:22:33:44:55:66")
    result = await _start_user(hass)
    result = await _sign_in(hass, result["flow_id"], [make_service_info(LOCKED), odd])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert set(_sensors(result["result"])) == {ADDRESS}


async def test_signing_in_to_a_set_up_account_adds_new_sensors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(**{CONF_REFRESH_TOKEN: "old"})
    entry.add_to_hass(hass)
    result = await _start_user(hass)
    result = await _sign_in(hass, result["flow_id"], [make_service_info(LOCKED)])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "account_updated"
    assert result["description_placeholders"] == {"count": "1"}
    assert set(_sensors(entry)) == {ADDRESS}
    assert entry.data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN


# --- Bluetooth discovery ------------------------------------------------------


async def test_discovery_signs_in_and_creates_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    info = make_service_info(LOCKED)
    result = await _start_discovery(hass, info)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"
    result = await _sign_in(hass, result["flow_id"], [info])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _sensors(result["result"]) == {ADDRESS: SENSOR_DATA}


async def test_discovery_adds_sensor_to_signed_in_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No sign-in needed: the account entry's refresh token is used."""
    mock_cloud(aioclient_mock)
    entry = account_entry()
    entry.add_to_hass(hass)
    with patch(PATCH_NEARBY, return_value=[]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        result = await _start_discovery(hass)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "sensor_added"
    assert set(_sensors(entry)) == {ADDRESS}
    assert all("/oauth/login" not in str(c[1]) for c in aioclient_mock.mock_calls)


async def test_discovery_of_a_set_up_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    account_entry(sensor_subentry()).add_to_hass(hass)
    result = await _start_discovery(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_of_a_removed_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    account_entry(ignored_sensors=["94A081123456"]).add_to_hass(hass)
    result = await _start_discovery(hass)
    assert result["reason"] == "already_configured"


async def test_discovery_sensor_not_in_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, WINDOW_NODE)
    result = await _start_discovery(hass)
    result = await _sign_in(hass, result["flow_id"])
    assert result["errors"] == {"base": "not_in_account"}


async def test_discovery_unsupported_sensor_type(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A logic type the Kubu app does not know either."""
    mock_cloud(aioclient_mock, {**RETRO_PRO_NODE, "type": 99, "class": "window"})
    result = await _start_discovery(hass)
    result = await _sign_in(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_sensor"


@pytest.mark.parametrize("key", ["AB" * 16, "not-a-hex-key"])
async def test_discovery_key_does_not_match(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, key: str
) -> None:
    mock_cloud(aioclient_mock, {**RETRO_PRO_NODE, "dpKey": key})
    info = make_service_info(LOCKED)
    result = await _start_discovery(hass, info)
    result = await _sign_in(hass, result["flow_id"], [info])
    assert result["errors"] == {"base": "key_mismatch"}
    # hassfest does not allow URLs in strings; the error text uses this.
    assert result["description_placeholders"]["issues_url"].startswith("https://")


async def test_door_class_uses_door_sensor_model(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The app decodes "door" class sensors by class, whatever the type."""
    mock_cloud(aioclient_mock, DOOR_SENSOR_NODE)
    info = make_service_info(LOCKED, name="KubuDoorSensor-34-56")
    result = await _start_discovery(hass, info)
    result = await _sign_in(hass, result["flow_id"])
    assert result["step_id"] == "unverified"
    assert "Door Sensor" in result["description_placeholders"]["sensors"]


# --- Re-auth and reconfigure ------------------------------------------------------


async def test_reauth_account_keeps_new_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(**{CONF_REFRESH_TOKEN: "expired"})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "login"
    result = await _sign_in(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN


async def test_reauth_with_another_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    other = make_jwt({"sub": "someone-else"})
    aioclient_mock.post(
        f"{API_BASE}/oauth/login", json={**LOGIN_RESPONSE, "idToken": other}
    )
    aioclient_mock.get(f"{API_BASE}/view/rooms", json={"bleNodes": []})
    entry = account_entry(**{CONF_REFRESH_TOKEN: "expired"})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await _sign_in(hass, result["flow_id"])
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_REFRESH_TOKEN] == "expired"


async def test_reconfigure_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(**{CONF_REFRESH_TOKEN: "old"})
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await _sign_in(hass, result["flow_id"])
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN


def _legacy_entry(**data) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title=NAME,
        data={CONF_ADDRESS: ADDRESS, CONF_DP_KEY: TEST_KEY_HEX, CONF_SENSOR_TYPE: 8}
        | data,
    )


async def test_legacy_reauth_updates_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = _legacy_entry(**{CONF_DP_KEY: "00" * 16})
    entry.add_to_hass(hass)
    mock_cloud(aioclient_mock)
    result = await entry.start_reauth_flow(hass)
    result = await _sign_in(hass, result["flow_id"])
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_DP_KEY] == TEST_KEY_HEX
    assert CONF_REFRESH_TOKEN not in entry.data


async def test_legacy_reconfigure_refreshes_settings(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = _legacy_entry()
    entry.add_to_hass(hass)
    mock_cloud(aioclient_mock, {**RETRO_PRO_NODE, "vibSetting": "demo"})
    result = await entry.start_reconfigure_flow(hass)
    result = await _sign_in(hass, result["flow_id"])
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_VIB_SETTING] == "demo"


async def test_legacy_reauth_sensor_gone(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = _legacy_entry()
    entry.add_to_hass(hass)
    mock_cloud(aioclient_mock, WINDOW_NODE)
    result = await entry.start_reauth_flow(hass)
    result = await _sign_in(hass, result["flow_id"])
    assert result["errors"] == {"base": "not_in_account"}


# --- Adding a sensor by hand ----------------------------------------------------


async def _loaded_account(hass: HomeAssistant, *subentries) -> MockConfigEntry:
    entry = account_entry(*subentries)
    entry.add_to_hass(hass)
    with patch(PATCH_NEARBY, return_value=[]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_add_sensor_by_hand(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    entry = await _loaded_account(hass, sensor_subentry())
    with patch(PATCH_NEARBY, return_value=[]):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "sensor"), context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        choices = result["data_schema"].schema[CONF_ADDRESS].container
        assert choices == {
            "94A081000001": "KubuRetroPro-00-01 (94:A0:81:00:00:01) – not nearby"
        }
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_ADDRESS: "94A081000001"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert set(_sensors(entry)) == {ADDRESS, WINDOW_ADDRESS}


async def test_add_sensor_by_hand_clears_removed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A sensor the user removed can be added back by hand."""
    mock_cloud(aioclient_mock)
    entry = await _loaded_account(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "ignored_sensors": ["94A081123456"]}
    )
    with patch(PATCH_NEARBY, return_value=[]):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "sensor"), context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], {CONF_ADDRESS: "94A081123456"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data["ignored_sensors"] == []


async def test_add_sensor_when_all_are_set_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    entry = await _loaded_account(hass, sensor_subentry())
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "sensor"), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_sensors"


async def test_legacy_entries_have_no_sensor_flow(hass: HomeAssistant) -> None:
    entry = _legacy_entry()
    entry.add_to_hass(hass)
    assert entry.supported_subentry_types == {}


async def test_discovery_cards_close_when_sensors_are_added(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Two sensors found; signing in on one card sets up both, and closes both."""
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, {**WINDOW_NODE, "type": 8})
    first = make_service_info(LOCKED)
    second = make_service_info(
        LOCKED, address=WINDOW_ADDRESS, name="KubuRetroPro-00-01"
    )
    with patch(PATCH_NEARBY, return_value=[first, second]):
        flow_1 = await _start_discovery(hass, first)
        flow_2 = await _start_discovery(hass, second)
        assert flow_2["step_id"] == "login"
        result = await hass.config_entries.flow.async_configure(
            flow_1["flow_id"], LOGIN
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert set(_sensors(result["result"])) == {ADDRESS, WINDOW_ADDRESS}
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_ignored_account_does_not_block_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """ "Ignore" after sign-in ignores the account; setting it up later works."""
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    window = make_service_info(
        LOCKED, address=WINDOW_ADDRESS, name="KubuRetroPro-00-01"
    )
    result = await _start_discovery(hass, window)
    result = await _sign_in(hass, result["flow_id"], [window])
    assert result["step_id"] == "unverified"
    unique_id = hass.config_entries.flow.async_get(result["flow_id"])["context"][
        "unique_id"
    ]
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IGNORE},
        data={"unique_id": unique_id, "title": "x"},
    )
    await hass.async_block_till_done()

    result = await _start_user(hass)
    result = await _sign_in(hass, result["flow_id"], [make_service_info(LOCKED)])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert entry.source == config_entries.SOURCE_USER
    assert set(_sensors(entry)) == {ADDRESS}


async def test_reauth_of_loaded_account_reloads_it(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, caplog
) -> None:
    mock_cloud(aioclient_mock)
    entry = account_entry(sensor_subentry(), **{CONF_REFRESH_TOKEN: "expired"})
    entry.add_to_hass(hass)
    with patch(PATCH_NEARBY, return_value=[]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], LOGIN
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == REFRESH_TOKEN
    assert entry.runtime_data.refresh_token == REFRESH_TOKEN  # reloaded
    assert "update listener" not in caplog.text
