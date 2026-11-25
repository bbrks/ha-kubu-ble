"""Serve the Kubu sensor dashboard card, and load it on every dashboard.

Users don't add a Lovelace resource: the card comes and goes with the
integration. The URL has the integration version in it, so browsers fetch
the new card after an update.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_URL = f"/{DOMAIN}/kubu-ble-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "kubu-ble-card.js"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and add it to the frontend, if there is a frontend."""
    http = getattr(hass, "http", None)
    if "frontend" not in hass.config.components or http is None:
        _LOGGER.debug("No frontend: not adding the Kubu sensor card")
        return
    integration = await async_get_integration(hass, DOMAIN)
    await http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=True)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")
