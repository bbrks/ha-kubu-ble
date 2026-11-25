"""Tests for the Kubu advertisement protocol.

The plaintext frames are real frames from a Kubu Retro Pro (taken from the
Kubu app's debug log). The key here is made up; we encrypt the real
plaintext with it so the tests never need a real device key.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.kubu_ble.protocol import (
    MODELS,
    InvalidKey,
    KubuState,
    TiltTurnDecoder,
    battery_status,
    decode,
    door_state_type8,
    make_decoder,
    model_for,
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


# --- Other models (from the Kubu app, not checked on real sensors) ---------


@pytest.mark.parametrize(
    ("raw", "state"),
    [
        (0xFF01, KubuState.CLOSED),  # "Unlocked, Closed" in the app
        (0xFF10, KubuState.LOCKED),
        (0xFF11, KubuState.OPEN),
        (0xFFFF, KubuState.TAMPERED),
        (0xFF00, KubuState.UNKNOWN),
        (0xFFEA, KubuState.UNKNOWN),  # a Retro Pro "locked" means nothing here
    ],
)
def test_door_sensor_class(raw, state) -> None:
    """Class "door" wins over the type, like in the app."""
    for sensor_type in (2, 8):
        assert make_decoder(sensor_type, "door")(raw) is state


@pytest.mark.parametrize("sensor_type", [2, 3])
@pytest.mark.parametrize(
    ("raw", "state"),
    [
        (0xFF37, KubuState.OPEN),  # 55
        (0xFF3B, KubuState.OPEN),  # 59
        (0xFFFF, KubuState.OPEN),  # 63
        (0xFFEA, KubuState.CLOSED),
        (0xFF00, KubuState.CLOSED),
    ],
)
def test_contact_types(sensor_type, raw, state) -> None:
    assert make_decoder(sensor_type, "retroPro")(raw) is state


@pytest.mark.parametrize(
    ("raw", "type4", "type5"),
    [
        (0x30, KubuState.LOCKED, KubuState.CLOSED),  # only bits 5-4 set
        (0x3C, KubuState.LOCKED, KubuState.CLOSED),  # bits 5-4 and 3-2
        (0x03, KubuState.CLOSED, KubuState.LOCKED),  # only bits 1-0
        (0x0F, KubuState.CLOSED, KubuState.LOCKED),  # bits 3-2 and 1-0
        (0x33, KubuState.CLOSED, KubuState.CLOSED),  # both outer fields
        (0x00, KubuState.CLOSED, KubuState.CLOSED),
        (0x0C, KubuState.CLOSED, KubuState.CLOSED),
        (0x3F, KubuState.OPEN, KubuState.OPEN),
        (0x2A, KubuState.TAMPERED, KubuState.TAMPERED),  # 42
        (0x15, KubuState.TAMPERED, KubuState.TAMPERED),  # 21
        (0xFF30, KubuState.LOCKED, KubuState.CLOSED),  # high byte ignored
    ],
)
def test_multipoint_window_types(raw, type4, type5) -> None:
    assert make_decoder(4)(raw) is type4
    assert make_decoder(11)(raw) is type4
    assert make_decoder(5)(raw) is type5
    assert make_decoder(12)(raw) is type5


@pytest.mark.parametrize("sensor_type", [40, 41])
@pytest.mark.parametrize(
    ("raw", "state"),
    [
        (0xFF01, KubuState.OPEN),
        (0xFF02, KubuState.CLOSED),
        (0xFF03, KubuState.LOCKED),
        (0xFF00, KubuState.UNKNOWN),
        (0xFFFF, KubuState.UNKNOWN),
    ],
)
def test_byte9_types(sensor_type, raw, state) -> None:
    assert make_decoder(sensor_type)(raw) is state


def test_tilt_turn_type9_remembers_movable_tilt() -> None:
    decoder = make_decoder(9, "retroPro")
    assert isinstance(decoder, TiltTurnDecoder)
    seq = [0xFFFD, 0xFFEB, 0xFFD7, 0xFFFF, 0xFFFD, 0xFFFF, 0xFFF7, 0xFFFF, 0xFF00]
    assert [decoder(raw) for raw in seq] == [
        KubuState.LOCKED,  # 253 secure
        KubuState.OPEN,  # 235 open
        KubuState.TILTED,  # 215 tilt, handle can move
        KubuState.TILTED,  # 255 while that is remembered
        KubuState.LOCKED,  # 253 secure, forgets it
        KubuState.OPEN,  # 255 is open again
        KubuState.TILTED,  # 247 tilt, not movable
        KubuState.OPEN,  # 255
        KubuState.TAMPERED,  # not in any set
    ]


def test_tilt_turn_type10_swaps_secure_and_open() -> None:
    decoder = make_decoder(10)
    assert decoder(0xFFEB) is KubuState.LOCKED  # 235
    assert decoder(0xFFFD) is KubuState.OPEN  # 253
    assert decoder(0xFFF7) is KubuState.OPEN  # 247 is open on type 10
    assert decoder(0xFFB7) is KubuState.TILTED  # 183


def test_tilt_turn_decoders_are_separate() -> None:
    """Memory is per sensor."""
    a, b = make_decoder(9), make_decoder(9)
    a(0xFFD7)
    assert a(0xFFFF) is KubuState.TILTED
    assert b(0xFFFF) is KubuState.OPEN


def test_parse_uses_given_decoder() -> None:
    decoder = make_decoder(9)
    parse(bytes.fromhex("10010B2901B12CA7FFD7000010FF042E"), decoder=decoder)
    reading = parse(bytes.fromhex("10010B2901B12CA7FFFF000010FF042E"), decoder=decoder)
    assert reading.state is KubuState.TILTED


def test_unknown_types_have_no_decoder_or_model() -> None:
    assert make_decoder(99) is None
    assert make_decoder(0) is None
    assert model_for(99) is None
    assert model_for(1, "window") is None


def test_every_model_has_a_decoder() -> None:
    for sensor_type in MODELS:
        assert make_decoder(sensor_type) is not None, sensor_type


def test_models() -> None:
    retro = model_for(8, "retroPro", "af-d")
    assert retro.name == "Retro Pro"
    assert retro.verified
    assert model_for(8) == retro  # entries saved before the class was stored

    door = model_for(2, "door", "std-d")
    assert door.name == "Door Sensor"
    assert not door.window
    assert door.has_lock
    assert not door.verified

    contact = model_for(2, "retroPro", "gen-w")
    assert contact.window
    assert not contact.has_lock
    assert not contact.has_tamper
    assert not model_for(2, "retroPro", "gen-d").window

    tilt = model_for(9, "retroPro", "tt-w")
    assert tilt.has_tilt
    assert tilt.window

    for sensor_type, model in MODELS.items():
        assert model.verified == (sensor_type == 8)


# A sensor in setup (pairing) mode sends this in the clear: byte 0 is 0x0F,
# byte 1 the class, bytes 2-3 the firmware, bytes 10-15 its MAC address.
SETUP_ADVERT = bytes.fromhex("0F01042E00000000000094A081123456")


def test_setup_advert() -> None:
    from custom_components.kubu_ble.protocol import is_setup_advert

    assert is_setup_advert(SETUP_ADVERT)
    assert not is_setup_advert(encrypt(bytes.fromhex(REAL_FRAMES[0][0])))
    assert not is_setup_advert(SETUP_ADVERT[:15])
    assert not is_setup_advert(b"")
