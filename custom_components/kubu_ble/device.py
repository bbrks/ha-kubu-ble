"""Turn Bluetooth advertisements into Kubu readings."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from .const import LOCAL_NAME_PREFIXES
from .protocol import (
    COMPANY_ID,
    KubuModel,
    KubuReading,
    battery_status,
    decode,
    make_decoder,
    model_for,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KubuUpdate:
    """What the coordinator hands to each platform."""

    name: str
    rssi: int | None
    reading: KubuReading | None
    serial: str | None = None
    battery_status: str = "unknown"
    model: str | None = None
    model_id: str | None = None


def is_kubu_device(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if this looks like a Kubu BLE sensor."""
    if COMPANY_ID in service_info.manufacturer_data:
        return True
    name = service_info.name or ""
    return name.startswith(LOCAL_NAME_PREFIXES)


class KubuDeviceData:
    """Decode adverts for one sensor."""

    def __init__(
        self,
        key: bytes,
        sensor_type: int,
        on_bad_key: Callable[[], None] | None = None,
        bad_key_threshold: int = 5,
        serial: str | None = None,
        sensor_class: str | None = None,
        product_type: str | None = None,
    ) -> None:
        """Set up with the sensor's dpKey.

        on_bad_key is called once after bad_key_threshold new payloads in a
        row fail to decode, which means the stored key does not match.
        """
        self._key = key
        self._serial = serial
        self._sensor_type = sensor_type
        self._sensor_class = sensor_class
        self._decoder = make_decoder(sensor_type, sensor_class)
        self.model: KubuModel | None = model_for(
            sensor_type, sensor_class, product_type
        )
        self._model_id = " ".join(filter(None, [sensor_class, f"type {sensor_type}"]))
        self._on_bad_key = on_bad_key
        self._bad_key_threshold = bad_key_threshold
        self._bad_count = 0
        self._last_payload: bytes | None = None
        self._last_reading: KubuReading | None = None
        self.sensor_type = sensor_type
        self.battery_status = "unknown"

    @property
    def last_reading(self) -> KubuReading | None:
        """The latest good reading."""
        return self._last_reading

    def update(self, service_info: BluetoothServiceInfoBleak) -> KubuUpdate:
        """Decode the latest advert.

        The state lives in the scan response, so plain adverts (name only)
        just return the last good reading.
        """
        payload = service_info.manufacturer_data.get(COMPANY_ID)
        if payload is not None and payload != self._last_payload:
            self._last_payload = payload
            reading = decode(
                payload, self._key, self._sensor_type, self._sensor_class, self._decoder
            )
            if reading is None:
                self._bad_count += 1
                if self._bad_count == 1:
                    _LOGGER.warning(
                        "Could not decode data from %s; check the dpKey is right",
                        service_info.address,
                    )
                if self._bad_count == self._bad_key_threshold and self._on_bad_key:
                    self._on_bad_key()
            else:
                _LOGGER.debug(
                    "%s: %s (raw %s)",
                    service_info.address,
                    reading.state,
                    reading.plaintext.hex(),
                )
                self._bad_count = 0
                self.battery_status = battery_status(
                    reading.battery_mv, self.battery_status
                )
                # Events sometimes carry a bogus low voltage; keep the last good one.
                if reading.battery_mv is None and self._last_reading is not None:
                    reading = replace(reading, battery_mv=self._last_reading.battery_mv)
                self._last_reading = reading
        return KubuUpdate(
            name=service_info.name or service_info.address,
            rssi=service_info.rssi,
            reading=self._last_reading,
            serial=self._serial,
            battery_status=self.battery_status,
            model=self.model.name if self.model else None,
            model_id=self._model_id,
        )
