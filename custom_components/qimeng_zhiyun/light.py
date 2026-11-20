"""FastCon 灯实体。"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import fastcon
from .const import DOMAIN, MANUFACTURER
from .coordinator import Bulb, FastconHub

_LOGGER = logging.getLogger(__name__)

MIN_KELVIN = 2000  # 500 mireds
MAX_KELVIN = 6535  # 153 mireds


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """建立该 mesh 下的所有灯实体。"""
    hub: FastconHub = hass.data[DOMAIN][entry.entry_id]
    hub.set_add_entities(async_add_entities)

    entities = [FastconLight(hub, bulb) for bulb in list(hub.bulbs.values())]
    if entities:
        async_add_entities(entities, True)


class FastconLight(RestoreEntity, LightEntity):
    """一盏 FastCon 彩灯。

    灯泡是单向接收端、**不回报状态**，也没有 GATT 可读，所以：
      * 对外不声明 `assumed_state`（否则 HA 用两个闪电按钮代替开关），状态由集成自己维护
      * 本地记住最后一次下发的 开关/亮度/颜色/色温，并**持久化到 HA 的 restore 状态**，
        HA 重启后恢复上次的状态，而不是掉回 off
      * 任何在 App 里手动改灯的操作都不会同步回来（这是协议限制，不是 bug）
    """

    # 不声明 assumed_state：否则 HA 的开关行会渲染成两个闪电按钮(flash-off/flash)，
    # 而不是普通开关。状态由集成自己维护并持久化，等价于"以 HA 下发为准"。
    _attr_assumed_state = False
    _attr_has_entity_name = True
    _attr_supported_color_modes = {ColorMode.RGB, ColorMode.COLOR_TEMP}
    _attr_min_color_temp_kelvin = MIN_KELVIN
    _attr_max_color_temp_kelvin = MAX_KELVIN
    _attr_color_mode = ColorMode.RGB

    def __init__(self, hub: FastconHub, bulb: Bulb) -> None:
        self.hub = hub
        self.bulb = bulb
        self._attr_unique_id = bulb.unique_id
        self._attr_name = None
        self._attr_is_on = False
        self._attr_brightness = 255
        self._attr_rgb_color = (255, 255, 255)
        self._attr_color_temp_kelvin = 4000
        self._known_color: str | None = None  # "rgb" / "temp" / None

    # ------------------------------------------------------------------ HA
    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.bulb.unique_id)},
            name=self.bulb.suggested_name,
            manufacturer=MANUFACTURER,
            model="FastCon BLE Light",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_restore_state()
        self.async_on_remove(self.hub.register_listener(self._handle_hub_update))

    async def _async_restore_state(self) -> None:
        """恢复 HA 重启前最后一次下发/假定的状态。

        主要来源是我们自己的 Store（见 coordinator），每次下发都会记；restore_state 作兜底。
        """
        saved = self.hub.saved_states.get(self.bulb.light_id)
        if saved:
            self._attr_is_on = bool(saved.get("is_on"))
            self._attr_brightness = saved.get("brightness") or self._attr_brightness
            if saved.get("rgb_color"):
                self._attr_rgb_color = tuple(saved["rgb_color"])
            if saved.get("color_temp_kelvin"):
                self._attr_color_temp_kelvin = int(saved["color_temp_kelvin"])
            mode = saved.get("color_mode")
            if mode:
                self._attr_color_mode = ColorMode(mode)
                self._known_color = (
                    "rgb" if mode == ColorMode.RGB else "temp" if mode == ColorMode.COLOR_TEMP else None
                )
            _LOGGER.debug("从 Store 恢复灯 %s 状态: %s", self.bulb.light_id, saved)
            return
        last_state = await self.async_get_last_state()
        if last_state is None:
            return
        attributes = last_state.attributes
        self._attr_is_on = last_state.state == STATE_ON
        if (brightness := attributes.get(ATTR_BRIGHTNESS)) is not None:
            self._attr_brightness = int(brightness)
        if (rgb := attributes.get(ATTR_RGB_COLOR)) is not None:
            self._attr_rgb_color = tuple(rgb)
        if (kelvin := attributes.get(ATTR_COLOR_TEMP_KELVIN)) is not None:
            self._attr_color_temp_kelvin = int(kelvin)
        color_mode = attributes.get(ATTR_COLOR_MODE)
        if color_mode:
            self._attr_color_mode = ColorMode(color_mode)
            if color_mode == ColorMode.RGB:
                self._known_color = "rgb"
            elif color_mode == ColorMode.COLOR_TEMP:
                self._known_color = "temp"

    @callback
    def _handle_hub_update(self) -> None:
        self._attr_available = self.bulb.available
        self.async_write_ha_state()

    # --------------------------------------------------------------- 控制
    def _light_data(
        self,
        *,
        is_on: bool,
        brightness: int | None = None,
        rgb: tuple[int, int, int] | None = None,
        kelvin: int | None = None,
    ) -> bytes:
        if not is_on:
            return self.hub.build_light_data(is_on=False)
        level = self._attr_brightness if brightness is None else brightness
        if rgb is not None:
            return self.hub.build_light_data(is_on=True, brightness=level, rgb=rgb)
        if kelvin is not None:
            return self.hub.build_light_data(
                is_on=True, brightness=level, color_temp_kelvin=kelvin
            )
        # 只开关：沿用灯泡自己的颜色，只发亮度字节
        return self.hub.build_light_data(is_on=True, brightness=level)

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)

        if rgb is not None:
            data = self._light_data(is_on=True, brightness=brightness, rgb=rgb)
            self._attr_color_mode = ColorMode.RGB
        elif kelvin is not None:
            data = self._light_data(is_on=True, brightness=brightness, kelvin=kelvin)
            self._attr_color_mode = ColorMode.COLOR_TEMP
        elif self._known_color == "rgb":
            data = self._light_data(
                is_on=True, brightness=brightness, rgb=self._attr_rgb_color
            )
        elif self._known_color == "temp":
            data = self._light_data(
                is_on=True, brightness=brightness, kelvin=self._attr_color_temp_kelvin
            )
        else:
            data = self._light_data(is_on=True, brightness=brightness)

        await self.hub.async_send_light(self.bulb.light_id, data)

        self._attr_is_on = True
        if brightness is not None:
            self._attr_brightness = brightness
        if rgb is not None:
            self._attr_rgb_color = rgb
            self._known_color = "rgb"
        elif kelvin is not None:
            self._attr_color_temp_kelvin = kelvin
            self._known_color = "temp"
        self.hub.remember_state(
            self.bulb.light_id,
            {
                "is_on": True,
                "brightness": self._attr_brightness,
                "rgb_color": list(self._attr_rgb_color) if self._attr_rgb_color else None,
                "color_temp_kelvin": self._attr_color_temp_kelvin,
                "color_mode": str(self._attr_color_mode) if self._attr_color_mode else None,
            },
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_send_light(self.bulb.light_id, self.hub.build_light_data(is_on=False))
        self._attr_is_on = False
        self.hub.remember_state(self.bulb.light_id, {"is_on": False})
        self.async_write_ha_state()