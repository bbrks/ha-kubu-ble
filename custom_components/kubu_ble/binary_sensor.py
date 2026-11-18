"""Binary sensors for Kubu BLE."""

from __future__ import annotations

from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataProcessor,
    PassiveBluetoothDataUpdate,
    PassiveBluetoothProcessorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KubuConfigEntry
from .device import KubuUpdate
from .entity import build_update
from .protocol import VIBRATION_FORCE, KubuState

DESCRIPTIONS = {
    # LOCK device class: on = unlocked.
    "lock": BinarySensorEntityDescription(
        key="lock",
        device_class=BinarySensorDeviceClass.LOCK,
    ),
    # DOOR device class: on = open.
    "door": BinarySensorEntityDescription(
        key="door",
        device_class=BinarySensorDeviceClass.DOOR,
    ),
    "tamper": BinarySensorEntityDescription(
        key="tamper",
        device_class=BinarySensorDeviceClass.TAMPER,
    ),
    # Byte 10 of the frame ("SureShield" in the Kubu app). Both stay on
    # until the sensor sends its next frame.
    # Any level: 0x10 "vibration activity" or 0x20 "excessive force".
    "vibration": BinarySensorEntityDescription(
        key="vibration",
        translation_key="vibration",
        device_class=BinarySensorDeviceClass.VIBRATION,
    ),
    # Only 0x20: "Security Alert! ... excessive force being applied".
    "excessive_force": BinarySensorEntityDescription(
        key="excessive_force",
        translation_key="excessive_force",
        device_class=BinarySensorDeviceClass.VIBRATION,
    ),
    "battery_low": BinarySensorEntityDescription(
        key="battery_low",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
}


def _lock(_u, r) -> bool | None:
    if r.state is KubuState.LOCKED:
        return False
    if r.state in (KubuState.CLOSED, KubuState.OPEN):
        return True
    return None


def _open(_u, r) -> bool | None:
    if r.state is KubuState.OPEN:
        return True
    if r.state in (KubuState.CLOSED, KubuState.LOCKED):
        return False
    return None


def _tamper(_u, r) -> bool | None:
    if r.state is KubuState.UNKNOWN:
        return None
    return r.state is KubuState.TAMPERED


VALUES = {
    "lock": _lock,
    "door": _open,
    "tamper": _tamper,
    "vibration": lambda _u, r: r.vibration != 0,
    "excessive_force": lambda _u, r: r.vibration == VIBRATION_FORCE,
    "battery_low": lambda u, _r: (
        None if u.battery_status == "unknown" else u.battery_status != "good"
    ),
}


def update_to_bluetooth_data_update(
    update: KubuUpdate,
) -> PassiveBluetoothDataUpdate[bool | None]:
    """Convert a Kubu update to binary sensor data."""
    return build_update(update, DESCRIPTIONS, VALUES)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KubuConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Kubu binary sensors."""
    coordinator = entry.runtime_data.coordinator
    processor = PassiveBluetoothDataProcessor(update_to_bluetooth_data_update)
    entry.async_on_unload(
        processor.async_add_entities_listener(
            KubuBinarySensorEntity, async_add_entities
        )
    )
    entry.async_on_unload(
        coordinator.async_register_processor(processor, BinarySensorEntityDescription)
    )


class KubuBinarySensorEntity(
    PassiveBluetoothProcessorEntity[
        PassiveBluetoothDataProcessor[bool | None, KubuUpdate]
    ],
    BinarySensorEntity,
):
    """A Kubu binary sensor."""

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.processor.entity_data.get(self.entity_key)
