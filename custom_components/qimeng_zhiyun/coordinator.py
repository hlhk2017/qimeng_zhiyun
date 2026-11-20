"""FastCon 灯组（一个 mesh）的运行时状态。"""

from __future__ import annotations

from collections.abc import Callable
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from . import fastcon
from .adv import BulbAdvertisement
from .const import (
    CONF_ADV_DURATION,
    CONF_LIGHT_STYLE,
    CONF_MESH_KEY,
    CONF_REPEAT,
    CONF_TRANSMITTER_TYPE,
    DEFAULT_ADV_DURATION,
    DEFAULT_REPEAT,
    DEFAULT_STYLE,
    STYLE_APP,
    TRANSPORT_TEXT,
)
from .transmitter import FastconTransmitter, TransmitError, build_transmitter

_LOGGER = logging.getLogger(__name__)


class Bulb:
    """一个灯泡的运行时信息。"""

    def __init__(
        self,
        hub: "FastconHub",
        light_id: int,
        address: str | None = None,
        name: str | None = None,
    ) -> None:
        self.hub = hub
        self.light_id = light_id
        self.address = address
        self.name = name
        self.rssi: int | None = None
        self.available = address is not None
        self.last_advertisement: BulbAdvertisement | None = None

    @property
    def unique_id(self) -> str:
        return f"{self.hub.entry.entry_id}_light_{self.light_id}"

    @property
    def suggested_name(self) -> str:
        return self.name or f"FastCon 灯 {self.light_id}"


class FastconHub:
    """一个 mesh（家庭）的出口：负责发现 + 发送。"""

    def __init__(self, hass: HomeAssistant, entry, options: dict) -> None:
        self.hass = hass
        self.entry = entry
        self.mesh_key = fastcon.mesh_key_from_str(options[CONF_MESH_KEY])
        self.transmitter: FastconTransmitter = build_transmitter(hass, options)
        self.adv_duration = int(options.get(CONF_ADV_DURATION, DEFAULT_ADV_DURATION))
        self.repeat = int(options.get(CONF_REPEAT, DEFAULT_REPEAT))
        self.transport = options.get(CONF_TRANSMITTER_TYPE, TRANSPORT_TEXT)
        self.light_style = options.get(CONF_LIGHT_STYLE, DEFAULT_STYLE)

        self._store: Store = Store(hass, 1, f"qimeng_zhiyun.{entry.entry_id}.state")
        # light_id -> {is_on, brightness, rgb_color, color_temp_kelvin, color_mode}
        self.saved_states: dict[int, dict] = {}

        self.bulbs: dict[int, Bulb] = {}
        # 只发现、还没分配 light_id 的灯泡：address -> 信息
        self.discovered: dict[str, dict] = {}
        self._by_address: dict[str, Bulb] = {}
        self._sequence = 0x20
        self._add_entities: Callable[[list], None] | None = None
        self._listeners: list[Callable[[], None]] = []

    async def async_load_states(self) -> None:
        """加载上次持久化的灯状态（单向灯泡，HA 侧自己记住）。"""
        data = await self._store.async_load()
        if isinstance(data, dict):
            for key, value in (data.get("lights") or {}).items():
                try:
                    self.saved_states[int(key)] = value
                except (TypeError, ValueError):
                    continue
        _LOGGER.debug("加载持久化灯状态: %s", self.saved_states)

    def remember_state(self, light_id: int, state: dict) -> None:
        """记录最后一次下发的状态并延迟落盘（HA 重启后恢复）。"""
        merged = dict(self.saved_states.get(light_id) or {})
        merged.update(state)
        self.saved_states[light_id] = merged
        self._store.async_delay_save(
            lambda: {
                "lights": {str(k): v for k, v in self.saved_states.items()}
            },
            3,
        )

    def build_light_data(self, **kwargs) -> bytes:
        """按配置的格式（标准 6 字节 / App 7 字节）构造 light_data。"""
        if self.light_style == STYLE_APP:
            return fastcon.light_data_app(**kwargs)
        return fastcon.light_data(**kwargs)

    # ---------------------------------------------------------------- 发送
    @property
    def sequence(self) -> int:
        return self._sequence

    def next_sequence(self) -> int:
        self._sequence += 1
        if self._sequence > 0xFE:
            self._sequence = 0x10
        return self._sequence

    async def async_send_light(self, light_id: int, light_data: bytes) -> None:
        payload = fastcon.single_control(
            light_id, self.mesh_key, light_data, sequence=self.next_sequence()
        )
        await self._async_send(payload)

    async def async_send_group(self, group_id: int, light_data: bytes) -> None:
        payload = fastcon.group_control(
            group_id, self.mesh_key, light_data, sequence=self.next_sequence()
        )
        await self._async_send(payload)

    async def async_send_set_group(self, light_id: int, group_id: int) -> None:
        payload = fastcon.set_group(
            light_id, self.mesh_key, group_id, sequence=self.next_sequence()
        )
        await self._async_send(payload)

    async def async_send_payload(self, payload: bytes) -> None:
        """直接发一条已经算好的 payload（调试用）。"""
        await self._async_send(payload)

    async def _async_send(self, payload: bytes) -> None:
        _LOGGER.debug(
            "FastCon 广播 light payload=%s (%s)",
            payload.hex().upper(),
            self.transmitter.describe(),
        )
        try:
            await self.transmitter.async_send(payload)
        except TransmitError as err:
            _LOGGER.warning("FastCon 发射失败: %s", err)
            raise

    # ---------------------------------------------------------------- 发现
    def get_light(self, light_id: int) -> Bulb | None:
        return self.bulbs.get(light_id)

    def add_light(self, light_id: int, address: str | None = None, name: str | None = None) -> Bulb:
        bulb = self.bulbs.get(light_id)
        if bulb is None:
            bulb = Bulb(self, light_id, address=address, name=name)
            self.bulbs[light_id] = bulb
            if address:
                self._by_address[address] = bulb
            if self._add_entities is not None:
                from .light import FastconLight

                self._add_entities([FastconLight(self, bulb)])
                _LOGGER.info("发现新的 FastCon 灯: light_id=%s 地址=%s", light_id, address)
        else:
            if address and bulb.address != address:
                bulb.address = address
                self._by_address[address] = bulb
                bulb.available = True
            if name and not bulb.name:
                bulb.name = name
        for listener in list(self._listeners):
            listener()
        return bulb

    def async_handle_advertisement(self, advertisement: BulbAdvertisement) -> None:
        if advertisement.kind in ("unknown", "unparsed", "control", "group"):
            return
        now = time.monotonic()
        info = self.discovered.setdefault(
            advertisement.address,
            {"address": advertisement.address, "first_seen": now},
        )
        info.update(
            {
                "last_seen": now,
                "rssi": advertisement.rssi,
                "kind": advertisement.kind,
                "light_id": advertisement.light_id,
                "name": advertisement.name,
                "did": advertisement.address.replace(":", ""),
                "data": advertisement.data.hex().upper(),
            }
        )
        bulb = self._by_address.get(advertisement.address)
        if bulb is None and advertisement.light_id is not None:
            bulb = self.add_light(advertisement.light_id, address=advertisement.address)
        if bulb is None:
            return
        bulb.last_advertisement = advertisement
        bulb.rssi = advertisement.rssi
        if advertisement.name and not bulb.name:
            bulb.name = advertisement.name
        if advertisement.address and bulb.address is None:
            bulb.address = advertisement.address
            self._by_address[advertisement.address] = bulb
        bulb.available = True
        for listener in list(self._listeners):
            listener()

    def register_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def set_add_entities(self, func: Callable[[list], None]) -> None:
        self._add_entities = func