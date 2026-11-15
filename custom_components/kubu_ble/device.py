"""Turn Bluetooth advertisements into Kubu readings."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from .const import LOCAL_NAME_PREFIXES
from .protocol import COMPANY_ID, KubuReading, decode

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class KubuUpdate:
    """What the coordinator hands to each platform."""

    name: str
    rssi: int | None
    reading: KubuReading | None
    serial: str | None = None


def is_kubu_device(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if this looks like a Kubu BLE sensor."""
    if COMPANY_ID in service_info.manufacturer_data:
        return True
    name = service_info.name or ""
    return name.startswith(LOCAL_NAME_PREFIXES)


class KubuDeviceData:
    """Decode adverts for one sensor."""

    def __init__(self, key: bytes, sensor_type: int, serial: str | None = None) -> None:
        """Set up with the sensor's dpKey."""
        self._key = key
        self._serial = serial
        self._sensor_type = sensor_type
        self._last_payload: bytes | None = None
        self._last_reading: KubuReading | None = None
        self._bad_count = 0

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
            reading = decode(payload, self._key, self._sensor_type)
            if reading is None:
                self._bad_count += 1
                if self._bad_count == 1:
                    _LOGGER.warning(
                        "Could not decode data from %s; check the dpKey is right",
                        service_info.address,
                    )
            else:
                _LOGGER.debug(
                    "%s: %s (raw %s)",
                    service_info.address,
                    reading.state,
                    reading.plaintext.hex(),
                )
                self._bad_count = 0
                self._last_reading = reading
        return KubuUpdate(
            name=service_info.name or service_info.address,
            rssi=service_info.rssi,
            reading=self._last_reading,
            serial=self._serial,
        )
