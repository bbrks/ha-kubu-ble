"""Constants for the Kubu BLE integration."""

from __future__ import annotations

DOMAIN = "kubu_ble"

CONF_DP_KEY = "dp_key"
CONF_SENSOR_TYPE = "sensor_type"
CONF_SERIAL = "serial"
CONF_SENSOR_CLASS = "sensor_class"
CONF_PRODUCT_TYPE = "product_type"
CONF_VIB_SETTING = "vib_setting"

# Account entries (one per Kubu account) keep these. Never the password:
# like the Kubu app, a refresh token gets new 5 minute ID tokens.
CONF_REFRESH_TOKEN = "refresh_token"
# Sensors removed by the user, so the cloud refresh does not add them again.
CONF_IGNORED = "ignored_sensors"
# Option: never visit the Kubu cloud on our own after setup. Sensors are
# read over Bluetooth only; the cloud is used only when the user signs in
# again, reconfigures or adds a sensor by hand.
CONF_OFFLINE = "offline_mode"

# Each sensor is a subentry of its account entry.
SUBENTRY_SENSOR = "sensor"

# How often to check the Kubu account for changes made in the Kubu app
# (names, settings). New sensors don't wait for this: hearing a Kubu sensor
# that is not set up starts a check (see account.py).
CLOUD_REFRESH_INTERVAL_HOURS = 24
# After hearing a new Kubu sensor, wait this long before checking the cloud
# (so its scan response can arrive, and several new sensors share a check),
# and check because of new sensors at most once per cooldown.
CLOUD_TRIGGER_DELAY_SECONDS = 30
CLOUD_TRIGGER_COOLDOWN_SECONDS = 600
# A heard sensor that is not in the account (a neighbour's, say) does not
# start another check for this long.
NOT_IN_ACCOUNT_SKIP_HOURS = 24
# Sensor lists fetched from the cloud are reused for this long, so adding
# several sensors in a row (or discovery right after a refresh) is one visit.
CLOUD_CACHE_SECONDS = 600

# Repair issues.
ISSUE_LEGACY_ENTRIES = "legacy_entries"
# By subentry ID, not address: diagnostics include the domain's issue IDs.
ISSUE_NOT_IN_ACCOUNT = "not_in_account_{subentry_id}"

# Re-ask for sign-in after this many adverts that do not decode.
BAD_KEY_REAUTH_THRESHOLD = 5

# Sensor "logic" type from the Kubu cloud. 8 is the Retro Pro door sensor.
# The other known types are in protocol.MODELS.
SENSOR_TYPE_RETRO_PRO = 8
SENSOR_CLASS_RETRO_PRO = "retroPro"

# The Kubu app names Retro Pro / Anywhere Pro sensors (serial "AVR...")
# KubuRetroPro-XX-XX and all other BLE sensors KubuDoorSensor-XX-XX.
LOCAL_NAME_PREFIXES = ("KubuRetroPro-", "KubuDoorSensor-")

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
