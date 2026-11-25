#!/usr/bin/env python3
"""Sign in to the Kubu cloud and list BLE sensors (keys hidden).

Usage:
  .venv/bin/python dev/kubu_cloud_check.py you@example.com
  .venv/bin/python dev/kubu_cloud_check.py you@example.com --save-token FILE
  .venv/bin/python dev/kubu_cloud_check.py --token FILE

--save-token keeps the refresh token from this sign-in in FILE (readable
only by you). --token uses that file instead of a password, the way the
integration does after setup. Run it again days or weeks later to see how
long a refresh token lasts: it prints when the password was last used.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import getpass
import os
import pathlib
import sys

import aiohttp

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from custom_components.kubu_ble.cloud import (
    KubuAuthError,
    KubuCloud,
    KubuCloudError,
)
from custom_components.kubu_ble.protocol import model_for


def _when(seconds: object) -> str:
    if not isinstance(seconds, int | float):
        return "?"
    return dt.datetime.fromtimestamp(seconds, dt.UTC).strftime("%Y-%m-%d %H:%M UTC")


def _token_age(cloud: KubuCloud) -> None:
    """Print when the password was last used, from the new ID token."""
    claims = cloud.id_token_claims
    auth_time = claims.get("auth_time")
    if isinstance(auth_time, int | float):
        days = (dt.datetime.now(dt.UTC).timestamp() - auth_time) / 86400
        print(f"Password last used {_when(auth_time)} ({days:.1f} days ago).")
    print(f"ID token valid until {_when(claims.get('exp'))}.")


def _save_token(path: pathlib.Path, token: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(token)
    print(f"Saved the refresh token to {path} (keep it private).")


async def main(args: argparse.Namespace) -> int:
    password = None if args.token else getpass.getpass("Kubu password: ")
    async with aiohttp.ClientSession() as session:
        cloud = KubuCloud(session)
        try:
            if args.token:
                await cloud.refresh(args.token.read_text().strip())
                print("Refresh token still works.")
            else:
                await cloud.login(args.email, password or "")
                print("Signed in OK.")
            _token_age(cloud)
            nodes = await cloud.get_ble_nodes()
        except KubuAuthError as err:
            print(f"Rejected: {err}")
            if args.token:
                print("  The refresh token has expired or was revoked.")
            else:
                print("  1005/1008 = wrong email or password, 1105 = locked 5 min,")
                print("  1141 = email not verified")
            return 1
        except KubuCloudError as err:
            print(f"Cloud error: {err}")
            return 1
    if args.save_token:
        if cloud.refresh_token:
            _save_token(args.save_token, cloud.refresh_token)
        else:
            print("Sign-in gave no refresh token, so nothing was saved.")
    if not nodes:
        print("Signed in, but no BLE sensors found in this account.")
        return 1
    for mac, node in nodes.items():
        model = model_for(node.sensor_type, node.sensor_class, node.product_type)
        if model is None:
            known = "NOT KNOWN"
        else:
            known = f"{model.name}{'' if model.verified else ' (unverified)'}"
        print(
            f"{node.name}  mac={mac}  type={node.sensor_type}  class={node.sensor_class}"
            f"  product={node.product_type}  serial={node.serial}  fw={node.firmware}"
            f"  dpKey={node.dp_key[:4]}…  model={known}"
        )
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("email", nargs="?", help="Kubu account email")
    parser.add_argument("--save-token", type=pathlib.Path, metavar="FILE")
    parser.add_argument("--token", type=pathlib.Path, metavar="FILE")
    args = parser.parse_args()
    if bool(args.email) == bool(args.token):
        parser.error("give an email to sign in, or --token FILE, not both")
    if args.token and args.save_token:
        parser.error("--save-token only works with a password sign-in")
    return args


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse_args())))
