# Development tools

Reverse-engineering notes and scripts for Kubu BLE sensors.

## `kubu_get_key_adb.py`

Reads sensor keys from the Kubu Android app's log over `adb`.
The app very helpfully logs its full API responses (including `dpKey`) to logcat.

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
