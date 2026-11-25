"""Kubu account entries: one per Kubu account, one subentry per sensor.

The sensors are read locally over Bluetooth. The cloud is only needed once,
to get each sensor's key. Unless offline mode is on, the account entry also
keeps a refresh token (never the password) and checks the cloud daily, and
when a new Kubu sensor is heard, to:

- keep each sensor's name, key and settings up to date,
- add sensors from the account as soon as they are seen nearby,
- say when a sensor is no longer in the account.

Setup never waits for the cloud: if it is down, the sensors still work.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_register_callback,
)
from homeassistant.config_entries import (
    SOURCE_BLUETOOTH,
    SOURCE_IGNORE,
    ConfigEntry,
    ConfigSubentry,
    ConfigSubentryData,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

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
    CLOUD_TRIGGER_COOLDOWN_SECONDS,
    CLOUD_TRIGGER_DELAY_SECONDS,
    CONF_DP_KEY,
    CONF_IGNORED,
    CONF_OFFLINE,
    CONF_PRODUCT_TYPE,
    CONF_REFRESH_TOKEN,
    CONF_SENSOR_CLASS,
    CONF_SENSOR_TYPE,
    CONF_SERIAL,
    CONF_VIB_SETTING,
    DOMAIN,
    ISSUE_NOT_IN_ACCOUNT,
    LOCAL_NAME_PREFIXES,
    NOT_IN_ACCOUNT_SKIP_HOURS,
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


def is_offline(entry: ConfigEntry) -> bool:
    """True if the user chose offline mode for this account entry."""
    return bool(entry.options.get(CONF_OFFLINE))


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


DATA_ACCOUNTS = f"{DOMAIN}_accounts"


@callback
def async_account_for(hass: HomeAssistant, entry: ConfigEntry) -> KubuAccount:
    """The KubuAccount of an account entry, kept over reloads.

    Every sensor change reloads the entry. Keeping the account means its
    last check, cached sensor list and skip list survive that, so a reload
    does not visit the cloud again straight away.
    """
    accounts: dict[str, KubuAccount] = hass.data.setdefault(DATA_ACCOUNTS, {})
    if (account := accounts.get(entry.entry_id)) is None:
        account = accounts[entry.entry_id] = KubuAccount(hass, entry)
    account.status_for_mode()
    return account


@callback
def async_forget_account(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the kept KubuAccount when its entry is deleted."""
    hass.data.get(DATA_ACCOUNTS, {}).pop(entry.entry_id, None)


def get_account(hass: HomeAssistant, entry: ConfigEntry) -> KubuAccount | None:
    """The running KubuAccount of a loaded account entry."""
    runtime = getattr(entry, "runtime_data", None)
    return getattr(runtime, "account", None)


# --- The account -------------------------------------------------------------

# Cloud status, as shown by the "Kubu cloud" status sensor.
STATUS_OK = "ok"
STATUS_CANNOT_CONNECT = "cannot_connect"
STATUS_SIGN_IN_EXPIRED = "sign_in_expired"
STATUS_OFFLINE = "offline_mode"
STATUS_NOT_CHECKED = "not_checked"
CLOUD_STATUSES = [
    STATUS_OK,
    STATUS_CANNOT_CONNECT,
    STATUS_SIGN_IN_EXPIRED,
    STATUS_OFFLINE,
    STATUS_NOT_CHECKED,
]

# Why a cloud check ran.
REASON_STARTUP = "startup"
REASON_SCHEDULED = "scheduled"
REASON_NEW_SENSOR = "new_sensor_heard"
REASON_CHECK_NOW = "check_now"
REASON_BAD_KEY = "bad_key"


class KubuAccount:
    """Talks to the Kubu cloud for one account entry.

    It checks the cloud at setup, once a day, and soon after Bluetooth hears
    a Kubu sensor that is not set up yet (at most once per cooldown). A
    sensor that turns out not to be in the account (a neighbour's) does not
    start another check for a day. In offline mode it only checks when the
    user asks (Check now, Add sensor, signing in again).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Set up for this account entry."""
        self.hass = hass
        self.entry = entry
        self._nodes: dict[str, KubuCloudNode] | None = None
        self._fetched_at = 0.0
        self.last_refresh_ok: bool | None = None
        # How many sensors the last refresh changed or added.
        self.last_changes = 0
        # Shown by the "Kubu cloud" entities.
        self.status = STATUS_OFFLINE if is_offline(entry) else STATUS_NOT_CHECKED
        self.last_attempt: datetime | None = None
        self.last_success: datetime | None = None
        self.last_reason: str | None = None
        # [{name, set_up, nearby}] from the last good check; no addresses.
        self.account_sensors: list[dict[str, Any]] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._next_poll: datetime | None = None
        # The Bluetooth trigger.
        self._pending: set[str] = set()  # MACs heard, waiting for a check
        self._not_ours: dict[str, float] = {}  # MAC -> monotonic expiry
        self._trigger_cancel: CALLBACK_TYPE | None = None
        self._trigger_at: datetime | None = None
        self._last_check_mono: float | None = None

    # --- Listeners, for the "Kubu cloud" entities

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call listener whenever the cloud status changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _async_notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @property
    def next_check(self) -> datetime | None:
        """When the next automatic check runs (None in offline mode)."""
        if is_offline(self.entry):
            return None
        times = [t for t in (self._trigger_at, self._next_poll) if t is not None]
        return min(times, default=None)

    # --- Talking to the cloud

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
    def status_for_mode(self) -> None:
        """Show offline mode (or leave it) after the option changed."""
        if is_offline(self.entry):
            self.status = STATUS_OFFLINE
        elif self.status == STATUS_OFFLINE:
            self.status = STATUS_NOT_CHECKED

    def _checked_lately(self, seconds: float) -> bool:
        """True if the last check worked and was this recent."""
        return (
            self.last_refresh_ok is True
            and self._last_check_mono is not None
            and time.monotonic() - self._last_check_mono < seconds
        )

    @callback
    def async_start(self) -> None:
        """Check now, then daily and when a new sensor is heard.

        In offline mode, never: the sensors are read over Bluetooth only.
        After a reload, skip the first check if the last one was recent.
        """
        if is_offline(self.entry):
            _LOGGER.debug("Offline mode: not checking the Kubu cloud")
            return
        interval = timedelta(hours=CLOUD_REFRESH_INTERVAL_HOURS)
        self._next_poll = dt_util.utcnow() + interval
        self.entry.async_on_unload(
            async_track_time_interval(
                self.hass,
                self._async_scheduled_refresh,
                interval,
                name=f"{DOMAIN} cloud refresh",
            )
        )
        matchers = [
            BluetoothCallbackMatcher(manufacturer_id=COMPANY_ID, connectable=False),
            *(
                BluetoothCallbackMatcher(local_name=f"{prefix}*", connectable=False)
                for prefix in LOCAL_NAME_PREFIXES
            ),
        ]
        for matcher in matchers:
            self.entry.async_on_unload(
                async_register_callback(
                    self.hass,
                    self._async_heard,
                    matcher,
                    BluetoothScanningMode.PASSIVE,
                )
            )
        self.entry.async_on_unload(self._async_cancel_trigger)
        if self._checked_lately(CLOUD_CACHE_SECONDS):
            return
        self.entry.async_create_background_task(
            self.hass, self.async_refresh(REASON_STARTUP), f"{DOMAIN} cloud refresh"
        )

    async def _async_scheduled_refresh(self, _now: Any) -> None:
        self._next_poll = dt_util.utcnow() + timedelta(
            hours=CLOUD_REFRESH_INTERVAL_HOURS
        )
        await self.async_refresh(REASON_SCHEDULED)

    async def async_refresh(
        self, reason: str = REASON_SCHEDULED
    ) -> dict[str, KubuCloudNode] | None:
        """Fetch the account's sensors and apply any changes.

        Returns the sensors, or None if the cloud could not be reached.
        """
        self.last_attempt = dt_util.utcnow()
        self.last_reason = reason
        self._last_check_mono = time.monotonic()
        try:
            nodes = await self.async_get_nodes(max_age=0)
        except KubuAuthError as err:
            self.last_refresh_ok = False
            self._set_status(STATUS_SIGN_IN_EXPIRED)
            _LOGGER.warning(
                "Kubu sign-in has expired; asking to sign in again: %s", err
            )
            self.entry.async_start_reauth(self.hass)
            self._async_notify()
            return None
        except KubuCloudError as err:
            self.last_refresh_ok = False
            self._set_status(STATUS_CANNOT_CONNECT)
            _LOGGER.warning(
                "Could not reach the Kubu cloud; sensors still work, will try again "
                "later: %s",
                err,
            )
            self._async_notify()
            return None
        self.last_refresh_ok = True
        self.last_success = self.last_attempt
        self._set_status(STATUS_OK)
        self.async_apply(nodes)
        self._async_note_strangers(nodes)
        self._async_notify()
        return nodes

    def _set_status(self, status: str) -> None:
        # In offline mode the status says so, even after a manual check.
        self.status = STATUS_OFFLINE if is_offline(self.entry) else status

    @callback
    def async_apply(self, nodes: Mapping[str, KubuCloudNode]) -> None:
        """Update sensors from the cloud and add new ones seen nearby."""
        changes = 0
        for subentry in sensor_subentries(self.entry):
            changes += self._async_update_sensor(subentry, nodes)
        nearby = nearby_sensors(self.hass)
        skip = configured_sensors(self.hass)
        for new in sensors_to_add(self.hass, nodes, nearby, skip):
            _LOGGER.info("Adding Kubu sensor %s from your Kubu account", new["title"])
            self.hass.config_entries.async_add_subentry(
                self.entry, ConfigSubentry(**new)
            )
            changes += 1
        self.last_changes = changes
        set_up = {
            normalize_mac(s.unique_id)
            for s in sensor_subentries(self.entry)
            if s.unique_id
        }
        self.account_sensors = [
            {"name": node.name, "set_up": mac in set_up, "nearby": mac in nearby}
            for mac, node in sorted(nodes.items(), key=lambda item: item[1].name)
        ]

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

    async def async_check_for(self, mac: str) -> dict[str, KubuCloudNode] | None:
        """The account's sensors, for Bluetooth discovery of one sensor.

        Uses the last list if it has the sensor, or if a check worked in the
        last minute. Otherwise runs a full check now (which adds the sensor
        if it is in the account), instead of the Bluetooth trigger's later
        one. Returns None if the cloud could not be reached.
        """
        if self._nodes is not None and (mac in self._nodes or self._checked_lately(60)):
            return self._nodes
        return await self.async_refresh(REASON_NEW_SENSOR)

    # --- The Bluetooth trigger

    @callback
    def _async_heard(
        self, service_info: BluetoothServiceInfoBleak, _change: BluetoothChange
    ) -> None:
        """A Kubu sensor was heard: check the cloud soon if it is new."""
        if not is_kubu_device(service_info):
            return
        mac = normalize_mac(service_info.address)
        if mac in self._pending:
            return
        if (expiry := self._not_ours.get(mac)) is not None:
            if expiry > time.monotonic():
                return
            del self._not_ours[mac]
        if mac in configured_sensors(self.hass):
            return
        _LOGGER.debug(
            "Heard a Kubu sensor that is not set up: %s", service_info.address
        )
        self._pending.add(mac)
        if self._trigger_cancel is None:
            delay: float = CLOUD_TRIGGER_DELAY_SECONDS
            if self._last_check_mono is not None:
                since = time.monotonic() - self._last_check_mono
                delay = max(delay, CLOUD_TRIGGER_COOLDOWN_SECONDS - since)
            self._trigger_at = dt_util.utcnow() + timedelta(seconds=delay)
            self._trigger_cancel = async_call_later(
                self.hass, delay, self._async_triggered_refresh
            )
            self._async_notify()

    async def _async_triggered_refresh(self, _now: Any) -> None:
        self._trigger_cancel = None
        self._trigger_at = None
        await self.async_refresh(REASON_NEW_SENSOR)

    @callback
    def _async_note_strangers(self, nodes: Mapping[str, KubuCloudNode]) -> None:
        """After a good check, skip heard sensors that are not in the account.

        Not while a sensor is in setup mode: it is probably being added in
        the Kubu app right now, and should be picked up soon after.
        """
        expiry = time.monotonic() + NOT_IN_ACCOUNT_SKIP_HOURS * 3600
        for mac, address in nearby_sensors(self.hass).items():
            if mac in nodes:
                continue
            if (payload := live_payload(self.hass, address)) and is_setup_advert(
                payload
            ):
                continue
            if mac not in self._not_ours:
                _LOGGER.debug("Kubu sensor %s is not in your account", address)
            self._not_ours[mac] = expiry
        self._pending.clear()
        self._async_cancel_trigger()

    @callback
    def _async_cancel_trigger(self) -> None:
        if self._trigger_cancel is not None:
            self._trigger_cancel()
            self._trigger_cancel = None
            self._trigger_at = None
