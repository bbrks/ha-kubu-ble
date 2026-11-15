"""End-to-end tests: adverts in, entity states out."""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kubu_ble.const import CONF_DP_KEY, CONF_SENSOR_TYPE, DOMAIN

from .conftest import (
    ADDRESS,
    CLOSED,
    LOCKED,
    OPEN,
    TEST_KEY_HEX,
    inject,
    make_service_info,
)

LOCK = "binary_sensor.kuburetropro_34_56_lock"
DOOR = "binary_sensor.kuburetropro_34_56_door"
TAMPER = "binary_sensor.kuburetropro_34_56_tamper"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_DP_KEY: TEST_KEY_HEX, CONF_SENSOR_TYPE: 8},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_states_follow_adverts(hass: HomeAssistant) -> None:
    entry = await _setup(hass)

    # A name-only advert creates no entities yet.
    inject(hass, make_service_info(None))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK) is None

    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_OFF  # off = locked
    assert hass.states.get(DOOR).state == STATE_OFF
    assert hass.states.get(TAMPER).state == STATE_OFF

    inject(hass, make_service_info(OPEN))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_ON
    assert hass.states.get(DOOR).state == STATE_ON

    inject(hass, make_service_info(CLOSED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_ON  # closed but unlocked
    assert hass.states.get(DOOR).state == STATE_OFF

    # A name-only advert keeps the last state.
    inject(hass, make_service_info(None))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_ON
    assert hass.states.get(DOOR).state == STATE_OFF

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_wrong_key_creates_no_entities(hass: HomeAssistant, caplog) -> None:
    await _setup(hass)
    inject(hass, make_service_info(LOCKED, key_hex="ff" * 16))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK) is None
    assert "check the dpKey" in caplog.text


async def test_lock_has_plain_lock_wording(hass: HomeAssistant) -> None:
    """Closed-but-unlocked really is unlocked (door opens from outside)."""
    from homeassistant.helpers import entity_registry as er

    await _setup(hass)
    inject(hass, make_service_info(CLOSED))
    await hass.async_block_till_done()
    entry = er.async_get(hass).async_get(LOCK)
    assert entry.translation_key is None  # device class "Lock": Locked/Unlocked
    assert hass.states.get(LOCK).state == STATE_ON  # unlocked
