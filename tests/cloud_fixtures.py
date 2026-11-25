"""Fake Kubu cloud responses (shapes copied from real responses, values made up)."""

from __future__ import annotations

import base64
import json

from homeassistant.config_entries import ConfigSubentryData
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kubu_ble.cloud import API_BASE

from .conftest import ADDRESS, NAME, TEST_KEY_HEX

HOME_ID = "0123456789abcdef0123456789abcdef"
USER_ID = "11111111-2222-3333-4444-555555555555"
EMAIL = "me@example.com"
REFRESH_TOKEN = "test-refresh-token"


def make_jwt(claims: dict) -> str:
    """An unsigned JWT with these claims (the client never checks signatures)."""

    def part(obj: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()
        return raw.rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(claims)}.sig"


TOKEN = make_jwt({"sub": USER_ID, "token_use": "id"})

LOGIN_RESPONSE = {
    # The API wants the ID token as Bearer; the access token must not be used.
    "accessToken": "test-access-token-not-for-api",
    "idToken": TOKEN,
    "refreshToken": REFRESH_TOKEN,
    "tokenType": "Bearer",
    "expiresIn": 300,
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


# POST /oauth (refresh) answers 202 with new ID and access tokens only.
REFRESH_RESPONSE = {
    "accessToken": "test-access-token-not-for-api",
    "idToken": TOKEN,
    "tokenType": "Bearer",
    "expiresIn": 300,
}


def mock_cloud(
    aioclient_mock: AiohttpClientMocker,
    *nodes: dict,
    login_status: int = 200,
    refresh_status: int = 202,
) -> None:
    aioclient_mock.post(
        f"{API_BASE}/oauth/login",
        status=login_status,
        json=LOGIN_RESPONSE if login_status == 200 else {"message": "nope"},
    )
    aioclient_mock.post(
        f"{API_BASE}/oauth",
        status=refresh_status,
        json=REFRESH_RESPONSE if refresh_status == 202 else {"message": "expired"},
    )
    # After a refresh there are no homes in the answer, so they come from here.
    aioclient_mock.get(
        f"{API_BASE}/users", json={"records": [{"homeId": HOME_ID, "role": "sAdmin"}]}
    )
    aioclient_mock.get(
        f"{API_BASE}/view/rooms",
        json=rooms_response(*(nodes or (RETRO_PRO_NODE,))),
    )


# --- Account entries -----------------------------------------------------------

# Patch to choose which Kubu sensors HA "hears" (flows and the cloud refresh).
PATCH_NEARBY = "custom_components.kubu_ble.account.async_discovered_service_info"

# What a sensor subentry stores for RETRO_PRO_NODE.
SENSOR_DATA = {
    "address": ADDRESS,
    "dp_key": TEST_KEY_HEX,
    "sensor_type": 8,
    "serial": "AVR000000001",
    "sensor_class": "retroPro",
    "product_type": "af-d",
    "vib_setting": "high",
}
WINDOW_ADDRESS = "94:A0:81:00:00:01"  # WINDOW_NODE


def sensor_subentry(
    address: str = ADDRESS, title: str = NAME, **data: object
) -> ConfigSubentryData:
    return ConfigSubentryData(
        data={**SENSOR_DATA, "address": address, **data},
        subentry_type="sensor",
        title=title,
        unique_id=address,
    )


def account_entry(*subentries: ConfigSubentryData, **data: object) -> MockConfigEntry:
    return MockConfigEntry(
        domain="kubu_ble",
        unique_id=USER_ID,
        title=EMAIL,
        data={"email": EMAIL, "refresh_token": REFRESH_TOKEN, **data},
        subentries_data=list(subentries),
    )
