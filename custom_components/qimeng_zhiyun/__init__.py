"""启明智云 FastCon 蓝牙灯 —— HA 集成入口。

一块 ESP32（ESPHome 固件）当 BLE 广播发射器；本集成负责：
  * 通过 HA 的 bluetooth 集成（本机适配器或 ESPHome 蓝牙代理）被动扫描 FastCon 灯广播 → 自动发现
  * 解析/构造 FastCon 协议帧（纯 Python，见 fastcon.py）
  * 把命令交给发射器广播出去
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback

from .adv import parse_advertisement
from .const import CONF_LIGHTS, CONF_MESH_KEY, DOMAIN
from .coordinator import FastconHub
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.LIGHT]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """注册调试服务。"""
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立配置项。"""
    options = {**entry.data, **entry.options}
    if CONF_MESH_KEY not in options:
        _LOGGER.error("配置项缺少 mesh_key")
        return False

    hub = FastconHub(hass, entry, options)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub

    # 手动配置的灯（自动发现的灯来自蓝牙广播）
    for light_conf in options.get(CONF_LIGHTS, []):
        try:
            hub.add_light(
                int(light_conf["light_id"]),
                address=light_conf.get("address"),
                name=light_conf.get("name"),
            )
        except (KeyError, TypeError, ValueError):
            _LOGGER.warning("忽略无效的手动灯配置: %s", light_conf)

    await hub.async_load_states()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    unsub = _async_register_bluetooth(hass, hub)
    if unsub is not None:
        entry.async_on_unload(unsub)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _LOGGER.info(
        "启明智云 FastCon 已启动：%s，发射器 %s，已配置灯 %s 个",
        entry.title,
        hub.transmitter.describe(),
        len(hub.bulbs),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载配置项。"""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项变化后重载。"""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_register_bluetooth(hass: HomeAssistant, hub: FastconHub):
    """注册 FastCon 广播回调（需要 HA 的 bluetooth 集成）。"""
    try:
        from homeassistant.components.bluetooth import (
            BluetoothCallbackMatcher,
            BluetoothScanningMode,
            async_register_callback,
        )
    except ImportError:  # pragma: no cover
        _LOGGER.warning("未找到 bluetooth 集成，自动发现不可用")
        return None

    from .const import COMPANY_ID

    @callback
    def _async_on_advertisement(service_info, change) -> None:
        # 诊断：所有 0xFFF0 广播
        if COMPANY_ID in service_info.manufacturer_data:
            _LOGGER.debug(
                "收到 FastCon 广播 %s rssi=%s data=%s",
                service_info.address,
                service_info.rssi,
                bytes(service_info.manufacturer_data[COMPANY_ID]).hex().upper(),
            )
        payload = service_info.manufacturer_data.get(COMPANY_ID)
        if not payload:
            return
        advertisement = parse_advertisement(
            service_info.address,
            bytes(payload),
            mesh_key=hub.mesh_key,
            rssi=service_info.rssi,
            name=service_info.name,
        )
        if advertisement is None:
            return
        _LOGGER.debug(
            "FastCon 广播 %s kind=%s light_id=%s data=%s",
            service_info.address,
            advertisement.kind,
            advertisement.light_id,
            advertisement.data.hex().upper(),
        )
        hub.async_handle_advertisement(advertisement)

    return async_register_callback(
        hass,
        _async_on_advertisement,
        BluetoothCallbackMatcher(manufacturer_id=COMPANY_ID),
        BluetoothScanningMode.PASSIVE,
    )