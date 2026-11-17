"""The Kubu BLE integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.bluetooth import BluetoothScanningMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .const import (
    ACTIVE_SCAN_DURATION,
    ACTIVE_SCAN_INTERVAL,
    AVAILABILITY_GRACE_SECONDS,
    BAD_KEY_REAUTH_THRESHOLD,
    BURST_COOLDOWN_SECONDS,
    BURST_SCAN_SECONDS,
    BURST_SETTLE_SECONDS,
    CONF_DP_KEY,
    CONF_SENSOR_TYPE,
    CONF_SERIAL,
    SENSOR_TYPE_RETRO_PRO,
)
from .coordinator import KubuCoordinator
from .device import KubuDeviceData
from .protocol import parse_key
from .scan_trigger import BurstScanTrigger

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)


@dataclass
class KubuRuntimeData:
    """What a loaded config entry holds."""

    coordinator: KubuCoordinator
    device: KubuDeviceData


type KubuConfigEntry = ConfigEntry[KubuRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: KubuConfigEntry) -> bool:
    """Set up a Kubu sensor from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    data = KubuDeviceData(
        parse_key(entry.data[CONF_DP_KEY]),
        entry.data.get(CONF_SENSOR_TYPE, SENSOR_TYPE_RETRO_PRO),
        on_bad_key=lambda: entry.async_start_reauth(hass),
        bad_key_threshold=BAD_KEY_REAUTH_THRESHOLD,
        serial=entry.data.get(CONF_SERIAL),
    )
    coordinator = KubuCoordinator(
        hass,
        _LOGGER,
        address=address,
        # The state is only in the scan response, so we need active scanning.
        mode=BluetoothScanningMode.ACTIVE,
        update_method=data.update,
        scan_interval=ACTIVE_SCAN_INTERVAL,
        scan_duration=ACTIVE_SCAN_DURATION,
        availability_grace=AVAILABILITY_GRACE_SECONDS,
    )
    entry.runtime_data = KubuRuntimeData(coordinator, data)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Start only after all platforms have subscribed.
    entry.async_on_unload(coordinator.async_start())
    entry.async_on_unload(
        BurstScanTrigger(
            hass,
            address,
            settle=BURST_SETTLE_SECONDS,
            duration=BURST_SCAN_SECONDS,
            cooldown=BURST_COOLDOWN_SECONDS,
        ).async_start()
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KubuConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
