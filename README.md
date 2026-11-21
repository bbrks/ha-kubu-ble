# Kubu BLE for Home Assistant

[![Install from Marketplace](https://img.shields.io/badge/Marketplace-install-18BCF2?logo=homeassistant&logoColor=white)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=bbrks&repository=ha-kubu-ble&category=integration) [![Latest release](https://img.shields.io/github/v/release/bbrks/ha-kubu-ble)](https://github.com/bbrks/ha-kubu-ble/releases/latest) [![Validate](https://github.com/bbrks/ha-kubu-ble/actions/workflows/validate.yaml/badge.svg)](https://github.com/bbrks/ha-kubu-ble/actions/workflows/validate.yaml) [![hassfest](https://github.com/bbrks/ha-kubu-ble/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/bbrks/ha-kubu-ble/actions/workflows/hassfest.yaml) [![Tests](https://github.com/bbrks/ha-kubu-ble/actions/workflows/tests.yaml/badge.svg)](https://github.com/bbrks/ha-kubu-ble/actions/workflows/tests.yaml)

This Home Assistant integration provides support for local/offline Kubu Bluetooth door and window sensors.
It uses Home Assistant's own Bluetooth adapter (or an ESPHome Bluetooth proxy). You don't need a Kubu hub.

Kubu sensors encrypt state with a key stored in Kubu's cloud.
During setup, the integration will sign in to your Kubu account to get the key for offline decryption.
Your email and password are not saved.

This project was achieved by reverse engineering the Kubu Android app - but is not made by nor supported by Kubu.

## Supported sensors

This integration _should_ support all sensors supported by the Kubu Android app v8.0.8, however I have only tested
the Kubu Retro Pro (`KubuRetroPro-XX-XX`) sensor inside the Avantis MagnaBolt lock, running firmware `04.46`

All Kubu Bluetooth sensors use the same radio format and the integration attempts to reads everything the Kubu app supports:

| Sensor (as named in the Kubu app) | Class / type | States | Tested |
|---|---|---|---|
| Retro Pro | `retroPro` / 8 | open, closed, locked, tampered | Yes |
| Door Sensor (lift-lever door lock) | `door` / any | open, closed, locked, tampered | No |
| Anywhere Pro, surface mounted | 2 | open, closed | No |
| Anywhere Pro, vertical sliding window | 3 | open, closed | No |
| "Works with Kubu" window | 4, 5, 11, 12 | open, closed, locked, tampered | No |
| "Works with Kubu" window (newer calibration) | 40, 41 | open, closed, locked | No |
| "Works with Kubu" tilt & turn window | 9, 10 | open, tilted, locked, tampered | No |

Their states are decoded the same way the Android app does it (see [docs/PROTOCOL.md](docs/PROTOCOL.md)),
but none of them have been tried on a real sensor. The integration setup tells you when a detected sensor is untested.

Let me know how it goes, even if it works fine. I can flag it as tested.
Setup actively refuses any sensor type that the Kubu app I decompiled didn't recognise since I have no references for their BLE protocol.

A few things definitely won't work, or haven't been tested and probably won't work:
- Sensors moved to Zigbee with a Kubu Home Hub stop sending Bluetooth data. This integration wouldn't be able to find them.
- Vibration/excessive force sensor - It might work, but I could never get it showing anything, even in "demo mode" sensitivity.
- A sensor shared to you from someone else's Kubu account may not come with its key. TBD - this is untested.
- Other sensor firmware versions - unable to test.

## Pre-requisites

- Home Assistant 2026.10 or newer.
- Sensor setup in the Kubu app first, tied to your Kubu account. Have the Kubu login ready (email and password only - no Google auth)
- HA Bluetooth or ESPHome BT Proxy scanning mode Auto (default/preferred) or Active.

## Install

Through HACS/Marketplace:

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=bbrks&repository=ha-kubu-ble&category=integration)

Until it is in the Marketplace catalog, you can also add it by hand: go to `Settings > Marketplace > Add from link`, enter `bbrks/ha-kubu-ble`, then install Kubu BLE.

To install it manually instead, download the latest release, copy `custom_components/kubu_ble` into your Home Assistant `config/custom_components/` folder, and restart Home Assistant.

## Set up

Home Assistant should find the sensor by itself and list it under `Settings > Devices & services > Discovered`. If it doesn't, add a device manually:

[![Open your Home Assistant instance and start setting up Kubu BLE.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=kubu_ble)

1. Sign in with your Kubu email and password. 
2. If you have more sensors, setting up the next ones shortly after doesn't need another sign-in.

Entities only appear once Home Assistant has decoded the sensor's data.
If nothing shows up, turn the key or open the door once, and check that your scanning mode isn't Passive.

If the stored key ever stops working to decrypt, Home Assistant will ask you to sign in again.

## Problems and reports

### Report sensor state bugs/misreporting

Systematically do each action possible for the sensor - e.g. lock, unlock, open, close, etc.
Make a note of what actions you did and in what order - it will help correlate the captured packets which are shared in the diagnostics file.

Go to `Settings > Devices & services > Kubu BLE > ⋮ > Download diagnostics` and attach the file to a [new issue - with your reported set of steps](https://github.com/bbrks/ha-kubu-ble/issues)

The file contains the sensor type, firmware and recent decrypted frames for analysis.
The key, Bluetooth address and serial number are removed for privacy reasons.

### Report new/unknown sensor type
If setup says your sensor isn't known at all, run `dev/kubu_cloud_check.py` (see [dev/README.md](dev/README.md)) and include the `type=`, `class=` and `product=` values it prints, but avoid sharing the key.

## Development

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
```

The reverse-engineering tools are described in [dev/README.md](dev/README.md).

The bluetooth protocol is described in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## License

MIT
