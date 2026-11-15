"""Tests for the Kubu BLE config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kubu_ble.cloud import API_BASE
from custom_components.kubu_ble.const import (
    CONF_DP_KEY,
    CONF_SENSOR_TYPE,
    CONF_SERIAL,
    DOMAIN,
)

from .cloud_fixtures import RETRO_PRO_NODE, WINDOW_NODE, mock_cloud
from .conftest import ADDRESS, LOCKED, NAME, TEST_KEY_HEX, make_service_info

PATCH_SETUP = "custom_components.kubu_ble.async_setup_entry"
PATCH_DISCOVERED = (
    "custom_components.kubu_ble.config_flow.async_discovered_service_info"
)
LOGIN = {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "secret"}
EXPECTED_DATA = {
    CONF_ADDRESS: ADDRESS,
    CONF_DP_KEY: TEST_KEY_HEX,
    CONF_SENSOR_TYPE: 8,
    CONF_SERIAL: "AVR000000001",
}


async def _start_discovery(hass: HomeAssistant, info=None):
    info = info or make_service_info(LOCKED)
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=info
    )


async def test_discovery_and_login(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    info = make_service_info(LOCKED)
    result = await _start_discovery(hass, info)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"

    with (
        patch(PATCH_DISCOVERED, return_value=[info]),
        patch(PATCH_SETUP, return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], LOGIN
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME
    assert result["data"] == EXPECTED_DATA
    assert result["result"].unique_id == ADDRESS
    # The password is never stored.
    assert "secret" not in str(result["data"])


async def test_bad_login(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, login_status=401)
    result = await _start_discovery(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cloud_down(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, login_status=503)
    result = await _start_discovery(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_sensor_not_in_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, WINDOW_NODE)
    result = await _start_discovery(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
    assert result["errors"] == {"base": "not_in_account"}


async def test_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS, data={}).add_to_hass(hass)
    result = await _start_discovery(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_step_picks_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    info = make_service_info(LOCKED)
    other = make_service_info(None, name="SomethingElse", address="11:22:33:44:55:66")
    with patch(PATCH_DISCOVERED, return_value=[info, other]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["step_id"] == "user"
    assert list(result["data_schema"].schema[CONF_ADDRESS].container) == [ADDRESS]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["step_id"] == "login"
    with (
        patch(PATCH_DISCOVERED, return_value=[info]),
        patch(PATCH_SETUP, return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], LOGIN
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == EXPECTED_DATA


async def test_user_step_no_devices(hass: HomeAssistant) -> None:
    with patch(PATCH_DISCOVERED, return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_locked_out_and_unverified_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    for code, error in ((1105, "too_many_attempts"), (1141, "email_not_verified")):
        aioclient_mock.clear_requests()
        aioclient_mock.post(
            f"{API_BASE}/oauth/login",
            status=401 if code == 1141 else 403,
            json={"code": code},
        )
        result = await _start_discovery(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], LOGIN
        )
        assert result["errors"] == {"base": error}
        hass.config_entries.flow.async_abort(result["flow_id"])


async def test_cloud_key_does_not_match_live_data(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, {**RETRO_PRO_NODE, "dpKey": "AB" * 16})
    info = make_service_info(LOCKED)
    result = await _start_discovery(hass, info)
    with patch(PATCH_DISCOVERED, return_value=[info]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], LOGIN
        )
    assert result["errors"] == {"base": "key_mismatch"}


async def test_cloud_key_not_hex(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A key that is not 32 hex characters is an error, not a crash."""
    mock_cloud(aioclient_mock, {**RETRO_PRO_NODE, "dpKey": "not-a-hex-key"})
    result = await _start_discovery(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "key_mismatch"}
