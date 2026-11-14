"""Fixtures for Kubu BLE tests."""

from __future__ import annotations

import time

import pytest
from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

ADDRESS = "94:A0:81:12:34:56"
NAME = "KubuRetroPro-34-56"


def make_service_info(
    *,
    name: str = NAME,
    address: str = ADDRESS,
    rssi: int = -70,
) -> BluetoothServiceInfoBleak:
    """Build a fake name-only advert."""
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=rssi,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="local",
        device=BLEDevice(address, name, None),
        advertisement=None,
        connectable=True,
        time=time.monotonic(),
        tx_power=None,
        raw=None,
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load custom_components/kubu_ble."""
    return


@pytest.fixture(autouse=True)
def auto_mock_bluetooth(mock_bluetooth):
    """Stop the real Bluetooth stack from starting."""
    return
