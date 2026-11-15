"""Tests for the Kubu cloud client."""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kubu_ble.cloud import (
    API_BASE,
    KubuAuthError,
    KubuCloud,
    KubuCloudError,
    normalize_mac,
)

from .cloud_fixtures import (
    HOME_ID,
    RETRO_PRO_NODE,
    TOKEN,
    WINDOW_NODE,
    mock_cloud,
)
from .conftest import TEST_KEY_HEX


async def test_login_and_list_nodes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, RETRO_PRO_NODE, WINDOW_NODE)
    cloud = KubuCloud(async_get_clientsession(hass))
    await cloud.login("me@example.com", "secret")
    nodes = await cloud.get_ble_nodes()

    assert set(nodes) == {"94A081123456", "94A081000001"}
    node = nodes["94A081123456"]
    assert node.dp_key == TEST_KEY_HEX.upper()
    assert node.sensor_type == 8
    assert node.serial == "AVR000000001"
    assert node.name == "KubuRetroPro-34-56"

    login_call, rooms_call = aioclient_mock.mock_calls
    assert login_call[0].upper() == "POST"
    assert login_call[2] == b'{"email":"me@example.com","password":"secret"}'
    assert rooms_call[1].query["homeId"] == HOME_ID
    assert rooms_call[3]["Authorization"] == f"Bearer {TOKEN}"


async def test_requests_look_like_the_android_app(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    cloud = KubuCloud(async_get_clientsession(hass))
    await cloud.login("me@example.com", "secret")
    await cloud.get_ble_nodes()

    login_headers = aioclient_mock.mock_calls[0][3]
    rooms_headers = aioclient_mock.mock_calls[1][3]
    for headers in (login_headers, rooms_headers):
        assert headers["User-Agent"] == "okhttp/5.0.0-alpha.2"
        assert headers["Accept-Encoding"] == "gzip"
    assert login_headers["Content-Type"] == "application/json; charset=UTF-8"
    assert "Authorization" not in login_headers
    assert "Content-Type" not in rooms_headers


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_login_rejected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    mock_cloud(aioclient_mock, login_status=status)
    cloud = KubuCloud(async_get_clientsession(hass))
    with pytest.raises(KubuAuthError):
        await cloud.login("me@example.com", "wrong")


async def test_server_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, login_status=500)
    cloud = KubuCloud(async_get_clientsession(hass))
    with pytest.raises(KubuCloudError) as err:
        await cloud.login("me@example.com", "secret")
    assert not isinstance(err.value, KubuAuthError)


async def test_network_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{API_BASE}/oauth/login", exc=aiohttp.ClientError("boom"))
    cloud = KubuCloud(async_get_clientsession(hass))
    with pytest.raises(KubuCloudError):
        await cloud.login("me@example.com", "secret")


async def test_homes_fallback(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """If login has no homes, use /users?type=all."""
    aioclient_mock.post(
        f"{API_BASE}/oauth/login",
        json={"accessToken": "x", "idToken": TOKEN, "homes": []},
    )
    aioclient_mock.get(
        f"{API_BASE}/users", json={"records": [{"homeId": HOME_ID, "role": "sAdmin"}]}
    )
    aioclient_mock.get(f"{API_BASE}/view/rooms", json={"bleNodes": [RETRO_PRO_NODE]})
    cloud = KubuCloud(async_get_clientsession(hass))
    await cloud.login("me@example.com", "secret")
    assert set(await cloud.get_ble_nodes()) == {"94A081123456"}


def test_normalize_mac() -> None:
    assert normalize_mac("94:a0:81:12:34:56") == "94A081123456"
    assert normalize_mac("94A081123456") == "94A081123456"


async def test_login_error_code_is_kept(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        f"{API_BASE}/oauth/login",
        status=403,
        json={"code": 1105, "message": "Too many attempts"},
    )
    cloud = KubuCloud(async_get_clientsession(hass))
    with pytest.raises(KubuAuthError) as err:
        await cloud.login("me@example.com", "secret")
    assert err.value.status == 403
    assert err.value.code == 1105
    assert "Too many attempts" in str(err.value)
    assert "secret" not in str(err.value)


async def test_error_body_not_json(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{API_BASE}/oauth/login", status=401, text="<html>nope")
    cloud = KubuCloud(async_get_clientsession(hass))
    with pytest.raises(KubuAuthError) as err:
        await cloud.login("me@example.com", "secret")
    assert err.value.code is None
    assert "<html>nope" in str(err.value)


async def test_error_body_not_an_object(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{API_BASE}/oauth/login", status=401, json=["nope"])
    cloud = KubuCloud(async_get_clientsession(hass))
    with pytest.raises(KubuAuthError) as err:
        await cloud.login("me@example.com", "secret")
    assert err.value.code is None


async def test_login_without_id_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{API_BASE}/oauth/login", json={"accessToken": "x"})
    cloud = KubuCloud(async_get_clientsession(hass))
    with pytest.raises(KubuCloudError, match="no idToken"):
        await cloud.login("me@example.com", "secret")
