"""Constants for the Kubu BLE integration."""

from __future__ import annotations

DOMAIN = "kubu_ble"

CONF_DP_KEY = "dp_key"
CONF_SENSOR_TYPE = "sensor_type"
CONF_SERIAL = "serial"

# Re-ask for sign-in after this many adverts that do not decode.
BAD_KEY_REAUTH_THRESHOLD = 5

# Sensor "logic" type from the Kubu cloud. 8 is the Retro Pro door sensor.
SENSOR_TYPE_RETRO_PRO = 8

LOCAL_NAME_PREFIXES = ("KubuRetroPro-",)

MANUFACTURER = "Kubu"

# For adapters in HA's "Auto" scanning mode: a fallback active scan window
# this often (seconds). State changes are caught by the burst trigger
# below; the sensor's idle adverts (about every 30 s) also trigger windows
# that pick up heartbeats, so this only needs to be HA's default.
# Adapters in "Active" mode ignore this.
ACTIVE_SCAN_INTERVAL = 300.0
ACTIVE_SCAN_DURATION = 10.0

# Burst trigger (see scan_trigger.py): after a name-only advert, wait this
# long for the scan response, then ask for an active window of this length,
# at most once per cooldown.
# Measured on a real Retro Pro: with an active adapter the scan response
# arrives within ~10 ms of the advert, and a state-change burst lasts 3-6 s.
# The cooldown is just longer than one window, so back-to-back events (or
# an event right after an idle advert's window) still get their own window.
BURST_SETTLE_SECONDS = 0.3
BURST_SCAN_SECONDS = 5.0
BURST_COOLDOWN_SECONDS = 6.0


# Only report the sensor unavailable after this long without any packet.
# It normally sends something at least every ~30-90 s (see coordinator.py).
AVAILABILITY_GRACE_SECONDS = 300.0
