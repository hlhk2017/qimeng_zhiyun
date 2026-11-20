"""把 payload 广播出去的几种后端。

1. TEXT    : 由我们编译的 ESPHome 发射器固件提供 text 实体（推荐，最稳）
2. SERVICE : ESPHome 用户动作（`api: actions:`），HA 里会生成 `esphome.<action>` 服务
3. BLUEZ   : 本机蓝牙适配器（实验性，需要 BlueZ + dbus）
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from .const import TRANSPORT_BLUEZ, TRANSPORT_SERVICE, TRANSPORT_TEXT

_LOGGER = logging.getLogger(__name__)


class TransmitError(Exception):
    """发送失败。"""


class FastconTransmitter:
    """发射器基类。"""

    def __init__(self, hass: HomeAssistant, adv_duration: int = 60, repeat: int = 2) -> None:
        self.hass = hass
        self.adv_duration = adv_duration
        self.repeat = repeat

    async def async_send(self, payload: bytes) -> None:
        raise NotImplementedError

    def describe(self) -> str:
        return type(self).__name__


class TextTransmitter(FastconTransmitter):
    """通过 ESPHome 固件里的 text 实体下发原始 payload（十六进制）。"""

    def __init__(self, hass, entity_id: str, adv_duration=60, repeat=2) -> None:
        super().__init__(hass, adv_duration, repeat)
        self.entity_id = entity_id

    def describe(self) -> str:
        return f"text:{self.entity_id}"

    async def async_send(self, payload: bytes) -> None:
        state = self.hass.states.get(self.entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            raise TransmitError(f"发射器实体不可用: {self.entity_id}")
        value = payload.hex()
        # ESPHome 的 text 实体只在字符串"变化"时触发 on_value：
        # 同一 payload 连发（或 sequence 刚好相同）时先清空，确保一定触发。
        if state is not None and state.state == value:
            await self.hass.services.async_call(
                "text",
                "set_value",
                {"entity_id": self.entity_id, "value": ""},
                blocking=True,
            )
            await asyncio.sleep(0.12)
        for _ in range(max(1, self.repeat)):
            await self.hass.services.async_call(
                "text",
                "set_value",
                {"entity_id": self.entity_id, "value": value},
                blocking=True,
            )
            if self.repeat > 1:
                await asyncio.sleep(0.05)


class FanoutTransmitter(FastconTransmitter):
    """把同一条 payload 同时交给多个发射器（不同房间的代理都能广播）。"""

    def __init__(self, transmitters: list[FastconTransmitter]) -> None:
        super().__init__(transmitters[0].hass, transmitters[0].adv_duration, 1)
        self.transmitters = transmitters

    def describe(self) -> str:
        return "fanout[" + ", ".join(t.describe() for t in self.transmitters) + "]"

    async def async_send(self, payload: bytes) -> None:
        results = await asyncio.gather(
            *(t.async_send(payload) for t in self.transmitters),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            _LOGGER.warning(
                "部分发射器发送失败（%s/%s）: %s",
                len(errors),
                len(results),
                "; ".join(str(e) for e in errors[:3]),
            )
            if len(errors) == len(results):
                raise TransmitError("所有发射器都发送失败")


class ServiceTransmitter(FastconTransmitter):
    """通过 ESPHome 用户动作服务下发原始 payload。"""

    def __init__(
        self,
        hass,
        service: str,
        device_id: str | None = None,
        adv_duration=60,
        repeat=2,
    ) -> None:
        super().__init__(hass, adv_duration, repeat)
        self.service = service
        self.device_id = device_id or None

    def describe(self) -> str:
        return f"service:{self.service}"

    async def async_send(self, payload: bytes) -> None:
        if "." in self.service:
            domain, service = self.service.split(".", 1)
        else:
            domain, service = "esphome", self.service
        target = {"device_id": self.device_id} if self.device_id else None
        for _ in range(max(1, self.repeat)):
            await self.hass.services.async_call(
                domain,
                service,
                {"payload": payload.hex()},
                blocking=True,
                target=target,
            )
            if self.repeat > 1:
                await asyncio.sleep(0.05)


class BluezTransmitter(FastconTransmitter):
    """用本机 BlueZ 适配器直接注册一个 LE 广播（实验性）。"""

    def __init__(self, hass, adapter: str = "hci0", adv_duration=60, repeat=2) -> None:
        super().__init__(hass, adv_duration, repeat)
        self.adapter = adapter or "hci0"
        self._bus = None
        self._counter = 0

    def describe(self) -> str:
        return f"bluez:{self.adapter}"

    async def _async_bus(self):
        if self._bus is not None:
            return self._bus
        try:
            from dbus_fast.aio import MessageBus
        except ImportError as err:  # pragma: no cover
            raise TransmitError("缺少 dbus-fast，无法使用本机蓝牙广播") from err
        self._bus = await MessageBus().connect()
        return self._bus

    async def async_send(self, payload: bytes) -> None:
        from dbus_fast import Variant
        from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property

        class Advertisement(ServiceInterface):
            def __init__(self, manufacturer_data: dict[int, bytes]) -> None:
                super().__init__("org.bluez.LEAdvertisement1")
                self._manufacturer_data = manufacturer_data

            @dbus_property(access=PropertyAccess.READ)
            def Type(self) -> "s":  # noqa: N802
                return "broadcast"

            @dbus_property(access=PropertyAccess.READ)
            def ManufacturerData(self) -> "a{qv}":  # noqa: N802
                return {
                    key: Variant("ay", list(value))
                    for key, value in self._manufacturer_data.items()
                }

            @dbus_property(access=PropertyAccess.READ)
            def Includes(self) -> "as":  # noqa: N802
                return []

            def Release(self) -> None:  # noqa: N802
                return None

        bus = await self._async_bus()
        self._counter += 1
        path = f"/com/qimeng/zhiyun/adv{self._counter % 200}"
        advertisement = Advertisement({0xFFF0: bytes(payload)})
        bus.export(path, advertisement)
        adapter_path = f"/org/bluez/{self.adapter}"
        try:
            introspection = await bus.introspect("org.bluez", adapter_path)
            obj = bus.get_proxy_object("org.bluez", adapter_path, introspection)
            manager = obj.get_interface("org.bluez.LEAdvertisingManager1")
            await manager.call_register_advertisement(path, {})
            await asyncio.sleep(max(0.02, self.adv_duration / 1000))
        except Exception as err:  # pragma: no cover - 依赖真实适配器
            raise TransmitError(f"BlueZ 广播失败: {err}") from err
        finally:
            try:
                await manager.call_unregister_advertisement(path)  # type: ignore[name-defined]
            except Exception:  # pragma: no cover
                pass
            try:
                bus.unexport(path)
            except Exception:  # pragma: no cover
                pass


def build_transmitter(hass: HomeAssistant, options: dict) -> FastconTransmitter:
    """按配置构造发射器。"""
    from .const import (
        CONF_ADV_DURATION,
        CONF_BLUEZ_ADAPTER,
        CONF_REPEAT,
        CONF_TRANSMITTER_DEVICE,
        CONF_TRANSMITTER_ENTITY,
        CONF_TRANSMITTER_SERVICE,
        CONF_TRANSMITTER_TYPE,
        DEFAULT_ADV_DURATION,
        DEFAULT_BLUEZ_ADAPTER,
        DEFAULT_REPEAT,
        DEFAULT_SERVICE,
        TRANSPORT_BLUEZ,
        TRANSPORT_TEXT,
    )

    adv_duration = int(options.get(CONF_ADV_DURATION, DEFAULT_ADV_DURATION))
    repeat = int(options.get(CONF_REPEAT, DEFAULT_REPEAT))
    transport = options.get(CONF_TRANSMITTER_TYPE, TRANSPORT_TEXT)

    if transport == TRANSPORT_TEXT:
        entities = list(options.get("transmitter_entities") or [])
        single = options.get(CONF_TRANSMITTER_ENTITY)
        if single and single not in entities:
            entities.append(single)
        entities = [e for e in entities if e]
        if not entities:
            raise TransmitError("未配置发射器 text 实体")
        transmitters = [
            TextTransmitter(hass, entity, adv_duration, repeat) for entity in entities
        ]
        if len(transmitters) == 1:
            return transmitters[0]
        return FanoutTransmitter(transmitters)
    if transport == TRANSPORT_SERVICE:
        return ServiceTransmitter(
            hass,
            options.get(CONF_TRANSMITTER_SERVICE, DEFAULT_SERVICE),
            options.get(CONF_TRANSMITTER_DEVICE),
            adv_duration,
            repeat,
        )
    if transport == TRANSPORT_BLUEZ:
        return BluezTransmitter(
            hass,
            options.get(CONF_BLUEZ_ADAPTER, DEFAULT_BLUEZ_ADAPTER),
            adv_duration,
            repeat,
        )
    raise TransmitError(f"未知发射器类型: {transport}")