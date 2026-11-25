"""The "Kubu cloud" device of an account entry.

Diagnostic entities that show how the (optional) cloud side is doing: its
status, when it last checked, what it found, and a Check now button.
Legacy one-sensor entries have no cloud side, so they get none.
"""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .account import KubuAccount
from .const import DOMAIN, MANUFACTURER

CLOUD_DEVICE_NAME = "Kubu cloud"


def cloud_device_info(account: KubuAccount) -> DeviceInfo:
    """The service device the cloud entities belong to."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"account_{account.entry.unique_id}")},
        name=CLOUD_DEVICE_NAME,
        manufacturer=MANUFACTURER,
        entry_type=DeviceEntryType.SERVICE,
    )


class KubuCloudEntity(Entity):
    """An entity that follows the account's cloud status."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, account: KubuAccount, key: str) -> None:
        """Set up for this account."""
        self.account = account
        self._attr_translation_key = key
        self._attr_unique_id = f"{account.entry.unique_id}-{key}"
        self._attr_device_info = cloud_device_info(account)

    async def async_added_to_hass(self) -> None:
        """Follow the account's updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self.account.async_add_listener(self._async_updated))

    @callback
    def _async_updated(self) -> None:
        self.async_write_ha_state()
