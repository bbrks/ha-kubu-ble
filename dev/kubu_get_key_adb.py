#!/usr/bin/env python3
"""Find Kubu BLE sensor keys in the Kubu Android app's log.

The Kubu app (com.avantis.kubu) logs its API responses to logcat. The
response for your home includes each BLE sensor's "dpKey". This script
reads that log and prints the key for each sensor.

Usage:
  1. Turn on USB debugging on the Android phone and plug it in.
  2. Open the Kubu app and go to the home screen (this loads your sensors).
  3. Run:  python3 dev/kubu_get_key_adb.py
     or:   adb logcat -d > log.txt && python3 dev/kubu_get_key_adb.py log.txt

Keep the key private. Anyone with it can read your lock state over the air.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any


def _walk(obj: Any, found: dict[str, dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        if "dpKey" in obj and ("sNo" in obj or "nodeMac" in obj):
            mac = obj.get("sNo") or obj.get("nodeMac")
            found[mac] = obj
        for value in obj.values():
            _walk(value, found)
    elif isinstance(obj, list):
        for value in obj:
            _walk(value, found)


def find_nodes(log_text: str) -> dict[str, dict[str, Any]]:
    """Return {mac: node} for every node with a dpKey in the log."""
    found: dict[str, dict[str, Any]] = {}
    for line in log_text.splitlines():
        if "dpKey" not in line:
            continue
        start = line.find("{")
        if start < 0:
            continue
        try:
            _walk(json.loads(line[start:]), found)
        except ValueError:
            continue
    return found


def _format_mac(mac: str) -> str:
    mac = mac.upper().replace(":", "")
    return ":".join(mac[i : i + 2] for i in range(0, len(mac), 2))


def main() -> int:
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        try:
            text = subprocess.run(
                ["adb", "logcat", "-d"],
                capture_output=True,
                text=True,
                errors="replace",
                check=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as err:
            print(f"Could not run adb: {err}", file=sys.stderr)
            return 1

    nodes = find_nodes(text)
    if not nodes:
        print(
            "No keys found. Open the Kubu app on the phone, wait for your "
            "sensors to load, then run this again.",
            file=sys.stderr,
        )
        return 1

    for mac, node in nodes.items():
        print(f"Device:   {node.get('devAlias') or node.get('name')}")
        print(f"Address:  {_format_mac(mac)}")
        print(f"Class:    {node.get('class')}  (type {node.get('type')})")
        print(f"Firmware: {node.get('fwVer')}")
        print(f"dpKey:    {node.get('dpKey')}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
