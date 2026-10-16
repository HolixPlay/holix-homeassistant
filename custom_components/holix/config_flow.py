"""Setup: zeroconf/manual → pairing with the on-screen PIN → entry. Options: ambilight lights."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import instance_id
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import (
    DEFAULT_PATH,
    DEFAULT_PORT,
    PROTO_VERSION,
    HolixCommandError,
    HolixConnection,
    HolixError,
    HolixProtocolError,
)
from .const import (
    CONF_AMBIENT_FPS,
    CONF_AMBIENT_LIGHTS,
    CONF_CLIENT_ID,
    CONF_DEVICE_ID,
    CONF_PATH,
    CONF_TOKEN,
    DEFAULT_AMBIENT_FPS,
    DOMAIN,
    MAX_AMBIENT_FPS,
    ZONES,
)

_LOGGER = logging.getLogger(__name__)

PIN_ERRORS = {"invalid_pin", "pairing_not_started", "too_many_attempts"}


class HolixConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self.host = ""
        self.port = DEFAULT_PORT
        self.path = DEFAULT_PATH
        self.name = ""
        self.conn: HolixConnection | None = None

    # --- helpers ---------------------------------------------------------------
    async def _client_id(self) -> str:
        return await instance_id.async_get(self.hass)

    async def _open(self) -> None:
        await self._close()
        self.conn = HolixConnection(
            async_get_clientsession(self.hass), self.host, self.port, self.path
        )
        device = await self.conn.connect()
        self.name = device.name
        await self.async_set_unique_id(device.id, raise_on_progress=False)

    async def _close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def _start_pair(self) -> ConfigFlowResult:
        assert self.conn is not None
        await self.conn.pair_start(await self._client_id(), self.hass.config.location_name or "Home Assistant")
        return await self.async_step_pair()

    def async_remove(self) -> None:
        if self.conn is not None:
            self.hass.async_create_task(self._close())

    # --- steps -----------------------------------------------------------------
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self.host = user_input[CONF_HOST].strip()
            self.port = int(user_input[CONF_PORT])
            try:
                await self._open()
                self._abort_if_unique_id_configured(updates={CONF_HOST: self.host, CONF_PORT: self.port})
                return await self._start_pair()
            except HolixProtocolError:
                errors["base"] = "protocol"
            except HolixError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=self.host or vol.UNDEFINED): str,
                    vol.Required(CONF_PORT, default=self.port): vol.All(vol.Coerce(int), vol.Range(1, 65535)),
                }
            ),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        props = discovery_info.properties
        device_id = props.get("id")
        if not device_id or discovery_info.port is None:
            return self.async_abort(reason="invalid_discovery")
        try:
            if int(props.get("proto", "1")) > PROTO_VERSION:
                return self.async_abort(reason="unsupported_version")
        except ValueError:
            return self.async_abort(reason="invalid_discovery")
        self.host = discovery_info.host
        self.port = discovery_info.port
        self.path = props.get("path") or DEFAULT_PATH
        self.name = discovery_info.name.removesuffix("." + discovery_info.type)
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self.host, CONF_PORT: self.port, CONF_PATH: self.path}
        )
        self.context["title_placeholders"] = {CONF_NAME: self.name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            try:
                await self._open()
                return await self._start_pair()
            except HolixError:
                return self.async_abort(reason="cannot_connect")
        return self.async_show_form(
            step_id="zeroconf_confirm", description_placeholders={CONF_NAME: self.name}
        )

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and self.conn is not None:
            client_id = await self._client_id()
            try:
                token = await self.conn.pair_finish(client_id, user_input["pin"].strip())
                await self.conn.auth(client_id, token)
            except HolixCommandError as err:
                if err.code == "invalid_pin":
                    errors["base"] = "invalid_pin"
                elif err.code in PIN_ERRORS:
                    # expired / too many attempts: request a new PIN
                    try:
                        return await self._start_pair_with_error(err.code)
                    except HolixError:
                        return self.async_abort(reason="cannot_connect")
                else:
                    errors["base"] = "unknown"
            except HolixError:
                await self._close()
                return self.async_abort(reason="cannot_connect")
            else:
                await self._close()
                data = {
                    CONF_HOST: self.host,
                    CONF_PORT: self.port,
                    CONF_PATH: self.path,
                    CONF_CLIENT_ID: client_id,
                    CONF_TOKEN: token,
                    CONF_DEVICE_ID: self.unique_id,
                }
                if self.source == SOURCE_REAUTH:
                    entry = self._get_reauth_entry()
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                    return self.async_update_reload_and_abort(entry, data_updates=data)
                return self.async_create_entry(title=self.name, data=data)
        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({vol.Required("pin"): str}),
            description_placeholders={CONF_NAME: self.name},
            errors=errors,
        )

    async def _start_pair_with_error(self, code: str) -> ConfigFlowResult:
        assert self.conn is not None
        await self.conn.pair_start(await self._client_id(), self.hass.config.location_name or "Home Assistant")
        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({vol.Required("pin"): str}),
            description_placeholders={CONF_NAME: self.name},
            errors={"base": "pin_expired" if code == "pairing_not_started" else code},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self.host = entry_data[CONF_HOST]
        self.port = entry_data[CONF_PORT]
        self.path = entry_data.get(CONF_PATH, DEFAULT_PATH)
        self.name = self._get_reauth_entry().title
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._open()
                return await self._start_pair()
            except HolixError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="reauth_confirm", description_placeholders={CONF_NAME: self.name}, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HolixOptionsFlow:
        return HolixOptionsFlow()


class HolixOptionsFlow(OptionsFlow):
    """Use HA lights as ambilight targets: pick lights → assign a zone to each."""

    def __init__(self) -> None:
        self._lights: list[str] = []
        self._fps = DEFAULT_AMBIENT_FPS

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current: dict[str, str] = dict(self.config_entry.options.get(CONF_AMBIENT_LIGHTS) or {})
        if user_input is not None:
            self._lights = list(user_input.get("lights") or [])
            self._fps = int(user_input[CONF_AMBIENT_FPS])
            if not self._lights:
                return self.async_create_entry(data={CONF_AMBIENT_LIGHTS: {}, CONF_AMBIENT_FPS: self._fps})
            return await self.async_step_zones()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional("lights", default=list(current)): EntitySelector(
                        EntitySelectorConfig(domain="light", multiple=True)
                    ),
                    vol.Required(
                        CONF_AMBIENT_FPS,
                        default=self.config_entry.options.get(CONF_AMBIENT_FPS, DEFAULT_AMBIENT_FPS),
                    ): NumberSelector(NumberSelectorConfig(min=1, max=MAX_AMBIENT_FPS, step=1, mode="slider")),
                }
            ),
        )

    async def async_step_zones(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current: dict[str, str] = dict(self.config_entry.options.get(CONF_AMBIENT_LIGHTS) or {})
        if user_input is not None:
            mapping = {light: user_input[light] for light in self._lights}
            return self.async_create_entry(data={CONF_AMBIENT_LIGHTS: mapping, CONF_AMBIENT_FPS: self._fps})
        selector = SelectSelector(SelectSelectorConfig(options=ZONES, translation_key="zone"))
        return self.async_show_form(
            step_id="zones",
            data_schema=vol.Schema(
                {vol.Required(light, default=current.get(light, "average")): selector for light in self._lights}
            ),
        )
