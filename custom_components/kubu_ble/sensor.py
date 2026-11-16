"""Sensors for Kubu BLE."""

from __future__ import annotations

from typing import override

from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataProcessor,
    PassiveBluetoothDataUpdate,
    PassiveBluetoothProcessorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KubuConfigEntry
from .device import KubuUpdate
from .entity import build_update
from .protocol import KubuState

DESCRIPTIONS = {
    # Locked / closed / open / tampered in one entity.
    "state": SensorEntityDescription(
        key="state",
        translation_key="state",
        device_class=SensorDeviceClass.ENUM,
        options=[s.value for s in KubuState],
    ),
    # Firmware version, sent in every frame.
    "firmware": SensorEntityDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "battery_voltage": SensorEntityDescription(
        key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        suggested_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "signal_strength": SensorEntityDescription(
        key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
}

VALUES = {
    "state": lambda _u, r: r.state.value,
    "firmware": lambda _u, r: r.firmware,
    "battery_voltage": lambda _u, r: r.battery_mv,
    "signal_strength": lambda u, _r: u.rssi,
}


def update_to_bluetooth_data_update(
    update: KubuUpdate,
) -> PassiveBluetoothDataUpdate[str | int | None]:
    """Convert a Kubu update to sensor data."""
    return build_update(update, DESCRIPTIONS, VALUES)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KubuConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Kubu sensors."""
    coordinator = entry.runtime_data.coordinator
    processor = PassiveBluetoothDataProcessor(update_to_bluetooth_data_update)
    entry.async_on_unload(
        processor.async_add_entities_listener(KubuSensorEntity, async_add_entities)
    )
    entry.async_on_unload(
        coordinator.async_register_processor(processor, SensorEntityDescription)
    )


class KubuSensorEntity(
    PassiveBluetoothProcessorEntity[
        PassiveBluetoothDataProcessor[str | int | None, KubuUpdate]
    ],
    SensorEntity,
):
    """A Kubu sensor."""

    @property
    @override
    def native_value(self) -> str | int | None:
        """Return the state."""
        return self.processor.entity_data.get(self.entity_key)
