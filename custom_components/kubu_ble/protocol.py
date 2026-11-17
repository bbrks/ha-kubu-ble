"""Kubu BLE advertisement protocol.

Pure Python, no Home Assistant imports, so it can move to its own PyPI
package later. See docs/PROTOCOL.md for the full write-up.

Kubu BLE sensors put their state in the manufacturer data of their
advertisements (company ID 3535 / 0x0DCF). The payload is one 16 byte
AES-128-ECB block, encrypted with the sensor's per-device "dpKey".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

COMPANY_ID = 3535
BLOCK_SIZE = 16

FRAME_EVENT = 0x10
FRAME_HEARTBEAT = 0x20
KNOWN_FRAME_TYPES = (FRAME_EVENT, FRAME_HEARTBEAT)

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
# raw_status is bytes 8-9 of the frame, big endian. How to read it depends
# on the sensor's "logic type" from the Kubu cloud.


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


# --- Frames -----------------------------------------------------------------


def parse(plain: bytes, sensor_type: int = 8) -> KubuReading:
    """Parse a decrypted 16 byte block."""
    if len(plain) != BLOCK_SIZE:
        raise ValueError(f"plaintext must be {BLOCK_SIZE} bytes, got {len(plain)}")

    battery = int.from_bytes(plain[2:4], "big")
    raw_status = int.from_bytes(plain[8:10], "big")
    if sensor_type == 8:
        state = door_state_type8(raw_status)
    else:
        state = KubuState.UNKNOWN

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


def decode(payload: bytes, key: bytes, sensor_type: int = 8) -> KubuReading | None:
    """Decrypt and parse manufacturer data. Return None if it does not decode."""
    if len(payload) != BLOCK_SIZE:
        return None
    plain = decrypt(payload, key)
    if not looks_valid(plain):
        return None
    return parse(plain, sensor_type)


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
