#!/usr/bin/env python3
"""Sign in to the Kubu cloud and list BLE sensors (keys hidden).

Usage: .venv/bin/python dev/kubu_cloud_check.py you@example.com
"""

from __future__ import annotations

import asyncio
import getpass
import pathlib
import sys

import aiohttp

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from custom_components.kubu_ble.cloud import (
    KubuAuthError,
    KubuCloud,
    KubuCloudError,
)


async def main(email: str) -> int:
    password = getpass.getpass("Kubu password: ")
    async with aiohttp.ClientSession() as session:
        cloud = KubuCloud(session)
        try:
            await cloud.login(email, password)
            print("Signed in OK.")
            nodes = await cloud.get_ble_nodes()
        except KubuAuthError as err:
            print(f"Rejected: {err}")
            print("  1005/1008 = wrong email or password, 1105 = locked 5 min,")
            print("  1141 = email not verified")
            return 1
        except KubuCloudError as err:
            print(f"Cloud error: {err}")
            return 1
    if not nodes:
        print("Signed in, but no BLE sensors found in this account.")
        return 1
    for mac, node in nodes.items():
        print(
            f"{node.name}  mac={mac}  type={node.sensor_type}  class={node.sensor_class}"
            f"  product={node.product_type}  serial={node.serial}  fw={node.firmware}"
            f"  dpKey={node.dp_key[:4]}…"
        )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
