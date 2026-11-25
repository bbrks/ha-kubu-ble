"""Diagnostics download for Kubu BLE.

Lets users share what their sensor sends, so other models and firmware can
be supported. Keys, tokens, email, address and serial number are removed.
Download it for the whole entry, or for one sensor from its device page.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS, CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceEntry

from . import KubuConfigEntry, KubuSensor
from .const import CONF_DP_KEY, CONF_IGNORED, CONF_REFRESH_TOKEN, CONF_SERIAL
from .protocol import KubuReading

TO_REDACT = {
    CONF_DP_KEY,
    CONF_ADDRESS,
    CONF_SERIAL,
    CONF_EMAIL,
    CONF_REFRESH_TOKEN,
    CONF_IGNORED,
    "title",
}


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


def _sensor(sensor: KubuSensor) -> dict[str, Any]:
    device = sensor.device
    model = device.model
    return {
        "data": async_redact_data(dict(sensor.data), TO_REDACT),
        "model": model.name if model else None,
        "model_verified": model.verified if model else False,
        "available": sensor.coordinator.available,
        "last_reading": _reading(device.last_reading),
        "frames_that_did_not_decode": device.bad_frames,
        "frames_missed": device.missed_frames,
        "battery_status": device.battery_status,
        "recent_frames": [
            {"time": round(t, 1), "frame": _reading(r)} for t, r in device.recent_frames
        ],
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: KubuConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    if runtime.account is None:  # legacy one-sensor entry
        sensor = runtime.sensors[None]
        return {"entry": async_redact_data(dict(entry.data), TO_REDACT)} | {
            k: v for k, v in _sensor(sensor).items() if k != "data"
        }
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "ignored_sensors": len(entry.data.get(CONF_IGNORED, ())),
        "offline_mode": runtime.offline,
        "last_cloud_refresh_ok": runtime.account.last_refresh_ok,
        "sensors": [_sensor(sensor) for sensor in runtime.sensors.values()],
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: KubuConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for one sensor."""
    addresses = {
        value for kind, value in device.connections if kind == CONNECTION_BLUETOOTH
    }
    for sensor in entry.runtime_data.sensors.values():
        if sensor.coordinator.address.lower() in {a.lower() for a in addresses}:
            return _sensor(sensor)
    return {}
