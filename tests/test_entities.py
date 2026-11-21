"""End-to-end tests: adverts in, entity states out."""

from __future__ import annotations

from homeassistant.const import CONF_ADDRESS, STATE_OFF, STATE_ON, STATE_UNKNOWN
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


async def test_repeated_bad_payloads_start_reauth(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    # Five different payloads that do not decode with the stored key.
    for i in range(5):
        frame = f"10010B29{i:04X}2CA7FFEA000010FF042E"
        inject(hass, make_service_info(frame, key_hex="ff" * 16))
        await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]
    assert flows[0]["context"]["entry_id"] == entry.entry_id


async def test_no_state_sensor(hass: HomeAssistant) -> None:
    """Lock + door cover the state; there is no separate state sensor."""
    await _setup(hass)
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kuburetropro_34_56_state") is None


def test_only_known_entities_filters_restored_keys() -> None:
    """Restored data for a removed key must not create an entity."""
    from types import SimpleNamespace

    from custom_components.kubu_ble.entity import only_known_entities

    added: list = []
    add = only_known_entities({"lock": object()}, added.extend)
    lock = SimpleNamespace(entity_key=SimpleNamespace(key="lock"))
    state = SimpleNamespace(entity_key=SimpleNamespace(key="state"))
    add([lock, state])
    assert added == [lock]


VIBRATION = "binary_sensor.kuburetropro_34_56_vibration"
# LOCKED frame with byte 10 set: 0x10 activity, 0x20 security.
VIB_ACTIVITY = "10010B2901B12CA7FFEA100010FF042E"
VIB_SECURITY = "10010B2901B12CA8FFEA200010FF042E"
# LOCKED with the next counter: the sensor never sends a payload twice.
LOCKED_NEXT = "10010B2901B22CA7FFEA000010FF042E"


async def test_vibration_and_tamper_are_primary(hass: HomeAssistant) -> None:
    from homeassistant.helpers import entity_registry as er

    await _setup(hass)
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get(VIBRATION).state == STATE_OFF

    inject(hass, make_service_info(VIB_ACTIVITY))
    await hass.async_block_till_done()
    assert hass.states.get(VIBRATION).state == STATE_ON

    inject(hass, make_service_info(LOCKED_NEXT))
    await hass.async_block_till_done()
    assert hass.states.get(VIBRATION).state == STATE_OFF

    inject(hass, make_service_info(VIB_SECURITY))
    await hass.async_block_till_done()
    assert hass.states.get(VIBRATION).state == STATE_ON
    assert hass.states.get(LOCK).state == STATE_OFF  # still locked

    registry = er.async_get(hass)
    for entity_id in (VIBRATION, TAMPER):
        assert registry.async_get(entity_id).entity_category is None


def test_current_description_replaces_restored_one() -> None:
    from types import SimpleNamespace

    from custom_components.kubu_ble.entity import only_known_entities

    new_desc = object()
    added: list = []
    add = only_known_entities({"tamper": new_desc}, added.extend)
    restored = SimpleNamespace(
        entity_key=SimpleNamespace(key="tamper"), entity_description="old"
    )
    add([restored])
    assert added == [restored]
    assert restored.entity_description is new_desc


FORCE = "binary_sensor.kuburetropro_34_56_excessive_force"


async def test_excessive_force_only_on_strong_hit(hass: HomeAssistant) -> None:
    await _setup(hass)
    inject(hass, make_service_info(VIB_ACTIVITY))
    await hass.async_block_till_done()
    assert hass.states.get(VIBRATION).state == STATE_ON
    assert hass.states.get(FORCE).state == STATE_OFF
    inject(hass, make_service_info(VIB_SECURITY))
    await hass.async_block_till_done()
    assert hass.states.get(VIBRATION).state == STATE_ON
    assert hass.states.get(FORCE).state == STATE_ON


async def test_lock_has_plain_lock_wording(hass: HomeAssistant) -> None:
    """Closed-but-unlocked really is unlocked (door opens from outside)."""
    from homeassistant.helpers import entity_registry as er

    await _setup(hass)
    inject(hass, make_service_info(CLOSED))
    await hass.async_block_till_done()
    entry = er.async_get(hass).async_get(LOCK)
    assert entry.translation_key is None  # device class "Lock": Locked/Unlocked
    assert hass.states.get(LOCK).state == STATE_ON  # unlocked


async def test_firmware_and_battery_type(hass: HomeAssistant) -> None:
    from homeassistant.helpers import entity_registry as er

    await _setup(hass)
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kuburetropro_34_56_firmware").state == "04.46"
    battery_type = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{ADDRESS}-battery_type"
    )
    assert hass.states.get(battery_type).state == "2× CR1632"


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


async def test_new_values_logged_once(hass: HomeAssistant, caplog) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="custom_components.kubu_ble")
    await _setup(hass)
    # Status low6 = 43 (0xEB): a "locked" variant not seen on real sensors yet.
    for counter in (1, 2):
        inject(hass, make_service_info(f"10010B29{counter:04X}2CA7FFEB000010FF042E"))
        await hass.async_block_till_done()
    assert caplog.text.count("new status 0x2b") == 1
    # Unknown frame type 0x30.
    inject(hass, make_service_info("30010B2900032CA7FFEA000010FF042E"))
    await hass.async_block_till_done()
    assert "new frame type 0x30" in caplog.text


async def test_sensitivity_sensor_from_entry(hass: HomeAssistant) -> None:
    from custom_components.kubu_ble.const import CONF_VIB_SETTING

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_DP_KEY: TEST_KEY_HEX,
            CONF_SENSOR_TYPE: 8,
            CONF_VIB_SETTING: "demo",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(_sensitivity_entity_id(hass)).state == "demo"


async def test_sensitivity_unknown_when_not_set(hass: HomeAssistant) -> None:
    await _setup(hass)  # no vib_setting in the Kubu account
    assert hass.states.get(_sensitivity_entity_id(hass)).state == "unknown"


def _sensitivity_entity_id(hass: HomeAssistant) -> str:
    from homeassistant.helpers import entity_registry as er

    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{ADDRESS}-vibration_sensitivity"
    )
    assert entity_id
    return entity_id


async def test_sensitivity_off_is_valid(hass: HomeAssistant) -> None:
    from custom_components.kubu_ble.const import CONF_VIB_SETTING

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_DP_KEY: TEST_KEY_HEX,
            CONF_SENSOR_TYPE: 8,
            CONF_VIB_SETTING: "off",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(_sensitivity_entity_id(hass)).state == "off"


def test_icons_match_translation_keys() -> None:
    """Every icon entry needs a translation key, and is valid for hassfest."""
    import json
    import pathlib

    base = pathlib.Path(__file__).parents[1] / "custom_components" / "kubu_ble"
    icons = json.loads((base / "icons.json").read_text())["entity"]
    names = json.loads((base / "translations" / "en.json").read_text())["entity"]
    for platform, entries in icons.items():
        for key, icon in entries.items():
            assert key in names[platform], f"{platform}.{key} has no translation"
            assert icon["default"].startswith("mdi:")
            assert all(v.startswith("mdi:") for v in icon.get("state", {}).values())


async def test_no_battery_level_entity(hass: HomeAssistant) -> None:
    await _setup(hass)
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.kuburetropro_34_56_battery_level") is None


def _frame(status_hex: str) -> str:
    """A real Retro Pro frame with bytes 8-9 swapped for status_hex."""
    return f"10010B2901B12CA7{status_hex}000010FF042E"


async def _setup_model(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_DP_KEY: TEST_KEY_HEX, **data},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_tilt_turn_window(hass: HomeAssistant) -> None:
    from homeassistant.helpers import device_registry as dr

    from custom_components.kubu_ble.const import CONF_PRODUCT_TYPE, CONF_SENSOR_CLASS

    entry = await _setup_model(
        hass,
        **{
            CONF_SENSOR_TYPE: 9,
            CONF_SENSOR_CLASS: "retroPro",
            CONF_PRODUCT_TYPE: "tt-w",
        },
    )
    window = "binary_sensor.kuburetropro_34_56_window"
    tilted = "binary_sensor.kuburetropro_34_56_tilted"

    inject(hass, make_service_info(_frame("FFFD")))  # secure
    await hass.async_block_till_done()
    assert hass.states.get(window).state == STATE_OFF
    assert hass.states.get(LOCK).state == STATE_OFF
    assert hass.states.get(tilted).state == STATE_OFF
    assert hass.states.get(TAMPER).state == STATE_OFF
    assert hass.states.get(DOOR) is None  # a window, not a door

    inject(hass, make_service_info(_frame("FFD7")))  # tilt
    await hass.async_block_till_done()
    assert hass.states.get(window).state == STATE_ON
    assert hass.states.get(tilted).state == STATE_ON
    assert hass.states.get(LOCK).state == STATE_ON

    inject(hass, make_service_info(_frame("FFEB")))  # open
    await hass.async_block_till_done()
    assert hass.states.get(window).state == STATE_ON
    assert hass.states.get(tilted).state == STATE_OFF

    inject(hass, make_service_info(_frame("FF00")))  # in no set: tampered
    await hass.async_block_till_done()
    assert hass.states.get(TAMPER).state == STATE_ON
    assert hass.states.get(tilted).state == STATE_UNKNOWN
    assert hass.states.get(window).state == STATE_UNKNOWN

    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.model == "Works with Kubu tilt & turn window"
    assert device.model_id == "retroPro type 9"


async def test_open_closed_only_sensor(hass: HomeAssistant) -> None:
    """Types 2 and 3 only know open and closed: no lock or tamper entity."""
    from custom_components.kubu_ble.const import CONF_SENSOR_CLASS

    await _setup_model(hass, **{CONF_SENSOR_TYPE: 2, CONF_SENSOR_CLASS: "retroPro"})
    window = "binary_sensor.kuburetropro_34_56_window"

    inject(hass, make_service_info(_frame("FFFF")))
    await hass.async_block_till_done()
    assert hass.states.get(window).state == STATE_ON
    assert hass.states.get(LOCK) is None
    assert hass.states.get(TAMPER) is None
    assert hass.states.get(BATT_LOW) is not None

    inject(hass, make_service_info(_frame("FFEA")))
    await hass.async_block_till_done()
    assert hass.states.get(window).state == STATE_OFF


async def test_door_sensor_class(hass: HomeAssistant) -> None:
    from custom_components.kubu_ble.const import CONF_SENSOR_CLASS

    await _setup_model(hass, **{CONF_SENSOR_TYPE: 2, CONF_SENSOR_CLASS: "door"})
    inject(hass, make_service_info(_frame("FF10")))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_OFF
    assert hass.states.get(DOOR).state == STATE_OFF

    inject(hass, make_service_info(_frame("FF01")))  # unlocked, closed
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_ON
    assert hass.states.get(DOOR).state == STATE_OFF

    inject(hass, make_service_info(_frame("FF00")))  # unknown value
    await hass.async_block_till_done()
    assert hass.states.get(TAMPER).state == "unknown"


def _from_source(source: str, info):
    info.source = source
    return info


async def test_old_payload_from_other_adapter_is_ignored(hass: HomeAssistant) -> None:
    """Two adapters: HA can hand back one adapter's older payload.

    habluetooth keeps each adapter's last manufacturer data. A passive
    adapter that hears a name-only advert still carries the payload from
    its last active window, which may be older than what HA already showed.
    """
    await _setup(hass)
    inject(hass, _from_source("hci0", make_service_info(LOCKED)))
    inject(hass, _from_source("proxy", make_service_info(OPEN)))
    await hass.async_block_till_done()
    assert hass.states.get(DOOR).state == STATE_ON

    stale = make_service_info(LOCKED, raw=make_service_info(None).raw)
    inject(hass, _from_source("hci0", stale))
    await hass.async_block_till_done()
    assert hass.states.get(DOOR).state == STATE_ON
    assert hass.states.get(LOCK).state == STATE_ON
