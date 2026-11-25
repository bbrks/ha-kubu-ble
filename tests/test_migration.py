"""Moving old one-sensor entries into an account entry."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS, CONF_EMAIL, CONF_PASSWORD, STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kubu_ble.const import (
    CONF_DP_KEY,
    CONF_SENSOR_TYPE,
    DOMAIN,
    ISSUE_LEGACY_ENTRIES,
)

from .cloud_fixtures import (
    EMAIL,
    PATCH_NEARBY,
    USER_ID,
    WINDOW_NODE,
    account_entry,
    mock_cloud,
    sensor_subentry,
)
from .conftest import ADDRESS, LOCKED, NAME, TEST_KEY_HEX, inject, make_service_info

LOCK = "binary_sensor.kuburetropro_34_56_lock"
LOGIN = {CONF_EMAIL: EMAIL, CONF_PASSWORD: "secret"}


async def _legacy(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title=NAME,
        data={CONF_ADDRESS: ADDRESS, CONF_DP_KEY: TEST_KEY_HEX, CONF_SENSOR_TYPE: 8},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_OFF
    return entry


def _issue(hass: HomeAssistant) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_LEGACY_ENTRIES)


async def test_legacy_entry_asks_to_sign_in(hass: HomeAssistant) -> None:
    entry = await _legacy(hass)
    issue = _issue(hass)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders == {"count": "1"}

    await hass.config_entries.async_remove(entry.entry_id)
    assert _issue(hass) is None


async def _check_moved(hass: HomeAssistant, legacy: MockConfigEntry) -> None:
    assert hass.config_entries.async_get_entry(legacy.entry_id) is None
    (account,) = hass.config_entries.async_entries(DOMAIN)
    assert account.unique_id == USER_ID
    (subentry,) = account.subentries.values()
    assert subentry.unique_id == ADDRESS

    ent = er.async_get(hass).async_get(LOCK)
    assert ent is not None  # same entity ID as before
    assert ent.name == "Front door lock"  # the user's own name is kept
    assert ent.config_entry_id == account.entry_id
    assert ent.config_subentry_id == subentry.subentry_id
    device = dr.async_get(hass).async_get(ent.device_id)
    assert device.config_entry_id == account.entry_id
    assert device.config_subentry_id == subentry.subentry_id
    assert _issue(hass) is None

    # The last state is still shown, and new adverts keep coming in.
    assert hass.states.get(LOCK).state == STATE_OFF
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_OFF


async def test_sign_in_moves_legacy_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    legacy = await _legacy(hass)
    er.async_get(hass).async_update_entity(LOCK, name="Front door lock")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PATCH_NEARBY, return_value=[]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], LOGIN
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await _check_moved(hass, legacy)


async def test_repair_moves_legacy_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    assert await async_setup_component(hass, "repairs", {})
    legacy = await _legacy(hass)
    er.async_get(hass).async_update_entity(LOCK, name="Front door lock")

    from homeassistant.components.repairs import repairs_flow_manager

    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": ISSUE_LEGACY_ENTRIES})
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"] == {"count": "1"}
    with patch(PATCH_NEARBY, return_value=[]):
        result = await manager.async_configure(result["flow_id"], LOGIN)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await _check_moved(hass, legacy)


async def test_repair_bad_login(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock, login_status=401)
    assert await async_setup_component(hass, "repairs", {})
    await _legacy(hass)

    from homeassistant.components.repairs import repairs_flow_manager

    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": ISSUE_LEGACY_ENTRIES})
    result = await manager.async_configure(result["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_move_does_not_delete_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Entities are moved, not deleted and brought back."""
    mock_cloud(aioclient_mock)
    await _legacy(hass)
    ent_reg = er.async_get(hass)
    ent_reg.async_update_entity(LOCK, name="Front door lock", labels={"doors"})
    old_id = ent_reg.async_get(LOCK).id
    removed: list[str] = []
    original = ent_reg.async_remove

    def tracked(entity_id: str) -> None:
        removed.append(entity_id)
        original(entity_id)

    account = account_entry(sensor_subentry())
    account.add_to_hass(hass)
    with (
        patch.object(ent_reg, "async_remove", tracked),
        patch(PATCH_NEARBY, return_value=[]),
    ):
        assert await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
    assert removed == []
    ent = ent_reg.async_get(LOCK)
    assert ent.id == old_id
    assert ent.labels == {"doors"}
    assert ent.config_subentry_id is not None


@pytest.mark.parametrize("account_first", [False, True])
async def test_both_kinds_loading_at_startup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, account_first: bool
) -> None:
    """A move left half-done (both entries exist) finishes at startup."""
    mock_cloud(aioclient_mock)
    legacy = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title=NAME,
        data={CONF_ADDRESS: ADDRESS, CONF_DP_KEY: TEST_KEY_HEX, CONF_SENSOR_TYPE: 8},
    )
    account = account_entry(sensor_subentry())
    for entry in (account, legacy) if account_first else (legacy, account):
        entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=legacy.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, ADDRESS)},
        name=NAME,
    )
    dev_reg.async_update_device(device.id, area_id="hall")
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{ADDRESS}-lock",
        config_entry=legacy,
        suggested_object_id="kuburetropro_34_56_lock",
        device_id=device.id,
    )
    ent_reg.async_update_entity(LOCK, name="Front door lock")

    with patch(PATCH_NEARBY, return_value=[]):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()

    assert hass.config_entries.async_get_entry(legacy.entry_id) is None
    ent = ent_reg.async_get(LOCK)
    assert ent.config_entry_id == account.entry_id
    assert ent.name == "Front door lock"
    assert dev_reg.async_get(device.id).area_id == "hall"
    inject(hass, make_service_info(LOCKED))
    await hass.async_block_till_done()
    assert hass.states.get(LOCK).state == STATE_OFF


async def test_repair_with_account_that_lacks_the_sensors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Signing in to another account says so, and keeps the notice."""
    mock_cloud(aioclient_mock, WINDOW_NODE)
    assert await async_setup_component(hass, "repairs", {})
    await _legacy(hass)

    from homeassistant.components.repairs import repairs_flow_manager

    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": ISSUE_LEGACY_ENTRIES})
    result = await manager.async_configure(result["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_sensors_to_move"}
    assert _issue(hass) is not None
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_repair_remembers_email_and_offers_offline_mode(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_cloud(aioclient_mock)
    assert await async_setup_component(hass, "repairs", {})
    legacy = await _legacy(hass)
    # An email kept from an earlier sign-in (legacy reauth) is filled in.
    hass.config_entries.async_update_entry(
        legacy, data={**legacy.data, CONF_EMAIL: EMAIL}
    )

    from homeassistant.components.repairs import repairs_flow_manager

    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": ISSUE_LEGACY_ENTRIES})
    email_key = next(k for k in result["data_schema"].schema if k == CONF_EMAIL)
    assert email_key.description == {"suggested_value": EMAIL}
    with patch(PATCH_NEARBY, return_value=[]):
        result = await manager.async_configure(
            result["flow_id"], {**LOGIN, "offline_mode": True}
        )
        await hass.async_block_till_done()
    (account,) = hass.config_entries.async_entries(DOMAIN)
    assert account.options == {"offline_mode": True}
    assert account.subentries
