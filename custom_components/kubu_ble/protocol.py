"""Kubu BLE advertisement protocol.

Pure Python, no Home Assistant imports, so it can move to its own PyPI
package later. See docs/PROTOCOL.md for the full write-up.

Kubu BLE sensors put their state in the manufacturer data of their
advertisements (company ID 3535 / 0x0DCF). The payload is one 16 byte
AES-128-ECB block, encrypted with the sensor's per-device "dpKey".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

COMPANY_ID = 3535
BLOCK_SIZE = 16

FRAME_EVENT = 0x10
FRAME_HEARTBEAT = 0x20
KNOWN_FRAME_TYPES = (FRAME_EVENT, FRAME_HEARTBEAT)
# Byte 0 of the plain (not encrypted) advert a sensor sends in setup mode.
SETUP_ADVERT = 0x0F

# Byte 10, "SureShield" in the Kubu app.
VIBRATION_ACTIVITY = 0x10  # "vibration activity"
VIBRATION_FORCE = 0x20  # "excessive force", Security Alert

# Battery thresholds used by the Kubu app (millivolts).
BATTERY_CRITICAL_MV = 2134
BATTERY_LOW_MV = 2181
# Once low or critical, only report good again above this (app behaviour).
BATTERY_RECOVER_MV = 2600
# Readings below this are glitches seen during events; ignore them.
BATTERY_MIN_VALID_MV = 1000

_KEY_RE = re.compile(r"^[0-9a-fA-F]{32}$")


class KubuState(StrEnum):
    """State of the door or window as reported by the sensor."""

    LOCKED = "locked"  # closed and locked ("Locked" or "Secure" in the app)
    CLOSED = "closed"  # closed but not locked
    OPEN = "open"
    TILTED = "tilted"  # tilt & turn window open in tilt ("Vent mode" in the app)
    TAMPERED = "tampered"
    UNKNOWN = "unknown"


class InvalidKey(ValueError):
    """The key is not 32 hex characters."""


@dataclass(frozen=True, slots=True)
class KubuReading:
    """One decoded advertisement."""

    frame_type: int
    class_id: int
    battery_mv: int | None
    counter: int
    raw_status: int
    state: KubuState
    vibration: int
    firmware: str
    plaintext: bytes

    @property
    def is_event(self) -> bool:
        """Return True if this frame was sent because something changed."""
        return self.frame_type == FRAME_EVENT


def parse_key(text: str) -> bytes:
    """Turn a 32 character hex key into 16 bytes."""
    cleaned = text.strip().replace(" ", "").replace(":", "")
    if not _KEY_RE.match(cleaned):
        raise InvalidKey("key must be 32 hex characters")
    return bytes.fromhex(cleaned)


def decrypt(payload: bytes, key: bytes) -> bytes:
    """Decrypt one AES-128-ECB block."""
    if len(payload) != BLOCK_SIZE:
        raise ValueError(f"payload must be {BLOCK_SIZE} bytes, got {len(payload)}")
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return decryptor.update(payload) + decryptor.finalize()


def is_setup_advert(payload: bytes) -> bool:
    """Return True if the raw payload looks like a setup mode advert.

    In setup (pairing) mode the sensor sends its manufacturer data in the
    clear: byte 0 is 0x0F, byte 1 the class, bytes 2-3 the firmware and
    bytes 10-15 its MAC address. The Kubu app checks byte 0 before it
    decrypts anything. An encrypted payload starts with 0x0F 1 time in
    256, so only treat it as setup mode if it also fails to decode.
    """
    return len(payload) == BLOCK_SIZE and payload[0] == SETUP_ADVERT


def looks_valid(plain: bytes) -> bool:
    """Cheap sanity check, to spot a wrong key.

    AES-ECB has no integrity check, so a wrong key just gives random bytes.
    Only rely on things the format defines, not values seen on one device:
    byte 0 is a frame type (high nibble set, low nibble clear) and bytes
    14-15 are the firmware version as two decimal numbers (e.g. 04.46).
    A random block passes this about 1 time in 112, so judge a key by a
    run of frames, not by one.
    """
    return (
        len(plain) == BLOCK_SIZE
        and plain[0] != 0
        and plain[0] & 0x0F == 0
        and plain[14] < 100
        and plain[15] < 100
    )


# --- Status decoders --------------------------------------------------------
#
# All taken from the Kubu Android app 8.0.8: BLEScannerService
# .evaluateSensorState() (by logic type) and .evaluateDoorSensorState()
# (class "door", which the app checks before the type). Only type 8 has
# been checked against a real sensor; see MODELS below.
#
# raw_status is bytes 8-9 of the frame, big endian.

StatusDecoder = Callable[[int], KubuState]


def door_state_type8(raw_status: int) -> KubuState:
    """Logic type 8: Retro Pro door lock sensor. Verified."""
    low6 = raw_status & 0x3F
    if low6 in (42, 43, 46):
        return KubuState.LOCKED
    if low6 in (58, 59, 62):
        return KubuState.CLOSED
    if low6 == 63:
        return KubuState.OPEN
    return KubuState.TAMPERED


def door_state_class_door(raw_status: int) -> KubuState:
    """Class "door": Kubu Door Sensor in a lift-lever door lock.

    The app shows FF01 as "Unlocked, Closed".
    """
    return {
        0xFF01: KubuState.CLOSED,
        0xFF10: KubuState.LOCKED,
        0xFF11: KubuState.OPEN,
        0xFFFF: KubuState.TAMPERED,
    }.get(raw_status, KubuState.UNKNOWN)


def contact_state(raw_status: int) -> KubuState:
    """Logic types 2 and 3: open/closed only (magnet contact)."""
    if raw_status & 0x3F in (55, 59, 63):
        return KubuState.OPEN
    return KubuState.CLOSED


def _multipoint_state(raw_status: int, mirrored: bool) -> KubuState:
    """Logic types 4/11 and 5/12: window with a multi-point lock.

    The low 6 bits are three 2-bit fields. A field is "set" when both its
    bits are 1. Types 5 and 12 are types 4 and 11 with the outer fields
    swapped (the app picks one during calibration, so it is probably the
    hand of the window).
    """
    low6 = raw_status & 0x3F
    if low6 in (42, 21):
        return KubuState.TAMPERED
    if low6 == 63:
        return KubuState.OPEN
    low = low6 & 3 == 3  # bits 1-0
    high = (low6 >> 4) & 3 == 3  # bits 5-4
    if mirrored:
        low, high = high, low
    if high and not low:
        return KubuState.LOCKED  # "secure"
    return KubuState.CLOSED


def multipoint_state(raw_status: int) -> KubuState:
    """Logic types 4 and 11."""
    return _multipoint_state(raw_status, mirrored=False)


def multipoint_state_mirrored(raw_status: int) -> KubuState:
    """Logic types 5 and 12."""
    return _multipoint_state(raw_status, mirrored=True)


def byte9_state(raw_status: int) -> KubuState:
    """Logic types 40 and 41: byte 9 is 1 open, 2 closed, 3 secure."""
    return {
        1: KubuState.OPEN,
        2: KubuState.CLOSED,
        3: KubuState.LOCKED,
    }.get(raw_status & 0xFF, KubuState.UNKNOWN)


# Logic types 9 and 10: tilt & turn window. Only byte 9 is used. The sets
# are copied as they are in the app; type 10 is type 9 with the "secure"
# and "open" sets mostly swapped.
_TILT_TURN_SETS: dict[int, tuple[frozenset[int], ...]] = {
    # (secure, tilt and "movable", tilt, open)
    9: (
        frozenset({253, 189, 245, 181, 213, 149, 229, 165, 173}),
        frozenset(
            {215, 151, 223, 159, 246, 182, 230, 166, 214}
            | {150, 222, 158, 254, 190, 218, 154, 250, 186}
        ),
        frozenset({247, 183, 231, 167, 219, 155, 217, 153, 251, 187, 249, 185}),
        frozenset({235, 171, 234, 170, 233, 169, 239, 175, 237, 191, 255}),
    ),
    10: (
        frozenset({235, 171, 234, 170, 233, 169, 239, 175, 237, 173, 191}),
        frozenset(
            {215, 151, 223, 159, 246, 182, 230, 166, 214}
            | {150, 222, 158, 254, 190, 218, 154, 250, 186}
        ),
        frozenset({183, 231, 167, 219, 155, 217, 153, 251, 187, 249, 185}),
        frozenset({253, 189, 245, 181, 213, 149, 229, 165, 247, 255}),
    ),
}


class TiltTurnDecoder:
    """Logic types 9 and 10. Needs memory, so one instance per sensor.

    Like the app, it remembers whether the last tilt reading was one where
    the handle can move. While that is set, 0xFF reads as still tilted
    rather than open.
    """

    def __init__(self, sensor_type: int) -> None:
        """Pick the value sets for type 9 or 10."""
        self._secure, self._tilt_movable, self._tilt, self._open = _TILT_TURN_SETS[
            sensor_type
        ]
        self.tilt_movable = False

    def __call__(self, raw_status: int) -> KubuState:
        """Decode one status."""
        value = raw_status & 0xFF
        if value == 0xFF and self.tilt_movable:
            return KubuState.TILTED
        if value in self._secure:
            self.tilt_movable = False
            return KubuState.LOCKED
        if value in self._tilt_movable:
            self.tilt_movable = True
            return KubuState.TILTED
        if value in self._tilt:
            self.tilt_movable = False
            return KubuState.TILTED
        if value in self._open:
            return KubuState.OPEN
        return KubuState.TAMPERED


SENSOR_CLASS_DOOR = "door"

_DECODERS_BY_TYPE: dict[int, StatusDecoder] = {
    2: contact_state,
    3: contact_state,
    4: multipoint_state,
    11: multipoint_state,
    5: multipoint_state_mirrored,
    12: multipoint_state_mirrored,
    8: door_state_type8,
    40: byte9_state,
    41: byte9_state,
}


def make_decoder(
    sensor_type: int, sensor_class: str | None = None
) -> StatusDecoder | None:
    """Return a status decoder for this sensor, or None if unknown.

    Make one per sensor and keep it: some decoders remember past frames.
    """
    if sensor_class == SENSOR_CLASS_DOOR:
        return door_state_class_door
    if sensor_type in _TILT_TURN_SETS:
        return TiltTurnDecoder(sensor_type)
    return _DECODERS_BY_TYPE.get(sensor_type)


# --- Models -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KubuModel:
    """What one kind of sensor reports, and how sure we are about it."""

    name: str
    window: bool  # a window, not a door
    has_lock: bool  # reports locked vs closed
    has_tamper: bool  # has a "tampered" state
    has_tilt: bool = False  # tilt & turn
    verified: bool = False  # checked against a real sensor


_DOOR_SENSOR = KubuModel("Door Sensor", False, True, True)
_CONTACT = KubuModel("Anywhere Pro (open/closed)", True, False, False)
_CONTACT_DOOR = KubuModel("Anywhere Pro (open/closed)", False, False, False)
_VERTICAL_SLIDER = KubuModel("Anywhere Pro (vertical slider)", True, False, False)
_WWK_WINDOW = KubuModel("Works with Kubu window", True, True, True)
_WWK_WINDOW_40 = KubuModel("Works with Kubu window", True, True, False)
_WWK_TILT_TURN = KubuModel(
    "Works with Kubu tilt & turn window", True, True, True, has_tilt=True
)

# By logic type. Names come from the app's setup screens: type 2 is the
# surface mounted (or fallback) setup, 3 the vertical sliding window setup,
# 4/5 a "Works with Kubu" window (product "sas-w"), 9/10 a "Works with
# Kubu" tilt & turn window (product "tt-w"). The app has decoders for 11
# and 12 but no setup screen that picks them.
MODELS: dict[int, KubuModel] = {
    8: KubuModel("Retro Pro", False, True, True, verified=True),
    2: _CONTACT,
    3: _VERTICAL_SLIDER,
    4: _WWK_WINDOW,
    5: _WWK_WINDOW,
    11: _WWK_WINDOW,
    12: _WWK_WINDOW,
    9: _WWK_TILT_TURN,
    10: _WWK_TILT_TURN,
    40: _WWK_WINDOW_40,
    41: _WWK_WINDOW_40,
}


def model_for(
    sensor_type: int, sensor_class: str | None = None, product_type: str | None = None
) -> KubuModel | None:
    """Return the model for a sensor's class and logic type, or None."""
    if sensor_class == SENSOR_CLASS_DOOR:
        return _DOOR_SENSOR
    model = MODELS.get(sensor_type)
    if model is _CONTACT and (product_type or "").endswith("-d"):
        return _CONTACT_DOOR  # open/closed sensors can be fitted to a door too
    return model


# --- Frames -----------------------------------------------------------------


def parse(
    plain: bytes,
    sensor_type: int = 8,
    sensor_class: str | None = None,
    decoder: StatusDecoder | None = None,
) -> KubuReading:
    """Parse a decrypted 16 byte block.

    Pass the sensor's own decoder to keep its memory between frames;
    otherwise a fresh one is made from the type and class.
    """
    if len(plain) != BLOCK_SIZE:
        raise ValueError(f"plaintext must be {BLOCK_SIZE} bytes, got {len(plain)}")

    battery = int.from_bytes(plain[2:4], "big")
    raw_status = int.from_bytes(plain[8:10], "big")
    if decoder is None:
        decoder = make_decoder(sensor_type, sensor_class)
    state = decoder(raw_status) if decoder is not None else KubuState.UNKNOWN

    return KubuReading(
        frame_type=plain[0],
        class_id=plain[1],
        battery_mv=battery if battery >= BATTERY_MIN_VALID_MV else None,
        counter=int.from_bytes(plain[4:6], "big"),
        raw_status=raw_status,
        state=state,
        vibration=plain[10],
        firmware=f"{plain[14]:02d}.{plain[15]:02d}",
        plaintext=plain,
    )


def decode(
    payload: bytes,
    key: bytes,
    sensor_type: int = 8,
    sensor_class: str | None = None,
    decoder: StatusDecoder | None = None,
) -> KubuReading | None:
    """Decrypt and parse manufacturer data. Return None if it does not decode."""
    if len(payload) != BLOCK_SIZE:
        return None
    plain = decrypt(payload, key)
    if not looks_valid(plain):
        return None
    return parse(plain, sensor_type, sensor_class, decoder)


def battery_status(mv: int | None, previous: str = "unknown") -> str:
    """Battery level, with the Kubu app's hysteresis.

    Readings vary by ~150 mV between frames, so once the battery is low or
    critical it only counts as good again above BATTERY_RECOVER_MV. A
    missing reading keeps the previous level.
    """
    if mv is None:
        return previous
    if mv <= BATTERY_CRITICAL_MV:
        return "critical"
    if mv < BATTERY_LOW_MV:
        return "critical" if previous == "critical" else "low"
    if mv <= BATTERY_RECOVER_MV and previous in ("low", "critical"):
        return previous
    return "good"


def raw_has_state(raw: bytes | None) -> bool:
    """Return True if a raw advertising packet carries Kubu state.

    The sensor's normal advert only has flags and its name. The state is
    in the scan response, as manufacturer data for company 3535.
    """
    if not raw:
        return False
    i = 0
    while i < len(raw):
        length = raw[i]
        if length == 0 or i + 1 + length > len(raw):
            break
        ad_type = raw[i + 1]
        if ad_type == 0xFF and length >= 3:
            company = raw[i + 2] | raw[i + 3] << 8
            if company == COMPANY_ID:
                return True
        i += 1 + length
    return False
