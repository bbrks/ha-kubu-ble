"""Fixtures for Kubu BLE tests."""

from __future__ import annotations

import time

import pytest
from bleak.backends.device import BLEDevice
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_get_advertisement_callback,
)
from homeassistant.core import HomeAssistant

ADDRESS = "94:A0:81:12:34:56"
NAME = "KubuRetroPro-34-56"
TEST_KEY_HEX = "000102030405060708090a0b0c0d0e0f"

LOCKED = "10010B2901B12CA7FFEA000010FF042E"
CLOSED = "10010B2B01AE83A3FFFA000010FF042E"
OPEN = "10010B3101AF392FFFFF000010FF042E"
GLITCH_BATTERY = "1001011901B06C8AFFFA000010FF042E"


def encrypt_hex(plain_hex: str, key_hex: str = TEST_KEY_HEX) -> bytes:
    """Encrypt a plaintext frame the way the sensor does."""
    enc = Cipher(algorithms.AES(bytes.fromhex(key_hex)), modes.ECB()).encryptor()
    return enc.update(bytes.fromhex(plain_hex)) + enc.finalize()


def make_service_info(
    plain_hex: str | None = None,
    *,
    key_hex: str = TEST_KEY_HEX,
    name: str = NAME,
    address: str = ADDRESS,
    rssi: int = -70,
    raw: bytes | None | str = "auto",
) -> BluetoothServiceInfoBleak:
    """Build a fake packet.

    Without plain_hex it is the name-only advert. With plain_hex it is the
    scan response carrying the encrypted state. raw defaults to matching
    real packets; pass None to mimic a backend without raw data.
    """
    mfr = {3535: encrypt_hex(plain_hex, key_hex)} if plain_hex else {}
    if raw == "auto":
        if plain_hex:
            raw = bytes([19, 0xFF, 0xCF, 0x0D]) + mfr[3535]
        else:
            name_bytes = name.encode()
            raw = bytes([2, 0x01, 0x06, len(name_bytes) + 1, 0x09]) + name_bytes
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=rssi,
        manufacturer_data=mfr,
        service_data={},
        service_uuids=[],
        source="local",
        device=BLEDevice(address, name, None),
        advertisement=None,
        connectable=True,
        time=time.monotonic(),
        tx_power=None,
        raw=raw,
    )


def inject(hass: HomeAssistant, info: BluetoothServiceInfoBleak) -> None:
    """Feed an advert into HA's Bluetooth manager."""
    async_get_advertisement_callback(hass)(info)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load custom_components/kubu_ble."""
    return


@pytest.fixture(autouse=True)
def auto_mock_bluetooth(mock_bluetooth):
    """Stop the real Bluetooth stack from starting."""
    return
