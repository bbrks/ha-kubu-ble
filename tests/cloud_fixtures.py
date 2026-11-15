"""Fake Kubu cloud responses (shapes copied from real responses, values made up)."""

from __future__ import annotations

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kubu_ble.cloud import API_BASE

from .conftest import TEST_KEY_HEX

HOME_ID = "0123456789abcdef0123456789abcdef"
TOKEN = "test-id-token"

LOGIN_RESPONSE = {
    # The API wants the ID token as Bearer; the access token must not be used.
    "accessToken": "test-access-token-not-for-api",
    "idToken": TOKEN,
    "refreshToken": "refresh",
    "tokenType": "Bearer",
    "expiresIn": 3600,
    "emailVerified": True,
    "homes": [{"id": HOME_ID, "name": "Home", "role": "sAdmin"}],
}

RETRO_PRO_NODE = {
    "channel": "BLE",
    "homeId": HOME_ID,
    "sNo": "94A081123456",
    "provKey": "ff" * 16,
    "serial": "AVR000000001",
    "dpKey": TEST_KEY_HEX.upper(),
    "productType": "af-d",
    "devAlias": "KubuRetroPro-34-56",
    "fwVer": "04.46",
    "type": 8,
    "class": "retroPro",
    "vibSetting": "high",
}

# A tilt & turn window. Anywhere Pro sensors use the Retro Pro hardware
# (serial "AVR..."), so the app stores them with class "retroPro" too.
WINDOW_NODE = {
    **RETRO_PRO_NODE,
    "sNo": "94A081000001",
    "devAlias": "KubuRetroPro-00-01",
    "productType": "tt-w",
    "type": 9,
}

# A Kubu Door Sensor (serial "AVM...") in a lift-lever door lock.
DOOR_SENSOR_NODE = {
    **RETRO_PRO_NODE,
    "serial": "AVM000000001",
    "devAlias": "KubuDoorSensor-34-56",
    "productType": "std-d",
    "type": 2,
    "class": "door",
}


def rooms_response(*nodes: dict) -> dict:
    return {"homeId": HOME_ID, "bleNodes": list(nodes), "entities": []}


def mock_cloud(
    aioclient_mock: AiohttpClientMocker,
    *nodes: dict,
    login_status: int = 200,
) -> None:
    aioclient_mock.post(
        f"{API_BASE}/oauth/login",
        status=login_status,
        json=LOGIN_RESPONSE if login_status == 200 else {"message": "nope"},
    )
    aioclient_mock.get(
        f"{API_BASE}/view/rooms",
        json=rooms_response(*(nodes or (RETRO_PRO_NODE,))),
    )
