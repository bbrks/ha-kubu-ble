"""Config flow for Kubu BLE.

Setup signs in to the user's Kubu account and makes one entry for the
account, with one subentry per sensor. It keeps a refresh token, never the
password. Sensors are added:

- when signing in: every account sensor HA can hear, plus any old
  one-sensor entries (those move into the account entry),
- when Bluetooth finds a sensor of an account that is already set up,
- by the account entry's cloud refresh, as soon as they are seen,
- by hand, with "Add sensor" on the account entry.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_IMPORT,
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_ADDRESS, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .account import (
    configured_sensors,
    get_account,
    is_account_entry,
    is_offline,
    key_matches_live_data,
    nearby_sensors,
    node_model,
    sensor_data,
    sensor_subentries,
    sensor_subentry,
    sensors_to_add,
)
from .cloud import (
    ERROR_EMAIL_NOT_VERIFIED,
    ERROR_TOO_MANY_TRIES,
    KubuAuthError,
    KubuCloud,
    KubuCloudError,
    KubuCloudNode,
    normalize_mac,
)
from .const import (
    CONF_OFFLINE,
    CONF_PRODUCT_TYPE,
    CONF_REFRESH_TOKEN,
    CONF_SENSOR_CLASS,
    CONF_SENSOR_TYPE,
    DOMAIN,
    SUBENTRY_SENSOR,
)
from .device import is_kubu_device
from .protocol import InvalidKey, KubuModel, model_for

_LOGGER = logging.getLogger(__name__)

ISSUES_URL = "https://github.com/bbrks/ha-kubu-ble/issues"

LOGIN_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)
# The first sign-in also asks about offline mode.
SETUP_SCHEMA = LOGIN_SCHEMA.extend({vol.Optional(CONF_OFFLINE, default=False): bool})
OPTIONS_SCHEMA = vol.Schema({vol.Optional(CONF_OFFLINE, default=False): bool})

# Passed to the import step by the repair flow, after it signed in.
IMPORT_USER_ID = "user_id"
IMPORT_NODES = "nodes"


class SignIn:
    """The result of a good sign-in."""

    def __init__(
        self,
        email: str,
        refresh_token: str,
        user_id: str,
        nodes: dict[str, KubuCloudNode],
    ) -> None:
        """Keep what the rest of the flow needs."""
        self.email = email
        self.refresh_token = refresh_token
        self.user_id = user_id
        self.nodes = nodes


async def async_sign_in(
    hass: HomeAssistant, email: str, password: str
) -> tuple[SignIn | None, str | None]:
    """Sign in and list the account's sensors: (SignIn, None) or (None, error)."""
    cloud = KubuCloud(async_get_clientsession(hass))
    try:
        await cloud.login(email, password)
        nodes = await cloud.get_ble_nodes()
    except KubuAuthError as err:
        _LOGGER.warning("Kubu sign-in rejected: %s", err)
        if err.code == ERROR_TOO_MANY_TRIES:
            return None, "too_many_attempts"
        if err.code == ERROR_EMAIL_NOT_VERIFIED:
            return None, "email_not_verified"
        return None, "invalid_auth"
    except KubuCloudError as err:
        _LOGGER.warning("Kubu cloud error: %s", err)
        return None, "cannot_connect"
    if not cloud.refresh_token:
        _LOGGER.warning("Kubu sign-in gave no refresh token")
        return None, "cannot_connect"
    email = email.strip()
    user_id = cloud.user_id or email.lower()
    return SignIn(email, cloud.refresh_token, user_id, nodes), None


def known_email(hass: HomeAssistant, entry: ConfigEntry | None = None) -> str | None:
    """The email to fill in on the sign-in form: the entry's own, else the
    one used last. Only the email is kept, never the password.
    """
    if entry is not None and (email := entry.data.get(CONF_EMAIL)):
        return email
    used = [
        e
        for e in hass.config_entries.async_entries(DOMAIN, include_ignore=False)
        if e.data.get(CONF_EMAIL)
    ]
    if not used:
        return None
    return max(used, key=lambda e: e.modified_at).data[CONF_EMAIL]


def legacy_sensors(hass: HomeAssistant) -> dict[str, str]:
    """{MAC: address} of old one-sensor entries, to move into the account."""
    return {
        normalize_mac(e.data[CONF_ADDRESS]): e.data[CONF_ADDRESS]
        for e in hass.config_entries.async_entries(DOMAIN, include_ignore=False)
        if not is_account_entry(e) and CONF_ADDRESS in e.data
    }


def _unsupported(node: KubuCloudNode) -> dict[str, str]:
    return {
        "sensor_class": node.sensor_class or "?",
        "sensor_type": str(node.sensor_type),
        "issues_url": ISSUES_URL,
    }


class KubuConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kubu BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Set up the flow."""
        self._address: str | None = None
        self._name: str | None = None
        self._sign_in: SignIn | None = None
        self._new_sensors: list[ConfigSubentryData] = []
        self._offline: bool | None = None  # None: not asked (reauth etc.)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Account entries can have sensors added by hand."""
        if not is_account_entry(config_entry):
            return {}
        return {SUBENTRY_SENSOR: SensorSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> KubuOptionsFlow:
        """Offline mode can be turned on or off later."""
        return KubuOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Only account entries have options."""
        return is_account_entry(config_entry)

    # --- Starting points ----------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a Kubu account."""
        return await self.async_step_login(user_input)

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a sensor found by Bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not is_kubu_device(discovery_info):
            return self.async_abort(reason="not_supported")
        mac = normalize_mac(discovery_info.address)
        if mac in configured_sensors(self.hass):
            return self.async_abort(reason="already_configured")
        self._address = discovery_info.address
        self._name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": self._name}

        # Already signed in to an account that has this sensor? Just add it.
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if (account := get_account(self.hass, entry)) is None or is_offline(entry):
                continue  # offline mode: only the user starts cloud visits
            try:
                nodes = await account.async_get_nodes()
            except KubuAuthError:
                entry.async_start_reauth(self.hass)
                continue
            except KubuCloudError as err:
                _LOGGER.debug("Kubu cloud error: %s", err)
                continue
            if (node := nodes.get(mac)) is None:
                continue
            if node_model(node) is None:
                return self.async_abort(
                    reason="unsupported_sensor",
                    description_placeholders=_unsupported(node),
                )
            if not key_matches_live_data(self.hass, self._address, node):
                return self.async_abort(
                    reason="key_mismatch",
                    description_placeholders={"issues_url": ISSUES_URL},
                )
            self.hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(**sensor_subentry(self._address, node)),
            )
            return self.async_abort(
                reason="sensor_added", description_placeholders={"name": node.name}
            )
        return await self.async_step_login()

    async def async_step_import(
        self, import_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Finish a sign-in done by the repair flow."""
        self._sign_in = SignIn(
            import_data[CONF_EMAIL],
            import_data[CONF_REFRESH_TOKEN],
            import_data[IMPORT_USER_ID],
            import_data[IMPORT_NODES],
        )
        self._offline = bool(import_data.get(CONF_OFFLINE, False))
        return await self._async_set_up_account()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Sign in again: the refresh token expired, or a key stopped working."""
        entry = self._get_reauth_entry()
        if not is_account_entry(entry):
            self._address = entry_data[CONF_ADDRESS]
        self._name = entry.title
        return await self.async_step_login()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in again to refresh keys and settings from Kubu now."""
        entry = self._get_reconfigure_entry()
        if not is_account_entry(entry):
            self._address = entry.data[CONF_ADDRESS]
        self._name = entry.title
        return await self.async_step_login()

    # --- Kubu account -------------------------------------------------------

    async def async_step_login(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in to the Kubu account."""
        errors: dict[str, str] = {}
        first_setup = self.source not in (SOURCE_REAUTH, SOURCE_RECONFIGURE)
        if user_input is not None:
            if first_setup:
                self._offline = bool(user_input.get(CONF_OFFLINE, False))
            sign_in, error = await async_sign_in(
                self.hass, user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                assert sign_in is not None
                self._sign_in = sign_in
                result = await self._async_after_sign_in()
                if isinstance(result, str):
                    errors["base"] = result
                else:
                    return result

        if user_input:
            email = user_input[CONF_EMAIL]
        elif self.source == SOURCE_REAUTH:
            email = known_email(self.hass, self._get_reauth_entry())
        elif self.source == SOURCE_RECONFIGURE:
            email = known_email(self.hass, self._get_reconfigure_entry())
        else:
            email = known_email(self.hass)
        suggested: dict[str, Any] = {CONF_EMAIL: email} if email else {}
        if user_input and CONF_OFFLINE in user_input:
            suggested[CONF_OFFLINE] = user_input[CONF_OFFLINE]
        schema = SETUP_SCHEMA if first_setup else LOGIN_SCHEMA
        return self.async_show_form(
            step_id="login",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders={
                "name": self._name or "your sensors",
                "issues_url": ISSUES_URL,
            },
        )

    async def _async_after_sign_in(self) -> ConfigFlowResult | str:
        """Act on a good sign-in. Returns an error key to show on the form."""
        assert self._sign_in is not None
        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            entry = (
                self._get_reauth_entry()
                if self.source == SOURCE_REAUTH
                else self._get_reconfigure_entry()
            )
            if is_account_entry(entry):
                return self._async_update_account(entry)
            return self._async_update_legacy(entry)

        # Check the sensor Bluetooth found, so the user hears what is wrong.
        if self._address is not None:
            node = self._sign_in.nodes.get(normalize_mac(self._address))
            if node is None:
                return "not_in_account"
            if node_model(node) is None:
                return self.async_abort(
                    reason="unsupported_sensor",
                    description_placeholders=_unsupported(node),
                )
            if not key_matches_live_data(self.hass, self._address, node):
                return "key_mismatch"
        return await self._async_set_up_account()

    def _async_update_account(self, entry: ConfigEntry) -> ConfigFlowResult:
        assert self._sign_in is not None
        if entry.unique_id != self._sign_in.user_id:
            return self.async_abort(reason="wrong_account")
        updates = {
            CONF_EMAIL: self._sign_in.email,
            CONF_REFRESH_TOKEN: self._sign_in.refresh_token,
        }
        if entry.state is ConfigEntryState.LOADED:
            # Its update listener reloads it when the token changes.
            return self.async_update_and_abort(entry, data_updates=updates)
        return self.async_update_reload_and_abort(entry, data_updates=updates)

    def _async_update_legacy(self, entry: ConfigEntry) -> ConfigFlowResult | str:
        """Old one-sensor entry: fetch its key and settings again."""
        assert self._sign_in is not None and self._address is not None
        node = self._sign_in.nodes.get(normalize_mac(self._address))
        if node is None:
            return "not_in_account"
        if node_model(node) is None:
            return self.async_abort(
                reason="unsupported_sensor", description_placeholders=_unsupported(node)
            )
        if not key_matches_live_data(self.hass, self._address, node):
            return "key_mismatch"
        return self.async_update_reload_and_abort(
            entry,
            data_updates={
                **sensor_data(self._address, node),
                CONF_EMAIL: self._sign_in.email,  # to fill in next time
            },
        )

    async def _async_set_up_account(self) -> ConfigFlowResult:
        """Create the account entry, or add sensors to the existing one."""
        assert self._sign_in is not None
        sign_in = self._sign_in
        await self.async_set_unique_id(sign_in.user_id, raise_on_progress=False)

        legacy = legacy_sensors(self.hass)
        wanted = {**nearby_sensors(self.hass), **legacy}
        if self._address is not None:
            wanted[normalize_mac(self._address)] = self._address
        skip = configured_sensors(self.hass) - set(legacy)
        if self._address is not None:
            skip.discard(normalize_mac(self._address))  # asked for by name
        self._new_sensors = sensors_to_add(self.hass, sign_in.nodes, wanted, skip)
        data = {CONF_EMAIL: sign_in.email, CONF_REFRESH_TOKEN: sign_in.refresh_token}

        entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, sign_in.user_id
        )
        if entry is not None and entry.source != SOURCE_IGNORE:
            have = {normalize_mac(s.unique_id or "") for s in sensor_subentries(entry)}
            added = [
                s
                for s in self._new_sensors
                if normalize_mac(s["unique_id"] or "") not in have
            ]
            for new in added:
                self.hass.config_entries.async_add_subentry(
                    entry, ConfigSubentry(**new)
                )
            options = dict(entry.options)
            if self._offline is not None:
                options[CONF_OFFLINE] = self._offline
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, **data}, options=options
            )
            if self._address is not None and any(
                s["unique_id"] == self._address for s in added
            ):
                return self.async_abort(
                    reason="sensor_added",
                    description_placeholders={"name": self._name or self._address},
                )
            return self.async_abort(
                reason="account_updated",
                description_placeholders={"count": str(len(added))},
            )

        unverified = [
            s["title"]
            for s in self._new_sensors
            if (model := _model_of(s)) is not None and not model.verified
        ]
        if unverified and self.source != SOURCE_IMPORT:
            return await self.async_step_unverified()
        return self._async_create_account()

    async def async_step_unverified(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Say which sensors are of untested models, and ask for reports."""
        if user_input is not None:
            return self._async_create_account()
        names = [
            f"- {s['title']}: {model.name}"
            for s in self._new_sensors
            if (model := _model_of(s)) is not None and not model.verified
        ]
        return self.async_show_form(
            step_id="unverified",
            description_placeholders={
                "sensors": "\n".join(names),
                "issues_url": ISSUES_URL,
            },
        )

    def _async_create_account(self) -> ConfigFlowResult:
        assert self._sign_in is not None
        return self.async_create_entry(
            title=self._sign_in.email,
            data={
                CONF_EMAIL: self._sign_in.email,
                CONF_REFRESH_TOKEN: self._sign_in.refresh_token,
            },
            subentries=self._new_sensors,
            options={CONF_OFFLINE: bool(self._offline)},
        )


class KubuOptionsFlow(OptionsFlow):
    """Turn offline mode on or off."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, **user_input}
            )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, {CONF_OFFLINE: is_offline(self.config_entry)}
            ),
        )


def _model_of(subentry: ConfigSubentryData) -> KubuModel | None:
    data = subentry["data"]
    return model_for(
        data[CONF_SENSOR_TYPE], data.get(CONF_SENSOR_CLASS), data.get(CONF_PRODUCT_TYPE)
    )


def _format_mac(mac: str) -> str:
    return ":".join(mac[i : i + 2] for i in range(0, 12, 2))


class SensorSubentryFlow(ConfigSubentryFlow):
    """Add a sensor from the Kubu account by hand."""

    def __init__(self) -> None:
        """Set up the flow."""
        self._choices: dict[str, KubuCloudNode] = {}
        self._addresses: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick one of the account's sensors that is not set up yet."""
        entry = self._get_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            mac = user_input[CONF_ADDRESS]
            node = self._choices[mac]
            address = self._addresses[mac]
            if node_model(node) is None:
                return self.async_abort(
                    reason="unsupported_sensor",
                    description_placeholders=_unsupported(node),
                )
            if not key_matches_live_data(self.hass, address, node):
                errors["base"] = "key_mismatch"
            else:
                try:
                    data = sensor_data(address, node)
                except InvalidKey:
                    errors["base"] = "key_mismatch"
                else:
                    return self.async_create_entry(
                        title=node.name, data=data, unique_id=address
                    )

        if not self._choices:
            account = get_account(self.hass, entry)
            if account is None:
                return self.async_abort(reason="account_not_loaded")
            try:
                nodes = await account.async_get_nodes()
            except KubuAuthError:
                entry.async_start_reauth(self.hass)
                return self.async_abort(reason="reauth_required")
            except KubuCloudError:
                return self.async_abort(reason="cannot_connect")
            nearby = nearby_sensors(self.hass)
            # The ones the user removed may be added back by hand.
            taken = configured_sensors(self.hass, include_removed=False)
            for mac, node in nodes.items():
                if mac in taken:
                    continue
                self._choices[mac] = node
                self._addresses[mac] = nearby.get(mac, _format_mac(mac))
            if not self._choices:
                return self.async_abort(reason="no_sensors")

        nearby = nearby_sensors(self.hass)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            mac: f"{node.name} ({_format_mac(mac)})"
                            + ("" if mac in nearby else " – not nearby")
                            for mac, node in self._choices.items()
                        }
                    )
                }
            ),
            errors=errors,
            description_placeholders={"issues_url": ISSUES_URL},
        )
