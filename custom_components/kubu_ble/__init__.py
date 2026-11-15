"""The Kubu BLE integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.bluetooth import BluetoothScanningMode
from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothProcessorCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DP_KEY,
    CONF_SENSOR_TYPE,
    CONF_SERIAL,
    SENSOR_TYPE_RETRO_PRO,
)
from .device import KubuDeviceData, KubuUpdate
from .protocol import parse_key

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR]

_LOGGER = logging.getLogger(__name__)


@dataclass
class KubuRuntimeData:
    """What a loaded config entry holds."""

    coordinator: PassiveBluetoothProcessorCoordinator[KubuUpdate]
    device: KubuDeviceData


type KubuConfigEntry = ConfigEntry[KubuRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: KubuConfigEntry) -> bool:
    """Set up a Kubu sensor from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    data = KubuDeviceData(
        parse_key(entry.data[CONF_DP_KEY]),
        entry.data.get(CONF_SENSOR_TYPE, SENSOR_TYPE_RETRO_PRO),
        serial=entry.data.get(CONF_SERIAL),
    )
    coordinator = PassiveBluetoothProcessorCoordinator(
        hass,
        _LOGGER,
        address=address,
        # The state is only in the scan response, so we need active scanning.
        mode=BluetoothScanningMode.ACTIVE,
        update_method=data.update,
    )
    entry.runtime_data = KubuRuntimeData(coordinator, data)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Start only after all platforms have subscribed.
    entry.async_on_unload(coordinator.async_start())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KubuConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
