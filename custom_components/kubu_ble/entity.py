"""Shared helpers for Kubu entities."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataUpdate,
    PassiveBluetoothEntityKey,
)
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MANUFACTURER
from .device import KubuUpdate
from .protocol import KubuReading

if TYPE_CHECKING:
    from . import KubuSensor


def add_entities_for(
    sensor: KubuSensor, async_add_entities: AddConfigEntryEntitiesCallback
) -> AddConfigEntryEntitiesCallback:
    """async_add_entities that puts entities in the sensor's subentry."""
    if sensor.subentry_id is None:
        return async_add_entities  # legacy one-sensor entry
    return partial(async_add_entities, config_subentry_id=sensor.subentry_id)


def build_update[T](
    update: KubuUpdate,
    descriptions: dict[str, EntityDescription],
    values: dict[str, Callable[[KubuUpdate, KubuReading], Any]],
) -> PassiveBluetoothDataUpdate[T]:
    """Make a PassiveBluetoothDataUpdate from a KubuUpdate.

    Entities are only created once we have a good reading, so a wrong key
    does not leave a set of empty entities behind.
    """
    device = DeviceInfo(name=update.name, manufacturer=MANUFACTURER)
    if update.model:
        device["model"] = update.model
    if update.model_id:
        device["model_id"] = update.model_id
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


def only_known_entities(
    descriptions: dict[str, EntityDescription],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> Callable[[list[Entity]], None]:
    """Wrap async_add_entities to match entities to this version's code.

    HA restores each processor's saved entity descriptions on startup. Without
    this, an entity removed in a later version would come back (frozen), and
    a changed description would only apply after a second restart.
    """

    @callback
    def _add(entities: list[Entity]) -> None:
        known = []
        for entity in entities:
            key = entity.entity_key.key  # type: ignore[attr-defined]
            if key in descriptions:
                # Use the code's description, not a restored saved one,
                # so changes (e.g. entity category) apply straight away.
                entity.entity_description = descriptions[key]
                known.append(entity)
        async_add_entities(known)

    return _add
