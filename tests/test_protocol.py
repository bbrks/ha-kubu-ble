"""Tests for the Kubu advertisement protocol.

The plaintext frames are real frames from a Kubu Retro Pro (taken from the
Kubu app's debug log). The key here is made up; we encrypt the real
plaintext with it so the tests never need a real device key.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.kubu_ble.protocol import (
    InvalidKey,
    KubuState,
    battery_status,
    decode,
    door_state_type8,
    parse,
    parse_key,
)

TEST_KEY_HEX = "000102030405060708090a0b0c0d0e0f"
TEST_KEY = bytes.fromhex(TEST_KEY_HEX)

# (plaintext hex, expected state, expected battery mV, frame type)
REAL_FRAMES = [
    ("10010B2901B12CA7FFEA000010FF042E", KubuState.LOCKED, 2857, 0x10),
    ("20010BAF01B31744FFEA000010FF042E", KubuState.LOCKED, 2991, 0x20),
    ("10010B2B01AE83A3FFFA000010FF042E", KubuState.CLOSED, 2859, 0x10),
    ("10010B3101AF392FFFFF000010FF042E", KubuState.OPEN, 2865, 0x10),
    ("10010BC501ACD0CEFFFA000010FF042E", KubuState.CLOSED, 3013, 0x10),
    # Glitchy battery reading (281 mV) during an event.
    (
        "10010119 01B06C8AFFFA000010FF042E".replace(" ", ""),
        KubuState.CLOSED,
        None,
        0x10,
    ),
]


def encrypt(plain: bytes, key: bytes = TEST_KEY) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(plain) + enc.finalize()


@pytest.mark.parametrize(("plain_hex", "state", "battery", "frame"), REAL_FRAMES)
def test_decode_real_frames(plain_hex, state, battery, frame) -> None:
    payload = encrypt(bytes.fromhex(plain_hex))
    reading = decode(payload, TEST_KEY)
    assert reading is not None
    assert reading.state is state
    assert reading.battery_mv == battery
    assert reading.frame_type == frame
    assert reading.firmware == "04.46"
    assert reading.class_id == 1


def test_decode_wrong_key_returns_none() -> None:
    payload = encrypt(bytes.fromhex(REAL_FRAMES[0][0]))
    assert decode(payload, bytes(16)) is None


def test_decode_wrong_length_returns_none() -> None:
    assert decode(b"\x00" * 15, TEST_KEY) is None


def test_counter_and_event_flag() -> None:
    reading = parse(bytes.fromhex("10010B2901B12CA7FFEA000010FF042E"))
    assert reading.counter == 0x01B1
    assert reading.is_event
    assert not parse(bytes.fromhex("20010BAF01B31744FFEA000010FF042E")).is_event


@pytest.mark.parametrize(
    ("raw", "state"),
    [
        (0xFFEA, KubuState.LOCKED),
        (0xFFEB, KubuState.LOCKED),
        (0xFFEE, KubuState.LOCKED),
        (0xFFFA, KubuState.CLOSED),
        (0xFFFB, KubuState.CLOSED),
        (0xFFFE, KubuState.CLOSED),
        (0xFFFF, KubuState.OPEN),
        (0xFF00, KubuState.TAMPERED),
    ],
)
def test_door_state_type8(raw, state) -> None:
    assert door_state_type8(raw) is state


def test_unknown_sensor_type() -> None:
    reading = parse(bytes.fromhex("10010B2901B12CA7FFEA000010FF042E"), sensor_type=99)
    assert reading.state is KubuState.UNKNOWN


@pytest.mark.parametrize(
    "text",
    [
        TEST_KEY_HEX,
        TEST_KEY_HEX.upper(),
        f"  {TEST_KEY_HEX}  ",
        "00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f",
    ],
)
def test_parse_key_ok(text) -> None:
    assert parse_key(text) == TEST_KEY


@pytest.mark.parametrize("text", ["", "abc", "zz" * 16, TEST_KEY_HEX + "00"])
def test_parse_key_bad(text) -> None:
    with pytest.raises(InvalidKey):
        parse_key(text)


@pytest.mark.parametrize(
    ("mv", "status"),
    [
        (None, "unknown"),
        (2000, "critical"),
        (2134, "critical"),
        (2150, "low"),
        (2181, "good"),
        (3000, "good"),
    ],
)
def test_battery_status(mv, status) -> None:
    assert battery_status(mv) == status


def test_raw_has_state() -> None:
    from custom_components.kubu_ble.protocol import raw_has_state

    advert = bytes.fromhex("02010613094b756275526574726f50726f2d33342d3536")
    scan_rsp = bytes([19, 0xFF, 0xCF, 0x0D]) + bytes(16)
    other_company = bytes([19, 0xFF, 0x4C, 0x00]) + bytes(16)
    assert not raw_has_state(advert)
    assert raw_has_state(scan_rsp)
    assert raw_has_state(advert + scan_rsp)
    assert not raw_has_state(other_company)
    assert not raw_has_state(None)
    assert not raw_has_state(b"")
    assert not raw_has_state(bytes([30, 0xFF, 0xCF]))  # truncated


def test_vibration_byte() -> None:
    assert parse(bytes.fromhex("10010B2901B12CA7FFEA200010FF042E")).vibration == 0x20
    assert parse(bytes.fromhex("10010B2901B12CA7FFEA000010FF042E")).vibration == 0


def test_battery_hysteresis() -> None:
    """Like the Kubu app: once low, only good again above 2600 mV."""
    level = "unknown"
    seq = []
    for mv in (3000, 2170, 2300, 2600, 2601, 2130, 2170, 2700, None):
        level = battery_status(mv, level)
        seq.append(level)
    assert seq == [
        "good",
        "low",  # below 2181
        "low",  # 2300 is not enough to recover
        "low",  # nor exactly 2600
        "good",  # above 2600
        "critical",  # 2134 or less
        "critical",  # stays critical, does not go back to low
        "good",
        "good",  # missing reading keeps the level
    ]
