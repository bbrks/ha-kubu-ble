"""The "Check now" button of a Kubu account entry."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KubuConfigEntry
from .account import REASON_CHECK_NOW
from .cloud_entities import KubuCloudEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KubuConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button (account entries only)."""
    if (account := entry.runtime_data.account) is not None:
        async_add_entities([KubuCheckNowButton(account, "check_now")])


class KubuCheckNowButton(KubuCloudEntity, ButtonEntity):
    """Check the Kubu account now.

    Also works in offline mode: the user asked for it.
    """

    async def async_press(self) -> None:
        """Run a cloud check."""
        await self.account.async_refresh(REASON_CHECK_NOW)
