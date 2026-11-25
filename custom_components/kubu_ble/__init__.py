"""The Kubu BLE integration.

There are two kinds of config entry:

- An account entry (since 1.1.0): one per Kubu account, with one subentry
  per sensor. It keeps a refresh token to update sensors from the cloud.
- A legacy entry (before 1.1.0): one sensor, no cloud access after setup.
  These keep working. A repair asks the user to sign in once, then their
  sensors move into an account entry, keeping devices and entity IDs.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.bluetooth import BluetoothScanningMode
from homeassistant.components.bluetooth.passive_update_processor import (
    PASSIVE_UPDATE_PROCESSOR,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigSubentry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .account import (
    REASON_BAD_KEY,
    KubuAccount,
    async_abort_discovery_flows,
    async_account_for,
    async_forget_account,
    is_account_entry,
    is_offline,
    sensor_subentries,
)
from .card import async_register_card
from .cloud import normalize_mac
from .const import (
    ACTIVE_SCAN_DURATION,
    ACTIVE_SCAN_INTERVAL,
    AVAILABILITY_GRACE_SECONDS,
    BAD_KEY_REAUTH_THRESHOLD,
    BURST_COOLDOWN_SECONDS,
    BURST_SCAN_SECONDS,
    BURST_SETTLE_SECONDS,
    CONF_DP_KEY,
    CONF_IGNORED,
    CONF_PRODUCT_TYPE,
    CONF_REFRESH_TOKEN,
    CONF_SENSOR_CLASS,
    CONF_SENSOR_TYPE,
    CONF_SERIAL,
    DOMAIN,
    ISSUE_LEGACY_ENTRIES,
    ISSUE_NOT_IN_ACCOUNT,
    SENSOR_TYPE_RETRO_PRO,
)
from .coordinator import KubuCoordinator
from .device import KubuDeviceData
from .protocol import parse_key
from .scan_trigger import BurstScanTrigger

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)


@dataclass
class KubuSensor:
    """One running sensor."""

    subentry_id: str | None  # None for a legacy entry
    name: str  # the sensor's name in the Kubu app
    data: Mapping[str, Any]
    coordinator: KubuCoordinator
    device: KubuDeviceData


@dataclass
class KubuRuntimeData:
    """What a loaded config entry holds."""

    sensors: dict[str | None, KubuSensor]
    account: KubuAccount | None = None
    # The sensor subentries and refresh token this setup was built from, to
    # spot changes. A copy: HA changes subentry objects in place.
    snapshot: dict[str, _Snapshot] = field(default_factory=dict)
    refresh_token: str | None = None
    offline: bool = False
    # One reload covers any number of changes made in a row.
    reload_scheduled: bool = False


type KubuConfigEntry = ConfigEntry[KubuRuntimeData]
type _Snapshot = tuple[str, str | None, dict[str, Any]]


def _snapshot(entry: ConfigEntry) -> dict[str, _Snapshot]:
    return {
        s.subentry_id: (s.title, s.unique_id, dict(s.data))
        for s in sensor_subentries(entry)
    }


def _restore_key(entry: ConfigEntry, subentry_id: str) -> str:
    return f"{entry.entry_id}_{subentry_id}"


def _start_sensor(
    hass: HomeAssistant,
    entry: KubuConfigEntry,
    data: Mapping[str, Any],
    subentry: ConfigSubentry | None,
) -> KubuSensor:
    address: str = data[CONF_ADDRESS]
    device = KubuDeviceData(
        parse_key(data[CONF_DP_KEY]),
        data.get(CONF_SENSOR_TYPE, SENSOR_TYPE_RETRO_PRO),
        on_bad_key=lambda: _async_on_bad_key(hass, entry),
        bad_key_threshold=BAD_KEY_REAUTH_THRESHOLD,
        serial=data.get(CONF_SERIAL),
        sensor_class=data.get(CONF_SENSOR_CLASS),
        product_type=data.get(CONF_PRODUCT_TYPE),
        name=subentry.title if subentry else None,
    )
    coordinator = KubuCoordinator(
        hass,
        _LOGGER,
        address=address,
        # The state is only in the scan response, so we need active scanning.
        mode=BluetoothScanningMode.ACTIVE,
        update_method=device.update,
        scan_interval=ACTIVE_SCAN_INTERVAL,
        scan_duration=ACTIVE_SCAN_DURATION,
        availability_grace=AVAILABILITY_GRACE_SECONDS,
        restore_key=_restore_key(entry, subentry.subentry_id) if subentry else None,
    )
    return KubuSensor(
        subentry.subentry_id if subentry else None,
        subentry.title if subentry else entry.title,
        data,
        coordinator,
        device,
    )


def _async_on_bad_key(hass: HomeAssistant, entry: KubuConfigEntry) -> None:
    """The stored key stopped decoding the sensor's data.

    An account entry first fetches the keys again with its refresh token
    (it asks for the password only if that fails). A legacy entry, or one
    in offline mode, asks to sign in again.
    """
    account = entry.runtime_data.account
    if account is None or is_offline(entry):
        entry.async_start_reauth(hass)
        return

    async def _refresh_then_ask() -> None:
        _LOGGER.warning("A Kubu sensor key stopped working; fetching keys again")
        await account.async_refresh(REASON_BAD_KEY)
        if account.last_refresh_ok and not account.last_changes:
            # Kubu still has the same key: let the user know.
            entry.async_start_reauth(hass)

    entry.async_create_background_task(
        hass, _refresh_then_ask(), f"{DOMAIN} refresh after bad key"
    )


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Add the Kubu sensor dashboard card."""
    await async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KubuConfigEntry) -> bool:
    """Set up an account entry or a legacy one-sensor entry."""
    if not is_account_entry(entry):
        async_update_legacy_issue(hass)
        sensors = {None: _start_sensor(hass, entry, entry.data, None)}
        entry.runtime_data = KubuRuntimeData(sensors)
        await _async_start(hass, entry)
        return True

    legacy = _legacy_entries_by_mac(hass)
    sensors: dict[str | None, KubuSensor] = {}
    to_adopt: list[tuple[ConfigSubentry, ConfigEntry]] = []
    for subentry in sensor_subentries(entry):
        if old := legacy.get(normalize_mac(subentry.data[CONF_ADDRESS])):
            to_adopt.append((subentry, old))
            continue  # started after the move, so IDs are not taken twice
        sensors[subentry.subentry_id] = _start_sensor(
            hass, entry, subentry.data, subentry
        )
    account = async_account_for(hass, entry)
    entry.runtime_data = KubuRuntimeData(
        sensors,
        account,
        _snapshot(entry),
        entry.data[CONF_REFRESH_TOKEN],
        is_offline(entry),
    )
    await _async_start(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    async_abort_discovery_flows(
        hass, {normalize_mac(s.data[CONF_ADDRESS]) for s in sensor_subentries(entry)}
    )
    if to_adopt:
        hass.async_create_task(
            _async_adopt_legacy(hass, entry, to_adopt), f"{DOMAIN} move sensors"
        )
    else:
        account.async_start()
    return True


async def _async_start(hass: HomeAssistant, entry: KubuConfigEntry) -> None:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Start only after all platforms have subscribed.
    for sensor in entry.runtime_data.sensors.values():
        entry.async_on_unload(sensor.coordinator.async_start())
        entry.async_on_unload(
            BurstScanTrigger(
                hass,
                sensor.coordinator.address,
                settle=BURST_SETTLE_SECONDS,
                duration=BURST_SCAN_SECONDS,
                cooldown=BURST_COOLDOWN_SECONDS,
            ).async_start()
        )


async def async_unload_entry(hass: HomeAssistant, entry: KubuConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: KubuConfigEntry) -> None:
    """Clean up repair issues when an entry is deleted."""
    if is_account_entry(entry):
        async_forget_account(hass, entry)
        for subentry in sensor_subentries(entry):
            _delete_not_in_account_issue(hass, subentry.subentry_id)
    else:
        async_update_legacy_issue(hass, removed=entry.entry_id)


async def _async_update_listener(hass: HomeAssistant, entry: KubuConfigEntry) -> None:
    """Reload when sensors or options change, or after signing in again.

    Remember sensors the user removed, so the cloud refresh does not add
    them back. Adding one again by hand clears that.
    """
    runtime = entry.runtime_data
    if runtime.reload_scheduled:
        return
    old, new = runtime.snapshot, _snapshot(entry)
    ignored = set(entry.data.get(CONF_IGNORED, ()))
    new_ignored = set(ignored)
    for subentry_id, (_title, _unique_id, data) in old.items():
        if subentry_id not in new:
            new_ignored.add(normalize_mac(data[CONF_ADDRESS]))
            _delete_not_in_account_issue(hass, subentry_id)
    new_ignored -= {normalize_mac(data[CONF_ADDRESS]) for _, _, data in new.values()}
    changed = (
        new != old
        or entry.data[CONF_REFRESH_TOKEN] != runtime.refresh_token
        or is_offline(entry) != runtime.offline
    )
    if changed:
        runtime.reload_scheduled = True
    if new_ignored != ignored:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_IGNORED: sorted(new_ignored)}
        )
    if changed:
        hass.config_entries.async_schedule_reload(entry.entry_id)


def _delete_not_in_account_issue(hass: HomeAssistant, subentry_id: str) -> None:
    ir.async_delete_issue(
        hass, DOMAIN, ISSUE_NOT_IN_ACCOUNT.format(subentry_id=subentry_id)
    )


# --- Legacy entries ------------------------------------------------------------


def _legacy_entries_by_mac(hass: HomeAssistant) -> dict[str, ConfigEntry]:
    return {
        normalize_mac(e.data[CONF_ADDRESS]): e
        for e in hass.config_entries.async_entries(DOMAIN, include_ignore=False)
        if not is_account_entry(e) and CONF_ADDRESS in e.data
    }


def async_update_legacy_issue(hass: HomeAssistant, removed: str | None = None) -> None:
    """Ask once to sign in while there are legacy entries; clear it after."""
    remaining = [
        e for e in _legacy_entries_by_mac(hass).values() if e.entry_id != removed
    ]
    if remaining:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_LEGACY_ENTRIES,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_LEGACY_ENTRIES,
            translation_placeholders={"count": str(len(remaining))},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_LEGACY_ENTRIES)


async def _async_adopt_legacy(
    hass: HomeAssistant,
    entry: KubuConfigEntry,
    to_adopt: list[tuple[ConfigSubentry, ConfigEntry]],
) -> None:
    """Move legacy entries' devices and entities into this account entry.

    Entity unique IDs do not depend on the entry, so entity IDs, names,
    areas and history all stay. The legacy entry is unloaded first so the
    same entity IDs are never in use twice. Entities move before devices:
    moving a device to another entry deletes the entities still on the old
    one.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    restore = hass.data[PASSIVE_UPDATE_PROCESSOR].all_restore_data
    try:
        for subentry, old in to_adopt:
            if hass.config_entries.async_get_entry(old.entry_id) is None:
                continue  # already gone
            if old.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(old.entry_id)
            for entity in er.async_entries_for_config_entry(ent_reg, old.entry_id):
                ent_reg.async_update_entity(
                    entity.entity_id,
                    config_entry_id=entry.entry_id,
                    config_subentry_id=subentry.subentry_id,
                )
            for device in dr.async_entries_for_config_entry(dev_reg, old.entry_id):
                dev_reg.async_update_device(
                    device.id,
                    new_config_entry_id=entry.entry_id,
                    new_config_subentry_id=subentry.subentry_id,
                )
            # Keep the last states shown until the sensor is heard again.
            if old_data := restore.pop(old.entry_id, None):
                restore[_restore_key(entry, subentry.subentry_id)] = old_data
            await hass.config_entries.async_remove(old.entry_id)
            _LOGGER.info("Moved Kubu sensor %s into its account entry", subentry.title)
    finally:
        async_update_legacy_issue(hass)
        # Start the moved sensors (and the cloud refresh), even after an error.
        if hass.config_entries.async_get_entry(entry.entry_id) is not None:
            hass.config_entries.async_schedule_reload(entry.entry_id)
