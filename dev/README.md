# Development tools

These are for working on the integration, not for normal users. Users set
up sensors by signing in to their Kubu account in the config flow.

## `kubu_cloud_check.py`

Signs in to the Kubu cloud with the same client the integration uses and
lists your BLE sensors, with keys hidden. Good for checking the cloud API
still works. Run it in your own terminal (it asks for your password):

```sh
.venv/bin/python dev/kubu_cloud_check.py you@example.com
```

## `kubu_get_key_adb.py`

Reads sensor keys from the Kubu Android app's log over `adb`.
The app very helpfully logs its full API responses (including `dpKey`) to logcat.
Useful when reverse engineering, or to compare with what the cloud client returns.

```sh
python3 dev/kubu_get_key_adb.py            # reads `adb logcat -d`
python3 dev/kubu_get_key_adb.py log.txt    # or a saved log
```

## Capturing Bluetooth traffic

On Android: Developer options → *Enable Bluetooth HCI snoop log*, turn
Bluetooth off and on, use the Kubu app, then `adb bugreport bugreport.zip`.
The log is at `FS/data/misc/bluetooth/logs/btsnoop_hci.log` inside the zip.
Look for LE extended advertising reports from the sensor; the state is in
the scan response (event type `0x001b`), manufacturer ID 3535.
