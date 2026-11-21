"""Diagnostics download for Kubu BLE.

Lets users share what their sensor sends, so other models and firmware can
be supported. Keys, address and serial number are removed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from . import KubuConfigEntry
from .const import CONF_DP_KEY, CONF_SERIAL
from .protocol import KubuReading

TO_REDACT = {CONF_DP_KEY, CONF_ADDRESS, CONF_SERIAL, "title"}


def _reading(reading: KubuReading | None) -> dict[str, Any] | None:
    if reading is None:
        return None
    return {
        "plaintext": reading.plaintext.hex(),
        "frame_type": f"0x{reading.frame_type:02x}",
        "class_id": reading.class_id,
        "raw_status": f"0x{reading.raw_status:04x}",
        "state": reading.state.value,
        "battery_mv": reading.battery_mv,
        "vibration": f"0x{reading.vibration:02x}",
        "firmware": reading.firmware,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: KubuConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    device = entry.runtime_data.device
    model = device.model
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "model": model.name if model else None,
        "model_verified": model.verified if model else False,
        "available": entry.runtime_data.coordinator.available,
        "last_reading": _reading(device.last_reading),
        "frames_that_did_not_decode": device.bad_frames,
        "frames_missed": device.missed_frames,
        "battery_status": device.battery_status,
        "recent_frames": [
            {"time": round(t, 1), "frame": _reading(r)} for t, r in device.recent_frames
        ],
    }
