"""Helpers for Kubu BLE devices."""

from __future__ import annotations

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from .const import LOCAL_NAME_PREFIXES
from .protocol import COMPANY_ID


def is_kubu_device(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if this looks like a Kubu BLE sensor."""
    if COMPANY_ID in service_info.manufacturer_data:
        return True
    name = service_info.name or ""
    return name.startswith(LOCAL_NAME_PREFIXES)
