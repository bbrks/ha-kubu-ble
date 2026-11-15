"""Shared helpers for Kubu entities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataUpdate,
    PassiveBluetoothEntityKey,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription

from .const import MANUFACTURER
from .device import KubuUpdate
from .protocol import KubuReading


def build_update[T](
    update: KubuUpdate,
    descriptions: dict[str, EntityDescription],
    values: dict[str, Callable[[KubuUpdate, KubuReading], Any]],
) -> PassiveBluetoothDataUpdate[T]:
    """Make a PassiveBluetoothDataUpdate from a KubuUpdate.

    Entities are only created once we have a good reading, so a wrong key
    does not leave a set of empty entities behind.
    """
    device = DeviceInfo(name=update.name, manufacturer=MANUFACTURER, model="Retro Pro")
    if update.serial:
        device["serial_number"] = update.serial
    reading = update.reading
    if reading is None:
        return PassiveBluetoothDataUpdate(devices={None: device})

    device["sw_version"] = reading.firmware
    keys = {k: PassiveBluetoothEntityKey(k, None) for k in descriptions}
    return PassiveBluetoothDataUpdate(
        devices={None: device},
        entity_descriptions={keys[k]: d for k, d in descriptions.items()},
        entity_data={keys[k]: fn(update, reading) for k, fn in values.items()},
    )
