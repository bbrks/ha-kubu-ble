"""Config flow for Kubu BLE.

Setup signs in to the user's Kubu account once to fetch the sensor's key.
Only the key is stored; the email and password are not.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import (
    ERROR_EMAIL_NOT_VERIFIED,
    ERROR_TOO_MANY_TRIES,
    KubuAuthError,
    KubuCloud,
    KubuCloudError,
    KubuCloudNode,
    normalize_mac,
)
from .const import (
    CLOUD_CACHE_SECONDS,
    CONF_DP_KEY,
    CONF_PRODUCT_TYPE,
    CONF_SENSOR_CLASS,
    CONF_SENSOR_TYPE,
    CONF_SERIAL,
    CONF_VIB_SETTING,
    DATA_CLOUD_CACHE,
    DOMAIN,
)
from .device import is_kubu_device
from .protocol import (
    COMPANY_ID,
    InvalidKey,
    KubuModel,
    decode,
    model_for,
    parse_key,
)

_LOGGER = logging.getLogger(__name__)

ISSUES_URL = "https://github.com/bbrks/ha-kubu-ble/issues"

LOGIN_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)


def _title(info: BluetoothServiceInfoBleak) -> str:
    return info.name or info.address


def _model(node: KubuCloudNode) -> KubuModel | None:
    return model_for(node.sensor_type, node.sensor_class, node.product_type)


def _cached_node(hass: HomeAssistant, address: str) -> KubuCloudNode | None:
    cache = hass.data.get(DATA_CLOUD_CACHE)
    if not cache or time.monotonic() - cache[0] > CLOUD_CACHE_SECONDS:
        return None
    return cache[1].get(normalize_mac(address))


def _store_cache(hass: HomeAssistant, nodes: dict[str, KubuCloudNode]) -> None:
    hass.data[DATA_CLOUD_CACHE] = (time.monotonic(), nodes)


class KubuConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kubu BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Set up the flow."""
        self._address: str | None = None
        self._name: str | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}
        self._node: KubuCloudNode | None = None

    # --- Finding the device -------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device found by Bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not is_kubu_device(discovery_info):
            return self.async_abort(reason="not_supported")
        self._address = discovery_info.address
        self._name = _title(discovery_info)
        self.context["title_placeholders"] = {"name": self._name}
        return await self._async_step_after_device()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a device found nearby."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._address = address
            self._name = _title(self._discovered[address])
            return await self._async_step_after_device()

        current = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=False):
            if info.address in current or info.address in self._discovered:
                continue
            if is_kubu_device(info):
                self._discovered[info.address] = info

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            addr: f"{_title(info)} ({addr})"
                            for addr, info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    async def _async_step_after_device(self) -> ConfigFlowResult:
        assert self._address is not None
        if node := _cached_node(self.hass, self._address):
            # Signed in a moment ago while setting up another sensor.
            self._node = node
            return await self.async_step_confirm()
        return await self.async_step_login()

    # --- Kubu account -------------------------------------------------------

    async def async_step_login(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in to the Kubu account and fetch this sensor's key."""
        assert self._address is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            node, error = await self._async_fetch_node(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                assert node is not None
                if _model(node) is None:
                    return self.async_abort(
                        reason="unsupported_sensor",
                        description_placeholders={
                            "sensor_class": node.sensor_class or "?",
                            "sensor_type": str(node.sensor_type),
                            "issues_url": ISSUES_URL,
                        },
                    )
                if not self._key_matches_live_data(node):
                    errors["base"] = "key_mismatch"
                else:
                    return await self._async_finish(node)

        return self.async_show_form(
            step_id="login",
            data_schema=self.add_suggested_values_to_schema(
                LOGIN_SCHEMA,
                {CONF_EMAIL: user_input[CONF_EMAIL]} if user_input else {},
            ),
            errors=errors,
            description_placeholders={
                "name": self._name or self._address,
                "issues_url": ISSUES_URL,
            },
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup when the key is already known."""
        assert self._node is not None
        if user_input is not None:
            if _model(self._node) is None or not self._key_matches_live_data(
                self._node
            ):
                return await self.async_step_login()
            return await self._async_finish(self._node)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._name or self._address},
        )

    async def _async_finish(self, node: KubuCloudNode) -> ConfigFlowResult:
        """Create the entry, warning first if the model is not verified."""
        model = _model(node)
        if (
            model is not None
            and not model.verified
            and self.source not in ("reauth", "reconfigure")
        ):
            self._node = node
            return await self.async_step_unverified()
        return self._async_create_entry(node)

    async def async_step_unverified(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Say that this model has not been tested, and ask for reports."""
        assert self._node is not None
        if user_input is not None:
            return self._async_create_entry(self._node)
        model = _model(self._node)
        assert model is not None
        return self.async_show_form(
            step_id="unverified",
            description_placeholders={
                "name": self._name or self._address or "",
                "model": model.name,
                "sensor_class": self._node.sensor_class or "?",
                "sensor_type": str(self._node.sensor_type),
                "issues_url": ISSUES_URL,
            },
        )

    async def _async_fetch_node(
        self, email: str, password: str
    ) -> tuple[KubuCloudNode | None, str | None]:
        """Return (node, None) or (None, error key)."""
        assert self._address is not None
        cloud = KubuCloud(async_get_clientsession(self.hass))
        try:
            await cloud.login(email, password)
            nodes = await cloud.get_ble_nodes()
        except KubuAuthError as err:
            _LOGGER.warning("Kubu sign-in rejected: %s", err)
            if err.code == ERROR_TOO_MANY_TRIES:
                return None, "too_many_attempts"
            if err.code == ERROR_EMAIL_NOT_VERIFIED:
                return None, "email_not_verified"
            return None, "invalid_auth"
        except KubuCloudError as err:
            _LOGGER.warning("Kubu cloud error: %s", err)
            return None, "cannot_connect"
        _store_cache(self.hass, nodes)
        node = nodes.get(normalize_mac(self._address))
        if node is None:
            return None, "not_in_account"
        try:
            parse_key(node.dp_key)
        except InvalidKey:
            _LOGGER.warning("Kubu cloud sent a key that is not 32 hex characters")
            return None, "key_mismatch"
        return node, None

    def _key_matches_live_data(self, node: KubuCloudNode) -> bool:
        """Check the key against the latest scan response, if HA has one."""
        assert self._address is not None
        payload = None
        for info in async_discovered_service_info(self.hass, connectable=False):
            if info.address == self._address:
                payload = info.manufacturer_data.get(COMPANY_ID)
                break
        if payload is None:
            return True  # nothing to check against yet
        reading = decode(
            payload, parse_key(node.dp_key), node.sensor_type, node.sensor_class
        )
        return reading is not None

    def _async_create_entry(self, node: KubuCloudNode) -> ConfigFlowResult:
        assert self._address is not None
        data = {
            CONF_ADDRESS: self._address,
            CONF_DP_KEY: parse_key(node.dp_key).hex(),
            CONF_SENSOR_TYPE: node.sensor_type,
            CONF_SERIAL: node.serial,
            CONF_SENSOR_CLASS: node.sensor_class,
            CONF_PRODUCT_TYPE: node.product_type,
            CONF_VIB_SETTING: node.vib_setting,
        }
        if self.source == "reauth":
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        if self.source == "reconfigure":
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data_updates=data
            )
        return self.async_create_entry(title=node.name or self._name, data=data)

    # --- Re-auth (stored key does not decode) -------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Fetch the key again because the stored one does not decode."""
        self._address = entry_data[CONF_ADDRESS]
        self._name = self._get_reauth_entry().title
        return await self.async_step_login()

    # --- Reconfigure ("refresh from Kubu account") ------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in again to refresh the key and settings saved in Kubu.

        For example after changing the vibration sensitivity in the Kubu app.
        """
        entry = self._get_reconfigure_entry()
        self._address = entry.data[CONF_ADDRESS]
        self._name = entry.title
        return await self.async_step_login()
