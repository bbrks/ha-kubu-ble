"""Kubu account entries: one per Kubu account, one subentry per sensor.

The sensors are read locally over Bluetooth. The account entry keeps the
email and a refresh token (never the password), so it can visit the Kubu
cloud now and then to:

- keep each sensor's name, key and settings up to date,
- add sensors from the account as soon as they are seen nearby,
- say when a sensor is no longer in the account.

Setup never waits for the cloud: if it is down, the sensors still work.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.config_entries import (
    SOURCE_BLUETOOTH,
    SOURCE_IGNORE,
    ConfigEntry,
    ConfigSubentry,
    ConfigSubentryData,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .cloud import (
    KubuAuthError,
    KubuCloud,
    KubuCloudError,
    KubuCloudNode,
    normalize_mac,
)
from .const import (
    CLOUD_CACHE_SECONDS,
    CLOUD_REFRESH_INTERVAL_HOURS,
    CONF_DP_KEY,
    CONF_IGNORED,
    CONF_PRODUCT_TYPE,
    CONF_REFRESH_TOKEN,
    CONF_SENSOR_CLASS,
    CONF_SENSOR_TYPE,
    CONF_SERIAL,
    CONF_VIB_SETTING,
    DOMAIN,
    ISSUE_NOT_IN_ACCOUNT,
    SUBENTRY_SENSOR,
)
from .device import is_kubu_device
from .protocol import (
    COMPANY_ID,
    InvalidKey,
    KubuModel,
    decode,
    is_setup_advert,
    model_for,
    parse_key,
)

_LOGGER = logging.getLogger(__name__)


# --- Helpers shared with the config flow ------------------------------------


def is_account_entry(entry: ConfigEntry) -> bool:
    """Return True for an account entry, False for an old one-sensor entry."""
    return CONF_REFRESH_TOKEN in entry.data


def sensor_subentries(entry: ConfigEntry) -> list[ConfigSubentry]:
    """The sensor subentries of an account entry."""
    return [s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_SENSOR]


def node_model(node: KubuCloudNode) -> KubuModel | None:
    """The model of a sensor from the cloud, or None if it is not known."""
    return model_for(node.sensor_type, node.sensor_class, node.product_type)


def sensor_data(address: str, node: KubuCloudNode) -> dict[str, Any]:
    """What we store for one sensor (raises InvalidKey for a bad key)."""
    return {
        CONF_ADDRESS: address,
        CONF_DP_KEY: parse_key(node.dp_key).hex(),
        CONF_SENSOR_TYPE: node.sensor_type,
        CONF_SERIAL: node.serial,
        CONF_SENSOR_CLASS: node.sensor_class,
        CONF_PRODUCT_TYPE: node.product_type,
        CONF_VIB_SETTING: node.vib_setting,
    }


def sensor_subentry(address: str, node: KubuCloudNode) -> ConfigSubentryData:
    """A new sensor subentry."""
    return ConfigSubentryData(
        data=sensor_data(address, node),
        subentry_type=SUBENTRY_SENSOR,
        title=node.name,
        unique_id=address,
    )


def live_payload(hass: HomeAssistant, address: str) -> bytes | None:
    """The latest Kubu scan response HA has from this address, if any."""
    for info in async_discovered_service_info(hass, connectable=False):
        if info.address == address:
            return info.manufacturer_data.get(COMPANY_ID)
    return None


def key_matches_live_data(
    hass: HomeAssistant, address: str, node: KubuCloudNode
) -> bool:
    """Check the key against the latest scan response, if HA has one."""
    try:
        key = parse_key(node.dp_key)
    except InvalidKey:
        return False
    payload = live_payload(hass, address)
    if payload is None:
        return True  # nothing to check against yet
    return decode(payload, key, node.sensor_type, node.sensor_class) is not None


def nearby_sensors(hass: HomeAssistant) -> dict[str, str]:
    """{normalized MAC: address} of Kubu sensors HA can hear right now."""
    return {
        normalize_mac(info.address): info.address
        for info in async_discovered_service_info(hass, connectable=False)
        if is_kubu_device(info)
    }


def _is_mac(value: str) -> bool:
    mac = normalize_mac(value)
    return len(mac) == 12 and all(c in "0123456789ABCDEF" for c in mac)


@callback
def async_abort_discovery_flows(hass: HomeAssistant, macs: set[str]) -> None:
    """Close discovery cards for sensors that are set up now.

    A Bluetooth flow waiting at sign-in keeps the sensor's address as its
    unique ID, and HA does not check that against subentries.
    """
    for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN):
        unique_id = flow["context"].get("unique_id")
        if (
            flow["context"].get("source") == SOURCE_BLUETOOTH
            and unique_id
            and _is_mac(unique_id)
            and normalize_mac(unique_id) in macs
        ):
            hass.config_entries.flow.async_abort(flow["flow_id"])


def configured_sensors(hass: HomeAssistant, include_removed: bool = True) -> set[str]:
    """Normalized MACs of sensors that are set up or ignored.

    With include_removed, also those the user removed from an account
    entry, which the cloud refresh must not add back.
    """
    macs: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.source == SOURCE_IGNORE or not is_account_entry(entry):
            # An ignored account (unique ID = user ID) is not a sensor.
            if entry.unique_id and _is_mac(entry.unique_id):
                macs.add(normalize_mac(entry.unique_id))
            continue
        if include_removed:
            macs.update(normalize_mac(mac) for mac in entry.data.get(CONF_IGNORED, ()))
        macs.update(
            normalize_mac(s.unique_id) for s in sensor_subentries(entry) if s.unique_id
        )
    return macs


def sensors_to_add(
    hass: HomeAssistant,
    nodes: Mapping[str, KubuCloudNode],
    wanted: Mapping[str, str],
    skip: set[str],
) -> list[ConfigSubentryData]:
    """New sensor subentries for the wanted sensors ({MAC: address}).

    Leaves out sensors in skip, not in the account, of an unknown model, or
    whose key does not decode the data HA has from them.
    """
    found: list[ConfigSubentryData] = []
    for mac, address in wanted.items():
        node = nodes.get(mac)
        if mac in skip or node is None or node_model(node) is None:
            continue
        if (payload := live_payload(hass, address)) and is_setup_advert(payload):
            continue  # in setup mode: no state to check the key with yet
        if not key_matches_live_data(hass, address, node):
            _LOGGER.warning(
                "The key from Kubu for %s does not decode its data; not adding it",
                address,
            )
            continue
        found.append(sensor_subentry(address, node))
    return found


def get_account(hass: HomeAssistant, entry: ConfigEntry) -> KubuAccount | None:
    """The running KubuAccount of a loaded account entry."""
    runtime = getattr(entry, "runtime_data", None)
    return getattr(runtime, "account", None)


# --- The account -------------------------------------------------------------


class KubuAccount:
    """Talks to the Kubu cloud for one account entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Set up for this account entry."""
        self.hass = hass
        self.entry = entry
        self._nodes: dict[str, KubuCloudNode] | None = None
        self._fetched_at = 0.0
        self.last_refresh_ok: bool | None = None
        # How many sensors the last refresh changed or added.
        self.last_changes = 0

    async def async_get_nodes(
        self, max_age: float = CLOUD_CACHE_SECONDS
    ) -> dict[str, KubuCloudNode]:
        """Return {MAC: node} for the account, reusing a recent answer.

        Raises KubuAuthError if the refresh token no longer works.
        """
        if self._nodes is not None and time.monotonic() - self._fetched_at < max_age:
            return self._nodes
        cloud = KubuCloud(async_get_clientsession(self.hass))
        await cloud.refresh(self.entry.data[CONF_REFRESH_TOKEN])
        try:
            nodes = await cloud.get_ble_nodes()
        except KubuAuthError as err:
            # The token was just refreshed, so this is not an expired
            # sign-in (maybe a home shared with us); signing in won't help.
            raise KubuCloudError(str(err)) from err
        self._nodes, self._fetched_at = nodes, time.monotonic()
        return nodes

    @callback
    def async_start(self) -> None:
        """Refresh in the background now, then every few hours."""
        self.entry.async_on_unload(
            async_track_time_interval(
                self.hass,
                self._async_scheduled_refresh,
                timedelta(hours=CLOUD_REFRESH_INTERVAL_HOURS),
                name=f"{DOMAIN} cloud refresh",
            )
        )
        self.entry.async_create_background_task(
            self.hass, self.async_refresh(), f"{DOMAIN} cloud refresh"
        )

    async def _async_scheduled_refresh(self, _now: Any) -> None:
        await self.async_refresh()

    async def async_refresh(self) -> None:
        """Fetch the account's sensors and apply any changes."""
        try:
            nodes = await self.async_get_nodes(max_age=0)
        except KubuAuthError as err:
            self.last_refresh_ok = False
            _LOGGER.warning(
                "Kubu sign-in has expired; asking to sign in again: %s", err
            )
            self.entry.async_start_reauth(self.hass)
            return
        except KubuCloudError as err:
            self.last_refresh_ok = False
            _LOGGER.warning(
                "Could not reach the Kubu cloud; sensors still work, will try again "
                "later: %s",
                err,
            )
            return
        self.last_refresh_ok = True
        self.async_apply(nodes)

    @callback
    def async_apply(self, nodes: Mapping[str, KubuCloudNode]) -> None:
        """Update sensors from the cloud and add new ones seen nearby."""
        changes = 0
        for subentry in sensor_subentries(self.entry):
            changes += self._async_update_sensor(subentry, nodes)
        skip = configured_sensors(self.hass)
        for new in sensors_to_add(self.hass, nodes, nearby_sensors(self.hass), skip):
            _LOGGER.info("Adding Kubu sensor %s from your Kubu account", new["title"])
            self.hass.config_entries.async_add_subentry(
                self.entry, ConfigSubentry(**new)
            )
            changes += 1
        self.last_changes = changes

    @callback
    def _async_update_sensor(
        self, subentry: ConfigSubentry, nodes: Mapping[str, KubuCloudNode]
    ) -> bool:
        """Update one sensor; return True if it changed."""
        address = subentry.data[CONF_ADDRESS]
        issue_id = ISSUE_NOT_IN_ACCOUNT.format(subentry_id=subentry.subentry_id)
        node = nodes.get(normalize_mac(address))
        if node is None and not nodes:
            return False  # an empty answer is more likely a hiccup
        if node is None:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="not_in_account",
                translation_placeholders={"name": subentry.title},
            )
            return False
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        if node_model(node) is None:
            return False  # an odd new type from the cloud; keep what works
        try:
            data = sensor_data(address, node)
        except InvalidKey:
            return False
        if data == dict(subentry.data) and node.name == subentry.title:
            return False
        self.hass.config_entries.async_update_subentry(
            self.entry, subentry, data=data, title=node.name
        )
        return True
