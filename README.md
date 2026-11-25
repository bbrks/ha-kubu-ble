# Kubu BLE for Home Assistant

<!-- ha-marketplace:exclude-start -->

[![Install from Marketplace](https://img.shields.io/badge/Marketplace-install-18BCF2?logo=homeassistant&logoColor=white)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=bbrks&repository=ha-kubu-ble&category=integration) [![Latest release](https://img.shields.io/github/v/release/bbrks/ha-kubu-ble)](https://github.com/bbrks/ha-kubu-ble/releases/latest) [![Validate](https://github.com/bbrks/ha-kubu-ble/actions/workflows/validate.yaml/badge.svg)](https://github.com/bbrks/ha-kubu-ble/actions/workflows/validate.yaml) [![hassfest](https://github.com/bbrks/ha-kubu-ble/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/bbrks/ha-kubu-ble/actions/workflows/hassfest.yaml) [![Tests](https://github.com/bbrks/ha-kubu-ble/actions/workflows/tests.yaml/badge.svg)](https://github.com/bbrks/ha-kubu-ble/actions/workflows/tests.yaml)

<!-- ha-marketplace:exclude-end -->

This Home Assistant integration provides support for local/offline Kubu Bluetooth door and window sensors.
It uses Home Assistant's own Bluetooth adapter (or an ESPHome Bluetooth proxy). You don't need a Kubu hub.

<img width="1235" height="889" alt="Home Assistant screenshot of a Kubu BLE device" src="https://github.com/user-attachments/assets/d97d9081-0ac8-4651-ac54-00e9ebe755c7" />

Kubu sensors encrypt state with a key stored in Kubu's cloud.
The Kubu cloud is only **needed once**: you sign in at setup, the integration gets each sensor's key, and from then on reads the sensors locally over Bluetooth.
The sensors keep working if the Kubu cloud or your internet is down.

Everything else the cloud does here is **optional**. By default the integration keeps a sign-in token (never your password), like the Kubu app does, so it can pick up new sensors and changes made in the Kubu app.
Don't want that? Turn on [offline mode](#offline-mode).

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

Their states are decoded the same way the Android app does it (see [docs/PROTOCOL.md](https://github.com/bbrks/ha-kubu-ble/blob/main/docs/PROTOCOL.md)),
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

Home Assistant should find your sensors by itself and list them under `Settings > Devices & services > Discovered`. Or add the integration yourself:

[![Open your Home Assistant instance and start setting up Kubu BLE.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=kubu_ble)

1. Sign in with your Kubu email and password.
2. That's it. You get one entry for your Kubu account, with every sensor Home Assistant can hear in it.

After that:

- **New sensors** you set up in the Kubu app are added on their own, a few minutes after Home Assistant first hears them.
- **Changes in the Kubu app** (sensor name, vibration sensitivity) show up within a day, or straight away with *Check now* (see below).
- **Removed a sensor here?** It is not added back. To add it again, open the account entry and choose *Add sensor*.
- **Sensor removed from your Kubu account?** You get a repair notice. It keeps working with its saved key until you remove it.

Entities only appear once Home Assistant has decoded the sensor's data.
If nothing shows up, turn the key or open the door once, and check that your scanning mode isn't Passive.

### Offline mode

Don't want Home Assistant to visit the Kubu cloud after setup? Tick **Offline mode** when you sign in, or turn it on later under the account entry's *Configure*.
With offline mode on, sensors are read over Bluetooth only, and Home Assistant never contacts Kubu on its own.
New sensors and changes made in the Kubu app are only picked up when you start it: *Add sensor*, *Reconfigure*, or signing in again.
The sign-in token stays saved, so turning offline mode off again doesn't need your password.

### Cloud status

The account entry has a **Kubu cloud** device, which shows how the cloud side is doing:

- **Status**: OK, can't reach Kubu, sign-in expired, or offline mode.
- **Last check**, **Last successful check** and **Next check**. *Last check* also says why it ran (startup, daily check, new sensor heard, Check now).
- **Sensors in account**: how many sensors your Kubu account has. Its `sensors` attribute lists each one's name, whether it is set up here, and whether Home Assistant can hear it.
- **Check now**: checks your Kubu account straight away. This also works in offline mode, because you asked for it.

The integration checks your account once a day, and soon after it hears a Kubu sensor that isn't set up yet (at most once every 10 minutes).
A sensor that isn't in your account, a neighbour's say, doesn't start another check for a day.

### Signing in again

Sign-in forms fill in the email you used last. The password is never saved.

If a sensor's key stops working, Home Assistant fetches the keys again from your account (in offline mode, it asks you to sign in). If the sign-in token has expired, it asks you to sign in again. The sensors keep working meanwhile.

### Upgrading from 1.0

Before 1.1.0, each sensor was its own entry, and the integration never went back to the Kubu cloud. Those entries still work.
You will see a repair notice: sign in once, and your sensors move into one account entry. Devices, entity IDs, names and history stay the same.

## Dashboard card

The integration comes with a **Kubu sensor** card. It loads by itself: there is no resource to add.
Edit a dashboard, choose *Add card*, and search for *Kubu sensor*, or use YAML:

```yaml
type: custom:kubu-ble-card
device_id: <your sensor's device>
```

It shows the sensor's state the way the Kubu app does ("Locked", "Unlocked, closed", "Open", "Tamper alert", "Out of range"), with:

- an alert banner for tamper, vibration and excessive force,
- battery, signal and firmware,
- recent activity (from Home Assistant's history),
- the model and its settings.

Tap it, or any part of it, for more details.

| Option | Default | What it does |
|---|---|---|
| `device_id` | (required) | The Kubu sensor to show. |
| `name` | the device name | Show another name. |
| `compact` | `false` | One line, like a tile. Good for many sensors. |
| `show_battery` | `true` | Battery, signal and firmware. |
| `show_alerts` | `true` | Tamper, vibration and excessive force banner. |
| `show_activity` | `true` | Recent activity. |
| `show_details` | `true` | Model, vibration sensitivity and battery type. |
| `activity_hours` | `24` | How far back recent activity looks. |

## Problems and reports

### Report sensor state bugs/misreporting

Systematically do each action possible for the sensor - e.g. lock, unlock, open, close, etc.
Make a note of what actions you did and in what order - it will help correlate the captured packets which are shared in the diagnostics file.

Open the sensor's device page (`Settings > Devices & services > Kubu BLE`, then the sensor), choose `⋮ > Download diagnostics`, and attach the file to a [new issue - with your reported set of steps](https://github.com/bbrks/ha-kubu-ble/issues)

The file contains the sensor type, firmware and recent decrypted frames for analysis.
The key, Bluetooth address, serial number, email and sign-in token are removed for privacy reasons.

### Report new/unknown sensor type
If setup says your sensor isn't known at all, run `dev/kubu_cloud_check.py` (see [dev/README.md](https://github.com/bbrks/ha-kubu-ble/blob/main/dev/README.md)) and include the `type=`, `class=` and `product=` values it prints, but avoid sharing the key.

<!-- ha-marketplace:exclude-start -->

## Development

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
```

The reverse-engineering tools are described in [dev/README.md](dev/README.md).

The bluetooth protocol is described in [docs/PROTOCOL.md](docs/PROTOCOL.md).

<!-- ha-marketplace:exclude-end -->

## License

MIT
