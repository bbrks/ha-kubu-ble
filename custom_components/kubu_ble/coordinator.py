"""Coordinator with a grace period before marking the sensor unavailable.

HA marks a passive Bluetooth device unavailable when its scanner has
dropped it and no advert arrived within the device's learned advertising
interval (+5 s). HA learns that interval as the largest gap among the first
16 adverts it sees. The Kubu sensor is mostly quiet (a few adverts about
every 30 s, with fast bursts on state changes), so the learned interval can
be much shorter than a normal idle gap. On top of that, in "Auto" scanning
mode the adapter restarts its scan often, which empties the scanner's list
of seen devices. Together these give short, false "Unavailable" blips.

So we only pass the unavailable state on if the sensor stays silent for a
grace period, counted from its last packet.

HA saves each coordinator's last data under its config entry ID. An account
entry has one coordinator per sensor, so each needs its own restore key.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_address_present,
)
from homeassistant.components.bluetooth.passive_update_processor import (
    PASSIVE_UPDATE_PROCESSOR,
    PassiveBluetoothProcessorCoordinator,
    PassiveBluetoothProcessorData,
)
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_call_later

from .device import KubuUpdate

_LOGGER = logging.getLogger(__name__)


class KubuCoordinator(PassiveBluetoothProcessorCoordinator[KubuUpdate]):
    """Passive coordinator that ignores short gaps in adverts."""

    def __init__(
        self,
        *args: Any,
        availability_grace: float,
        restore_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set up; availability_grace is in seconds since the last packet.

        restore_key replaces the config entry ID as the key HA saves this
        coordinator's data under.
        """
        super().__init__(*args, **kwargs)
        if restore_key is not None:
            data: PassiveBluetoothProcessorData = self.hass.data[
                PASSIVE_UPDATE_PROCESSOR
            ]
            self.restore_key = restore_key
            self.restore_data = data.all_restore_data.setdefault(restore_key, {})
        self._availability_grace = availability_grace
        self._cancel_grace: CALLBACK_TYPE | None = None
        self._on_stop.append(self._async_cancel_grace)

    @callback
    def _async_cancel_grace(self) -> None:
        if self._cancel_grace is not None:
            self._cancel_grace()
            self._cancel_grace = None

    @callback
    def _async_handle_unavailable(
        self, service_info: BluetoothServiceInfoBleak
    ) -> None:
        silent_for = monotonic_time_coarse() - service_info.time
        if silent_for < self._availability_grace:
            _LOGGER.debug(
                "%s: HA reports unavailable after %.0fs of silence; waiting up to %.0fs",
                self.address,
                silent_for,
                self._availability_grace,
            )
            self._async_cancel_grace()
            self._cancel_grace = async_call_later(
                self.hass,
                self._availability_grace - silent_for,
                partial(self._async_grace_expired, service_info),
            )
            return
        super()._async_handle_unavailable(service_info)

    @callback
    def _async_grace_expired(
        self, service_info: BluetoothServiceInfoBleak, _now: Any
    ) -> None:
        self._cancel_grace = None
        if async_address_present(self.hass, self.address, connectable=False):
            return  # heard again in the meantime
        _LOGGER.debug("%s: still silent after the grace period", self.address)
        super()._async_handle_unavailable(service_info)
