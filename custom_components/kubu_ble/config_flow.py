"""Config flow for Kubu BLE."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import DOMAIN
from .device import is_kubu_device


def _title(info: BluetoothServiceInfoBleak) -> str:
    return info.name or info.address


class KubuConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kubu BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Set up the flow."""
        self._address: str | None = None
        self._name: str | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device found by Bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not is_kubu_device(discovery_info):
            return self.async_abort(reason="not_supported")
        self._address = discovery_info.address
        self._name = _title(discovery_info)
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_confirm()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a device found nearby."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._address = address
            self._name = _title(self._discovered[address])
            return await self.async_step_confirm()

        current = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=False):
            if info.address in current or info.address in self._discovered:
                continue
            if is_kubu_device(info):
                self._discovered[info.address] = info

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            addr: f"{_title(info)} ({addr})"
                            for addr, info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup of the sensor."""
        assert self._address is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._name or self._address,
                data={CONF_ADDRESS: self._address},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._name or self._address},
        )
