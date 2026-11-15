"""Minimal Kubu cloud client.

Used only during setup, to fetch each sensor's dpKey from the user's Kubu
account. After setup the integration works fully locally.

No Home Assistant imports, so it can move to its own package later.
"""

from __future__ import annotations

import json as json_module
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp


def _dumps(obj: Any) -> bytes:
    # Compact JSON, like Gson.
    return json_module.dumps(obj, separators=(",", ":")).encode()


_LOGGER = logging.getLogger(__name__)

API_BASE = "https://api-green.kubusmart.com"
TIMEOUT = aiohttp.ClientTimeout(total=20)

# Match the official Android app (com.avantis.kubu 8.0.8). It does not set
# its own User-Agent, so OkHttp sends its default one. The app has no
# device-specific header, so this is exactly what a real phone sends.
APP_HEADERS = {
    "User-Agent": "okhttp/5.0.0-alpha.2",
    "Accept-Encoding": "gzip",
}
JSON_CONTENT_TYPE = "application/json; charset=UTF-8"


class KubuCloudError(Exception):
    """Could not talk to the Kubu cloud."""


class KubuAuthError(KubuCloudError):
    """Sign-in was rejected."""

    def __init__(
        self, message: str, status: int | None = None, code: int | None = None
    ) -> None:
        """Keep the HTTP status and Kubu error code."""
        super().__init__(message)
        self.status = status
        self.code = code


# Kubu error codes seen in the app's login screen.
ERROR_BAD_LOGIN = (1005, 1008)  # wrong email or password
ERROR_TOO_MANY_TRIES = 1105  # locked for 5 minutes
ERROR_EMAIL_NOT_VERIFIED = 1141


def _error_detail(body: str) -> tuple[int | None, str]:
    """Pull the Kubu error code and message out of an error body."""
    try:
        data = json_module.loads(body)
    except ValueError:
        return None, body[:200]
    if not isinstance(data, dict):
        return None, body[:200]
    code = data.get("code")
    message = data.get("message") or data.get("error") or ""
    return (code if isinstance(code, int) else None), str(message)[:200]


@dataclass(frozen=True, slots=True)
class KubuCloudNode:
    """A BLE sensor in the user's Kubu account."""

    mac: str  # "AABBCCDDEEFF", upper case, no colons
    name: str
    serial: str | None
    dp_key: str
    sensor_type: int
    sensor_class: str | None
    product_type: str | None
    firmware: str | None


def normalize_mac(mac: str) -> str:
    """Turn any MAC format into AABBCCDDEEFF."""
    return mac.replace(":", "").replace("-", "").upper()


def _node_from_json(data: dict[str, Any]) -> KubuCloudNode | None:
    mac = data.get("sNo") or data.get("nodeMac")
    dp_key = data.get("dpKey")
    if not mac or not dp_key:
        return None
    return KubuCloudNode(
        mac=normalize_mac(mac),
        name=data.get("devAlias") or data.get("name") or mac,
        serial=data.get("serial"),
        dp_key=dp_key,
        sensor_type=int(data.get("type") or 0),
        sensor_class=data.get("class"),
        product_type=data.get("productType"),
        firmware=data.get("fwVer"),
    )


class KubuCloud:
    """Log in and list BLE sensors."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Use the given aiohttp session."""
        self._session = session
        self._token: str | None = None
        self._home_ids: list[str] = []

    async def _request(
        self, method: str, path: str, *, json: Any = None, params: Any = None
    ) -> Any:
        headers = dict(APP_HEADERS)
        if json is not None:
            headers["Content-Type"] = JSON_CONTENT_TYPE
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with self._session.request(
                method,
                f"{API_BASE}{path}",
                data=None if json is None else _dumps(json),
                params=params,
                headers=headers,
                timeout=TIMEOUT,
                # Don't add aiohttp's own headers (User-Agent, Accept).
                skip_auto_headers=("User-Agent", "Accept"),
            ) as resp:
                if resp.status >= 300:
                    code, message = _error_detail(await resp.text())
                    detail = f"{path}: HTTP {resp.status}, code {code}: {message}"
                    if resp.status in (400, 401, 403):
                        raise KubuAuthError(detail, resp.status, code)
                    raise KubuCloudError(detail)
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise KubuCloudError(f"{path}: {err}") from err

    async def login(self, email: str, password: str) -> None:
        """Log in with the email and password used in the Kubu app."""
        data = await self._request(
            "POST", "/oauth/login", json={"email": email.strip(), "password": password}
        )
        # The app sends the Cognito *ID* token as the Bearer token on API
        # calls, not the access token (the API returns 401 for that).
        token = data.get("idToken") if isinstance(data, dict) else None
        if not token:
            keys = sorted(data) if isinstance(data, dict) else type(data).__name__
            raise KubuCloudError(f"/oauth/login: no idToken in response (keys: {keys})")
        self._token = token
        self._home_ids = [
            h["id"]
            for h in data.get("homes") or []
            if isinstance(h, dict) and h.get("id")
        ]

    async def _get_home_ids(self) -> list[str]:
        if self._home_ids:
            return self._home_ids
        # Fallback: list every home this user can see.
        data = await self._request("GET", "/users", params={"type": "all"})
        records = (data.get("records") or []) if isinstance(data, dict) else []
        self._home_ids = [r["homeId"] for r in records if r.get("homeId")]
        return self._home_ids

    async def get_ble_nodes(self) -> dict[str, KubuCloudNode]:
        """Return {mac: node} for every BLE sensor in every home."""
        if not self._token:
            raise KubuAuthError("not logged in")
        nodes: dict[str, KubuCloudNode] = {}
        for home_id in await self._get_home_ids():
            data = await self._request("GET", "/view/rooms", params={"homeId": home_id})
            for raw in (data.get("bleNodes") or []) if isinstance(data, dict) else []:
                if isinstance(raw, dict) and (node := _node_from_json(raw)):
                    nodes[node.mac] = node
        _LOGGER.debug("Found %d BLE sensors in Kubu account", len(nodes))
        return nodes
