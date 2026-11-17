"""Ask for an active scan when the sensor sends a burst.

The sensor only puts its state in the scan response, so HA must scan
actively to read it. In HA's "Auto" scanning mode an adapter is passive
most of the time and only scans actively in short scheduled windows.

The sensor sends a burst of adverts whenever its state changes. Even a
passive adapter sees those adverts (they just carry no state). So when a
name-only advert arrives and no scan response follows within a moment,
we ask HA for a short active window right away, while the burst is still
going. Adapters in "Active" mode get the scan response straight away, so
this never fires for them.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_register_advertisement_callback,
    async_request_active_scan,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .protocol import raw_has_state

_LOGGER = logging.getLogger(__name__)


class BurstScanTrigger:
    """Request an on-demand active scan when state did not arrive."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        *,
        settle: float,
        duration: float,
        cooldown: float,
    ) -> None:
        """Set up for one sensor address."""
        self._hass = hass
        self._address = address
        self._settle = settle
        self._duration = duration
        self._cooldown = cooldown
        self._last_packet: float | None = None
        self._last_state_packet = 0.0
        self._last_request: float | None = None
        self._pending_advert_at: float | None = None
        self._cancel_timer: CALLBACK_TYPE | None = None
        self._unsub: CALLBACK_TYPE | None = None

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Start listening. Returns a function that stops it."""
        self._unsub = async_register_advertisement_callback(
            self._hass, self._async_on_packet, self._address
        )
        return self._async_stop

    @callback
    def _async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def _async_on_packet(self, service_info: BluetoothServiceInfoBleak) -> None:
        now = time.monotonic()
        gap = None if self._last_packet is None else now - self._last_packet
        self._last_packet = now
        has_state = raw_has_state(service_info.raw)
        _LOGGER.debug(
            "%s: %s via %s, rssi %s, %s since last packet",
            self._address,
            "scan response (state)" if has_state else "advert (no state)",
            service_info.source,
            service_info.rssi,
            "first" if gap is None else f"{gap:.2f}s",
        )
        if has_state:
            self._last_state_packet = now
            return
        if self._cancel_timer is not None:
            return  # a check is already waiting
        self._pending_advert_at = now
        self._cancel_timer = async_call_later(
            self._hass, self._settle, self._async_check
        )

    @callback
    def _async_check(self, _now: Any) -> None:
        self._cancel_timer = None
        advert_at = self._pending_advert_at
        if advert_at is None or self._last_state_packet >= advert_at:
            return  # the adapter is scanning actively; state arrived
        now = time.monotonic()
        if self._last_request is not None and now - self._last_request < self._cooldown:
            return
        self._last_request = now
        _LOGGER.debug(
            "%s: advert without state; requesting %.0fs active scan",
            self._address,
            self._duration,
        )
        self._hass.async_create_background_task(
            self._async_safe_request(),
            f"kubu_ble active scan {self._address}",
            eager_start=True,
        )

    async def _async_safe_request(self) -> None:
        try:
            await async_request_active_scan(self._hass, self._duration)
        except Exception:
            _LOGGER.debug(
                "%s: active scan request failed", self._address, exc_info=True
            )
