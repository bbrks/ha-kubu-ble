"""Constants for the Kubu BLE integration."""

from __future__ import annotations

DOMAIN = "kubu_ble"

CONF_DP_KEY = "dp_key"
CONF_SENSOR_TYPE = "sensor_type"
CONF_SERIAL = "serial"

# Sensor "logic" type from the Kubu cloud. 8 is the Retro Pro door sensor.
SENSOR_TYPE_RETRO_PRO = 8

LOCAL_NAME_PREFIXES = ("KubuRetroPro-",)

MANUFACTURER = "Kubu"
