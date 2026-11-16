"""End-to-end tests: adverts in, entity states out."""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kubu_ble.const import CONF_DP_KEY, CONF_SENSOR_TYPE, DOMAIN

from .conftest import (
    ADDRESS,
    CLOSED,
    GLITCH_BATTERY,
    LOCKED,
    OPEN,
    TEST_KEY_HEX,
    inject,
    make_service_info,
)

LOCK = "binary_sensor.kuburetropro_34_56_lock"
DOOR = "binary_sensor.kuburetropro_34_56_door"
TAMPER = "binary_sensor.kuburetropro_34_56_tamper"
BATT_LOW = "binary_sensor.kuburetropro_34_56_battery"
VOLTAGE = "sensor.kuburetropro_34_56_voltage"
STATE = "sensor.kuburetropro_34_56_state"


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
    assert hass.states.get(BATT_LOW).state == STATE_OFF
    assert float(hass.states.get(VOLTAGE).state) == 2.857
    assert hass.states.get(STATE).state == "locked"

    inject(hass, make_service_info(OPEN))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_ON
    assert hass.states.get(DOOR).state == STATE_ON

    inject(hass, make_service_info(CLOSED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_ON  # closed but unlocked
    assert hass.states.get(DOOR).state == STATE_OFF

    # A glitchy low reading keeps the last good voltage.
    inject(hass, make_service_info(GLITCH_BATTERY))
    await hass.async_block_till_done()
    assert float(hass.states.get(VOLTAGE).state) == 2.859

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


async def test_firmware(hass: HomeAssistant) -> None:
    await _setup(hass)
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kuburetropro_34_56_firmware").state == "04.46"


async def test_battery_low_uses_hysteresis(hass: HomeAssistant) -> None:
    await _setup(hass)
    # battery mV are bytes 2-3; counter (4-5) changes so each frame is new.
    for counter, mv, expected in (
        (1, 3000, STATE_OFF),
        (2, 2170, STATE_ON),
        (3, 2400, STATE_ON),
        (4, 2700, STATE_OFF),
    ):
        frame = f"1001{mv:04X}{counter:04X}2CA7FFEA000010FF042E"
        inject(hass, make_service_info(frame))
        await hass.async_block_till_done()
        assert hass.states.get(BATT_LOW).state == expected, mv


async def test_no_battery_level_entity(hass: HomeAssistant) -> None:
    await _setup(hass)
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kuburetropro_34_56_battery_level") is None
