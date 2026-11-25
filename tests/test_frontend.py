"""The dashboard card is served and loaded by the integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from homeassistant.components.http import HomeAssistantHTTP
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.kubu_ble.card import CARD_PATH, CARD_URL
from custom_components.kubu_ble.const import DOMAIN

MANIFEST = Path(CARD_PATH).parents[1] / "manifest.json"


async def test_card_is_served_and_loaded(hass: HomeAssistant) -> None:
    hass.config.components.add("frontend")
    with (
        patch.object(
            HomeAssistantHTTP, "async_register_static_paths", AsyncMock()
        ) as register,
        patch("custom_components.kubu_ble.card.add_extra_js_url") as add_js,
    ):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    (paths,) = register.call_args.args
    (config,) = paths
    assert config.url_path == CARD_URL
    assert Path(config.path) == CARD_PATH
    version = json.loads(MANIFEST.read_text())["version"]
    add_js.assert_called_once_with(hass, f"{CARD_URL}?v={version}")


async def test_no_frontend_no_card(hass: HomeAssistant) -> None:
    with patch("custom_components.kubu_ble.card.add_extra_js_url") as add_js:
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    add_js.assert_not_called()


def test_card_file_is_shipped() -> None:
    text = CARD_PATH.read_text()
    assert "customElements.define(CARD_TYPE" in text
    assert "kubu-ble-card" in text
