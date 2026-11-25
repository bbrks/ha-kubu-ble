"""Sensors for Kubu BLE."""

from __future__ import annotations

from datetime import datetime
from typing import Any, override

from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataProcessor,
    PassiveBluetoothDataUpdate,
    PassiveBluetoothProcessorEntity,
)
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_ADDRESS,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KubuConfigEntry, KubuSensor
from .account import CLOUD_STATUSES
from .cloud_entities import KubuCloudEntity
from .const import (
    CONF_SENSOR_CLASS,
    CONF_SENSOR_TYPE,
    CONF_VIB_SETTING,
    SENSOR_CLASS_RETRO_PRO,
    SENSOR_TYPE_RETRO_PRO,
)
from .device import KubuUpdate
from .entity import add_entities_for, build_update, only_known_entities

DESCRIPTIONS = {
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
    for sensor in entry.runtime_data.sensors.values():
        add = add_entities_for(sensor, async_add_entities)
        processor = PassiveBluetoothDataProcessor(update_to_bluetooth_data_update)
        entry.async_on_unload(
            processor.async_add_entities_listener(
                KubuSensorEntity, only_known_entities(DESCRIPTIONS, add)
            )
        )
        entry.async_on_unload(
            sensor.coordinator.async_register_processor(
                processor, SensorEntityDescription
            )
        )
        add([KubuSensitivitySensor(sensor), KubuBatteryTypeSensor(sensor)])
    if (account := entry.runtime_data.account) is not None:
        async_add_entities(
            [
                KubuCloudStatusSensor(account, "cloud_status"),
                KubuLastCheckSensor(account, "last_check"),
                KubuLastSuccessSensor(account, "last_successful_check"),
                KubuNextCheckSensor(account, "next_check"),
                KubuAccountSensorsSensor(account, "sensors_in_account"),
            ]
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


# As stored in the Kubu account. The app shows "high" as "Default".
VIB_SETTINGS = ["off", "demo", "low", "high"]


class KubuSensitivitySensor(SensorEntity):
    """Vibration sensitivity as saved in the Kubu account.

    The sensor does not broadcast its sensitivity, so this is the value from
    the Kubu account, refreshed daily (or with Check now).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "vibration_sensitivity"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = VIB_SETTINGS
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, sensor: KubuSensor) -> None:
        """Set up from the sensor's stored data."""
        data = sensor.data
        address = data[CONF_ADDRESS]
        self._attr_unique_id = f"{address}-vibration_sensitivity"
        # Named, as this may create the device before any Bluetooth data.
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, address)}, name=sensor.name
        )
        value = data.get(CONF_VIB_SETTING)
        self._attr_native_value = value if value in VIB_SETTINGS else None


# Batteries per sensor class, as the Kubu app's BLE sensor info shows them:
# 2× CR1632 for class retroPro (Retro Pro / Anywhere Pro), else 2× CR2025.
BATTERY_RETRO_PRO = "2× CR1632"
BATTERY_OTHER = "2× CR2025"


class KubuBatteryTypeSensor(SensorEntity):
    """Which batteries the sensor takes (fixed per model)."""

    _attr_has_entity_name = True
    _attr_translation_key = "battery_type"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, sensor: KubuSensor) -> None:
        """Set up from the sensor's stored data."""
        data = sensor.data
        address = data[CONF_ADDRESS]
        self._attr_unique_id = f"{address}-battery_type"
        # Named, as this may create the device before any Bluetooth data.
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, address)}, name=sensor.name
        )
        sensor_class = data.get(CONF_SENSOR_CLASS)
        if sensor_class is None and data.get(CONF_SENSOR_TYPE) == SENSOR_TYPE_RETRO_PRO:
            sensor_class = SENSOR_CLASS_RETRO_PRO  # set up before the class was stored
        if sensor_class is None:
            self._attr_native_value = None
        elif sensor_class == SENSOR_CLASS_RETRO_PRO:
            self._attr_native_value = BATTERY_RETRO_PRO
        else:
            self._attr_native_value = BATTERY_OTHER


# --- The "Kubu cloud" device ---------------------------------------------------


class KubuCloudStatusSensor(KubuCloudEntity, SensorEntity):
    """How the last cloud check went, or offline mode."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CLOUD_STATUSES

    @property
    @override
    def native_value(self) -> str:
        """Return the status."""
        return self.account.status


class _KubuCloudRestoreSensor(KubuCloudEntity, RestoreSensor):
    """A cloud sensor that keeps its last value over reloads and restarts.

    Every change to the sensors reloads the entry (a new KubuAccount), and
    offline mode never checks on its own, so without this it would go blank.
    The restored value is shown until the account has a value of its own.
    """

    _restored_value: Any = None
    _restored_attributes: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last value."""
        await super().async_added_to_hass()
        if (data := await self.async_get_last_sensor_data()) is not None:
            self._restored_value = data.native_value
        if (state := await self.async_get_last_state()) is not None:
            self._restored_attributes = dict(state.attributes)

    def _value(self) -> Any:
        raise NotImplementedError

    @property
    @override
    def native_value(self) -> Any:
        """Return the account's value, else the restored one."""
        value = self._value()
        return self._restored_value if value is None else value


class KubuLastCheckSensor(_KubuCloudRestoreSensor):
    """When the cloud was last checked, good or bad, and why."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def _value(self) -> datetime | None:
        return self.account.last_attempt

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Why the last check ran."""
        if self.account.last_reason is not None:
            return {"reason": self.account.last_reason}
        if self._restored_attributes and "reason" in self._restored_attributes:
            return {"reason": self._restored_attributes["reason"]}
        return None


class KubuLastSuccessSensor(_KubuCloudRestoreSensor):
    """When the cloud was last reached."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def _value(self) -> datetime | None:
        return self.account.last_success


class KubuNextCheckSensor(KubuCloudEntity, SensorEntity):
    """When the next automatic check runs (unknown in offline mode)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the time."""
        return self.account.next_check


class KubuAccountSensorsSensor(_KubuCloudRestoreSensor):
    """How many sensors the Kubu account has, and which are set up here.

    Lists each sensor's name in the Kubu app only: no addresses or keys.
    """

    def _value(self) -> int | None:
        sensors = self.account.account_sensors
        return None if sensors is None else len(sensors)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The account's sensors: name, set up here, heard nearby."""
        if self.account.account_sensors is not None:
            return {"sensors": self.account.account_sensors}
        if self._restored_attributes and "sensors" in self._restored_attributes:
            return {"sensors": self._restored_attributes["sensors"]}
        return None
