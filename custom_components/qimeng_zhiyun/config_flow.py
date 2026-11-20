"""配置流程。"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, selector

from .const import (
    CONF_ADV_DURATION,
    CONF_LIGHT_STYLE,
    CONF_TRANSMITTER_ENTITIES,
    CONF_BLUEZ_ADAPTER,
    CONF_LIGHTS,
    CONF_MESH_KEY,
    CONF_REPEAT,
    CONF_TRANSMITTER_DEVICE,
    CONF_TRANSMITTER_ENTITY,
    CONF_TRANSMITTER_SERVICE,
    CONF_TRANSMITTER_TYPE,
    DEFAULT_ADV_DURATION,
    DEFAULT_BLUEZ_ADAPTER,
    DEFAULT_REPEAT,
    DEFAULT_SERVICE,
    DEFAULT_STYLE,
    DOMAIN,
    STYLE_LABELS,
    TRANSPORT_BLUEZ,
    TRANSPORT_LABELS,
    TRANSPORT_SERVICE,
    TRANSPORT_TEXT,
)
from .fastcon import mesh_key_from_str

_LOGGER = logging.getLogger(__name__)

CONF_ACTION = "action"
ACTION_SAVE = "save"
ACTION_ADD_LIGHT = "add_light"
ACTION_SCAN = "scan"


def _mesh_schema(defaults: dict[str, Any], *, with_action: bool = False) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(
            CONF_MESH_KEY, default=defaults.get(CONF_MESH_KEY, "")
        ): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)),
        vol.Required(
            CONF_TRANSMITTER_TYPE,
            default=defaults.get(CONF_TRANSMITTER_TYPE, TRANSPORT_TEXT),
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(TRANSPORT_LABELS.keys()),
                mode=selector.SelectSelectorMode.LIST,
                translation_key="transmitter_type",
            )
        ),
        vol.Optional(
            CONF_ADV_DURATION,
            default=defaults.get(CONF_ADV_DURATION, DEFAULT_ADV_DURATION),
        ): vol.All(int, vol.Range(min=10, max=500)),
        vol.Optional(
            CONF_REPEAT, default=defaults.get(CONF_REPEAT, DEFAULT_REPEAT)
        ): vol.All(int, vol.Range(min=1, max=5)),
        vol.Optional(
            CONF_LIGHT_STYLE, default=defaults.get(CONF_LIGHT_STYLE, DEFAULT_STYLE)
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(STYLE_LABELS.keys()),
                mode=selector.SelectSelectorMode.LIST,
                translation_key="light_style",
            )
        ),
    }
    if with_action:
        fields[
            vol.Optional(CONF_ACTION, default=ACTION_SAVE)
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[ACTION_SAVE, ACTION_ADD_LIGHT, ACTION_SCAN],
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="options_action",
            )
        )
    return vol.Schema(fields)


def _transmitter_schema(transport: str, defaults: dict[str, Any]) -> vol.Schema:
    if transport == TRANSPORT_TEXT:
        current = list(defaults.get(CONF_TRANSMITTER_ENTITIES) or [])
        single = defaults.get(CONF_TRANSMITTER_ENTITY)
        if single and single not in current:
            current.append(single)
        selector_config = selector.EntitySelectorConfig(domain="text", multiple=True)
        if current:
            return vol.Schema(
                {
                    vol.Required(
                        CONF_TRANSMITTER_ENTITIES, default=current
                    ): selector.EntitySelector(selector_config)
                }
            )
        return vol.Schema(
            {
                vol.Required(CONF_TRANSMITTER_ENTITIES): selector.EntitySelector(
                    selector_config
                )
            }
        )
    if transport == TRANSPORT_SERVICE:
        return vol.Schema(
            {
                vol.Required(
                    CONF_TRANSMITTER_SERVICE,
                    default=defaults.get(CONF_TRANSMITTER_SERVICE, DEFAULT_SERVICE),
                ): cv.string,
                vol.Optional(
                    CONF_TRANSMITTER_DEVICE,
                    default=defaults.get(CONF_TRANSMITTER_DEVICE) or vol.UNDEFINED,
                ): cv.string,
            }
        )
    return vol.Schema(
        {
            vol.Required(
                CONF_BLUEZ_ADAPTER,
                default=defaults.get(CONF_BLUEZ_ADAPTER, DEFAULT_BLUEZ_ADAPTER),
            ): cv.string,
        }
    )


class QimengZhiyunConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """配置流程。"""

    VERSION = 1

    def __init__(self) -> None:
        self._defaults: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """第一步：mesh key + 发射器类型。"""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                mesh_key_from_str(user_input[CONF_MESH_KEY])
            except ValueError:
                errors[CONF_MESH_KEY] = "invalid_mesh_key"
            if not errors:
                self._defaults = dict(user_input)
                return await self.async_step_transmitter()
        return self.async_show_form(
            step_id="user", data_schema=_mesh_schema(self._defaults), errors=errors
        )

    async def async_step_transmitter(self, user_input: dict[str, Any] | None = None):
        """第二步：发射器细节。"""
        transport = self._defaults[CONF_TRANSMITTER_TYPE]
        if user_input is not None:
            data = {**self._defaults, **user_input}
            return self.async_create_entry(
                title=f"启明智云 FastCon（{TRANSPORT_LABELS.get(transport, transport)}）",
                data=data,
            )
        return self.async_show_form(
            step_id="transmitter",
            data_schema=_transmitter_schema(transport, self._defaults),
            description_placeholders={
                "transport": TRANSPORT_LABELS.get(transport, transport)
            },
        )

    async def async_step_bluetooth(self, discovery_info):
        """蓝牙自动发现：已有条目时忽略，避免刷屏。"""
        await self.async_set_unique_id("qimeng_zhiyun_mesh")
        self._abort_if_unique_id_configured()
        if self.hass.config_entries.async_entries(DOMAIN):
            return self.async_abort(reason="already_configured")
        return await self.async_step_user()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return QimengZhiyunOptionsFlow()


class QimengZhiyunOptionsFlow(config_entries.OptionsFlow):
    """选项：改 mesh key / 发射器，或手动添加灯。"""

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            action = user_input.pop(CONF_ACTION, ACTION_SAVE)
            try:
                mesh_key_from_str(user_input[CONF_MESH_KEY])
            except ValueError:
                errors[CONF_MESH_KEY] = "invalid_mesh_key"
            if not errors:
                self._pending = {**current, **user_input}
                if action == ACTION_ADD_LIGHT:
                    return await self.async_step_add_light()
                if action == ACTION_SCAN:
                    return await self.async_step_scan()
                return await self.async_step_transmitter()
        return self.async_show_form(
            step_id="init",
            data_schema=_mesh_schema(current, with_action=True),
            errors=errors,
        )

    async def async_step_transmitter(self, user_input: dict[str, Any] | None = None):
        transport = self._pending[CONF_TRANSMITTER_TYPE]
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self._pending, **user_input}
            )
        return self.async_show_form(
            step_id="transmitter",
            data_schema=_transmitter_schema(transport, self._pending),
            description_placeholders={
                "transport": TRANSPORT_LABELS.get(transport, transport)
            },
        )

    async def async_step_add_light(self, user_input: dict[str, Any] | None = None):
        current = self._pending
        lights = list(current.get(CONF_LIGHTS, []))
        errors: dict[str, str] = {}
        if user_input is not None:
            light_id = int(user_input["light_id"])
            if any(int(item.get("light_id", -1)) == light_id for item in lights):
                errors["light_id"] = "already_exists"
            else:
                lights.append(
                    {"light_id": light_id, "name": user_input.get("name") or None}
                )
                return self.async_create_entry(
                    title="", data={**current, CONF_LIGHTS: lights}
                )
        return self.async_show_form(
            step_id="add_light",
            data_schema=vol.Schema(
                {
                    vol.Required("light_id"): vol.All(int, vol.Range(min=1, max=255)),
                    vol.Optional("name", default=""): cv.string,
                }
            ),
            errors=errors,
        )
    async def async_step_scan(self, user_input: dict[str, Any] | None = None):
        """扫描附近广播中的 FastCon 灯，选一个分配 light_id。"""
        errors: dict[str, str] = {}
        discovered: dict[str, dict] = {}
        try:
            from homeassistant.components.bluetooth import (
                BluetoothCallbackMatcher,
                BluetoothScanningMode,
                async_process_advertisements,
            )
            from .adv import parse_advertisement
            from .const import COMPANY_ID, CONF_MESH_KEY
            from .fastcon import mesh_key_from_str

            mesh_key = mesh_key_from_str(self._pending[CONF_MESH_KEY])

            def _match(service_info) -> bool:
                payload = service_info.manufacturer_data.get(COMPANY_ID)
                if not payload:
                    return False
                adv = parse_advertisement(
                    service_info.address,
                    bytes(payload),
                    mesh_key=mesh_key,
                    rssi=service_info.rssi,
                    name=service_info.name,
                )
                if adv is None or adv.kind in ("tx-echo", "control", "group"):
                    return False
                discovered[service_info.address] = {
                    "address": service_info.address,
                    "rssi": service_info.rssi,
                    "name": adv.name,
                    "kind": adv.kind,
                    "light_id": adv.light_id,
                }
                return False

            try:
                await async_process_advertisements(
                    self.hass,
                    _match,
                    BluetoothCallbackMatcher(manufacturer_id=COMPANY_ID),
                    BluetoothScanningMode.ACTIVE,
                    timeout=8,
                )
            except Exception:  # noqa: BLE001 - 超时即结束扫描
                pass
        except ImportError:
            errors["base"] = "bluetooth_unavailable"

        if user_input is not None:
            address = user_input["address"]
            light_id = int(user_input["light_id"])
            lights = list(self._pending.get(CONF_LIGHTS, []))
            if any(int(item.get("light_id", -1)) == light_id for item in lights):
                errors["light_id"] = "already_exists"
            else:
                lights.append(
                    {
                        "light_id": light_id,
                        "name": user_input.get("name") or discovered.get(address, {}).get("name"),
                        "address": address,
                    }
                )
                return self.async_create_entry(
                    title="", data={**self._pending, CONF_LIGHTS: lights}
                )

        if not discovered:
            return self.async_show_form(
                step_id="scan_empty",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        options = [
            f"{addr}（{info.get('name') or '未命名'}，RSSI {info.get('rssi')}）"
            for addr, info in discovered.items()
        ]
        mapping = {
            f"{addr}（{info.get('name') or '未命名'}，RSSI {info.get('rssi')}）": addr
            for addr, info in discovered.items()
        }
        schema = vol.Schema(
            {
                vol.Required("address"): vol.In(options),
                vol.Required("light_id", default=1): vol.All(
                    int, vol.Range(min=1, max=255)
                ),
                vol.Optional("name", default=""): cv.string,
            }
        )
        return self.async_show_form(
            step_id="scan",
            data_schema=schema,
            errors=errors,
            description_placeholders={"count": str(len(discovered))},
        )

    async def async_step_scan_empty(self, user_input: dict[str, Any] | None = None):
        """扫描没结果时的提示页。"""
        return self.async_show_form(step_id="scan_empty", data_schema=vol.Schema({}))
