"""给调试用的服务：直接发一条 FastCon 广播 / 原始 payload。"""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from . import fastcon
from .const import DOMAIN
from .coordinator import FastconHub

_LOGGER = logging.getLogger(__name__)

SERVICE_SEND_RAW = "send_raw"
SERVICE_SEND_LIGHT = "send_light"

SEND_RAW_SCHEMA = vol.Schema(
    {
        vol.Required("payload"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)

SEND_LIGHT_SCHEMA = vol.Schema(
    {
        vol.Required("light_id"): vol.All(int, vol.Range(min=0, max=255)),
        vol.Required("light_data"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """注册服务（幂等）。"""

    def _get_hub(entry_id: str | None) -> FastconHub:
        hubs: dict[str, FastconHub] = hass.data.get(DOMAIN, {})
        if not hubs:
            raise ValueError("没有已加载的启明智云 FastCon 配置")
        if entry_id:
            if entry_id not in hubs:
                raise ValueError(f"找不到配置项 {entry_id}")
            return hubs[entry_id]
        return next(iter(hubs.values()))

    async def _async_send_raw(call: ServiceCall) -> None:
        hub = _get_hub(call.data.get("entry_id"))
        payload = bytes.fromhex(call.data["payload"].replace(" ", ""))
        await hub.async_send_payload(payload)

    async def _async_send_light(call: ServiceCall) -> None:
        hub = _get_hub(call.data.get("entry_id"))
        data = bytes.fromhex(call.data["light_data"].replace(" ", ""))
        await hub.async_send_light(call.data["light_id"], data)

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_RAW):
        hass.services.async_register(
            DOMAIN, SERVICE_SEND_RAW, _async_send_raw, schema=SEND_RAW_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_LIGHT):
        hass.services.async_register(
            DOMAIN, SERVICE_SEND_LIGHT, _async_send_light, schema=SEND_LIGHT_SCHEMA
        )